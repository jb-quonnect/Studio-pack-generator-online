"""
Studio Pack Generator Online - RSS Handler

Handles RSS feed parsing and podcast episode management:
- Parse RSS/Atom feeds using feedparser
- Extract episode metadata (title, description, duration, image)
- Download audio enclosures
- Split episodes into parts based on user settings
"""

import os
import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from urllib.parse import urlparse
import requests

try:
    import feedparser
except ImportError:
    feedparser = None

from .utils import sanitize_filename, ensure_dir, format_duration


logger = logging.getLogger(__name__)


@dataclass
class RssEpisode:
    """Represents a single podcast episode."""
    
    title: str
    url: str  # Audio URL
    description: str = ""
    duration: float = 0.0  # In seconds
    image_url: Optional[str] = None
    published: Optional[str] = None
    season: Optional[int] = None
    episode_number: Optional[int] = None
    guid: str = ""
    
    # Local paths after download
    audio_path: Optional[str] = None
    image_path: Optional[str] = None
    
    # Selection state
    selected: bool = True


@dataclass
class RssFeed:
    """Represents a parsed RSS feed."""
    
    title: str
    description: str = ""
    image_url: Optional[str] = None
    link: str = ""
    episodes: List[RssEpisode] = field(default_factory=list)
    
    # Metadata
    author: str = ""
    language: str = "fr"


def parse_duration(duration_str: str) -> float:
    """
    Parse duration string to seconds.
    
    Supports formats:
    - HH:MM:SS
    - MM:SS
    - Seconds as integer
    
    Args:
        duration_str: Duration string
        
    Returns:
        Duration in seconds
    """
    if not duration_str:
        return 0.0
    
    try:
        # Try integer seconds
        return float(duration_str)
    except ValueError:
        pass
    
    # Try HH:MM:SS or MM:SS
    parts = duration_str.split(':')
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        pass
    
    return 0.0


def parse_rss_feed(url: str, existing_title: Optional[str] = None, existing_image_url: Optional[str] = None) -> Optional[RssFeed]:
    """
    Parse an RSS feed from URL.
    
    Args:
        url: RSS feed URL
        existing_title: Optional title to force/use (for Radio France)
        existing_image_url: Optional image URL to force/use (for Radio France)
        
    Returns:
        RssFeed object or None if failed
    """
    if feedparser is None:
        logger.error("feedparser not installed")
        return None
    
    # Handle Radio France Virtual URLs
    if url.startswith('rf://'):
        try:
            from .radiofrance_api import RadioFranceClient
            show_id = url.replace('rf://', '')
            logger.info(f"Detected Radio France virtual URL for show ID: {show_id}, using existing metadata if available")
            return RadioFranceClient.get_feed(show_id, existing_title, existing_image_url)
        except Exception as e:
            logger.error(f"Failed to fetch Radio France feed: {e}")
            return None

    try:
        logger.info(f"Parsing RSS feed: {url}")
        
        # Parse the feed
        feed = feedparser.parse(url)
        
        if feed.bozo and not feed.entries:
            logger.error(f"Failed to parse RSS: {feed.bozo_exception}")
            return None
        
        # Extract feed metadata
        rss_feed = RssFeed(
            title=feed.feed.get('title', 'Unknown Podcast'),
            description=feed.feed.get('description', ''),
            link=feed.feed.get('link', ''),
            author=feed.feed.get('author', ''),
            language=feed.feed.get('language', 'fr')
        )
        
        # Get feed image
        if 'image' in feed.feed:
            rss_feed.image_url = feed.feed.image.get('href') or feed.feed.image.get('url')
        elif 'itunes_image' in feed.feed:
            rss_feed.image_url = feed.feed.itunes_image.get('href')
        
        # Parse episodes
        for entry in feed.entries:
            episode = parse_episode(entry, rss_feed.image_url)
            if episode:
                rss_feed.episodes.append(episode)
                
        # Auto-assign chronological numbers to episodes if they lack one
        # Feed is generally newest-first. So index 0 is the newest (total).
        total_eps = len(rss_feed.episodes)
        for i, ep in enumerate(rss_feed.episodes):
            if not ep.episode_number:
                ep.episode_number = str(total_eps - i)
        
        logger.info(f"Parsed {len(rss_feed.episodes)} episodes from {rss_feed.title}")
        return rss_feed
        
    except Exception as e:
        logger.error(f"Failed to parse RSS feed: {e}")
        return None


