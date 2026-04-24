"""URL normalization helpers."""
import re
from urllib.parse import urlparse, urlunparse, urlencode, parse_qsl


_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "msclkid", "ref", "source", "affiliate",
    "mc_eid", "mc_cid", "_ga", "igshid",
}


def clean_url(url: str) -> str:
    """Strip tracking query params and fragments."""
    if not url:
        return url
    try:
        parsed = urlparse(url)
        clean_qs = [(k, v) for k, v in parse_qsl(parsed.query) if k not in _TRACKING_PARAMS]
        return urlunparse((
            parsed.scheme, parsed.netloc, parsed.path,
            parsed.params, urlencode(clean_qs), ""
        ))
    except Exception:
        return url


def normalize_url(url: str) -> str:
    """Ensure https scheme and strip trailing slash."""
    url = (url or "").strip().strip(").,;\"'`")
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        # bare domain
        if re.match(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$", url, re.I):
            url = f"https://{url}"
        else:
            return url
    try:
        parsed = urlparse(url)
        path = parsed.path.rstrip("/") or "/"
        return urlunparse(("https", parsed.netloc, path, "", "", ""))
    except Exception:
        return url
