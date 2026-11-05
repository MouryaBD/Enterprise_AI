"""
Deterministic test fixtures for the enrichment pipeline.
No randomness. Same input always returns same output.
"""

MOCK_ACCOUNTS = {
    "ATH-001": {"business_name": "Athens Plumbing Co", "address": "123 Main St Athens GA"},
    "ATH-002": {"business_name": "Classic City Bakery", "address": "456 Broad St Athens GA"},
    "ATH-003": {"business_name": "UGA Print Shop", "address": "789 College Ave Athens GA"},
    "ATH-004": {"business_name": "Athens Auto Repair", "address": "321 Oak St Athens GA"},
}

_SOS_DATA = {
    "Athens Plumbing Co":   {"entity_type": "LLC",             "date_established": "2005-03-15"},
    "Classic City Bakery":  {"entity_type": "Sole Proprietor", "date_established": "2010-07-22"},
    "UGA Print Shop":       {"entity_type": "Corporation",     "date_established": "1998-01-10"},
    "Athens Auto Repair":   {"entity_type": "LLC",             "date_established": "2015-06-01"},
}

_PROFILE_DATA = {
    "ATH-001": {"business_name": "Athens Plumbing Co",  "address": "123 Main St",    "city": "Athens", "state": "GA", "zip": "30601"},
    "ATH-002": {"business_name": "Classic City Bakery", "address": "456 Broad St",   "city": "Athens", "state": "GA", "zip": "30601"},
    "ATH-003": {"business_name": "UGA Print Shop",      "address": "789 College Ave","city": "Athens", "state": "GA", "zip": "30602"},
    "ATH-004": {"business_name": "Athens Auto Repair",  "address": "321 Oak St",     "city": "Athens", "state": "GA", "zip": "30601"},
}

_WEBSITE_CANDIDATES = {
    "ATH-001": ["https://athensplumbing.com", "https://athensplumbing.net", "https://yelp.com/biz/athens-plumbing"],
    "ATH-002": ["https://classiccitybakery.com", "https://classiccitybakery.net", "https://facebook.com/classiccitybakery"],
    "ATH-003": ["https://ugaprintshop.com", "https://ugaprint.com", "https://facebook.com/ugaprintshop"],
    "ATH-004": ["https://athensautorepair.com", "https://athensauto.net", "https://yelp.com/biz/athens-auto"],
}

MOCK_EXTRACTION_RESULTS = {
    "ATH-001": {
        "phone":               {"value": "(706) 555-0192",                         "confidence": 0.65, "evidence_quote": "Call us at (706) 555-0192"},
        "email":               {"value": "info@athensplumbing.com",                "confidence": 0.91, "evidence_quote": "Email: info@athensplumbing.com"},
        "principal_name":      {"value": "Robert Chen",                            "confidence": 0.88, "evidence_quote": "Owner: Robert Chen"},
        "business_description":{"value": "Residential and commercial plumbing services.", "confidence": 0.93, "evidence_quote": "We provide residential and commercial plumbing services."},
    },
    "ATH-002": {
        "email": {"value": "hello@classiccitybakery.com", "confidence": 0.60, "evidence_quote": "Contact: hello@classiccitybakery.com"},
        "phone": {"value": "(706) 555-0847",              "confidence": 0.84, "evidence_quote": "Call (706) 555-0847"},
    },
}

# ATH-003: all fields above threshold (approved run with no review)
_ATH_003_HIGH_CONFIDENCE = {
    "phone":               {"value": "(706) 555-0300", "confidence": 0.90, "evidence_quote": "Phone: (706) 555-0300"},
    "email":               {"value": "info@ugaprintshop.com", "confidence": 0.90, "evidence_quote": "Email: info@ugaprintshop.com"},
    "principal_name":      {"value": "Jane Smith", "confidence": 0.90, "evidence_quote": "Owner: Jane Smith"},
    "business_description":{"value": "Full service print shop.", "confidence": 0.90, "evidence_quote": "Full service print shop."},
}

# ATH-004: used for interruption test — high confidence
_ATH_004_HIGH_CONFIDENCE = {
    "phone":               {"value": "(706) 555-0400", "confidence": 0.90, "evidence_quote": "Phone: (706) 555-0400"},
    "email":               {"value": "info@athensautorepair.com", "confidence": 0.90, "evidence_quote": "Email: info@athensautorepair.com"},
    "principal_name":      {"value": "Mike Johnson", "confidence": 0.90, "evidence_quote": "Owner: Mike Johnson"},
    "business_description":{"value": "Auto repair and maintenance.", "confidence": 0.90, "evidence_quote": "Auto repair and maintenance."},
}

_NAME_TO_ID = {v["business_name"]: k for k, v in MOCK_ACCOUNTS.items()}


def mock_sos_lookup(business_name: str) -> dict:
    result = _SOS_DATA.get(business_name)
    if result:
        return result
    # Fallback for accounts not in test fixtures
    return {"entity_type": "LLC", "date_established": "2015-01-01"}


