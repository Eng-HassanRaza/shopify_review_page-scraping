"""Country name → ISO-2 code, and date-string detection."""
import re

# Month names used to detect date strings
_MONTHS = {
    "january","february","march","april","may","june",
    "july","august","september","october","november","december",
    "jan","feb","mar","apr","jun","jul","aug","sep","oct","nov","dec",
}

# Regex patterns that indicate a date, not a country
_DATE_PATTERNS = [
    re.compile(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b'),          # 15/03/2025
    re.compile(r'\b\d{4}[/-]\d{1,2}[/-]\d{1,2}\b'),             # 2025-03-15
    re.compile(r'\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{1,2},?\s*\d{4}\b', re.I),
    re.compile(r'\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{4}\b', re.I),
]


def is_date_string(text: str) -> bool:
    """Return True if the text looks like a date rather than a country name."""
    if not text:
        return False
    t = text.strip().lower()
    # Contains a 4-digit year
    if re.search(r'\b20\d{2}\b', t):
        return True
    # Starts with or contains a month name
    first_word = t.split()[0] if t.split() else ""
    if first_word in _MONTHS:
        return True
    # Matches any date pattern
    return any(p.search(t) for p in _DATE_PATTERNS)


# Country name → ISO-2 code (covers most Shopify App Store reviewer countries)
_COUNTRY_MAP: dict = {
    # A
    "afghanistan": "af", "albania": "al", "algeria": "dz", "andorra": "ad",
    "angola": "ao", "argentina": "ar", "armenia": "am", "australia": "au",
    "austria": "at", "azerbaijan": "az",
    # B
    "bahrain": "bh", "bangladesh": "bd", "belgium": "be", "bolivia": "bo",
    "bosnia": "ba", "brazil": "br", "brunei": "bn", "bulgaria": "bg",
    # C
    "cambodia": "kh", "cameroon": "cm", "canada": "ca", "chile": "cl",
    "china": "cn", "colombia": "co", "costa rica": "cr", "croatia": "hr",
    "cyprus": "cy", "czech republic": "cz", "czechia": "cz",
    # D
    "denmark": "dk", "dominican republic": "do",
    # E
    "ecuador": "ec", "egypt": "eg", "el salvador": "sv", "estonia": "ee",
    "ethiopia": "et",
    # F
    "finland": "fi", "france": "fr",
    # G
    "georgia": "ge", "germany": "de", "ghana": "gh", "greece": "gr",
    "guatemala": "gt",
    # H
    "honduras": "hn", "hong kong": "hk", "hungary": "hu",
    # I
    "iceland": "is", "india": "in", "indonesia": "id", "iran": "ir",
    "iraq": "iq", "ireland": "ie", "israel": "il", "italy": "it",
    # J
    "jamaica": "jm", "japan": "jp", "jordan": "jo",
    # K
    "kazakhstan": "kz", "kenya": "ke", "kuwait": "kw",
    # L
    "latvia": "lv", "lebanon": "lb", "lithuania": "lt", "luxembourg": "lu",
    # M
    "malaysia": "my", "malta": "mt", "mexico": "mx", "moldova": "md",
    "morocco": "ma",
    # N
    "nepal": "np", "netherlands": "nl", "new zealand": "nz", "nicaragua": "ni",
    "nigeria": "ng", "norway": "no",
    # O
    "oman": "om",
    # P
    "pakistan": "pk", "panama": "pa", "peru": "pe", "philippines": "ph",
    "poland": "pl", "portugal": "pt",
    # Q
    "qatar": "qa",
    # R
    "romania": "ro", "russia": "ru", "russian federation": "ru",
    # S
    "saudi arabia": "sa", "senegal": "sn", "serbia": "rs", "singapore": "sg",
    "slovakia": "sk", "slovenia": "si", "south africa": "za", "south korea": "kr",
    "korea": "kr", "spain": "es", "sri lanka": "lk", "sweden": "se",
    "switzerland": "ch",
    # T
    "taiwan": "tw", "tanzania": "tz", "thailand": "th", "tunisia": "tn",
    "turkey": "tr", "türkiye": "tr",
    # U
    "uganda": "ug", "ukraine": "ua", "united arab emirates": "ae", "uae": "ae",
    "united kingdom": "gb", "uk": "gb", "england": "gb", "scotland": "gb",
    "wales": "gb", "great britain": "gb",
    "united states": "us", "usa": "us", "u.s.": "us", "u.s.a.": "us",
    "uruguay": "uy", "uzbekistan": "uz",
    # V
    "venezuela": "ve", "vietnam": "vn",
    # Z
    "zambia": "zm", "zimbabwe": "zw",
}


def country_to_iso_code(country: str) -> str:
    """
    Convert a country name to an ISO-2 code (lowercase).
    Returns "" if the name is unrecognised or looks like a date.
    """
    if not country:
        return ""
    if is_date_string(country):
        return ""
    key = country.strip().lower()
    # Direct lookup
    if key in _COUNTRY_MAP:
        return _COUNTRY_MAP[key]
    # Partial match — handle "United States of America" etc.
    for name, code in _COUNTRY_MAP.items():
        if name in key or key in name:
            return code
    return ""
