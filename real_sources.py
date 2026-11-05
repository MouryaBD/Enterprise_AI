"""
Real data source implementations for live enrichment.
Used by pipeline.py when ANTHROPIC_API_KEY is set (instead of mock_data).
Tests always use mock_data — this module is never imported in test mode.
"""
import re
import time
import requests
from bs4 import BeautifulSoup

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# ── Entity type patterns parseable directly from business name ────────────────
_ENTITY_PATTERNS = [
    (r'\bL\.?L\.?C\.?\b',          'LLC'),
    (r'\bIncorporated\b',           'Corporation'),
    (r'\bInc\.?\b',                 'Corporation'),
    (r'\bCorporation\b',            'Corporation'),
    (r'\bCorp\.?\b',                'Corporation'),
    (r'\bP\.?C\.?\b',               'Professional Corporation'),
    (r'\bP\.?A\.?\b',               'Professional Association'),
    (r'\bL\.?L\.?P\.?\b',           'Limited Liability Partnership'),
    (r'\bL\.?P\.?\b',               'Limited Partnership'),
    (r'\bN\.?V\.?\b',               'Corporation'),
    (r'\bSole Proprietor\b',        'Sole Proprietor'),
]


# ── Stage 1: GA SOS lookup ────────────────────────────────────────────────────

def sos_lookup(business_name: str) -> dict:
    """
    Attempt to get entity_type and date_established from GA SOS eCorp search.
    entity_type is reliably parsed from the business name itself.
    date_established comes from GA SOS if the scrape succeeds.
    """
    result = {}

    # 1. Parse entity_type directly from name (highly reliable)
    for pattern, etype in _ENTITY_PATTERNS:
        if re.search(pattern, business_name, re.IGNORECASE):
            result['entity_type'] = etype
            break

    # 2. Try GA SOS for date_established
    try:
        session = requests.Session()
        resp = session.get(
            "https://ecorp.sos.ga.gov/BusinessSearch",
            headers=_HEADERS, timeout=10
        )
        if resp.ok:
            soup = BeautifulSoup(resp.text, 'html.parser')
            # Grab any hidden form tokens
            hidden = {
                i.get('name'): i.get('value', '')
                for i in soup.find_all('input', {'type': 'hidden'})
                if i.get('name')
            }
            form = soup.find('form')
            action = 'https://ecorp.sos.ga.gov/BusinessSearch'
            if form and form.get('action'):
                a = form['action']
                action = a if a.startswith('http') else 'https://ecorp.sos.ga.gov' + a

            payload = {**hidden, 'BusinessName': business_name, 'BusinessType': 'All'}
            resp2 = session.post(action, data=payload, headers=_HEADERS, timeout=10)

            if resp2.ok:
                soup2 = BeautifulSoup(resp2.text, 'html.parser')
                text = soup2.get_text(' ', strip=True)
                # Look for a date near "Formation" or "Registration" labels
                date_match = re.search(
                    r'(?:Formation|Registration|Organized|Date)[^0-9]*'
                    r'(\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})',
                    text, re.IGNORECASE
                )
                if date_match:
                    raw = date_match.group(1)
                    # Normalise to YYYY-MM-DD
                    if '/' in raw:
                        m, d, y = raw.split('/')
                        result['date_established'] = f"{y}-{int(m):02d}-{int(d):02d}"
                    else:
                        result['date_established'] = raw
    except Exception:
        pass  # SOS unavailable — entity_type still set from name parse

    return result


# ── Stage 2: Profile lookup ───────────────────────────────────────────────────

def profile_lookup(account: dict) -> dict:
    """
    Return the account's own existing DB data as its profile.
    Avoids replacing real imported values with mock placeholders.
    """
    profile_fields = [
        'business_name', 'address', 'city', 'state', 'zip',
        'phone', 'email', 'website', 'principal_name', 'principal_title',
        'num_employees', 'business_description',
    ]
    return {f: account[f] for f in profile_fields if account.get(f) is not None}


# ── Stage 3: Website discovery ────────────────────────────────────────────────

