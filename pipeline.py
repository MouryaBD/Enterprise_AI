"""
Enrichment pipeline. Runs stages 1-7 and applies confidence gating.
"""
import os

import config
import db
import mock_data


def run_pipeline(account_id: str, run_id: str, start_stage: int = 1):
    """
    Run enrichment stages for an account.
    start_stage allows resumption from a specific stage.
    """
    use_mock = os.getenv("ANTHROPIC_API_KEY") is None
    if use_mock:
        extractor = mock_data
    else:
        import claude_client
        extractor = claude_client

    # Set the active run id so write_field can log events
    db._active_run_id = run_id

    try:
        field_results = {}   # {field_name: {value, confidence, evidence_quote, source}}
        legal_data = {}
        profile_data = {}
        accepted_url = None

        account = db.get_account(account_id)

        # ------------------------------------------------------------------ #
        # STAGE 1 — GA SOS legal resolution
        # ------------------------------------------------------------------ #
        if start_stage <= 1:
            sos_result = mock_data.mock_sos_lookup(account["business_name"])
            if sos_result:
                legal_data = sos_result
                db.set_legal_locked(run_id)
            else:
                db.log_event(run_id, "stage_skipped", {"stage": 1, "reason": "sos_unavailable"})

            db.update_stage(run_id, 1)
            db.log_event(run_id, "stage_complete", {"stage": 1})

        # ------------------------------------------------------------------ #
        # STAGE 2 — Profile lookup
        # ------------------------------------------------------------------ #
        if start_stage <= 2:
            profile_data = mock_data.mock_profile_lookup(account_id)
            for field, value in profile_data.items():
                if value is not None and field in config.ALL_FIELDS:
                    field_results[field] = {
                        "value": value,
                        "confidence": 1.0,
                        "evidence_quote": "profile lookup",
                        "source": "profile",
                    }

            db.update_stage(run_id, 2)
            db.log_event(run_id, "stage_complete", {"stage": 2})

        # ------------------------------------------------------------------ #
        # STAGE 3 — Website discovery
        # ------------------------------------------------------------------ #
        if start_stage <= 3:
            name = account.get("business_name", "")
            address = account.get("address", "")
            candidates = mock_data.mock_website_candidates(account_id)
            scores = extractor.score_websites(candidates, name, address)

            best = None
            for s in scores:
                if s.get("score", 0) >= 0.70:
                    if best is None or s["score"] > best["score"]:
                        best = s

            if best is None:
                db.log_event(run_id, "stage_skipped", {"stage": 3, "reason": "no_site_accepted"})
                accepted_url = None
            else:
                accepted_url = best["url"]
                if "website" not in field_results:
                    field_results["website"] = {
                        "value": accepted_url,
                        "confidence": 1.0,
                        "evidence_quote": "discovered",
                        "source": "website_discovery",
                    }

            db.update_stage(run_id, 3)
            db.log_event(run_id, "stage_complete", {"stage": 3})

        # ------------------------------------------------------------------ #
        # STAGE 4 — Page fetch
        # ------------------------------------------------------------------ #
        if start_stage <= 4:
            if not accepted_url:
                db.log_event(run_id, "stage_skipped", {"stage": 4, "reason": "no_accepted_url"})
                evidence_text = ""
            else:
                import requests
                from bs4 import BeautifulSoup

                pages = ["/", "/about", "/contact", "/team"]
                evidence_parts = []
                for path in pages:
                    url = accepted_url.rstrip("/") + path
                    try:
                        resp = requests.get(url, timeout=config.CALL_TIMEOUT)
                        soup = BeautifulSoup(resp.text, "html.parser")
                        text = soup.get_text(separator=" ", strip=True)
                        # Truncate to ~4000 tokens (approx 4 chars per token)
                        text = text[:16000]
                        evidence_parts.append(text)
                    except Exception as e:
                        db.log_event(run_id, "stage_skipped",
                                     {"stage": 4, "reason": f"fetch_error: {url}", "error": str(e)})

                evidence_text = "\n\n".join(evidence_parts)
                # Total cap
                evidence_text = evidence_text[:16000]

            db.update_stage(run_id, 4)
            db.log_event(run_id, "stage_complete", {"stage": 4})

        else:
            # Resuming past stage 4 — no evidence text available
            evidence_text = ""

        # ------------------------------------------------------------------ #
        # STAGE 5 — Pass A: website evidence
        # ------------------------------------------------------------------ #
        if start_stage <= 5:
            existing = {f: field_results[f]["value"] for f in field_results}
            result = extractor.extract_pass_a(evidence_text, existing)

            for field, data in result.items():
                if data is None:
                    continue
                if field in config.LEGAL_FIELDS:
                    continue
                if field in field_results:
                    # Keep whichever has higher confidence
                    if data.get("confidence", 0) > field_results[field].get("confidence", 0):
                        field_results[field] = {
                            "value": data["value"],
                            "confidence": data["confidence"],
                            "evidence_quote": data.get("evidence_quote", ""),
                            "source": "website_pass_a",
                        }
                else:
                    field_results[field] = {
                        "value": data["value"],
                        "confidence": data["confidence"],
                        "evidence_quote": data.get("evidence_quote", ""),
                        "source": "website_pass_a",
                    }

            db.update_stage(run_id, 5)
            db.log_event(run_id, "stage_complete", {"stage": 5})

        # ------------------------------------------------------------------ #
        # STAGE 6 — Pass B: SOS + profile
        # ------------------------------------------------------------------ #
        if start_stage <= 6:
            still_needed = [
                f for f in config.ALL_FIELDS
                if field_results.get(f) is None and f not in config.LEGAL_FIELDS
            ]
            result = extractor.extract_pass_b(legal_data, profile_data, still_needed)

            for field, data in result.items():
                if data is None:
                    continue
                if field in config.LEGAL_FIELDS:
                    continue
                if field not in field_results:
                    field_results[field] = {
                        "value": data["value"],
                        "confidence": data["confidence"],
                        "evidence_quote": data.get("evidence_quote", ""),
                        "source": "sos_profile_pass_b",
                    }

            db.update_stage(run_id, 6)
            db.log_event(run_id, "stage_complete", {"stage": 6})

        # ------------------------------------------------------------------ #
        # STAGE 7 — Web consensus
        # ------------------------------------------------------------------ #
        if start_stage <= 7:
            retry_fields = [
                f for f in ["phone", "email", "address"]
                if field_results.get(f) is None
            ]
            for field in retry_fields:
                snippets = mock_data.mock_web_search(field, account.get("business_name", ""))
                result = extractor.extract_web_consensus(field, snippets)
                data = result.get(field)
                if data is not None and field not in field_results:
                    field_results[field] = {
                        "value": data["value"],
                        "confidence": data["confidence"],
                        "evidence_quote": data.get("evidence_quote", ""),
                        "source": "web_consensus",
                    }

            db.update_stage(run_id, 7)
            db.log_event(run_id, "stage_complete", {"stage": 7})

        # ------------------------------------------------------------------ #
        # CONFIDENCE GATE
        # ------------------------------------------------------------------ #
        # Write legal fields directly (locked, no confidence check)
        for field in config.LEGAL_FIELDS:
            value = legal_data.get(field)
            if value is not None:
                db.write_field(account_id, field, value)
                db.log_event(run_id, "auto_applied", {"field": field, "source": "sos_locked"})

        # Process all other fields
        queued_any = False
        for field, data in field_results.items():
            if field in config.LEGAL_FIELDS:
                continue
            value = data.get("value")
            if value is None:
                continue
            confidence = data.get("confidence", 0)
            source = data.get("source", "unknown")
            evidence_quote = data.get("evidence_quote", "")

            if confidence >= config.CONFIDENCE_THRESHOLD:
                db.write_field(account_id, field, value)
                db.log_event(run_id, "auto_applied", {"field": field, "confidence": confidence})
            else:
                db.enqueue_review(
                    run_id, account_id, field, value,
                    source, confidence, evidence_quote
                )
                db.log_event(run_id, "review_requested", {"field": field})
                queued_any = True

        if queued_any:
            db.update_state(run_id, "awaiting_review")
        else:
            db.update_state(run_id, "approved")
            db.set_enrichment_status(account_id, "enriched")

    except Exception:
        db.log_event(run_id, "run_failed", {"account_id": account_id})
        db.update_state(run_id, "failed")
        raise
    finally:
        db._active_run_id = None
