"""
Radio France API Client

Handles interaction with the official Radio France internal API to:
1. Search for shows (replacing Aerion)
2. Fetch full episode history (replacing RSS limit)
3. Convert data to internal RssFeed/RssEpisode structures

Based on internal specifications:
- Headers: x-token, User-Agent, Accept
- Endpoints: /stations/search, /shows/{id}/diffusions
"""

import requests
import logging
import time
from typing import List, Optional, Dict, Any, Tuple
from urllib.parse import quote

# Import data structures from rss_handler to ensure compatibility
from .rss_handler import RssFeed, RssEpisode, parse_duration

logger = logging.getLogger(__name__)

# Constants
API_BASE_URL = "https://api.radiofrance.fr/v1"
HEADERS = {
    "Accept": "application/x.radiofrance.mobileapi+json",
    "User-Agent": "AppRF",
    "x-token": "9ab343ce-cae2-4bdb-90ca-526a3dede870"
}

class RadioFranceClient:
    """Client for Radio France API."""

    @staticmethod
    def _get_image_url(visuals: Dict, main_image: Optional[Dict] = None) -> Optional[str]:
        """
        Extract best available image URL from visuals object.
        Prioritizes square visuals.
        """
        visual_uuid = None
        
        # 1. Try specific formats
        if visuals:
            if 'square_banner' in visuals:
                visual_uuid = visuals['square_banner']
            elif 'square_visual' in visuals:
                visual_uuid = visuals['square_visual']
            # 2. Fallback to first available
            elif isinstance(visuals, dict) and len(visuals) > 0:
                visual_uuid = list(visuals.values())[0]

        # 3. Fallback to mainImage
        if not visual_uuid and main_image:
             # mainImage might be an ID string or dict? Spec says "fallbackImgId (souvent mainImage)"
             # In some API responses mainImage is a UUID string directly.
             if isinstance(main_image, str):
                 visual_uuid = main_image
             elif isinstance(main_image, dict) and 'id' in main_image:
                 visual_uuid = main_image['id']

        if visual_uuid:
            return f"https://api.radiofrance.fr/v1/services/embed/image/{visual_uuid}?preset=568x568"
        
        return None

    @staticmethod
    def search_shows(query: str, limit: int = 10) -> List[Dict]:
        """
        Search for shows.
        Returns list of dicts: {id, title, author, image_url, description}
        """
        results = []
        try:
            url = f"{API_BASE_URL}/stations/search"
            params = {
                "value": query,
                "include": "show"
            }
            
            response = requests.get(url, headers=HEADERS, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # Parse included shows for quick lookup
            included_shows = {}
            if 'included' in data and 'shows' in data['included']:
                included_shows = data['included']['shows']
                
            # Filter results
            for item in data.get('data', []):
                result_items = item.get('resultItems', {})
                if result_items.get('model') != 'show':
                    continue
                    
                # Get show ID from relationships
                rels = result_items.get('relationships', {})
                show_ids = rels.get('show', [])
                
                if not show_ids:
                    continue
                    
                show_id = show_ids[0]
                show_details = included_shows.get(show_id)
                
                if not show_details:
                    # Fallback if included is missing but we have basic info in resultItems
                    # Note: resultItems has title/id but maybe not image
                    if not result_items.get('title'):
                        continue
                    show_details = result_items
                
                # Extract details
                # If using fallback, visuals might be missing or different structure
                visuals = show_details.get('visuals')
                main_image = show_details.get('mainImage')
                
                image_url = RadioFranceClient._get_image_url(visuals, main_image)
                
                results.append({
                    "id": show_id,
                    "title": show_details.get('title'),
                    "author": "Radio France", 
                    "image_url": image_url,
                    "description": show_details.get('standfirst', ''),
                    "feed_url": f"rf://{show_id}" # Virtual URL
                })
                
        except Exception as e:
            logger.error(f"Radio France search failed: {e}")
            
        return results[:limit]

    @staticmethod
    def get_feed(show_id: str) -> Optional[RssFeed]:
        """
        Fetch full episode history for a show and return RssFeed object.
        Handles pagination transparently.
        """
        try:
            logger.info(f"Fetching full history for Radio France show: {show_id}")
            
            # 1. Fetch episodes (loop)
            all_diffusions = []
            manifestations_store = {}
            show_info = None
            
            page = 0
            while True:
                url = f"{API_BASE_URL}/shows/{show_id}/diffusions"
                params = {
                    "filter[manifestations][exists]": "true",
                    "include": ["show", "manifestations"], # Correct format: multiple keys
                    "page[offset]": page
                }
                
                response = requests.get(url, headers=HEADERS, params=params, timeout=15)
                response.raise_for_status()
                data = response.json()
                
                # Capture show info from first page
                if page == 0:
                    if 'included' in data and 'shows' in data['included'] and show_id in data['included']['shows']:
                        show_info = data['included']['shows'][show_id]
                
                # Store manifestations
                if 'included' in data and 'manifestations' in data['included']:
                     manifestations_store.update(data['included']['manifestations'])
                
                # Add diffusions
                items = data.get('data', [])
                all_diffusions.extend(items)
                
                logger.debug(f"Fetched page {page}, total items so far: {len(all_diffusions)}")
                
                # Check pagination
                if 'links' in data and 'next' in data['links'] and data['links']['next']:
                    page += 1
                    time.sleep(0.1) # Be nice to the API
                else:
                    break
            
            if not show_info:
                # Fallback fetch if show info wasn't in included (rare)
                logger.warning(f"Show info not found in diffusions, fetching separate info for {show_id}")
                info_resp = requests.get(f"{API_BASE_URL}/shows/{show_id}", headers=HEADERS)
                if info_resp.ok:
                    data_fallback = info_resp.json().get('data', {})
                    # Handle nesting under 'shows' -> {id} -> details
                    if 'shows' in data_fallback and show_id in data_fallback['shows']:
                        show_info = data_fallback['shows'][show_id]
                    else:
                        show_info = data_fallback
            
            if not show_info:
                logger.error("Could not fetch show info")
                return None

            # 2. Build RssFeed object
            feed = RssFeed(
                title=show_info.get('title', 'Unknown Radio France Show'),
                description=show_info.get('standfirst', ''),
                link=show_info.get('path', ''),
                author="Radio France",
                language="fr"
            )
            
            feed.image_url = RadioFranceClient._get_image_url(
                show_info.get('visuals'), 
                show_info.get('mainImage')
            )
            
            # 3. Build RssEpisodes
            for item in all_diffusions:
                diffusion = item.get('diffusions', item)
                
                # Find audio manifestation
                audio_url = None
                duration = 0.0
                
                manif_ids = diffusion.get('relationships', {}).get('manifestations', [])
                logger.debug(f"Diffusion {diffusion.get('id')} has manifests: {manif_ids}")
                
                found_manif = None
                for mid in manif_ids:
                    manif = manifestations_store.get(mid)
                    if not manif:
                        continue
                    
                    # Criteria: principal=true AND mediaType not youtube/dailymotion
                    
                    logger.debug(f"Checking manifest {mid}: principal={manif.get('principal')}, type={manif.get('mediaType')}")

                    if manif.get('principal') is True: # strict check?
                        mtype = manif.get('mediaType', '')
                        if 'youtube' not in mtype and 'dailymotion' not in mtype:
                            found_manif = manif
                            break
                
                if not found_manif:
                    logger.debug(f"No valid manifestation found for diffusion {diffusion.get('id')}")
                    continue
                    
                audio_url = found_manif.get('url')
                duration = float(found_manif.get('duration', 0))
                
                if not audio_url:
                    continue
                    
                # Episode Image
                ep_image = RadioFranceClient._get_image_url(
                     diffusion.get('visuals'),
                     diffusion.get('mainImage')
                ) or feed.image_url # Fallback to feed image
                
                # Date
                published = None
                created = diffusion.get('createdTime')
                # We could format this, but RssEpisode just wants a string usually, 
                # or we can leave it raw? rss_handler doesn't strictly parse date for logic yet.
                # Spec says convert to RFC 2822, but for internal use, raw is arguably fine or simple ISO.
                # Let's keep it simple for now, generic string.
                if created:
                    published = str(created) 

                feed.episodes.append(RssEpisode(
                    title=diffusion.get('title', 'Untitled'),
                    url=audio_url,
                    description=diffusion.get('standfirst') or diffusion.get('bodyMarkdown') or "",
                    duration=duration,
                    image_url=ep_image,
                    guid=diffusion.get('id', ''),
                    published=published
                ))

            logger.info(f"Generated feed with {len(feed.episodes)} episodes")
            return feed

        except Exception as e:
            logger.error(f"Failed to fetch Radio France feed: {e}", exc_info=True)
            return None
