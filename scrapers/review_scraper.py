"""Scrape Shopify App Store review pages."""
import logging
import random
import re
import time
from typing import Dict, Generator, List, Optional
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def extract_app_name(url: str) -> str:
    try:
        parts = [p for p in urlparse(url).path.split("/") if p]
        if "reviews" in parts:
            idx = parts.index("reviews")
            if idx > 0:
                return parts[idx - 1]
    except Exception:
        pass
    return "unknown_app"


class ReviewScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(_HEADERS)

    def _get(self, url: str, retries: int = 3) -> Optional[requests.Response]:
        for attempt in range(retries):
            try:
                time.sleep(random.uniform(2.0, 4.5))
                r = self.session.get(url, timeout=30)
                r.raise_for_status()
                return r
            except Exception as e:
                logger.warning("Attempt %d failed for %s: %s", attempt + 1, url, e)
                if attempt < retries - 1:
                    time.sleep(random.uniform(5, 10))
        return None

    def _parse_rating(self, section) -> Optional[int]:
        # aria-label: "4 out of 5 stars"
        for el in section.find_all(attrs={"aria-label": True}):
            m = re.search(r"(\d+)\s*(?:out of 5|stars?)", el["aria-label"], re.I)
            if m:
                r = int(m.group(1))
                if 1 <= r <= 5:
                    return r
        # data-rating attribute
        for attr in ("data-rating", "data-star-rating"):
            val = section.get(attr)
            if val:
                try:
                    r = int(val)
                    if 1 <= r <= 5:
                        return r
                except ValueError:
                    pass
        # Unicode stars in text
        text = section.get_text()
        filled = text.count("★")
        if 1 <= filled <= 5:
            return filled
        return None

    def _parse_page(self, soup: BeautifulSoup, url_rating: Optional[int]) -> List[Dict]:
        sections = (
            soup.find_all("div", {"data-merchant-review": True})
            or soup.find_all("div", {"data-review-id": True})
            or soup.find_all(["article", "section"], class_=lambda c: c and "review" in c.lower())
        )

        reviews = []
        for sec in sections:
            try:
                # Store name
                name_el = (
                    sec.find("span", class_=lambda c: c and "tw-overflow-hidden" in c and "tw-text-ellipsis" in c)
                    or sec.find("a", href=lambda h: h and "/stores/" in h)
                )
                if not name_el:
                    continue
                store_name = name_el.get_text(strip=True)
                if not store_name:
                    continue

                # Country
                country = ""
                for div in sec.find_all("div", class_=lambda c: c and "tw-text-body-xs" in c):
                    t = div.get_text(strip=True)
                    if len(t) > 2 and not any(w in t.lower() for w in ("year", "month", "day", "ago", "replied")):
                        country = t
                        break

                # Review text
                review_text = ""
                for sel in [
                    lambda s: s.find("div", {"data-truncate-content-copy": True}),
                    lambda s: s.find("p", class_=lambda c: c and "tw-break-words" in c),
                ]:
                    el = sel(sec)
                    if el:
                        review_text = el.get_text(strip=True)
                        break

                # Date
                date_el = sec.find("time")
                review_date = (date_el.get("datetime") or date_el.get_text(strip=True)) if date_el else ""

                # Usage duration
                usage_duration = ""
                usage_el = sec.find(
                    "div",
                    string=lambda t: t and any(w in t.lower() for w in ("month", "year", "day", "ago")),
                )
                if usage_el:
                    usage_duration = usage_el.get_text(strip=True)

                rating = self._parse_rating(sec) or url_rating

                reviews.append({
                    "store_name": store_name,
                    "country": country,
                    "review_date": review_date,
                    "review_text": review_text,
                    "usage_duration": usage_duration,
                    "rating": rating,
                })
            except Exception as e:
                logger.debug("Error parsing review section: %s", e)

        return reviews

    def scrape(
        self, review_url: str, limit: int = 0, start_page: int = 1
    ) -> Generator[tuple, None, None]:
        """
        Yield (page_number, batch) tuples page-by-page.

        limit  — max NEW stores to insert this run (0 = no limit); caller tracks this.
        start_page — first page to fetch (for cursor-based resumption).
        """
        url_rating = None
        try:
            qs = parse_qs(urlparse(review_url).query)
            if "rating" in qs:
                url_rating = int(qs["rating"][0])
        except Exception:
            pass

        empty_streak = 0
        page = start_page

        while True:
            sep = "&" if "?" in review_url else "?"
            page_url = f"{review_url}{sep}page={page}"

            resp = self._get(page_url)
            if not resp:
                empty_streak += 1
                if empty_streak >= 2:
                    break
                page += 1
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            batch = self._parse_page(soup, url_rating)

            if not batch:
                empty_streak += 1
                if empty_streak >= 2:
                    break
            else:
                empty_streak = 0
                logger.info("Page %d: %d reviews", page, len(batch))
                yield page, batch

            page += 1
