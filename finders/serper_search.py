"""
Geo-accurate Google Search via Serper.dev.
Supports multiple API keys — rotates to the next key automatically
when the current one hits a 429 or runs out of credits.
"""
import logging
import threading
import time
from typing import Dict, List, Optional

import requests

import config

logger = logging.getLogger(__name__)

_EXHAUSTED_PHRASES = (
    "credits",
    "quota",
    "limit exceeded",
    "insufficient",
    "plan",
)


def _looks_exhausted(resp: requests.Response) -> bool:
    if resp.status_code == 429:
        return True
    if resp.status_code in (402, 403):
        return True
    try:
        body = resp.json()
        msg = str(body.get("message", "")).lower()
        return any(p in msg for p in _EXHAUSTED_PHRASES)
    except Exception:
        return False


class SerperSearch:
    """
    Thread-safe Serper.dev wrapper with multi-key rotation.

    Usage:
        searcher = SerperSearch()
        results = searcher.search("Earlwood Equine store website", country_code="au")
        # results → list of {"title": ..., "link": ..., "snippet": ...}
    """

    def __init__(self):
        self._keys = list(config.SERPER_API_KEYS)
        if not self._keys:
            raise ValueError(
                "No Serper API keys configured. "
                "Set SERPER_API_KEYS=key1,key2,key3 in your .env"
            )
        self._idx = 0
        self._lock = threading.Lock()
        logger.info("SerperSearch ready — %d key(s) loaded", len(self._keys))

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        country_code: str = "",
        num: int = None,
    ) -> List[Dict]:
        """
        Run a Google Search via Serper and return organic results.

        Args:
            query:        Search query string.
            country_code: ISO-2 code (e.g. "au", "gb"). Passed as `gl`
                          to Serper so results are geo-localised.
            num:          Number of results (default: config.SERPER_RESULTS).

        Returns:
            List of dicts with keys: title, link, snippet.
            Empty list if all keys are exhausted or request fails.
        """
        num = num or config.SERPER_RESULTS
        attempts = len(self._keys)

        for _ in range(attempts):
            key = self._current_key()
            payload: Dict = {"q": query, "num": num}
            if country_code:
                payload["gl"] = country_code.lower()

            try:
                resp = requests.post(
                    config.SERPER_BASE_URL,
                    json=payload,
                    headers={
                        "X-API-KEY": key,
                        "Content-Type": "application/json",
                    },
                    timeout=15,
                )
            except requests.RequestException as e:
                logger.warning("SerperSearch request error: %s", e)
                return []

            if resp.status_code == 200:
                data = resp.json()
                organic = data.get("organic", [])
                logger.info(
                    "SerperSearch q=%r gl=%r → %d results (key #%d)",
                    query, country_code, len(organic), self._idx + 1,
                )
                return [
                    {
                        "title":   r.get("title", ""),
                        "link":    r.get("link", ""),
                        "snippet": r.get("snippet", ""),
                    }
                    for r in organic
                    if r.get("link")
                ]

            if _looks_exhausted(resp):
                logger.warning(
                    "SerperSearch key #%d exhausted (status=%d) — rotating",
                    self._idx + 1, resp.status_code,
                )
                self._rotate()
                time.sleep(0.5)
                continue

            # Other error — log and return empty
            logger.error(
                "SerperSearch unexpected status %d: %s",
                resp.status_code, resp.text[:200],
            )
            return []

        logger.error("SerperSearch: all %d key(s) exhausted", len(self._keys))
        return []

    def active_key_index(self) -> int:
        """Return the 1-based index of the currently active key (for logging)."""
        with self._lock:
            return self._idx + 1

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _current_key(self) -> str:
        with self._lock:
            return self._keys[self._idx]

    def _rotate(self) -> None:
        with self._lock:
            next_idx = (self._idx + 1) % len(self._keys)
            if next_idx == self._idx:
                logger.error("SerperSearch: only one key available, cannot rotate")
                return
            logger.info(
                "SerperSearch: rotating key %d → %d",
                self._idx + 1, next_idx + 1,
            )
            self._idx = next_idx
