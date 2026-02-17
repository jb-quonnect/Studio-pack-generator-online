"""
Lunii Pack Converter

Converts Studio Pack ZIP (story.json + assets/) into the native Lunii format:
- BMP 4-bit Grayscale RLE images
- MP3 Mono 44100Hz 64kbps (no ID3 tags)
- XXTEA encryption (V2) or AES-CBC (V3)
- Binary index files (.ni, .li, .ri, .si, .bt)
- Structured .content/REF/ directory

Binary formats strictly match olup/lunii-admin-web reference implementation.
"""

import io
import json
import logging
import os
import struct
import subprocess
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

# XXTEA V2 Common Key
XXTEA_KEY_V2 = bytes([
    0x91, 0xBD, 0x7A, 0x0A, 0xA7, 0x54, 0x40, 0xA9,
    0xBB, 0xD4, 0x9D, 0x6C, 0xE0, 0xDC, 0xC0, 0xE3
])
_XXTEA_DELTA = 0x9E3779B9

# Encryption block size (first 512 bytes of each asset are encrypted)
ENCRYPTION_BLOCK_SIZE = 512

# Target dimensions for Lunii images
LUNII_IMAGE_WIDTH = 320
LUNII_IMAGE_HEIGHT = 240

# NI file constants
NI_HEADER_SIZE = 512
NI_NODE_SIZE = 44

# Blank MP3 — minimal valid MP3 frame (silence, ~26ms)
# This is used for stage nodes that have no audio
BLANK_MP3 = bytes([
    0xFF, 0xFB, 0x90, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x58, 0x69, 0x6E, 0x67,
    0x00, 0x00, 0x00, 0x0F, 0x00, 0x00, 0x00, 0x01,
    0x00, 0x00, 0x00, 0x68, 0x00, 0x10, 0x20, 0x30,
    0x40, 0x50, 0x60, 0x70, 0x80, 0x90, 0xA0, 0xB0,
    0xC0, 0xD0, 0xE0, 0xFF,
] + [0x00] * 36)

# ─── XXTEA Encryption (V2) ───────────────────────────────────────────────────


def _bytes_to_uint32_le(data: bytes) -> List[int]:
    """Convert bytes to list of uint32 (little-endian), matching reference toUint32Array(data, true)."""
    n = len(data) >> 2
    if len(data) & 3:
        n += 1
    v = [0] * n
    for i in range(len(data)):
        v[i >> 2] |= data[i] << ((i & 3) << 3)
    return v


def _bytes_to_uint32_be(data: bytes) -> List[int]:
    """Convert bytes to list of uint32 (big-endian key), matching reference toUint32Array(key, false)."""
    length = len(data)
    n = length >> 2
    if length & 3:
        n += 1
    v = [0] * n
    for i in range(length):
        v[i >> 2] |= data[length - 1 - i] << ((i & 3) << 3)
    v.reverse()
    return v


def _uint32_to_bytes(v: List[int]) -> bytes:
    """Convert list of uint32 to bytes (little-endian), matching reference toUint8Array."""
    n = len(v) << 2
    result = bytearray(n)
    for i in range(n):
        result[i] = (v[i >> 2] >> ((i & 3) << 3)) & 0xFF
    return bytes(result)