def parse_episode(entry: Dict, feed_image_url: Optional[str] = None) -> Optional[RssEpisode]:
    """
    Parse a single episode entry from feedparser.
    
    Args:
        entry: Feedparser entry dict
        feed_image_url: URL of the main feed image (to avoid duplicates)
        
    Returns:
        RssEpisode or None if no audio enclosure
    """
    title = entry.get('title', 'Unknown Episode')
    description = entry.get('description') or entry.get('summary', '')
    
    # Find audio enclosure
    audio_url = ""
    duration_str = "0"
    
    # Check for enclosures
    for enclosure in entry.get('enclosures', []):
        mime_type = enclosure.get('type', '')
        if mime_type.startswith('audio/') or enclosure.get('medium') == 'audio':
            audio_url = enclosure.get('href')
            # Try to get duration from enclosure if available
            # Note: Feedparser sometimes puts duration in different places
            break
            
    # Check for itunes:duration
    if 'itunes_duration' in entry:
        duration_str = entry['itunes_duration']
    
    # Parse duration
    duration = parse_duration(str(duration_str))
    
    if not audio_url:
        return None
    # Get image
    image_url = None
    candidates = []
    
    # 1. Try itunes_image (specific)
    if 'itunes_image' in entry:
        if isinstance(entry['itunes_image'], dict):
            candidates.append(entry['itunes_image'].get('href'))
        elif isinstance(entry['itunes_image'], str):
             candidates.append(entry['itunes_image'])

    # 2. Try image tag
    if 'image' in entry:
        if isinstance(entry['image'], dict):
             candidates.append(entry['image'].get('href'))
        elif isinstance(entry['image'], str):
             candidates.append(entry['image'])
             
    # 3. Try media_content
    if 'media_content' in entry:
        for media in entry['media_content']:
            if 'image' in media.get('type', '') or media.get('medium') == 'image':
                candidates.append(media.get('url'))
                
    # 4. Try media_thumbnail
    if 'media_thumbnail' in entry:
         thumbs = entry['media_thumbnail']
         if isinstance(thumbs, list):
             for t in thumbs:
                 candidates.append(t.get('url'))
         elif isinstance(thumbs, dict):
             candidates.append(thumbs.get('url'))
             
    # 5. Try links
    if 'links' in entry:
        for link in entry['links']:
            if 'image' in link.get('type', '') or link.get('rel') == 'image':
                candidates.append(link.get('href'))

    # Select the first candidate that is NOT the feed image
    for url in candidates:
        if url and url != feed_image_url:
            image_url = url
            break
            
    # Fallback to feed image if nothing unique found
    if not image_url and feed_image_url:
        image_url = feed_image_url
    elif not image_url and candidates:
        image_url = candidates[0]
    
    # Get season/episode numbers
    season = None
    episode_num = None
    
    if 'itunes_season' in entry:
        try:
            season = int(entry['itunes_season'])
        except (ValueError, TypeError):
            pass
    
    if 'itunes_episode' in entry:
        try:
            episode_num = int(entry['itunes_episode'])
        except (ValueError, TypeError):
            pass
    
    # Get GUID
    guid = entry.get('id', '') or entry.get('guid', '')
    
    # Get published date
    published = entry.get('published', '')
    
    return RssEpisode(
        title=title,
        url=audio_url,
        description=description,
        duration=duration,
        image_url=image_url,
        season=season,
        episode_number=episode_num,
        guid=guid,
        published=published
    )


def download_episode_audio(
    episode: RssEpisode,
    output_dir: str,
    progress_callback=None
) -> bool:
    """
    Download the audio file for an episode.
    
    Args:
        episode: RssEpisode object
        output_dir: Directory to save to
        progress_callback: Optional callback for progress updates
        
    Returns:
        True if successful
    """
    if not episode.url:
        return False
    
    try:
        ensure_dir(output_dir)
        
        # Generate filename
        filename = sanitize_filename(episode.title)[:100]
        
        # Determine extension from URL
        parsed = urlparse(episode.url)
        ext = os.path.splitext(parsed.path)[1] or '.mp3'
        
        output_path = os.path.join(output_dir, f"{filename}{ext}")
        
        # Download with streaming
        logger.info(f"Downloading: {episode.title}")
        
        response = requests.get(episode.url, stream=True, timeout=300)
        response.raise_for_status()
        
        total_size = int(response.headers.get('content-length', 0))
        downloaded = 0
        
        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    
                    if progress_callback and total_size:
                        progress_callback(downloaded / total_size)
        
        episode.audio_path = output_path
        logger.info(f"Downloaded: {output_path}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to download episode: {e}")
        return False


