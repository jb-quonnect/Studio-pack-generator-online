"""
Lunii Pack Converter

Converts Studio Pack ZIP (story.json + assets/) into the native Lunii format:
- BMP 4-bit Grayscale RLE images
- MP3 Mono 44100Hz 64kbps (no ID3 tags)
- XXTEA encryption (V2) or AES-CBC (V3)
- Binary index files (.ni, .li, .ri, .si, .bt)
- Structured .content/REF/ directory

Based on specifications from olup/lunii-admin-web.
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
NI_VERSION = 1
NI_STORY_VERSION = 1

# ─── XXTEA Encryption (V2) ───────────────────────────────────────────────────


def _bytes_to_longs(data: bytes) -> List[int]:
    """Convert bytes to list of uint32 (little-endian)."""
    if len(data) % 4 != 0:
        data += b'\x00' * (4 - len(data) % 4)
    return [
        data[i] | (data[i+1] << 8) | (data[i+2] << 16) | (data[i+3] << 24)
        for i in range(0, len(data), 4)
    ]


def _longs_to_bytes(longs: List[int]) -> bytes:
    """Convert list of uint32 to bytes (little-endian)."""
    result = bytearray()
    for x in longs:
        result.append(x & 0xFF)
        result.append((x >> 8) & 0xFF)
        result.append((x >> 16) & 0xFF)
        result.append((x >> 24) & 0xFF)
    return bytes(result)


def xxtea_encrypt(data: bytes, key: bytes = XXTEA_KEY_V2) -> bytes:
    """
    Encrypt data using XXTEA algorithm.
    
    Args:
        data: Data to encrypt
        key: 16-byte key (defaults to V2 common key)
        
    Returns:
        Encrypted data
    """
    if len(data) == 0:
        return data

    v = _bytes_to_longs(data)
    k = _bytes_to_longs(key)
    n = len(v) - 1

    if n < 1:
        return data

    z = v[n]
    total = 0
    q = 6 + 52 // (n + 1)

    while q > 0:
        total = (total + _XXTEA_DELTA) & 0xFFFFFFFF
        e = (total >> 2) & 3
        for p in range(n):
            y = v[p + 1]
            mx = (((z >> 5 ^ y << 2) + (y >> 3 ^ z << 4)) ^ ((total ^ y) + (k[(p & 3) ^ e] ^ z)))
            v[p] = (v[p] + mx) & 0xFFFFFFFF
            z = v[p]
        p = n
        y = v[0]
        mx = (((z >> 5 ^ y << 2) + (y >> 3 ^ z << 4)) ^ ((total ^ y) + (k[(p & 3) ^ e] ^ z)))
        v[n] = (v[n] + mx) & 0xFFFFFFFF
        z = v[n]
        q -= 1

    return _longs_to_bytes(v)


# ─── AES-CBC Encryption (V3) ─────────────────────────────────────────────────


def aes_cbc_encrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    """
    Encrypt data using AES-CBC.
    
    Args:
        data: Data to encrypt
        key: AES key (16/24/32 bytes)
        iv: Initialization vector (16 bytes)
        
    Returns:
        Encrypted data (PKCS7 padded)
    """
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives import padding

        # PKCS7 padding
        padder = padding.PKCS7(128).padder()
        padded = padder.update(data) + padder.finalize()

        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        encryptor = cipher.encryptor()
        return encryptor.update(padded) + encryptor.finalize()
    except ImportError:
        logger.error("cryptography package not installed — AES-CBC (V3) unavailable")
        raise ImportError(
            "Le chiffrement V3 nécessite le package 'cryptography'. "
            "Installez-le avec: pip install cryptography"
        )


# ─── Image Conversion (BMP 4-bit Grayscale) ──────────────────────────────────


def convert_image_to_lunii_bmp(image_path: str) -> bytes:
    """
    Convert an image to Lunii-compatible BMP format.
    
    Format: 320x240, 4-bit Grayscale (16 levels), uncompressed.
    (Lunii firmware accepts both RLE and uncompressed 4-bit BMP)
    
    Args:
        image_path: Path to source image (PNG/JPG)
        
    Returns:
        BMP file content as bytes
    """
    img = Image.open(image_path)
    
    # Convert to grayscale
    img = img.convert('L')
    
    # Resize to 320x240 (fit with black padding, like existing image_processor)
    img = ImageOps.fit(img, (LUNII_IMAGE_WIDTH, LUNII_IMAGE_HEIGHT), method=Image.Resampling.LANCZOS)
    
    # Quantize to 16 levels (4-bit)
    img = img.quantize(colors=16)
    
    # Flip vertically (BMP stores rows bottom-to-top)
    img = img.transpose(Image.FLIP_TOP_BOTTOM)
    
    width, height = img.size
    pixels = list(img.getdata())
    
    # Pack 2 pixels per byte (4-bit each)
    row_size_bytes = width // 2  # 320 / 2 = 160 bytes per row
    # BMP rows must be padded to 4-byte boundary
    row_padding = (4 - (row_size_bytes % 4)) % 4
    padded_row_size = row_size_bytes + row_padding
    
    pixel_data = bytearray()
    for y in range(height):
        row_start = y * width
        for x in range(0, width, 2):
            p1 = pixels[row_start + x] & 0x0F
            p2 = pixels[row_start + x + 1] & 0x0F if (x + 1) < width else 0
            pixel_data.append((p1 << 4) | p2)
        # Add row padding
        pixel_data.extend(b'\x00' * row_padding)
    
    # Build 16-color grayscale palette (4 bytes per color: B, G, R, 0x00)
    palette = bytearray()
    for i in range(16):
        gray = i * 17  # 0, 17, 34, ... 255
        palette.extend([gray, gray, gray, 0x00])
    
    # BMP Header (14 bytes)
    palette_size = 16 * 4  # 64 bytes
    header_size = 14 + 40 + palette_size  # BMP header + DIB header + palette
    pixel_data_size = len(pixel_data)
    file_size = header_size + pixel_data_size
    
    bmp = bytearray()
    # BMP file header (14 bytes)
    bmp.extend(b'BM')                              # Signature
    bmp.extend(struct.pack('<I', file_size))        # File size
    bmp.extend(struct.pack('<HH', 0, 0))            # Reserved
    bmp.extend(struct.pack('<I', header_size))       # Pixel data offset
    
    # DIB header (BITMAPINFOHEADER - 40 bytes)
    bmp.extend(struct.pack('<I', 40))               # DIB header size
    bmp.extend(struct.pack('<i', width))             # Width
    bmp.extend(struct.pack('<i', height))            # Height
    bmp.extend(struct.pack('<H', 1))                 # Color planes
    bmp.extend(struct.pack('<H', 4))                 # Bits per pixel
    bmp.extend(struct.pack('<I', 0))                 # Compression (0 = uncompressed)
    bmp.extend(struct.pack('<I', pixel_data_size))   # Image size
    bmp.extend(struct.pack('<i', 2835))              # X pixels/meter (72 DPI)
    bmp.extend(struct.pack('<i', 2835))              # Y pixels/meter (72 DPI)
    bmp.extend(struct.pack('<I', 16))                # Colors in palette
    bmp.extend(struct.pack('<I', 16))                # Important colors
    
    # Palette
    bmp.extend(palette)
    
    # Pixel data
    bmp.extend(pixel_data)
    
    return bytes(bmp)


# ─── Audio Conversion (MP3 Mono 44100Hz 64kbps no ID3) ───────────────────────


def convert_audio_to_lunii_mp3(audio_path: str, output_path: str) -> bool:
    """
    Convert audio to Lunii-compatible MP3 format.
    
    Format: MP3, 44100Hz, Mono, 64kbps, no ID3 tags.
    
    Args:
        audio_path: Path to source audio
        output_path: Path for output file
        
    Returns:
        True if successful
    """
    try:
        result = subprocess.run([
            'ffmpeg', '-y', '-i', audio_path,
            '-ar', '44100',          # Sample rate
            '-ac', '1',              # Mono
            '-b:a', '64k',           # Bitrate
            '-map_metadata', '-1',   # Strip all metadata
            '-id3v2_version', '0',   # No ID3v2
            '-write_id3v1', '0',     # No ID3v1
            output_path
        ], capture_output=True, encoding='utf-8', errors='replace', timeout=120)
        
        if result.returncode != 0:
            logger.error(f"FFmpeg audio conversion failed: {result.stderr}")
            return False
        return True
    except Exception as e:
        logger.error(f"Audio conversion error: {e}")
        return False


# ─── Asset Encryption ────────────────────────────────────────────────────────


def encrypt_asset(data: bytes, version: str = "V2",
                  aes_key: Optional[bytes] = None,
                  aes_iv: Optional[bytes] = None) -> bytes:
    """
    Encrypt asset data (first 512 bytes only).
    
    Args:
        data: Raw asset data
        version: "V2" for XXTEA, "V3" for AES-CBC
        aes_key: AES key (required for V3)
        aes_iv: AES IV (required for V3)
        
    Returns:
        Encrypted data
    """
    if len(data) <= ENCRYPTION_BLOCK_SIZE:
        block = data
        remainder = b''
    else:
        block = data[:ENCRYPTION_BLOCK_SIZE]
        remainder = data[ENCRYPTION_BLOCK_SIZE:]
    
    if version == "V2":
        encrypted_block = xxtea_encrypt(block)
    elif version == "V3":
        if not aes_key or not aes_iv:
            raise ValueError("AES key and IV required for V3 encryption")
        encrypted_block = aes_cbc_encrypt(block, aes_key, aes_iv)
    else:
        raise ValueError(f"Unknown encryption version: {version}")
    
    return encrypted_block + remainder


# ─── Binary Index Generation ─────────────────────────────────────────────────


def generate_ni(stage_nodes: List[Dict], action_nodes: List[Dict],
                image_map: Dict[str, int], audio_map: Dict[str, int]) -> bytes:
    """
    Generate Node Index (.ni) binary file.
    
    Structure:
    - Header (512 bytes): version info, offsets, counts
    - Nodes (44 bytes each): image/audio indices, transitions, controls
    
    Args:
        stage_nodes: List of stage node dicts from story.json
        action_nodes: List of action node dicts from story.json
        image_map: asset_filename -> resource index
        audio_map: asset_filename -> sound index
        
    Returns:
        Binary content for .ni file
    """
    # Build action node lookup: action_id -> index
    action_id_to_idx = {}
    for i, action in enumerate(action_nodes):
        action_id_to_idx[action['id']] = i
    
    # Build stage node UUID -> index lookup
    stage_uuid_to_idx = {}
    for i, node in enumerate(stage_nodes):
        stage_uuid_to_idx[node['uuid']] = i
    
    # Header (25 bytes packed, padded to 512)
    header = struct.pack('<HHiiiiib',
        NI_VERSION,           # Version
        NI_STORY_VERSION,     # Story version
        NI_HEADER_SIZE,       # Offset to first node
        NI_NODE_SIZE,         # Node size
        len(stage_nodes),     # Number of stage nodes
        len(image_map),       # Number of images
        len(audio_map),       # Number of sounds
        1                     # Factory flag
    )
    header += b'\x00' * (NI_HEADER_SIZE - len(header))
    
    # Generate nodes
    nodes_data = bytearray()
    for node in stage_nodes:
        # Image index
        img_asset = node.get('image', '')
        img_idx = image_map.get(img_asset, -1) if img_asset else -1
        
        # Audio index (use storyAudio if available, otherwise audio)
        audio_asset = node.get('storyAudio') or node.get('audio', '')
        audio_idx = audio_map.get(audio_asset, -1) if audio_asset else -1
        
        # OK Transition: (target_node_index, options_count, selected_option_index)
        ok_trans = (-1, -1, -1)
        ok_data = node.get('okTransition')
        if ok_data:
            action_id = ok_data.get('actionNode', '')
            action_idx = action_id_to_idx.get(action_id, -1)
            if action_idx >= 0:
                action = action_nodes[action_idx]
                options = action.get('options', [])
                ok_trans = (action_idx, len(options), 0)
        
        # Home Transition
        home_trans = (-1, -1, -1)
        home_data = node.get('homeTransition')
        if home_data:
            action_id = home_data.get('actionNode', '')
            action_idx = action_id_to_idx.get(action_id, -1)
            if action_idx >= 0:
                action = action_nodes[action_idx]
                options = action.get('options', [])
                home_trans = (action_idx, len(options), 0)
        
        # Control settings
        ctrl = node.get('controlSettings', {})
        
        # Pack node (44 bytes)
        node_bytes = struct.pack('<iiiiiiiihhhhhh',
            img_idx,                                    # Image resource index
            audio_idx,                                  # Audio resource index
            ok_trans[0], ok_trans[1], ok_trans[2],      # OK transition
            home_trans[0], home_trans[1], home_trans[2], # Home transition
            1 if ctrl.get('wheel', True) else 0,        # Wheel enabled
            1 if ctrl.get('ok', True) else 0,           # OK enabled
            1 if ctrl.get('home', True) else 0,         # Home enabled
            1 if ctrl.get('pause', False) else 0,       # Pause enabled
            1 if ctrl.get('autoplay', False) else 0,    # Autoplay
            0                                            # Padding
        )
        nodes_data.extend(node_bytes)
    
    return header + bytes(nodes_data)


def generate_li(action_nodes: List[Dict], stage_uuid_to_idx: Dict[str, int]) -> bytes:
    """
    Generate List Index (.li) binary file.
    
    Contains int32 indices for each action node's options.
    
    Args:
        action_nodes: List of action node dicts
        stage_uuid_to_idx: stage UUID -> stage index mapping
        
    Returns:
        Binary content for .li file
    """
    data = bytearray()
    for action in action_nodes:
        for option_uuid in action.get('options', []):
            idx = stage_uuid_to_idx.get(option_uuid, 0)
            data.extend(struct.pack('<i', idx))
    return bytes(data)


def generate_ri(image_count: int) -> bytes:
    """
    Generate Resource Index (.ri) binary file.
    
    Each entry is 12 bytes: "000\\XXXXXXXX" (folder + filename).
    
    Args:
        image_count: Number of image resources
        
    Returns:
        Binary content for .ri file
    """
    data = bytearray()
    for i in range(image_count):
        entry = f"000\\{i:08d}".encode('ascii')
        data.extend(entry)
    return bytes(data)


def generate_si(audio_count: int) -> bytes:
    """
    Generate Sound Index (.si) binary file.
    
    Same format as .ri but for sound files.
    
    Args:
        audio_count: Number of sound resources
        
    Returns:
        Binary content for .si file
    """
    data = bytearray()
    for i in range(audio_count):
        entry = f"000\\{i:08d}".encode('ascii')
        data.extend(entry)
    return bytes(data)


def generate_bt() -> bytes:
    """
    Generate Boot file (.bt).
    
    Simple file marking the pack as bootable.
    Typically contains just a version marker.
    
    Returns:
        Binary content for bt file
    """
    return struct.pack('<I', 1)  # Version 1


# ─── ZIP Validation ──────────────────────────────────────────────────────────


def is_lunii_pack(zip_path: str) -> bool:
    """
    Check if a ZIP file already contains a valid Lunii pack structure.
    
    Validates:
    - Presence of .content/XXXXXXXX/ directory
    - Required binary files (ni, li, ri, si)
    - Asset directories (rf/, sf/)
    
    Args:
        zip_path: Path to ZIP file
        
    Returns:
        True if the ZIP is already in Lunii format
    """
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            names = zf.namelist()
            
            # Check for .content/ prefix
            content_dirs = [n for n in names if n.startswith('.content/')]
            if not content_dirs:
                return False
            
            # Find pack reference directory
            refs = set()
            for name in content_dirs:
                parts = name.split('/')
                if len(parts) >= 2 and parts[1]:
                    refs.add(parts[1])
            
            if not refs:
                return False
            
            # Check each ref directory for required files
            for ref in refs:
                prefix = f".content/{ref}/"
                ref_files = [n.replace(prefix, '') for n in names if n.startswith(prefix)]
                
                required = ['ni', 'li', 'ri', 'si']
                if not all(f in ref_files for f in required):
                    return False
                
                # Check for asset directories
                has_rf = any(f.startswith('rf/') for f in ref_files)
                has_sf = any(f.startswith('sf/') for f in ref_files)
                
                if not (has_rf and has_sf):
                    return False
            
            return True
    except Exception as e:
        logger.error(f"Error checking Lunii pack format: {e}")
        return False


def validate_studio_pack(zip_path: str) -> Tuple[bool, str]:
    """
    Validate that a Studio Pack ZIP has the correct format for Lunii conversion.
    
    Checks:
    - story.json exists and is valid
    - Assets (images/audio) exist and are referenced
    - Images are 320x240
    - Audio files are MP3
    
    Args:
        zip_path: Path to ZIP file
        
    Returns:
        Tuple of (is_valid, message)
    """
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            names = zf.namelist()
            
            # Check story.json
            if 'story.json' not in names:
                return False, "story.json manquant dans le ZIP"
            
            story_data = json.loads(zf.read('story.json'))
            stage_nodes = story_data.get('stageNodes', [])
            
            if not stage_nodes:
                return False, "Aucun stageNode trouvé dans story.json"
            
            # Collect referenced assets
            images = set()
            audios = set()
            for node in stage_nodes:
                if node.get('image'):
                    images.add(node['image'])
                if node.get('audio'):
                    audios.add(node['audio'])
                if node.get('storyAudio'):
                    audios.add(node['storyAudio'])
            
            # Verify assets exist
            missing = []
            for img in images:
                # story.json may store paths as 'assets/xxx.png' or just 'xxx.png'
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
        """
        Args:
            zip_path: Path to the Studio Pack ZIP
            version: "V2" for XXTEA, "V3" for AES-CBC
            aes_key: AES key bytes (required if version="V3")
            aes_iv: AES IV bytes (required if version="V3")
        """
        self.zip_path = zip_path
        self.version = version
        self.aes_key = aes_key
        self.aes_iv = aes_iv
        self._progress_callback: Optional[Callable[[float, str], None]] = None
    
    def _update_progress(self, progress: float, message: str):
        """Send progress update if callback is set."""
        if self._progress_callback:
            self._progress_callback(progress, message)
        logger.info(f"[{progress:.0%}] {message}")
    
    def convert(self, output_path: Optional[str] = None,
                progress_callback: Optional[Callable[[float, str], None]] = None) -> Optional[str]:
        """
        Convert the Studio Pack to Lunii format.
        
        Args:
            output_path: Optional path for output ZIP. If None, creates alongside input.
            progress_callback: Optional callback(progress: float, message: str)
            
        Returns:
            Path to the generated Lunii ZIP, or None if failed
        """
        self._progress_callback = progress_callback
        
        try:
            # Step 0: Check if already Lunii format
            self._update_progress(0.0, "Vérification du format...")
            if is_lunii_pack(self.zip_path):
                self._update_progress(1.0, "Le pack est déjà au format Lunii !")
                return self.zip_path
            
            # Step 1: Validate
            self._update_progress(0.05, "Validation du pack Studio...")
            is_valid, msg = validate_studio_pack(self.zip_path)
            if not is_valid:
                logger.error(f"Pack validation failed: {msg}")
                self._update_progress(0.0, f"❌ {msg}")
                return None
            
            # Step 2: Extract and parse
            self._update_progress(0.1, "Extraction et parsing...")
            with tempfile.TemporaryDirectory() as tmpdir:
                return self._do_convert(tmpdir, output_path)
                
        except Exception as e:
            logger.error(f"Lunii conversion failed: {e}", exc_info=True)
            self._update_progress(0.0, f"❌ Erreur: {e}")
            return None
    
    def _do_convert(self, tmpdir: str, output_path: Optional[str]) -> Optional[str]:
        """Internal conversion logic within a temp directory."""
        
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
        
        # Generate pack reference (8-char hex from UUID)
        pack_uuid = uuid.uuid4()
        pack_ref = pack_uuid.hex[:8].upper()
        
        # Create output structure
        content_dir = os.path.join(output_dir, ".content", pack_ref)
        rf_dir = os.path.join(content_dir, "rf", "000")
        sf_dir = os.path.join(content_dir, "sf", "000")
        os.makedirs(rf_dir, exist_ok=True)
        os.makedirs(sf_dir, exist_ok=True)
        
        # Collect unique assets
        self._update_progress(0.15, "Indexation des assets...")
        image_assets = []
        audio_assets = []
        image_set = set()
        audio_set = set()
        
        # Track the actual file paths within the extracted dir
        asset_paths = {}  # asset_ref -> actual_path
        
        for node in stage_nodes:
            img = node.get('image', '')
            if img and img not in image_set:
                image_set.add(img)
                image_assets.append(img)
                # Resolve actual path: story.json may store 'assets/x.png' or just 'x.png'
                if os.path.exists(os.path.join(extract_dir, img)):
                    asset_paths[img] = os.path.join(extract_dir, img)
                else:
                    asset_paths[img] = os.path.join(extract_dir, "assets", img)
            
            # Collect both navigation audio and story audio
            for key in ('audio', 'storyAudio'):
                aud = node.get(key, '')
                if aud and aud not in audio_set:
                    audio_set.add(aud)
                    audio_assets.append(aud)
                    if os.path.exists(os.path.join(extract_dir, aud)):
                        asset_paths[aud] = os.path.join(extract_dir, aud)
                    else:
                        asset_paths[aud] = os.path.join(extract_dir, "assets", aud)
        
        # Build asset -> index maps
        image_map = {asset: idx for idx, asset in enumerate(image_assets)}
        audio_map = {asset: idx for idx, asset in enumerate(audio_assets)}
        
        # Convert images
        self._update_progress(0.2, f"Conversion de {len(image_assets)} images...")
        for idx, img_asset in enumerate(image_assets):
            img_path = asset_paths.get(img_asset, '')
            if not os.path.exists(img_path):
                logger.warning(f"Image asset not found: {img_asset}")
                continue
            
            bmp_data = convert_image_to_lunii_bmp(img_path)
            encrypted_data = encrypt_asset(bmp_data, self.version, self.aes_key, self.aes_iv)
            
            out_file = os.path.join(rf_dir, f"{idx:08d}")
            with open(out_file, 'wb') as f:
                f.write(encrypted_data)
            
            progress = 0.2 + (0.2 * (idx + 1) / max(len(image_assets), 1))
            self._update_progress(progress, f"Image {idx + 1}/{len(image_assets)}: {img_asset}")
        
        # Convert audio
        self._update_progress(0.4, f"Conversion de {len(audio_assets)} fichiers audio...")
        for idx, aud_asset in enumerate(audio_assets):
            aud_path = asset_paths.get(aud_asset, '')
            if not os.path.exists(aud_path):
                logger.warning(f"Audio asset not found: {aud_asset}")
                continue
            
            # Convert to Lunii MP3 format
            temp_mp3 = os.path.join(tmpdir, f"temp_audio_{idx}.mp3")
            if not convert_audio_to_lunii_mp3(aud_path, temp_mp3):
                logger.error(f"Failed to convert audio: {aud_asset}")
                continue
            
            with open(temp_mp3, 'rb') as f:
                mp3_data = f.read()
            
            encrypted_data = encrypt_asset(mp3_data, self.version, self.aes_key, self.aes_iv)
            
            out_file = os.path.join(sf_dir, f"{idx:08d}")
            with open(out_file, 'wb') as f:
                f.write(encrypted_data)
            
            # Clean temp
            os.remove(temp_mp3)
            
            progress = 0.4 + (0.3 * (idx + 1) / max(len(audio_assets), 1))
            self._update_progress(progress, f"Audio {idx + 1}/{len(audio_assets)}: {aud_asset}")
        
        # Generate binary indices
        self._update_progress(0.75, "Génération des index binaires...")
        
        # Stage UUID -> index mapping
        stage_uuid_to_idx = {node['uuid']: i for i, node in enumerate(stage_nodes)}
        
        ni_data = generate_ni(stage_nodes, action_nodes, image_map, audio_map)
        li_data = generate_li(action_nodes, stage_uuid_to_idx)
        ri_data = generate_ri(len(image_assets))
        si_data = generate_si(len(audio_assets))
        bt_data = generate_bt()
        
        # Write index files
        for fname, data in [('ni', ni_data), ('li', li_data), ('ri', ri_data),
                            ('si', si_data), ('bt', bt_data)]:
            with open(os.path.join(content_dir, fname), 'wb') as f:
                f.write(data)
        
        # Generate metadata (md file - YAML)
        self._update_progress(0.85, "Génération des métadonnées...")
        md_content = (
            f"title: {title}\n"
            f"uuid: {pack_uuid}\n"
            f"ref: {pack_ref}\n"
            f"version: {self.version}\n"
            f"stageNodes: {len(stage_nodes)}\n"
            f"images: {len(image_assets)}\n"
            f"sounds: {len(audio_assets)}\n"
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