def _xxtea_encrypt_uint32(v: List[int], k: List[int]) -> List[int]:
    """XXTEA encrypt uint32 array in-place, matching reference encryptUint32Array."""
    length = len(v)
    n = length - 1
    z = v[n]
    total = 0
    q = (52 // length) + 1

    for _ in range(q):
        total = (total + _XXTEA_DELTA) & 0xFFFFFFFF
        e = (total >> 2) & 3
        for p in range(n):
            y = v[p + 1]
            mx = ((((z >> 5) ^ (y << 2)) + ((y >> 3) ^ (z << 4))) ^ ((total ^ y) + (k[(p & 3) ^ e] ^ z))) & 0xFFFFFFFF
            v[p] = (v[p] + mx) & 0xFFFFFFFF
            z = v[p]
        p = n
        y = v[0]
        mx = ((((z >> 5) ^ (y << 2)) + ((y >> 3) ^ (z << 4))) ^ ((total ^ y) + (k[(p & 3) ^ e] ^ z))) & 0xFFFFFFFF
        v[n] = (v[n] + mx) & 0xFFFFFFFF
        z = v[n]

    return v


def _xxtea_decrypt_uint32(v: List[int], k: List[int]) -> List[int]:
    """XXTEA decrypt uint32 array in-place."""
    length = len(v)
    n = length - 1
    q = (52 // length) + 1
    total = (q * _XXTEA_DELTA) & 0xFFFFFFFF
    y = v[0]

    for _ in range(q):
        e = (total >> 2) & 3
        for p in range(n, 0, -1):
            z = v[p - 1]
            mx = ((((z >> 5) ^ (y << 2)) + ((y >> 3) ^ (z << 4))) ^ ((total ^ y) + (k[(p & 3) ^ e] ^ z))) & 0xFFFFFFFF
            v[p] = (v[p] - mx) & 0xFFFFFFFF
            y = v[p]
        p = 0
        z = v[n]
        mx = ((((z >> 5) ^ (y << 2)) + ((y >> 3) ^ (z << 4))) ^ ((total ^ y) + (k[(p & 3) ^ e] ^ z))) & 0xFFFFFFFF
        v[0] = (v[0] - mx) & 0xFFFFFFFF
        y = v[0]
        total = (total - _XXTEA_DELTA) & 0xFFFFFFFF

    return v


def xxtea_encrypt(data: bytes, key: bytes = XXTEA_KEY_V2) -> bytes:
    """
    Encrypt data using XXTEA algorithm.
    Key bytes are interpreted in big-endian order (matching reference).
    Data bytes are interpreted in little-endian order.
    """
    if len(data) == 0:
        return data

    v = _bytes_to_uint32_le(data)
    k = _bytes_to_uint32_be(key)

    if len(v) < 2:
        return data

    encrypted = _xxtea_encrypt_uint32(v, k)
    return _uint32_to_bytes(encrypted)


def xxtea_decrypt(data: bytes, key: bytes = XXTEA_KEY_V2) -> bytes:
    """Decrypt data using XXTEA algorithm."""
    if len(data) == 0:
        return data

    v = _bytes_to_uint32_le(data)
    k = _bytes_to_uint32_be(key)

    if len(v) < 2:
        return data

    decrypted = _xxtea_decrypt_uint32(v, k)
    return _uint32_to_bytes(decrypted)


# ─── AES-CBC Encryption (V3) ─────────────────────────────────────────────────


def aes_cbc_encrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    """Encrypt data using AES-CBC with PKCS7 padding."""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives import padding

        padder = padding.PKCS7(128).padder()
        padded = padder.update(data) + padder.finalize()

        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        encryptor = cipher.encryptor()
        return encryptor.update(padded) + encryptor.finalize()
    except ImportError:
        raise ImportError(
            "Le chiffrement V3 nécessite le package 'cryptography'. "
            "Installez-le avec: pip install cryptography"
        )


# ─── Encrypt First Block (matching reference encryptFirstBlock) ──────────────


def encrypt_first_block(data: bytes, encrypt_fn, block_size: int = 512) -> bytes:
    """
    Encrypt the first `block_size` bytes of data using the given encryption function.
    Matches reference cipher.ts encryptFirstBlock exactly.
    """
    first_block_length = min(block_size, len(data))
    first_block = data[:first_block_length]

    encrypted_block = encrypt_fn(first_block)

    if len(encrypted_block) > len(data):
        return encrypted_block

    output = bytearray(data)
    output[:len(encrypted_block)] = encrypted_block
    return bytes(output)


# ─── V2 Specific Key (for BT generation) ─────────────────────────────────────


def v2_compute_specific_key(uuid_bytes: bytes) -> bytes:
    """
    Compute device-specific key from UUID bytes.
    Matches reference v2ComputeSpecificKeyFromUUID.
    """
    decrypted = xxtea_decrypt(uuid_bytes, XXTEA_KEY_V2)
    # Reorder bytes according to reference
    return bytes([
        decrypted[11], decrypted[10], decrypted[9], decrypted[8],
        decrypted[15], decrypted[14], decrypted[13], decrypted[12],
        decrypted[3], decrypted[2], decrypted[1], decrypted[0],
        decrypted[7], decrypted[6], decrypted[5], decrypted[4],
    ])


# ─── Image Conversion (BMP 4-bit Grayscale) ──────────────────────────────────


def convert_image_to_lunii_bmp(image_path: str) -> bytes:
    """
    Convert an image to Lunii-compatible BMP format.
    Format: 320x240, 4-bit Grayscale (16 levels), RLE4 compressed.
    Matches olup/lunii-admin-web image.ts create4BitGrayscaleBMP exactly.
    """
    img = Image.open(image_path)
    img = img.convert('L')
    img = ImageOps.fit(img, (LUNII_IMAGE_WIDTH, LUNII_IMAGE_HEIGHT), method=Image.Resampling.LANCZOS)
    # Flip vertically (BMP is bottom-up, reference flips before encoding)
    img = img.transpose(Image.FLIP_TOP_BOTTOM)

    width, height = img.size
    pixels = list(img.getdata())

    # RLE4 encode: encode as 4-bit grayscale with RLE compression (comp=2)
    # Matching reference: quantize each pixel to 4-bit (0-15)
    bmp_data = bytearray()
    for y in range(height):
        row_start = y * width
        run_length = 0
        run_color = 0

        for x in range(width):
            # Quantize 8-bit grayscale to 4-bit (0-15)
            grayscale_value = pixels[row_start + x] // 16

            if x == 0:
                run_length = 1
                run_color = grayscale_value
                continue

            if run_color == grayscale_value and run_length < 255:
                run_length += 1
            else:
                # Write run: count, then color byte (high nibble = low nibble = color)
                color8 = (run_color << 4) | run_color
                bmp_data.append(run_length)
                bmp_data.append(color8)
                run_length = 1
                run_color = grayscale_value

        # Commit last run of the line
        color8 = (run_color << 4) | run_color
        bmp_data.append(run_length)
        bmp_data.append(color8)

        # End of line marker (not for last line)
        if y < height - 1:
            bmp_data.extend([0x00, 0x00])

    # End of bitmap marker
    bmp_data.extend([0x00, 0x01])

    # Build BMP file
    header_size = 54  # 14 (file header) + 40 (DIB header)
    palette_size = 16 * 4
    data_offset = header_size + palette_size
    data_size = len(bmp_data)
    file_size = data_offset + data_size

    bmp = bytearray(file_size)

    # BMP file header (14 bytes)
    bmp[0:2] = b'BM'
    struct.pack_into('<I', bmp, 2, file_size)
    # Reserved (4 bytes, already 0)
    struct.pack_into('<I', bmp, 10, data_offset)

    # DIB header (40 bytes)
    struct.pack_into('<I', bmp, 14, 40)        # Header size
    struct.pack_into('<i', bmp, 18, width)     # Width
    struct.pack_into('<i', bmp, 22, height)    # Height
    struct.pack_into('<H', bmp, 26, 1)         # Color planes
    struct.pack_into('<H', bmp, 28, 4)         # Bits per pixel
    struct.pack_into('<I', bmp, 30, 2)         # Compression: BI_RLE4
    struct.pack_into('<I', bmp, 34, data_size) # Image data size
    # H/V resolution (0), palette colors (0), important colors (0) — already 0

    # Palette: 16 grayscale entries (matching reference: (255/16)*i)
    for i in range(16):
        idx = header_size + i * 4
        gray = int((255 / 16) * i)
        bmp[idx] = gray      # Blue
        bmp[idx + 1] = gray  # Green
        bmp[idx + 2] = gray  # Red
        bmp[idx + 3] = 0     # Reserved

    # Pixel data
    bmp[data_offset:data_offset + data_size] = bmp_data

    return bytes(bmp)


# ─── Audio Conversion (MP3 Mono 44100Hz 64kbps no ID3) ───────────────────────


def convert_audio_to_lunii_mp3(audio_path: str, output_path: str) -> bool:
    """Convert audio to Lunii-compatible MP3: 44100Hz, Mono, 128kbps, no ID3.
    Prepends 1 second of silence to compensate for Lunii firmware audio truncation."""
    try:
        result = subprocess.run([
            'ffmpeg', '-y',
            '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono',  # silence source
            '-i', audio_path,                                    # actual audio
            '-filter_complex',
            '[0:a]atrim=duration=1[silence];[silence][1:a]concat=n=2:v=0:a=1[out]',
            '-map', '[out]',
            '-ar', '44100',
            '-ac', '1',
            '-b:a', '128k',
            '-map_metadata', '-1',
            '-id3v2_version', '0',
            '-write_id3v1', '0',
            output_path
        ], capture_output=True, encoding='utf-8', errors='replace', timeout=120)

        if result.returncode != 0:
            logger.error(f"FFmpeg audio conversion failed: {result.stderr}")
            return False
        return True
    except Exception as e:
        logger.error(f"Audio conversion error: {e}")
        return False


# ─── Binary Index Generation (matching reference exactly) ────────────────────


def generate_ni(stage_nodes: List[Dict], action_nodes: List[Dict],
                image_assets: List[Dict], audio_assets: List[Dict],
                list_nodes: List[Dict], pack_version: int = 1) -> bytes:
    """
    Generate Node Index (.ni) binary file.
    Matches olup/lunii-admin-web ni.ts generateNiBinary exactly.
    
    Header (512 bytes):
      offset 0:  Uint16 LE — NI format version (1)
      offset 2:  Int16 LE  — Story pack version
      offset 4:  Int32 LE  — Stage nodes offset (512)
      offset 8:  Int32 LE  — Stage node size (44)
      offset 12: Int32 LE  — Stage node count
      offset 16: Int32 LE  — Image asset count
      offset 20: Int32 LE  — Sound asset count
      offset 24: Int8      — Is factory pack (1)
    
    Each node (44 bytes):
      offset 0:  Int32 LE — Image index
      offset 4:  Int32 LE — Audio index
      offset 8:  Int32 LE — OK transition absolutePosition in LI
      offset 12: Int32 LE — OK transition options count
      offset 16: Int32 LE — OK transition optionIndex
      offset 20: Int32 LE — Home transition absolutePosition in LI
      offset 24: Int32 LE — Home transition options count
      offset 28: Int32 LE — Home transition optionIndex
      offset 32: Int16 LE — Wheel enabled
      offset 34: Int16 LE — OK enabled
      offset 36: Int16 LE — Home enabled
      offset 38: Int16 LE — Pause enabled
      offset 40: Int16 LE — Autoplay enabled
      offset 42: Int16 LE — Padding (0)
    """
    # Build header using explicit byte offsets
    header = bytearray(NI_HEADER_SIZE)
    struct.pack_into('<H', header, 0, 1)                      # NI format version
    struct.pack_into('<h', header, 2, pack_version)           # Story pack version
    struct.pack_into('<i', header, 4, NI_HEADER_SIZE)         # Offset to first node
    struct.pack_into('<i', header, 8, NI_NODE_SIZE)           # Node size
    struct.pack_into('<i', header, 12, len(stage_nodes))      # Stage node count
    struct.pack_into('<i', header, 16, len(image_assets))     # Image count
    struct.pack_into('<i', header, 20, len(audio_assets))     # Sound count
    struct.pack_into('<b', header, 24, 1)                     # Factory flag

    # Build lookup maps
    # Image: asset_name -> position
    image_name_to_pos = {}
    for asset in image_assets:
        image_name_to_pos[asset['name']] = asset['position']

    # Audio: asset_name -> position (per stageNode, not deduplicated)
    audio_name_to_pos = {}
    for asset in audio_assets:
        audio_name_to_pos[asset['nodeUuid']] = asset['position']

    # ListNode: action_id -> ListNode
    list_node_by_id = {}
    for ln in list_nodes:
        list_node_by_id[ln['id']] = ln

    # Generate nodes
    nodes_data = bytearray()
    for node in stage_nodes:
        node_buf = bytearray(NI_NODE_SIZE)

        # Image index
        img = node.get('image', '')
        if img and img in image_name_to_pos:
            struct.pack_into('<i', node_buf, 0, image_name_to_pos[img])
        else:
            struct.pack_into('<i', node_buf, 0, -1)

        # Audio index (one per stageNode, by UUID)
        audio_pos = audio_name_to_pos.get(node['uuid'], -1)
        struct.pack_into('<i', node_buf, 4, audio_pos)

        # OK Transition
        ok_data = node.get('okTransition')
        if ok_data:
            action_id = ok_data.get('actionNode', '')
            ln = list_node_by_id.get(action_id)
            if ln:
                struct.pack_into('<i', node_buf, 8, ln['absolutePosition'])
                struct.pack_into('<i', node_buf, 12, len(ln['options']))
                struct.pack_into('<i', node_buf, 16, ok_data.get('optionIndex', 0))
            else:
                struct.pack_into('<i', node_buf, 8, -1)
                struct.pack_into('<i', node_buf, 12, -1)
                struct.pack_into('<i', node_buf, 16, -1)
        else:
            struct.pack_into('<i', node_buf, 8, -1)
            struct.pack_into('<i', node_buf, 12, -1)
            struct.pack_into('<i', node_buf, 16, -1)

        # Home Transition
        home_data = node.get('homeTransition')
        if home_data:
            action_id = home_data.get('actionNode', '')
            ln = list_node_by_id.get(action_id)
            if ln:
                struct.pack_into('<i', node_buf, 20, ln['absolutePosition'])
                struct.pack_into('<i', node_buf, 24, len(ln['options']))
                struct.pack_into('<i', node_buf, 28, home_data.get('optionIndex', 0))
            else:
                struct.pack_into('<i', node_buf, 20, -1)
                struct.pack_into('<i', node_buf, 24, -1)
                struct.pack_into('<i', node_buf, 28, -1)
        else:
            struct.pack_into('<i', node_buf, 20, -1)
            struct.pack_into('<i', node_buf, 24, -1)
            struct.pack_into('<i', node_buf, 28, -1)

        # Control flags — read from controlSettings (set by storyAudio expansion)
        # or compute from transitions as fallback
        ctrl = node.get('controlSettings')
        if ctrl:
            wheel_enabled = 1 if ctrl.get('wheel', False) else 0
            ok_enabled = 1 if ctrl.get('ok', False) else 0
            home_enabled = 1 if ctrl.get('home', False) else 0
            pause_enabled = 1 if ctrl.get('pause', False) else 0
            autoplay_enabled = 1 if ctrl.get('autoplay', False) else 0
        else:
            # Fallback: compute from transitions (reference ni.ts behavior)
            has_ok = ok_data is not None and ok_data.get('actionNode') and list_node_by_id.get(ok_data.get('actionNode', ''))
            has_home = home_data is not None and home_data.get('actionNode') and list_node_by_id.get(home_data.get('actionNode', ''))
            is_story_node = not has_ok
            if is_story_node:
                wheel_enabled, ok_enabled = 0, 0
                home_enabled, pause_enabled, autoplay_enabled = 1, 1, 1
            else:
                wheel_enabled, ok_enabled = 1, 1
                home_enabled = 1 if has_home else 0
                pause_enabled, autoplay_enabled = 0, 0

        struct.pack_into('<h', node_buf, 32, wheel_enabled)
        struct.pack_into('<h', node_buf, 34, ok_enabled)
        struct.pack_into('<h', node_buf, 36, home_enabled)
        struct.pack_into('<h', node_buf, 38, pause_enabled)
        struct.pack_into('<h', node_buf, 40, autoplay_enabled)
        struct.pack_into('<h', node_buf, 42, 0)  # padding

        nodes_data.extend(node_buf)

    return bytes(header) + bytes(nodes_data)


def generate_li(list_nodes: List[Dict], stage_nodes: List[Dict]) -> bytes:
    """
    Generate List Index (.li) binary file.
    Matches reference li.ts generateLiBinary.
    
    For each ListNode, for each option (stage UUID), write the stage index as Uint32 LE.
    """
    # Build stage UUID -> index map
    stage_uuid_to_idx = {node['uuid']: i for i, node in enumerate(stage_nodes)}

    # Calculate total buffer length
    if not list_nodes:
        return b''
    last = list_nodes[-1]
    total_options = last['absolutePosition'] + len(last['options'])
    buf_length = total_options * 4

    data = bytearray(buf_length)
    offset = 0
    for ln in list_nodes:
        for option_uuid in ln['options']:
            idx = stage_uuid_to_idx.get(option_uuid, 0)
            struct.pack_into('<I', data, offset, idx)
            offset += 4

    return bytes(data)


def generate_asset_binary(asset_list: List[Dict]) -> bytes:
    """
    Generate RI or SI binary file.
    Matches reference asset.ts generateBinaryFromAssetIndex.
    
    Concatenated ASCII strings: "000\\XXXXXXXX" for each asset (12 bytes each).
    """
    parts = []
    for i in range(len(asset_list)):
        path = f"000\\{i:08d}"
        parts.append(path)
    return ''.join(parts).encode('ascii')


def generate_bt_v2(ri_encrypted: bytes, uuid_bytes: bytes) -> bytes:
    """
    Generate BT binary for V2 devices.
    Matches reference bt.ts v2GenerateBtBinary.
    
    Takes the first 64 bytes of encrypted RI, encrypts with device-specific key.
    """
    specific_key = v2_compute_specific_key(uuid_bytes)
    first_block_length = min(64, len(ri_encrypted))
    first_block = ri_encrypted[:first_block_length]
    return xxtea_encrypt(first_block, specific_key)


# ─── Asset List Builders (matching reference generators/index.ts) ─────────────


def build_image_asset_list(stage_nodes: List[Dict]) -> List[Dict]:
    """
    Build image asset list matching reference getImageAssetList.
    One entry per stageNode that has an image. Position is sequential.
    """
    assets = []
    position = 0
    for node in stage_nodes:
        img = node.get('image', '')
        if img:
            assets.append({
                'nodeUuid': node['uuid'],
                'position': position,
                'name': img,
            })
            position += 1
    return assets


def build_audio_asset_list(stage_nodes: List[Dict]) -> List[Dict]:
    """
    Build audio asset list matching reference getAudioAssetList.
    One entry PER stageNode (not deduplicated). Uses BLANK_MP3 for missing audio.
    """
    assets = []
    position = 0
    for node in stage_nodes:
        audio = node.get('audio', '')
        if audio:
            assets.append({
                'nodeUuid': node['uuid'],
                'position': position,
                'name': audio,
            })
        else:
            assets.append({
                'nodeUuid': node['uuid'],
                'position': position,
                'name': '__BLANK_MP3__',
            })
        position += 1
    return assets


def build_list_nodes_index(action_nodes: List[Dict]) -> List[Dict]:
    """
    Build list nodes index matching reference getListNodesIndex.
    Each ListNode has an absolutePosition = sum of all previous nodes' option counts.
    """
    cursor = 0
    result = []
    for i, action in enumerate(action_nodes):
        options = action.get('options', [])
        result.append({
            'id': action['id'],
            'options': options,
            'position': i,
            'absolutePosition': cursor,
        })
        cursor += len(options)
    return result


# ─── ZIP Validation ──────────────────────────────────────────────────────────


def is_lunii_pack(zip_path: str) -> bool:
    """Check if a ZIP file already contains a valid Lunii pack structure."""
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            names = zf.namelist()
            content_dirs = [n for n in names if n.startswith('.content/')]
            if not content_dirs:
                return False

            refs = set()
            for name in content_dirs:
                parts = name.split('/')
                if len(parts) >= 2 and parts[1]:
                    refs.add(parts[1])

            if not refs:
                return False

            for ref in refs:
                prefix = f".content/{ref}/"
                ref_files = [n.replace(prefix, '') for n in names if n.startswith(prefix)]
                required = ['ni', 'li', 'ri', 'si']
                if not all(f in ref_files for f in required):
                    return False
                has_rf = any(f.startswith('rf/') for f in ref_files)
                has_sf = any(f.startswith('sf/') for f in ref_files)
                if not (has_rf and has_sf):
                    return False

            return True
    except Exception as e:
        logger.error(f"Error checking Lunii pack format: {e}")
        return False


def validate_studio_pack(zip_path: str) -> Tuple[bool, str]:
    """Validate that a Studio Pack ZIP has the correct format for Lunii conversion."""
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            names = zf.namelist()

            if 'story.json' not in names:
                return False, "story.json manquant dans le ZIP"

            story_data = json.loads(zf.read('story.json'))
            stage_nodes = story_data.get('stageNodes', [])

            if not stage_nodes:
                return False, "Aucun stageNode trouvé dans story.json"

            images = set()
            audios = set()
            for node in stage_nodes:
                if node.get('image'):
                    images.add(node['image'])
                if node.get('audio'):
                    audios.add(node['audio'])

            missing = []
            for img in images:
                if img not in names and f"assets/{img}" not in names:
                    missing.append(img)
            for aud in audios:
                if aud not in names and f"assets/{aud}" not in names:
                    missing.append(aud)

            if missing:
                return False, f"{len(missing)} assets manquants: {', '.join(missing[:5])}"

            return True, f"Pack valide: {len(stage_nodes)} nœuds, {len(images)} images, {len(audios)} sons"

    except json.JSONDecodeError as e:
        return False, f"story.json invalide: {e}"
    except Exception as e:
        return False, f"Erreur de validation: {e}"


# ─── Main Converter Class ────────────────────────────────────────────────────


class LuniiPackConverter:
    """
    Converts a Studio Pack ZIP into a Lunii-compatible pack.
    
    Usage:
        converter = LuniiPackConverter(zip_path, version="V2")
        result_path = converter.convert(progress_callback=my_callback)
    """

    def __init__(self, zip_path: str, version: str = "V2",
                 aes_key: Optional[bytes] = None,
                 aes_iv: Optional[bytes] = None):
        self.zip_path = zip_path
        self.version = version
        self.aes_key = aes_key
        self.aes_iv = aes_iv
        self._progress_callback: Optional[Callable[[float, str], None]] = None

    def _update_progress(self, progress: float, message: str):
        if self._progress_callback:
            self._progress_callback(progress, message)
        logger.info(f"[{progress:.0%}] {message}")

    def _get_encrypt_fn(self):
        """Get the encryption function for the current version."""
        if self.version == "V2":
            return lambda data: xxtea_encrypt(data, XXTEA_KEY_V2)
        elif self.version == "V3":
            if not self.aes_key or not self.aes_iv:
                raise ValueError("AES key and IV required for V3")
            return lambda data: aes_cbc_encrypt(data, self.aes_key, self.aes_iv)
        else:
            raise ValueError(f"Unknown version: {self.version}")

    def convert(self, output_path: Optional[str] = None,
                progress_callback: Optional[Callable[[float, str], None]] = None) -> Optional[str]:
        self._progress_callback = progress_callback

        try:
            self._update_progress(0.0, "Vérification du format...")
            if is_lunii_pack(self.zip_path):
                self._update_progress(1.0, "Le pack est déjà au format Lunii !")
                return self.zip_path

            self._update_progress(0.05, "Validation du pack Studio...")
            is_valid, msg = validate_studio_pack(self.zip_path)
            if not is_valid:
                logger.error(f"Pack validation failed: {msg}")
                self._update_progress(0.0, f"❌ {msg}")
                return None

            self._update_progress(0.1, "Extraction et parsing...")
            with tempfile.TemporaryDirectory() as tmpdir:
                return self._do_convert(tmpdir, output_path)

        except Exception as e:
            logger.error(f"Lunii conversion failed: {e}", exc_info=True)
            self._update_progress(0.0, f"❌ Erreur: {e}")
            return None

    def _do_convert(self, tmpdir: str, output_path: Optional[str]) -> Optional[str]:
        """Internal conversion logic."""

        extract_dir = os.path.join(tmpdir, "input")
        output_dir = os.path.join(tmpdir, "output")
        os.makedirs(extract_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        # Extract ZIP
        with zipfile.ZipFile(self.zip_path, 'r') as zf:
            zf.extractall(extract_dir)

        # Parse story.json
        story_path = os.path.join(extract_dir, "story.json")
        with open(story_path, 'r', encoding='utf-8') as f:
            story = json.load(f)

        stage_nodes = story.get('stageNodes', [])
        action_nodes = story.get('actionNodes', [])
        title = story.get('title', 'Mon Pack')
        description = story.get('description', '')
        pack_version = story.get('version', 2)

        # ─── Expand storyAudio into separate playback nodes ──────────────
        # The Lunii firmware only supports ONE audio per stageNode. Our story.json
        # uses `audio` for announcement and `storyAudio` for the full story content.
        # We need to split each story node into:
        #   1. Announcement node (image + short audio, okTransition → story action)
        #   2. Playback node (no image, full audio, autoplay + pause, homeTransition → back)
        # This matches the architecture of working custom packs (e.g., Pack 52).
        expanded_stages = []
        expanded_actions = list(action_nodes)  # Copy existing actions

        # Find the root menu action (the one referenced by the entrypoint's okTransition)
        root_action_id = None
        for node in stage_nodes:
            if node.get('okTransition') and node.get('type') in ('entrypoint', 'cover', None):
                root_action_id = node['okTransition'].get('actionNode')
                break

        for node in stage_nodes:
            story_audio = node.get('storyAudio')
            if story_audio and node.get('audio'):
                # This node has both announcement and story audio → split it

                # 1. Announcement node: keeps image + short audio
                #    Gets okTransition to a NEW action that leads to the playback node
                announce_node = dict(node)
                # Remove storyAudio from the announcement node
                announce_node.pop('storyAudio', None)

                # Create a new action node that leads to the playback node
                playback_uuid = str(uuid.uuid4())
                story_action_id = str(uuid.uuid4())
                story_action = {
                    'id': story_action_id,
                    'options': [playback_uuid]
                }
                expanded_actions.append(story_action)

                # Set okTransition on the announcement node → story action
                announce_node['okTransition'] = {
                    'actionNode': story_action_id,
                    'optionIndex': 0
                }
                # Control settings for announcement: NO wheel/ok (auto-transition)
                announce_node['controlSettings'] = {
                    'wheel': False, 'ok': False, 'home': True,
                    'pause': False, 'autoplay': True
                }
                expanded_stages.append(announce_node)

                # 2. Playback node: full story audio, no image, autoplay + pause
                playback_node = {
                    'uuid': playback_uuid,
                    'type': 'story',
                    'name': node.get('name', '') + ' (playback)',
                    'audio': story_audio,  # Full story audio
                    # No image (firmware shows nothing or uses parent)
                    'okTransition': None,
                    'controlSettings': {
                        'wheel': False, 'ok': False, 'home': True,
                        'pause': True, 'autoplay': True
                    }
                }

                # Add homeTransition back to the root menu if we know it
                if root_action_id:
                    playback_node['homeTransition'] = {
                        'actionNode': root_action_id,
                        'optionIndex': 0
                    }

                expanded_stages.append(playback_node)

                logger.info(f"Expanded storyAudio: '{node.get('name')}' → announcement + playback nodes")
            else:
                # Node has no storyAudio, or only one audio type → keep as-is
                # Ensure controlSettings exist
                if 'controlSettings' not in node:
                    has_transition = node.get('okTransition') is not None
                    node['controlSettings'] = {
                        'wheel': has_transition,
                        'ok': has_transition,
                        'home': not has_transition,  # home on terminal nodes
                        'pause': not has_transition,
                        'autoplay': not has_transition
                    }
                expanded_stages.append(node)

        # Replace with expanded node lists
        stage_nodes = expanded_stages
        action_nodes = expanded_actions
        logger.info(f"After expansion: {len(stage_nodes)} stageNodes, {len(action_nodes)} actionNodes")

        # ─── Generate pack UUID and reference ─────────────────────────────
        pack_uuid = uuid.uuid4()
        # Use first stageNode UUID if available (matching reference behavior)
        story_uuid = story.get('uuid') or (stage_nodes[0]['uuid'] if stage_nodes else str(pack_uuid))
        # REF = last 8 hex chars (matching olup/lunii-admin-web uuidToRef)
        pack_ref = story_uuid.replace('-', '')[-8:].upper()

        # Create output structure
        content_dir = os.path.join(output_dir, ".content", pack_ref)
        rf_dir = os.path.join(content_dir, "rf", "000")
        sf_dir = os.path.join(content_dir, "sf", "000")
        os.makedirs(rf_dir, exist_ok=True)
        os.makedirs(sf_dir, exist_ok=True)

        # Get encryption function
        encrypt_fn = self._get_encrypt_fn()

        # Build asset lists (matching reference structure)
        self._update_progress(0.15, "Indexation des assets...")
        image_assets = build_image_asset_list(stage_nodes)
        audio_assets = build_audio_asset_list(stage_nodes)
        list_nodes = build_list_nodes_index(action_nodes)

        # Resolve actual file paths
        asset_paths = {}
        for asset in image_assets:
            name = asset['name']
            if name not in asset_paths:
                if os.path.exists(os.path.join(extract_dir, name)):
                    asset_paths[name] = os.path.join(extract_dir, name)
                else:
                    asset_paths[name] = os.path.join(extract_dir, "assets", name)

        for asset in audio_assets:
            name = asset['name']
            if name == '__BLANK_MP3__':
                continue
            if name not in asset_paths:
                if os.path.exists(os.path.join(extract_dir, name)):
                    asset_paths[name] = os.path.join(extract_dir, name)
                else:
                    asset_paths[name] = os.path.join(extract_dir, "assets", name)

        # Convert images
        self._update_progress(0.2, f"Conversion de {len(image_assets)} images...")
        for idx, asset in enumerate(image_assets):
            img_path = asset_paths.get(asset['name'], '')
            if not os.path.exists(img_path):
                logger.warning(f"Image asset not found: {asset['name']}")
                continue

            bmp_data = convert_image_to_lunii_bmp(img_path)
            encrypted_data = encrypt_first_block(bmp_data, encrypt_fn)

            out_file = os.path.join(rf_dir, f"{idx:08d}")
            with open(out_file, 'wb') as f:
                f.write(encrypted_data)

            progress = 0.2 + (0.2 * (idx + 1) / max(len(image_assets), 1))
            self._update_progress(progress, f"Image {idx + 1}/{len(image_assets)}")

        # Convert audio (one per stage node, non-deduplicated)
        self._update_progress(0.4, f"Conversion de {len(audio_assets)} fichiers audio...")
        converted_audio_cache = {}  # name -> mp3 bytes (cache for dedup)
        
        for idx, asset in enumerate(audio_assets):
            name = asset['name']

            if name == '__BLANK_MP3__':
                mp3_data = BLANK_MP3
            elif name in converted_audio_cache:
                mp3_data = converted_audio_cache[name]
            else:
                aud_path = asset_paths.get(name, '')
                if not os.path.exists(aud_path):
                    logger.warning(f"Audio asset not found: {name}")
                    mp3_data = BLANK_MP3
                else:
                    temp_mp3 = os.path.join(tmpdir, f"temp_audio_{idx}.mp3")
                    if convert_audio_to_lunii_mp3(aud_path, temp_mp3):
                        with open(temp_mp3, 'rb') as f:
                            mp3_data = f.read()
                        os.remove(temp_mp3)
                    else:
                        logger.error(f"Failed to convert audio: {name}")
                        mp3_data = BLANK_MP3
                converted_audio_cache[name] = mp3_data

            encrypted_data = encrypt_first_block(mp3_data, encrypt_fn)

            out_file = os.path.join(sf_dir, f"{idx:08d}")
            with open(out_file, 'wb') as f:
                f.write(encrypted_data)

            progress = 0.4 + (0.3 * (idx + 1) / max(len(audio_assets), 1))
            self._update_progress(progress, f"Audio {idx + 1}/{len(audio_assets)}")

        # Generate binary indices
        self._update_progress(0.75, "Génération des index binaires...")

        ni_data = generate_ni(stage_nodes, action_nodes, image_assets,
                              audio_assets, list_nodes, pack_version)
        li_data = generate_li(list_nodes, stage_nodes)
        ri_data = generate_asset_binary(image_assets)
        si_data = generate_asset_binary(audio_assets)

        # Encrypt LI, RI, SI (first 512 bytes)
        li_encrypted = encrypt_first_block(li_data, encrypt_fn)
        ri_encrypted = encrypt_first_block(ri_data, encrypt_fn)
        si_encrypted = encrypt_first_block(si_data, encrypt_fn)

        # Generate BT
        if self.version == "V2":
            uuid_bytes = bytes.fromhex(story_uuid.replace('-', ''))
            bt_data = generate_bt_v2(ri_encrypted, uuid_bytes)
        else:
            # V3: bt binary comes from the device itself
            # For standalone conversion, write a minimal marker
            bt_data = bytes(64)

        # Write index files
        with open(os.path.join(content_dir, 'ni'), 'wb') as f:
            f.write(ni_data)
        with open(os.path.join(content_dir, 'li'), 'wb') as f:
            f.write(li_encrypted)
        with open(os.path.join(content_dir, 'ri'), 'wb') as f:
            f.write(ri_encrypted)
        with open(os.path.join(content_dir, 'si'), 'wb') as f:
            f.write(si_encrypted)
        with open(os.path.join(content_dir, 'bt'), 'wb') as f:
            f.write(bt_data)

        # Generate metadata (md file - YAML)
        self._update_progress(0.85, "Génération des métadonnées...")
        md_content = (
            f"title: {title}\n"
            f"description: {description}\n"
            f"uuid: {story_uuid}\n"
            f"ref: {pack_ref}\n"
            f"packType: custom\n"
        )
        with open(os.path.join(content_dir, 'md'), 'w', encoding='utf-8') as f:
            f.write(md_content)

        # Package to ZIP
        self._update_progress(0.9, "Création du ZIP Lunii...")
        if not output_path:
            base = os.path.splitext(self.zip_path)[0]
            output_path = f"{base}_lunii.zip"

        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zout:
            for root, dirs, files in os.walk(output_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arc_name = os.path.relpath(file_path, output_dir)
                    zout.write(file_path, arc_name)

        self._update_progress(1.0, f"✅ Pack Lunii généré: {os.path.basename(output_path)}")
        return output_path