def download_episode_image(episode: RssEpisode, output_dir: str) -> bool:
    """
    Download the image for an episode.
    
    Args:
        episode: RssEpisode object
        output_dir: Directory to save to
        
    Returns:
        True if successful
    """
    if not episode.image_url:
        return False
    
    try:
        ensure_dir(output_dir)
        
        filename = sanitize_filename(episode.title)[:100]
        
        # Determine extension
        parsed = urlparse(episode.image_url)
        ext = os.path.splitext(parsed.path)[1] or '.jpg'
        if ext not in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
            ext = '.jpg'
        
        output_path = os.path.join(output_dir, f"{filename}.item{ext}")
        
        response = requests.get(episode.image_url, timeout=30)
        response.raise_for_status()
        
        with open(output_path, 'wb') as f:
            f.write(response.content)
        
        episode.image_path = output_path
        return True
        
    except Exception as e:
        logger.error(f"Failed to download episode image: {e}")
        return False


def download_feed_image(feed: RssFeed, output_dir: str) -> Optional[str]:
    """
    Download the main image from a podcast feed.
    
    Args:
        feed: RssFeed object with image_url
        output_dir: Directory to save to
        
    Returns:
        Path to downloaded image or None if failed
    """
    if not feed.image_url:
        logger.info("No feed image URL available")
        return None
    
    try:
        ensure_dir(output_dir)
        
        filename = sanitize_filename(feed.title)[:50]
        
        # Determine extension
        parsed = urlparse(feed.image_url)
        ext = os.path.splitext(parsed.path)[1] or '.jpg'
        if ext not in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
            ext = '.jpg'
        
        output_path = os.path.join(output_dir, f"{filename}_cover{ext}")
        
        logger.info(f"Downloading feed image: {feed.image_url}")
        response = requests.get(feed.image_url, timeout=30)
        response.raise_for_status()
        
        with open(output_path, 'wb') as f:
            f.write(response.content)
        
        logger.info(f"Downloaded feed image: {output_path}")
        return output_path
        
    except Exception as e:
        logger.error(f"Failed to download feed image: {e}")
        return None


def split_episodes_into_parts(
    episodes: List[RssEpisode],
    episodes_per_part: int = 10
) -> List[List[RssEpisode]]:
    """
    Split episodes into parts based on the specified size.
    
    Args:
        episodes: List of episodes
        episodes_per_part: Number of episodes per part
        
    Returns:
        List of lists, each representing a part
    """
    if episodes_per_part <= 0:
        episodes_per_part = 10
    
    parts = []
    for i in range(0, len(episodes), episodes_per_part):
        parts.append(episodes[i:i + episodes_per_part])
    
    return parts


def group_episodes_by_season(
    episodes: List[RssEpisode]
) -> Dict[Optional[int], List[RssEpisode]]:
    """
    Group episodes by season number.
    
    Args:
        episodes: List of episodes
        
    Returns:
        Dictionary with season number as key, episodes as value
    """
    seasons: Dict[Optional[int], List[RssEpisode]] = {}
    
    for episode in episodes:
        season = episode.season
        if season not in seasons:
            seasons[season] = []
        seasons[season].append(episode)
    
    return seasons


def filter_episodes_by_duration(
    episodes: List[RssEpisode],
    min_duration: float = 0
) -> List[RssEpisode]:
    """
    Filter episodes by minimum duration.
    
    Args:
        episodes: List of episodes
        min_duration: Minimum duration in seconds
        
    Returns:
        Filtered list of episodes
    """
    if min_duration <= 0:
        return episodes
    
    return [ep for ep in episodes if ep.duration >= min_duration]


def get_selected_episodes(episodes: List[RssEpisode]) -> List[RssEpisode]:
    """
    Get only the selected episodes.
    
    Args:
        episodes: List of all episodes
        
    Returns:
        List of selected episodes
    """
    return [ep for ep in episodes if ep.selected]
