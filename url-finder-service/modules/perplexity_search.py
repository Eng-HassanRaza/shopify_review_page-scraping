"""Perplexity Chat Completions client for URL finding."""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class PerplexitySearch:
    def __init__(
        self,
        api_key: str,
        model: str = "sonar-pro",
        timeout: int = 20,
        top_n: int = 5,
        cache_ttl_seconds: int = 86400,
    ):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.top_n = max(1, min(int(top_n), 10))
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cache: Dict[str, Dict[str, Any]] = {}

    def _get_cached(self, cache_key: str) -> Optional[Dict[str, Any]]:
        item = self._cache.get(cache_key)
        if not item:
            return None
        if time.time() - item["ts"] > self.cache_ttl_seconds:
            self._cache.pop(cache_key, None)
            return None
        return item["value"]

    def _set_cached(self, cache_key: str, value: Dict[str, Any]) -> None:
        self._cache[cache_key] = {"ts": time.time(), "value": value}

    @staticmethod
    def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
        """
        Perplexity may return JSON wrapped in text. Try:
        - direct json.loads
        - extract first {...} block and parse
        """
        if not text:
            return None
        text = text.strip()
        try:
            return json.loads(text)
        except Exception:
            pass

        # Strip code fences if present
        fenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.MULTILINE).strip()
        if fenced != text:
            try:
                return json.loads(fenced)
            except Exception:
                pass

        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return None
        candidate = m.group(0)
        try:
            return json.loads(candidate)
        except Exception:
            return None

    @staticmethod
    def _normalize_candidates(candidates: Any) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not isinstance(candidates, list):
            return out
        seen = set()
        for c in candidates:
            if not isinstance(c, dict):
                continue
            url = (c.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            out.append(
                {
                    "url": url,
                    "title": (c.get("title") or "").strip(),
                    "snippet": (c.get("snippet") or "").strip(),
                    "confidence": c.get("confidence"),
                    "source": (c.get("source") or "").strip(),
                }
            )
        return out

    def find_store_url(
        self,
        store_name: str,
        country: str = "",
        review_text: str = "",
    ) -> Dict[str, Any]:
        """
        Returns:
        {
          "query": str,
          "selected_url": str|None,
          "confidence": float|None,
          "reasoning": str,
          "results": [ {url,title,snippet,confidence,source}, ... ]
        }
        """
        if not self.api_key:
            raise ValueError("Perplexity API key not configured")

        store_name = (store_name or "").strip()
        country = (country or "").strip()
        review_text = (review_text or "").strip()
        if not store_name:
            raise ValueError("store_name is required")

        query = f"{store_name} {country}".strip()
        normalized_query = " ".join(query.split()).lower()
        cache_key = f"{normalized_query}:{self.model}:{self.top_n}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        system_prompt = (
            "You are a web research assistant. Your job is to find the official Shopify storefront URL for a brand/store.\n"
            "Return ONLY valid JSON (no markdown, no extra text).\n"
            "Prefer the official storefront homepage (not social media, directories, app stores, marketplaces, or review pages).\n"
            "If multiple plausible domains exist, return the best one first with lower confidence.\n"
            "Confidence must be a number between 0 and 1.\n"
        )

        user_prompt = (
            f"Find the official Shopify storefront URL for:\n"
            f"- store_name: {store_name}\n"
            f"- country: {country or 'unknown'}\n"
            f"{('- review_context: ' + review_text[:400]) if review_text else ''}\n\n"
            f"Return JSON with this schema:\n"
            f'{{"selected_url": string|null, "confidence": number, "reasoning": string, '
            f'"candidates": [{{"url": string, "title": string, "snippet": string, "confidence": number}}...]}}\n\n'
            f"Include up to {self.top_n} candidates, sorted by best match first."
        )

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 900,
            "temperature": 0.2,
            "web_search_options": {
                "num_search_results": 10,
                "safe_search": True,
            },
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        start = time.time()
        response = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers=headers,
            json=payload,
            timeout=self.timeout,
        )
        duration_ms = int((time.time() - start) * 1000)

        if response.status_code != 200:
            logger.warning("Perplexity error %s: %s", response.status_code, response.text[:500])
            response.raise_for_status()

        data = response.json()

        content = ""
        try:
            content = data["choices"][0]["message"]["content"]
        except Exception:
            content = ""

        parsed = self._extract_json_object(content) or {}

        candidates = self._normalize_candidates(parsed.get("candidates"))

        # Fallback: if model didn't return candidates, use search_results from API response (if present)
        if not candidates and isinstance(data.get("search_results"), list):
            seen = set()
            for sr in data["search_results"][: self.top_n]:
                if not isinstance(sr, dict):
                    continue
                url = (sr.get("url") or "").strip()
                if not url or url in seen:
                    continue
                seen.add(url)
                candidates.append(
                    {
                        "url": url,
                        "title": (sr.get("title") or "").strip(),
                        "snippet": (sr.get("snippet") or "").strip(),
                        "confidence": None,
                        "source": (sr.get("source") or "").strip(),
                    }
                )

        selected_url = (parsed.get("selected_url") or "").strip() or (candidates[0]["url"] if candidates else None)
        confidence = parsed.get("confidence")
        reasoning = (parsed.get("reasoning") or "").strip()

        result = {
            "query": query,
            "selected_url": selected_url,
            "confidence": confidence,
            "reasoning": reasoning,
            "results": candidates,
            "duration_ms": duration_ms,
        }

        logger.info(
            "Perplexity query='%s' results=%s duration_ms=%s confidence=%s",
            query,
            len(candidates),
            duration_ms,
            confidence,
        )

        self._set_cached(cache_key, result)
        return result

