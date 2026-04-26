"""Async email scraper — crawls a store website and extracts email addresses."""
import asyncio
import html
import json
import logging
import re
import time
from typing import List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, urlunparse

import aiohttp
from bs4 import BeautifulSoup

import config
from utils.email_utils import is_valid_email

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}
_EMAIL_RE = re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}(?:\.[a-zA-Z]{2,})*')

_HIGH_VALUE_KEYWORDS = [
    "contact", "about", "support", "help", "team", "email",
    "faq", "policy", "privacy", "legal", "careers",
]
_SKIP_PATHS = ["/cart", "/checkout", "/account", "/search", "/products/", "/collections/"]


def _norm_url(url: str) -> str:
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path.rstrip("/") or "/", "", "", ""))


def _decode_cfemail(cf: str) -> Optional[str]:
    try:
        r = int(cf[:2], 16)
        return "".join(chr(int(cf[i:i+2], 16) ^ r) for i in range(2, len(cf), 2))
    except Exception:
        return None


def _decode_entities(text: str) -> str:
    return (
        text.replace("&#64;", "@").replace("&#064;", "@")
            .replace("&#46;", ".").replace("&#046;", ".")
            .replace("&amp;", "&")
    )


def _obfuscation_variants(text: str) -> List[str]:
    alt = (
        text.replace("(at)", "@").replace("[at]", "@").replace(" at ", "@")
            .replace("(dot)", ".").replace("[dot]", ".").replace(" dot ", ".")
            .replace(" AT ", "@").replace(" DOT ", ".")
    )
    return [text, html.unescape(text), _decode_entities(text), alt]


