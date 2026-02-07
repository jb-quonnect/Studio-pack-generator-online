"""
Studio Pack Generator Online - Audio Processor

Handles audio conversion, normalization, and format standardization using FFmpeg.
Target format: MP3, 44100Hz, Mono with dynamic audio normalization.
"""

import os
import subprocess
import shutil
import logging
from pathlib import Path
from typing import Optional, Tuple
from dataclasses import dataclass

from .utils import compute_sha1, ensure_dir


logger = logging.getLogger(__name__)


# Supported input formats
SUPPORTED_AUDIO_FORMATS = {'.mp3', '.ogg', '.opus', '.wav', '.m4a', '.flac'}

# Target format specifications
TARGET_SAMPLE_RATE = 44100
TARGET_CHANNELS = 1  # Mono
TARGET_FORMAT = 'mp3'


@dataclass
class AudioInfo:
    """Information about an audio file."""
    duration: float = 0.0
    sample_rate: int = 0
    channels: int = 0
    codec: str = ""
    bitrate: int = 0
    max_volume_db: float = 0.0


def is_ffmpeg_available() -> bool:
    """
    Check if FFmpeg is available in the system PATH.
    
    Returns:
        True if FFmpeg is available
    """
    try:
        result = subprocess.run(
            ['ffmpeg', '-version'],
            capture_output=True,
            encoding='utf-8',
            errors='replace',
            timeout=10
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def get_audio_info(file_path: str) -> Optional[AudioInfo]:
    """
    Get information about an audio file using FFprobe.
    
    Args:
        file_path: Path to audio file
        
    Returns:
        AudioInfo object or None if failed
    """
    try:
        # Get duration and format info
        result = subprocess.run([
            'ffprobe',
            '-v', 'error',
            '-show_entries', 'format=duration:stream=sample_rate,channels,codec_name,bit_rate',
            '-of', 'csv=p=0',
            file_path
        ], capture_output=True, encoding='utf-8', errors='replace', timeout=30)
        
        if result.returncode != 0:
            logger.error(f"FFprobe failed for {file_path}: {result.stderr}")
            return None
        
        # Parse output (format may vary)
        lines = result.stdout.strip().split('\n')
        info = AudioInfo()
        
        for line in lines:
            parts = line.split(',')
            for part in parts:
                try:
                    val = float(part)
                    if val > 1000:  # Likely sample rate or bitrate
                        if val > 10000:
                            info.sample_rate = int(val)
                        else:
                            info.bitrate = int(val)
                    elif val < 100:  # Likely duration or channels
                        if val < 10:
                            info.channels = int(val)
                        else:
                            info.duration = val
                except ValueError:
                    if part:
                        info.codec = part
        
        return info
        
    except subprocess.SubprocessError as e:
        logger.error(f"Failed to get audio info: {e}")
        return None


def analyze_volume(file_path: str) -> float:
    """
    Analyze the maximum volume of an audio file.
    
    Args:
        file_path: Path to audio file
        
    Returns:
        Maximum volume in dB (negative value, 0 = max)
    """
    try:
        result = subprocess.run([
            'ffmpeg',
            '-i', file_path,
            '-af', 'volumedetect',
            '-vn', '-sn', '-dn',
            '-f', 'null',
            'NUL' if os.name == 'nt' else '/dev/null'
        ], capture_output=True, encoding='utf-8', errors='replace', timeout=120)
        
        # Parse max_volume from stderr
        for line in result.stderr.split('\n'):
            if 'max_volume:' in line:
                # Extract value like "-12.5 dB"
                parts = line.split('max_volume:')[1].strip().split()
                if parts:
                    return float(parts[0])
        
        return 0.0
        
    except (subprocess.SubprocessError, ValueError) as e:
        logger.error(f"Failed to analyze volume: {e}")
        return 0.0


def convert_audio(
    input_path: str,
    output_path: str,
    normalize: bool = True,
    add_delay: bool = False,
    seek_start: Optional[str] = None,
    target_sample_rate: int = TARGET_SAMPLE_RATE,
    target_channels: int = TARGET_CHANNELS
) -> bool:
    """
    Convert an audio file to the target format with optional processing.
    
    Args:
        input_path: Source audio file
        output_path: Destination path (will be MP3)
        normalize: Apply dynamic audio normalization
        add_delay: Add 1 second silence at beginning and end
        seek_start: Skip beginning (format: "HH:mm:ss" or seconds)
        target_sample_rate: Output sample rate (default 44100)
        target_channels: Output channels (default 1 = mono)
        
    Returns:
        True if successful
    """
    if not os.path.exists(input_path):
        logger.error(f"Input file not found: {input_path}")
        return False
    
    # Build filter chain
    filters = []
    
    # Volume boost if needed
    if normalize:
        max_vol = analyze_volume(input_path)
        if max_vol < -1.0:  # Below -1dB, boost it
            boost = min(abs(max_vol), 10)  # Cap at 10dB boost
            filters.append(f"volume={boost}dB")
        
        # Dynamic normalization
        filters.append("dynaudnorm")
    
    # Add delay at beginning and end
    if add_delay:
        # 1000ms delay on all channels
        filters.append("adelay=1000|1000")
        filters.append("apad=pad_dur=1s")
    
    # Build FFmpeg command
    cmd = ['ffmpeg', '-y']
    
    # Seek if specified
    if seek_start:
        cmd.extend(['-ss', str(seek_start)])
    
    cmd.extend(['-i', input_path])
    
    # Apply filters if any
    if filters:
        cmd.extend(['-af', ','.join(filters)])
    
    # Output format settings
    cmd.extend([
        '-ac', str(target_channels),
        '-ar', str(target_sample_rate),
        '-map_metadata', '-1',  # Strip metadata
        '-fflags', '+bitexact',
        '-flags:a', '+bitexact',
        output_path
    ])
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            encoding='utf-8',
            errors='replace',
            timeout=300  # 5 minute timeout
        )
        
        if result.returncode != 0:
            logger.error(f"FFmpeg conversion failed: {result.stderr}")
            return False
        
        logger.info(f"Converted: {input_path} -> {output_path}")
        return True
        
    except subprocess.SubprocessError as e:
        logger.error(f"FFmpeg error: {e}")
        return False