def _clean_name_for_domain(business_name: str) -> list[str]:
    """Return a list of domain-friendly slug variations from a business name."""
    name = business_name.lower()
    # Strip legal suffixes
    for suf in [', llc', ' llc', ', inc', ' inc', ', corp', ' corp',
                ' ltd', ' co', ' p.c.', ' p.a.', ', p.c.', ', p.a.',
                ' n.v.', ' l.p.', ' l.l.p.', ' l.l.c.']:
        name = name.replace(suf, '')
    name = re.sub(r'[^a-z0-9\s]', '', name).strip()
    words = name.split()
    if not words:
        return []

    slugs = []
    slugs.append(''.join(words))                       # all words joined
    slugs.append('-'.join(words))                       # hyphenated
    if len(words) >= 2:
        slugs.append(''.join(words[:2]))                # first 2 words
        slugs.append('-'.join(words[:2]))
    if len(words) >= 3:
        slugs.append(''.join(words[:3]))
        slugs.append('-'.join(words[:3]))
    return list(dict.fromkeys(slugs))                   # deduplicate, preserve order


def _ddg_search_urls(query: str, limit: int = 5) -> list[str]:
    """Search DuckDuckGo HTML and return result URLs."""
    try:
        encoded = requests.utils.quote(query)
        resp = requests.get(
            f"https://html.duckduckgo.com/html/?q={encoded}",
            headers=_HEADERS, timeout=10
        )
        if not resp.ok:
            return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        urls = []
        # DDG result URLs are in <a class="result__url"> or href of result__a
        for a in soup.select('a.result__a')[:limit * 2]:
            href = a.get('href', '')
            # DDG wraps redirects — extract uddg= param or use href directly
            if 'uddg=' in href:
                from urllib.parse import urlparse, parse_qs, unquote
                qs = parse_qs(urlparse(href).query)
                raw = qs.get('uddg', [''])[0]
                href = unquote(raw)
            if href.startswith('http') and not any(
                skip in href for skip in [
                    'duckduckgo.com', 'google.com', 'bing.com',
                ]
            ):
                urls.append(href.rstrip('/'))
            if len(urls) >= limit:
                break
        return urls
    except Exception:
        return []


def _verify_url(url: str) -> bool:
    """Return True if the URL resolves with a 2xx/3xx status."""
    try:
        resp = requests.head(url, timeout=5, allow_redirects=True, headers=_HEADERS)
        return resp.status_code < 400
    except Exception:
        return False


def website_candidates(business_name: str, city: str = "Athens",
                        state: str = "GA") -> list[str]:
    """
    Return up to 5 candidate URLs for a business.
    Priority: DuckDuckGo search results > heuristic domain generation.
    """
    city = city or "Athens"
    state = state or "GA"
    candidates: list[str] = []

    # 1. DuckDuckGo search
    query = f'"{business_name}" {city} {state} official site'
    ddg = _ddg_search_urls(query, limit=5)
    candidates.extend(ddg)
    time.sleep(0.3)  # polite rate

    # 2. Heuristic URL generation as fallback
    city_slug = re.sub(r'[^a-z]', '', city.lower())
    for slug in _clean_name_for_domain(business_name):
        for tld in ['.com', '.net']:
            candidates.append(f"https://www.{slug}{tld}")
        candidates.append(f"https://www.{slug}{city_slug}.com")

    # 3. Verify & deduplicate
    seen: set[str] = set()
    verified: list[str] = []
    for url in candidates:
        url = url.rstrip('/')
        if url in seen:
            continue
        seen.add(url)
        if _verify_url(url):
            verified.append(url)
        if len(verified) >= 5:
            break
        time.sleep(0.1)

    # Fall back to unverified heuristic candidates if nothing resolved
    if not verified:
        slugs = _clean_name_for_domain(business_name)
        verified = [f"https://www.{s}.com" for s in slugs[:3]]

    return verified


# ── Stage 7: Web consensus search ────────────────────────────────────────────

def web_search(field_name: str, business_name: str,
               city: str = None) -> list[str]:
    """
    Search DuckDuckGo for a specific field and return text snippets.
    """
    city = city or "Athens GA"
    query_map = {
        "phone":   f"{business_name} {city} phone number contact",
        "email":   f"{business_name} {city} email address",
        "address": f"{business_name} {city} address location",
    }
    query = query_map.get(field_name, f"{business_name} {field_name}")

    try:
        encoded = requests.utils.quote(query)
        resp = requests.get(
            f"https://html.duckduckgo.com/html/?q={encoded}",
            headers=_HEADERS, timeout=10
        )
        if not resp.ok:
            return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        snippets = []
        for el in soup.select('a.result__snippet')[:6]:
            text = el.get_text(strip=True)
            if text:
                snippets.append(text)
        time.sleep(0.3)
        return snippets
    except Exception:
        return []
