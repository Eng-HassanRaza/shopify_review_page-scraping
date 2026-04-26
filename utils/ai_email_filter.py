"""GPT-based email filter — removes irrelevant emails from scraped list."""
import json
import logging
from typing import List, Optional
from urllib.parse import urlparse

from openai import OpenAI

import config
from utils.email_utils import is_valid_email, normalize_emails

logger = logging.getLogger(__name__)


def _root_domain(domain: str) -> str:
    """Return the registrable root (e.g. 'thefashiongiftshop' from 'thefashiongiftshop.co.uk')."""
    parts = domain.lower().lstrip("www.").split(".")
    # Drop common TLD suffixes so .com and .co.uk both collapse to the same root
    tld_suffixes = {"com", "co", "uk", "net", "org", "io", "store", "shop", "au", "ca", "de", "fr"}
    non_tld = [p for p in parts if p not in tld_suffixes]
    return non_tld[0] if non_tld else parts[0]


def _store_root(store_url: str) -> str:
    """Extract the root domain name from a store URL."""
    try:
        netloc = urlparse(
            store_url if store_url.startswith(("http://", "https://")) else f"https://{store_url}"
        ).netloc
        return _root_domain(netloc)
    except Exception:
        return ""


def _is_own_domain(email: str, store_root: str) -> bool:
    """Return True if the email's domain shares the same root as the store."""
    if not store_root:
        return False
    email_domain = email.split("@")[-1].lower()
    return _root_domain(email_domain) == store_root


class AIEmailFilter:
    def __init__(self):
        if not config.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is not set")
        self._client = OpenAI(api_key=config.OPENAI_API_KEY)

    def filter(
        self,
        raw_emails: List[str],
        store_url: str = "",
        store_name: str = "",
    ) -> List[str]:
        """
        Return a subset of raw_emails that are business-relevant.

        Strategy:
        - Always keep emails from the store's own domain (e.g. hello@, sales@, info@) —
          these are all potentially valuable regardless of prefix.
        - Use AI to evaluate emails from unrelated/external domains (scraping noise).
        - Falls back to normalized raw list if AI fails.
        """
        normalized = normalize_emails(raw_emails)
        if not normalized:
            return []

        store_root = _store_root(store_url)

        own_domain: List[str] = []
        external: List[str] = []
        for email in normalized:
            if _is_own_domain(email, store_root):
                own_domain.append(email)
            else:
                external.append(email)

        logger.info(
            "AIEmailFilter: %d normalized → %d own-domain (kept) + %d external (filtering)",
            len(normalized), len(own_domain), len(external),
        )

        # Own-domain emails: keep all of them — hello@, sales@, info@, support@, etc.
        # are all valid contact points for a store.
        kept_external: List[str] = []
        if external:
            kept_external = self._ai_filter_external(external, store_url, store_name, store_root)

        result = normalize_emails(own_domain + kept_external)
        logger.info(
            "AIEmailFilter: final → %d emails (own-domain=%d, external=%d)",
            len(result), len(own_domain), len(kept_external),
        )
        return result

    def _ai_filter_external(
        self,
        emails: List[str],
        store_url: str,
        store_name: str,
        store_root: str,
    ) -> List[str]:
        """Use GPT to decide which cross-domain emails are relevant vs scraping noise."""
        context = (
            f"Store URL: {store_url}\n"
            + (f"Store Name: {store_name}\n" if store_name else "")
            + (f"Store Domain Root: {store_root}\n" if store_root else "")
        )
        email_list = "\n".join(f"- {e}" for e in emails)

        prompt = (
            "These emails were scraped from a store's website but come from EXTERNAL/UNRELATED domains "
            "(not the store's own domain). Decide which ones are legitimately associated with this store "
            "vs random noise picked up during scraping.\n\n"
            f"{context}\n"
            f"External emails to evaluate ({len(emails)} total):\n{email_list}\n\n"
            "Keep: partner business emails, supplier contacts, or emails clearly linked to this store.\n"
            "Discard: random emails from unrelated websites, ad trackers, CDN domains, etc.\n\n"
            "Return ONLY emails from the list above (do not invent any).\n"
            'Return JSON: {"relevant_emails": ["email1@...", ...]}'
        )

        try:
            resp = self._client.chat.completions.create(
                model=config.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "Filter emails from the provided list only. Return valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            data = json.loads(resp.choices[0].message.content or "{}")
            returned = data.get("relevant_emails", [])

            original_set = {e.lower() for e in emails}
            valid = [e.lower() for e in returned if e.lower() in original_set and is_valid_email(e)]
            logger.info("AIEmailFilter external: %d → %d kept by AI", len(emails), len(valid))
            return valid
        except Exception as e:
            logger.warning("AIEmailFilter external filter failed (%s) — discarding external emails", e)
            return []