def process_audio_to_asset(
    input_path: str,
    assets_dir: str,
    normalize: bool = True,
    add_delay: bool = False
) -> Optional[str]:
    """
    Process an audio file and save it to assets with SHA1 filename.
    
    Args:
        input_path: Source audio file
        assets_dir: Assets directory path
        normalize: Apply normalization
        add_delay: Add silence padding
        
    Returns:
        Asset filename (SHA1.mp3) or None if failed
    """
    ensure_dir(assets_dir)
    
    # Create temp output path
    temp_output = os.path.join(assets_dir, "temp_converting.mp3")
    
    # Convert
    if not convert_audio(input_path, temp_output, normalize, add_delay):
        return None
    
    # Compute SHA1 of converted file
    sha1 = compute_sha1(temp_output)
    asset_filename = f"{sha1}.mp3"
    final_path = os.path.join(assets_dir, asset_filename)
    
    # Rename to SHA1 filename (or remove if duplicate)
    if os.path.exists(final_path):
        os.remove(temp_output)  # Already have this asset
    else:
        os.rename(temp_output, final_path)
    
    return asset_filename


def get_audio_duration(file_path: str) -> float:
    """
    Get the duration of an audio file in seconds.
    
    Args:
        file_path: Path to audio file
        
    Returns:
        Duration in seconds, or 0.0 if failed
    """
    try:
        result = subprocess.run([
            'ffprobe',
            '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'csv=p=0',
            file_path
        ], capture_output=True, encoding='utf-8', errors='replace', timeout=30)
        
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
        return 0.0
        
    except (subprocess.SubprocessError, ValueError):
        return 0.0


def needs_conversion(file_path: str) -> bool:
    """
    Check if an audio file needs conversion to target format.
    
    Args:
        file_path: Path to audio file
        
    Returns:
        True if conversion is needed
    """
    # Non-MP3 files always need conversion
    if Path(file_path).suffix.lower() != '.mp3':
        return True
    
    info = get_audio_info(file_path)
    if info is None:
        return True
    
    # Check if already in target format
    if info.sample_rate != TARGET_SAMPLE_RATE:
        return True
    if info.channels != TARGET_CHANNELS:
        return True
    
    return False
