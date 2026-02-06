"""Google Custom Search API client"""
import logging
import time
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class GoogleCustomSearch:
    def __init__(self, api_key: str, cx: str, timeout: int = 15, cache_ttl_seconds: int = 86400):
        self.api_key = api_key
        self.cx = cx
        self.timeout = timeout
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cache: Dict[str, Dict] = {}

    def _get_cached(self, cache_key: str) -> Optional[List[Dict]]:
        item = self._cache.get(cache_key)
        if not item:
            return None
        if time.time() - item["ts"] > self.cache_ttl_seconds:
            self._cache.pop(cache_key, None)
            return None
        return item["results"]

    def _set_cached(self, cache_key: str, results: List[Dict]) -> None:
        self._cache[cache_key] = {"ts": time.time(), "results": results}

    def search(self, query: str, num: int = 10) -> List[Dict]:
        """Search via Google CSE and return normalized results."""
        if not self.api_key or not self.cx:
            raise ValueError("Google CSE API key or CX not configured")

        normalized_query = " ".join(query.split()).strip().lower()
        cache_key = f"{normalized_query}:{num}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        params = {
            "key": self.api_key,
            "cx": self.cx,
            "q": query,
            "num": max(1, min(num, 10)),
        }

        try:
            start = time.time()
            response = requests.get("https://www.googleapis.com/customsearch/v1", params=params, timeout=self.timeout)
            duration_ms = int((time.time() - start) * 1000)

            if response.status_code != 200:
                logger.warning("CSE error %s: %s", response.status_code, response.text[:300])
                response.raise_for_status()

            data = response.json()
            items = data.get("items", []) or []
            results = []
            seen = set()
            for item in items:
                url = item.get("link")
                if not url or url in seen:
                    continue
                seen.add(url)
                results.append(
                    {
                        "url": url,
                        "title": item.get("title", ""),
                        "snippet": item.get("snippet", ""),
                        "display_link": item.get("displayLink", ""),
                    }
                )

            logger.info("CSE query='%s' results=%s duration_ms=%s", query, len(results), duration_ms)
            self._set_cached(cache_key, results)
            return results
        except Exception as exc:
            logger.error("CSE search failed: %s", exc, exc_info=True)
            raise