class EmailScraper:
    def __init__(self):
        self._delay = config.EMAIL_DELAY
        self._timeout = config.EMAIL_TIMEOUT
        self._max_pages = config.EMAIL_MAX_PAGES
        self._sitemap_limit = config.EMAIL_SITEMAP_LIMIT
        self._max_retries = 3

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    async def _get(
        self, session: aiohttp.ClientSession, url: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """Return (html_text, error_type). error_type: None | 'dns' | 'timeout' | 'connection'."""
        for attempt in range(self._max_retries):
            try:
                async with session.get(url, headers=_HEADERS, allow_redirects=True) as resp:
                    if resp.status == 429:
                        wait = int(resp.headers.get("Retry-After", 10))
                        await asyncio.sleep(min(wait, 60))
                        continue
                    if resp.status == 404:
                        return None, "http_error"
                    if resp.status >= 400:
                        if attempt < self._max_retries - 1:
                            await asyncio.sleep(2 ** attempt)
                            continue
                        return None, "http_error"
                    text = await resp.text(errors="replace")
                    return text, None
            except (asyncio.TimeoutError, aiohttp.ServerTimeoutError):
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    return None, "timeout"
            except aiohttp.ClientConnectorError as e:
                err = "dns" if "name resolution" in str(e).lower() else "connection"
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    return None, err
            except aiohttp.ClientError:
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    return None, "connection"
        return None, "connection"

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def _extract_from_html(self, html_text: str, base_url: str) -> Set[str]:
        is_xml = html_text.lstrip().startswith(("<?xml", "<urlset"))
        soup = BeautifulSoup(html_text, "xml" if is_xml else "html.parser")
        emails: Set[str] = set()

        # mailto links
        for a in soup.select("a[href^=mailto]"):
            addr = a["href"].split(":", 1)[1].split("?", 1)[0].strip()
            if is_valid_email(addr):
                emails.add(addr.lower())

        # data-email / data-contact attributes
        for el in soup.select("[data-email],[data-contact]"):
            addr = el.get("data-email") or el.get("data-contact", "")
            if is_valid_email(addr):
                emails.add(addr.lower())

        # Cloudflare obfuscation
        for el in soup.select("[data-cfemail]"):
            dec = _decode_cfemail(el["data-cfemail"])
            if dec and is_valid_email(dec):
                emails.add(dec.lower())

        # JSON-LD structured data
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                self._walk_json(json.loads(script.string or "{}"), emails)
            except Exception:
                pass

        # Plain text (with obfuscation variants)
        for variant in _obfuscation_variants(html_text):
            for m in _EMAIL_RE.finditer(variant):
                addr = m.group(0).lower()
                if is_valid_email(addr):
                    emails.add(addr)

        return emails

    def _walk_json(self, obj, emails: Set[str]) -> None:
        if isinstance(obj, dict):
            for v in obj.values():
                if isinstance(v, str) and is_valid_email(v):
                    emails.add(v.lower())
                else:
                    self._walk_json(v, emails)
        elif isinstance(obj, list):
            for item in obj:
                self._walk_json(item, emails)

    # ------------------------------------------------------------------
    # Page discovery
    # ------------------------------------------------------------------

    def _is_high_value(self, url: str) -> bool:
        return any(k in url.lower() for k in _HIGH_VALUE_KEYWORDS)

    def _base_pages(self, store_url: str) -> List[str]:
        paths = [
            "/", "/contact", "/pages/contact", "/pages/contact-us",
            "/about", "/about-us", "/pages/about", "/pages/about-us",
            "/help", "/support", "/faq", "/pages/help", "/pages/support",
            "/team", "/pages/team",
            "/policies/privacy-policy", "/policies/contact-information",
        ]
        return [urljoin(store_url, p) for p in paths]

    async def _sitemap_urls(self, session: aiohttp.ClientSession, store_url: str) -> List[str]:
        text, _ = await self._get(session, urljoin(store_url, "/sitemap.xml"))
        if not text:
            return []
        soup = BeautifulSoup(text, "xml")
        all_locs = [loc.get_text() for loc in soup.find_all("loc")]
        host = urlparse(store_url).netloc
        # Prioritise high-value pages
        prio = [u for u in all_locs if self._is_high_value(u) and urlparse(u).netloc == host]
        rest = [u for u in all_locs if u not in prio and urlparse(u).netloc == host]
        return (prio + rest)[: self._sitemap_limit]

    def _footer_links(self, soup: BeautifulSoup, store_url: str) -> List[str]:
        host = urlparse(store_url).netloc
        links = []
        for footer in soup.select("footer, [role=contentinfo], .footer, #footer"):
            for a in footer.select("a[href]"):
                full = urljoin(store_url, a["href"])
                p = urlparse(full)
                if p.netloc == host and self._is_high_value(full):
                    links.append(full)
        return links

    async def _discover_pages(self, session: aiohttp.ClientSession, store_url: str) -> List[str]:
        seen: Set[str] = set()
        queue: List[Tuple[str, int]] = []  # (url, priority)

        def add(url: str, priority: int) -> None:
            key = _norm_url(url)
            if key not in seen:
                seen.add(key)
                queue.append((url, priority))

        for url in self._base_pages(store_url):
            add(url, 1 if self._is_high_value(url) else 2)

        for url in await self._sitemap_urls(session, store_url):
            add(url, 1 if self._is_high_value(url) else 2)

        # Footer links from homepage
        home_text, _ = await self._get(session, store_url)
        if home_text:
            soup = BeautifulSoup(home_text, "html.parser")
            for url in self._footer_links(soup, store_url):
                add(url, 2)

        queue.sort(key=lambda x: x[1])
        return [url for url, _ in queue[: self._max_pages]]

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def _run(self, store_url: str) -> List[str]:
        timeout = aiohttp.ClientTimeout(total=self._timeout, connect=10)
        connector = aiohttp.TCPConnector(limit=10, ssl=False, enable_cleanup_closed=True)

        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            pages = await self._discover_pages(session, store_url)
            all_emails: Set[str] = set()
            visited: Set[str] = set()

            for i, url in enumerate(pages):
                key = _norm_url(url)
                if key in visited:
                    continue
                visited.add(key)

                text, err = await self._get(session, url)
                if text:
                    found = self._extract_from_html(text, url)
                    all_emails.update(found)
                    if found:
                        logger.debug("Page %s: %d emails", url, len(found))

                if i < len(pages) - 1:
                    await asyncio.sleep(self._delay)

        # Final filter — remove obvious false positives not caught by regex
        return sorted(
            e for e in all_emails
            if not any(s in e for s in (".png@", ".jpg@", ".css@", ".js@"))
        )

    def scrape(self, store_url: str) -> List[str]:
        """Synchronous wrapper around the async crawler."""
        if not store_url.startswith(("http://", "https://")):
            store_url = "https://" + store_url
        try:
            return asyncio.run(self._run(store_url))
        except Exception as e:
            logger.error("EmailScraper failed for %s: %s", store_url, e)
            return []
