"""
All Anthropic API calls for the enrichment pipeline.
Never called in tests (mocks used instead).
"""
import json
import os
import time
from typing import Optional

import config


def _get_client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is not set. Export your API key before running live enrichment."
        )
    import anthropic
    return anthropic.Anthropic(api_key=api_key)


def _call_with_retry(fn, *args, **kwargs):
    """Call fn with exponential backoff on API errors, up to MAX_RETRIES."""
    last_exc = None
    for attempt in range(config.MAX_RETRIES):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_exc = e
            wait = 2 ** attempt
            time.sleep(wait)
    raise last_exc


def score_websites(candidates: list, business_name: str, address: str) -> list:
    """
    Score each URL for relevance to the business.
    Returns list of {url, score, reason} sorted descending by score.
    """
    client = _get_client()

    url_list = "\n".join(f"- {url}" for url in candidates)
    prompt = (
        f"Score each URL 0.0-1.0 on: name match, address signals, source trust, "
        f"domain hygiene. Return JSON: [{{\"url\": ..., \"score\": ..., \"reason\": ...}}] "
        f"sorted descending. Only score URLs I provide. Do not invent any.\n\n"
        f"Business name: {business_name}\n"
        f"Address: {address}\n\n"
        f"URLs to score:\n{url_list}"
    )

    def _call():
        response = client.messages.create(
            model=config.MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text.strip()
        # Extract JSON array from response
        start = text.find("[")
        end = text.rfind("]") + 1
        return json.loads(text[start:end])

    return _call_with_retry(_call)


def extract_pass_a(evidence_text: str, existing_fields: dict) -> dict:
    """
    Extract fields from website evidence text.
    Targets: phone, email, principal_name, principal_title,
             business_description, num_employees
    Never targets entity_type or date_established.
    Returns {field: {value, confidence, evidence_quote}} — null value = not found.
    """
    client = _get_client()

    target_fields = ["phone", "email", "principal_name", "principal_title",
                     "business_description", "num_employees"]

    system = (
        "Extract only from the evidence provided. Return null for any field "
        "not explicitly in the evidence. Do not infer, guess, or combine partial signals."
    )

    existing_summary = json.dumps(existing_fields, indent=2) if existing_fields else "{}"
    prompt = (
        f"Extract the following fields from the evidence below.\n"
        f"Fields to extract: {', '.join(target_fields)}\n\n"
        f"Already known fields (do not re-extract):\n{existing_summary}\n\n"
        f"Evidence:\n{evidence_text}\n\n"
        f"Return JSON object with field names as keys. For each found field: "
        f"{{\"value\": ..., \"confidence\": 0.0-1.0, \"evidence_quote\": \"...\"}}\n"
        f"For fields not found: use null.\n"
        f"Example: {{\"phone\": {{\"value\": \"555-1234\", \"confidence\": 0.9, "
        f"\"evidence_quote\": \"Call 555-1234\"}}, \"email\": null}}"
    )

    def _call():
        response = client.messages.create(
            model=config.MODEL,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text.strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        return json.loads(text[start:end])

    return _call_with_retry(_call)


def extract_pass_b(sos_data: dict, profile_data: dict, still_needed: list) -> dict:
    """
    Extract remaining fields from SOS and profile data.
    Skips legal fields.
    Returns {field: {value, confidence, evidence_quote}} — null value = not found.
    """
    client = _get_client()

    # Filter out legal fields
    legal = set(config.LEGAL_FIELDS)
    fields_to_extract = [f for f in still_needed if f not in legal]
    if not fields_to_extract:
        return {}

    system = (
        "Extract only from the evidence provided. Return null for any field "
        "not explicitly in the evidence. Do not infer, guess, or combine partial signals."
    )

    prompt = (
        f"Extract the following fields from the SOS and profile data below.\n"
        f"Fields to extract: {', '.join(fields_to_extract)}\n\n"
        f"SOS Data:\n{json.dumps(sos_data, indent=2)}\n\n"
        f"Profile Data:\n{json.dumps(profile_data, indent=2)}\n\n"
        f"Return JSON object with field names as keys. For each found field: "
        f"{{\"value\": ..., \"confidence\": 0.0-1.0, \"evidence_quote\": \"...\"}}\n"
        f"For fields not found: use null."
    )

    def _call():
        response = client.messages.create(
            model=config.MODEL,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text.strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        return json.loads(text[start:end])

    return _call_with_retry(_call)


def extract_web_consensus(field_name: str, snippets: list) -> dict:
    """
    Extract field value by consensus from multiple web snippets.
    If 2+ snippets agree on the same value, boost confidence +0.1.
    Returns {field_name: {value, confidence, evidence_quote}} or {field_name: null}.
    """
    client = _get_client()

    snippets_text = "\n\n".join(f"Snippet {i+1}: {s}" for i, s in enumerate(snippets))
    prompt = (
        f"Extract the value for field '{field_name}' from the snippets below.\n"
        f"If 2 or more snippets agree on the same value, boost confidence by +0.1.\n\n"
        f"Snippets:\n{snippets_text}\n\n"
        f"Return JSON: {{\"{field_name}\": {{\"value\": ..., \"confidence\": 0.0-1.0, "
        f"\"evidence_quote\": \"...\"}}}} or {{\"{field_name}\": null}} if not found."
    )

    def _call():
        response = client.messages.create(
            model=config.MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text.strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        return json.loads(text[start:end])

    return _call_with_retry(_call)
