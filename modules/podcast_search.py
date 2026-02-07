"""
Unified Podcast Search Module

Handles searching for podcasts across multiple sources:
1. iTunes API (Global)
2. Radio France Aerion API (Specialized)

Provides a unified search function that merges and deduplicates results.
"""

import requests
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Optional, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# API Endpoints
ITUNES_API_URL = "https://itunes.apple.com/search"
AERION_API_URL = "https://radio-france-rss.aerion.workers.dev/search"

@dataclass
class SearchResult:
    """Standardized search result."""
    title: str
    author: str
    feed_url: str
    image_url: str
    source: str  # 'itunes' or 'aerion'
    description: str = ""
    
    def __hash__(self):
        return hash(self.feed_url)
        
    def __eq__(self, other):
        if not isinstance(other, SearchResult):
            return False
        return self.feed_url == other.feed_url


def search_itunes(query: str, limit: int = 25) -> List[SearchResult]:
    """
    Search podcasts using iTunes API.
    """
    results = []
    try:
        params = {
            "term": query,
            "entity": "podcast",
            "limit": limit
        }
        response = requests.get(ITUNES_API_URL, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()
        
        for item in data.get("results", []):
            if not item.get("feedUrl"):
                continue
                
            results.append(SearchResult(
                title=item.get("collectionName", "Unknown"),
                author=item.get("artistName", ""),
                feed_url=item.get("feedUrl"),
                image_url=item.get("artworkUrl600") or item.get("artworkUrl100", ""),
                source="itunes",
                description=""
            ))
            
    except Exception as e:
        logger.error(f"iTunes search failed: {e}")
        
    return results


def search_radio_france(query: str) -> List[SearchResult]:
    """
    Search podcasts using internal Radio France API.
    """
    results = []
    try:
        from .radiofrance_api import RadioFranceClient
        
        # Call official API
        rf_results = RadioFranceClient.search_shows(query)
        
        for item in rf_results:
            results.append(SearchResult(
                title=item.get("title", "Inconnu"),
                author="Radio France",
                feed_url=item.get("feed_url"), # Will be rf://{id}
                image_url=item.get("image_url", ""),
                source="radio_france",
                description=item.get("description", "")
            ))
            
    except Exception as e:
        logger.error(f"Radio France search failed: {e}")
        
    return results


def unified_search(query: str) -> List[SearchResult]:
    """
    Search both sources in parallel and merge results.
    Prioritizes Aerion for Radio France content.
    """
    all_results = []
    
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_itunes = executor.submit(search_itunes, query)
        future_rf = executor.submit(search_radio_france, query)
        
        # Wait for both
        futures = {future_itunes: "itunes", future_rf: "radio_france"}
        for future in as_completed(futures):
            source = futures[future]
            try:
                res = future.result()
                all_results.extend(res)
                logger.info(f"Got {len(res)} results from {source}")
            except Exception as e:
                logger.error(f"Search failed for {source}: {e}")

    # Deduplicate preserving order (Aerion first if we put them first? No, let's sort/prioritize)
    # Strategy: 
    # 1. Create a dict by feed_url to deduplicate
    # 2. If same feed exists in both, keep the one from Aerion if query implies RF?
    # Actually, Aerion results are likely higher quality for RF shows.
    
    # We want to prioritize Aerion results IF they match the query well.
    # Simple merge: prefer Aerion if duplicate.
    
    unique_results = {}
    
    # Process Aerion first to populate dict (priority)
    rf_results = [r for r in all_results if r.source == "radio_france"]
    itunes_results = [r for r in all_results if r.source == "itunes"]
    
    for r in rf_results:
        unique_results[r.feed_url] = r
        
    for r in itunes_results:
        if r.feed_url not in unique_results:
            unique_results[r.feed_url] = r
            
    # Convert back to list
    final_list = list(unique_results.values())
    
    # Sorting: 
    # It's hard to know which is "better" without relevance score.
    # But usually iTunes results are returned in relevance order.
    # We should preserve relative order from iTunes, but inject Aerion matches?
    
    # Simplified approach: Return Aerion matches first, then iTunes remainder.
    # This matches the user req: "Priorise les résultats provenant de Radio France"
    
    sorted_results = []
    
    # 1. Add Radio France results (they are already in unique_results, potentially replacing iTunes ones)
    for r in rf_results:
        if r.feed_url in unique_results: # Still valid
            sorted_results.append(unique_results[r.feed_url])
            del unique_results[r.feed_url] # Mark processed
            
    # 2. Add remaining iTunes results (that were not duplicates/covered by Aerion)
    for r in itunes_results:
        if r.feed_url in unique_results:
            sorted_results.append(unique_results[r.feed_url])
            del unique_results[r.feed_url]
            
    return sorted_results