def mock_profile_lookup(account_id: str) -> dict:
    result = _PROFILE_DATA.get(account_id)
    if result:
        return result
    # Fallback for accounts not in test fixtures
    return {"city": "Athens", "state": "GA", "zip": "30601"}


def mock_website_candidates(account_id: str) -> list:
    return _WEBSITE_CANDIDATES.get(account_id, ["https://example.com", "https://example.net", "https://example.org"])


def mock_score_websites(candidates: list, name: str, address: str) -> list:
    """First candidate gets 0.85, rest get 0.4."""
    results = []
    for i, url in enumerate(candidates):
        score = 0.85 if i == 0 else 0.4
        results.append({"url": url, "score": score, "reason": "mock scoring"})
    return results


# Alias used by pipeline.py via extractor.score_websites
score_websites = mock_score_websites


def mock_web_search(field_name: str, query: str) -> list:
    """Returns 3 plausible text snippets containing a value for field_name."""
    snippets_map = {
        "phone": [
            "Contact us at (706) 555-9999 for more information.",
            "Call our office: (706) 555-9999 Monday through Friday.",
            "Reach us by phone at (706) 555-9999.",
        ],
        "email": [
            "Send inquiries to contact@business.com",
            "Email us at contact@business.com for quotes.",
            "Our email address is contact@business.com.",
        ],
        "address": [
            "We are located at 100 Commerce St, Athens, GA 30601.",
            "Visit our store at 100 Commerce St in Athens.",
            "Our address: 100 Commerce St, Athens, Georgia.",
        ],
    }
    return snippets_map.get(field_name, [
        f"Information about {field_name} for {query}.",
        f"Details regarding {field_name}.",
        f"More {field_name} info here.",
    ])


def mock_extract_pass_a(evidence_text: str, existing_fields: dict) -> dict:
    """
    Infers account from existing_fields context (business_name).
    Never returns entity_type or date_established.
    """
    business_name = existing_fields.get("business_name")
    account_id = _NAME_TO_ID.get(business_name)

    if account_id == "ATH-001":
        base = dict(MOCK_EXTRACTION_RESULTS["ATH-001"])
    elif account_id == "ATH-002":
        base = dict(MOCK_EXTRACTION_RESULTS["ATH-002"])
    elif account_id == "ATH-003":
        base = dict(_ATH_003_HIGH_CONFIDENCE)
    elif account_id == "ATH-004":
        base = dict(_ATH_004_HIGH_CONFIDENCE)
    else:
        # Generic high-confidence result
        base = {
            "phone":               {"value": "(706) 555-0000", "confidence": 0.85, "evidence_quote": "Generic phone"},
            "email":               {"value": "info@example.com", "confidence": 0.85, "evidence_quote": "Generic email"},
            "business_description":{"value": "A local business.", "confidence": 0.85, "evidence_quote": "Generic description"},
        }

    # Strip legal fields — pass A never returns them
    return {k: v for k, v in base.items() if k not in ("entity_type", "date_established")}


# Alias used by pipeline.py via extractor.extract_pass_a
extract_pass_a = mock_extract_pass_a


_PASS_B_DEFAULTS = {
    "address":              "100 Commerce St",
    "city":                 "Athens",
    "state":                "GA",
    "zip":                  "30601",
    "phone":                "(706) 555-0000",
    "email":                "info@business.com",
    "website":              "N/A",
    "principal_name":       "Owner on File",
    "principal_title":      "Owner",
    "num_employees":        "1-10",
    "business_description": "Local business serving the Athens, GA area.",
}


def mock_extract_pass_b(sos_data: dict, profile_data: dict, still_needed: list) -> dict:
    """Returns remaining fields above threshold for fields in still_needed."""
    result = {}
    for field in still_needed:
        if field in ("entity_type", "date_established"):
            continue
        value = _PASS_B_DEFAULTS.get(field, "N/A")
        result[field] = {
            "value": value,
            "confidence": 0.85,
            "evidence_quote": f"Extracted from available sources for {field}",
        }
    return result


# Alias used by pipeline.py via extractor.extract_pass_b
extract_pass_b = mock_extract_pass_b


def mock_extract_web_consensus(field_name: str, snippets: list) -> dict:
    """Returns {field_name: {value, confidence, evidence_quote}}."""
    value_map = {
        "phone":   "(706) 555-9999",
        "email":   "contact@business.com",
        "address": "100 Commerce St, Athens, GA 30601",
    }
    value = value_map.get(field_name, f"mock_{field_name}")
    return {
        field_name: {
            "value": value,
            "confidence": 0.85,
            "evidence_quote": f"Consensus evidence for {field_name}",
        }
    }


# Alias used by pipeline.py via extractor.extract_web_consensus
extract_web_consensus = mock_extract_web_consensus
