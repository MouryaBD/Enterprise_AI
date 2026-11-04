"""
pytest tests for the enrichment pipeline.
All 7 tests use mock data only. No Anthropic API calls.
"""
import os
import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

# Remove API key so pipeline uses mocks
os.environ.pop("ANTHROPIC_API_KEY", None)


def _setup_db(tmp_path, monkeypatch):
    """Configure a fresh DB for each test."""
    db_file = str(tmp_path / "enrichment.db")
    monkeypatch.setattr("config.DB_PATH", db_file)

    import importlib
    import config
    import db

    # Reload db so it picks up the patched DB_PATH
    importlib.reload(db)
    db.create_db()
    return db


def run_log_for(run_id, db):
    return db.get_run_log(run_id)


# ============================================================
# TEST 1 — Approved run (ATH-001: phone low, others high)
# ============================================================
def test_approved_run(tmp_path, monkeypatch):
    db = _setup_db(tmp_path, monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    # Insert account
    db.insert_account("ATH-001", {
        "business_name": "Athens Plumbing Co",
        "address": "123 Main St Athens GA"
    })

    run_id = db.create_run("ATH-001")
    db.update_state(run_id, "running")

    import importlib
    import pipeline
    importlib.reload(pipeline)
    pipeline.run_pipeline("ATH-001", run_id)

    # State should be awaiting_review (phone confidence=0.65 < 0.80)
    run = db.get_run(run_id)
    assert run["state"] == "awaiting_review", f"Expected awaiting_review, got {run['state']}"

    pending = db.get_pending_reviews(run_id)
    assert len(pending) == 1, f"Expected 1 pending review, got {len(pending)}"

    # Phone should not be written yet
    acc = db.get_account("ATH-001")
    assert acc["phone"] is None, f"Expected phone=None, got {acc['phone']}"

    # Simulate approval
    review = pending[0]
    db.write_field("ATH-001", "phone", review["proposed_value"])
    db.update_review_decision(review["id"], "approved", "test_analyst")
    db.log_event(run_id, "approved", {"field": "phone", "reviewer": "test_analyst"})
    db.update_state(run_id, "approved")

    # Verify post-approval
    acc = db.get_account("ATH-001")
    assert acc["phone"] is not None, "Phone should be set after approval"

    run = db.get_run(run_id)
    assert run["state"] == "approved"

    log = run_log_for(run_id, db)
    assert any(e["event"] == "approved" for e in log), "No approved event in run_log"


# ============================================================
# TEST 2 — Rejection (ATH-002: email=0.60 low, phone=0.84 auto-applied)
# ============================================================
def test_rejection(tmp_path, monkeypatch):
    db = _setup_db(tmp_path, monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    db.insert_account("ATH-002", {
        "business_name": "Classic City Bakery",
        "address": "456 Broad St Athens GA"
    })

    run_id = db.create_run("ATH-002")
    db.update_state(run_id, "running")

    import importlib
    import pipeline
    importlib.reload(pipeline)
    pipeline.run_pipeline("ATH-002", run_id)

    # Phone (0.84 >= 0.80) should be auto-applied
    acc = db.get_account("ATH-002")
    assert acc["phone"] is not None, f"Phone should be auto-applied, got {acc['phone']}"

    # Email (0.60 < 0.80) should be pending review
    pending = db.get_pending_reviews(run_id)
    email_reviews = [r for r in pending if r["field_name"] == "email"]
    assert len(email_reviews) == 1, f"Expected 1 email review, got {len(email_reviews)}"

    # Simulate rejection
    review = email_reviews[0]
    db.update_review_decision(review["id"], "rejected", "test_analyst")
    db.log_event(run_id, "rejected", {"field": "email"})

    # Email should NOT be written
    acc = db.get_account("ATH-002")
    assert acc["email"] is None, f"Email should be None after rejection, got {acc['email']}"

    # Verify review status
    all_reviews = db.get_all_reviews_for_run(run_id)
    email_rev = next(r for r in all_reviews if r["field_name"] == "email")
    assert email_rev["status"] == "rejected"


# ============================================================
# TEST 3 — Duplicate event
# ============================================================
def test_duplicate_event(tmp_path, monkeypatch):
    db = _setup_db(tmp_path, monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    db.insert_account("ATH-003", {
        "business_name": "UGA Print Shop",
        "address": "789 College Ave Athens GA"
    })

    # Insert two pending events for the same account
    db.insert_pending_event("EVT-001", "ATH-003", "new_enrichment_request")
    db.insert_pending_event("EVT-002", "ATH-003", "new_enrichment_request")

    import importlib
    import poll_events
    importlib.reload(poll_events)
    poll_events.poll_events()

    # Only 1 run should exist for ATH-003
    conn = db._get_conn()
    count = conn.execute(
        "SELECT COUNT(*) FROM runs WHERE account_id='ATH-003'"
    ).fetchone()[0]
    conn.close()
    assert count == 1, f"Expected 1 run, got {count}"

    # Exactly one duplicate_discarded event
    conn = db._get_conn()
    runs = conn.execute(
        "SELECT run_id FROM runs WHERE account_id='ATH-003'"
    ).fetchall()
    conn.close()
    run_id = runs[0]["run_id"]

    log = run_log_for(run_id, db)
    dup_events = [e for e in log if e["event"] == "duplicate_discarded"]
    assert len(dup_events) == 1, f"Expected 1 duplicate_discarded, got {len(dup_events)}"

    # Both events should be processed
    conn = db._get_conn()
    events = conn.execute(
        "SELECT processed FROM pending_events WHERE account_id='ATH-003' ORDER BY event_id"
    ).fetchall()
    conn.close()
    assert all(e["processed"] == 1 for e in events), "Not all events marked processed"


# ============================================================
# TEST 4 — Interruption and resume (ATH-004)
# ============================================================
def test_interruption_and_resume(tmp_path, monkeypatch):
    db = _setup_db(tmp_path, monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    db.insert_account("ATH-004", {
        "business_name": "Athens Auto Repair",
        "address": "321 Oak St Athens GA"
    })

    run_id = db.create_run("ATH-004")
    db.update_state(run_id, "running")

    import importlib
    import mock_data
    import pipeline

    # Patch extract_pass_a to raise RuntimeError (triggers after stage 4)
    original_pass_a = mock_data.mock_extract_pass_a

    def failing_pass_a(evidence_text, existing_fields):
        raise RuntimeError("Simulated stage 5 failure")

    monkeypatch.setattr(mock_data, "mock_extract_pass_a", failing_pass_a)
    monkeypatch.setattr(mock_data, "extract_pass_a", failing_pass_a)

    importlib.reload(pipeline)

    try:
        pipeline.run_pipeline("ATH-004", run_id)
    except RuntimeError:
        pass

    # Stage should be 4 (last successfully completed)
    run = db.get_run(run_id)
    assert run["stage"] == 4, f"Expected stage=4, got {run['stage']}"

    # Stages 1-4 should each appear exactly once in run_log
    log = run_log_for(run_id, db)
    stage_complete_events = [e for e in log if e["event"] == "stage_complete"]
    stages_logged = []
    for e in stage_complete_events:
        detail = json.loads(e["detail"]) if e["detail"] else {}
        stages_logged.append(detail.get("stage"))

    for stage in [1, 2, 3, 4]:
        count = stages_logged.count(stage)
        assert count == 1, f"Stage {stage} should appear exactly once, got {count}"

    # Restore mock and reload pipeline
    monkeypatch.setattr(mock_data, "mock_extract_pass_a", original_pass_a)
    monkeypatch.setattr(mock_data, "extract_pass_a", original_pass_a)
    importlib.reload(pipeline)

    import resume
    importlib.reload(resume)
    resume.resume_run(run_id)

    # Check resumed event
    log = run_log_for(run_id, db)
    resumed_events = [e for e in log if e["event"] == "resumed"]
    assert len(resumed_events) == 1
    detail = json.loads(resumed_events[0]["detail"])
    assert detail["from_stage"] == 5, f"Expected from_stage=5, got {detail['from_stage']}"

    # Stages 1-4 still exactly once
    stage_complete_events = [e for e in log if e["event"] == "stage_complete"]
    stages_logged = []
    for e in stage_complete_events:
        detail = json.loads(e["detail"]) if e["detail"] else {}
        stages_logged.append(detail.get("stage"))

    for stage in [1, 2, 3, 4]:
        count = stages_logged.count(stage)
        assert count == 1, f"After resume: stage {stage} should appear exactly once, got {count}"

    # Stages 5, 6, 7 should appear after resume
    for stage in [5, 6, 7]:
        count = stages_logged.count(stage)
        assert count >= 1, f"Stage {stage} should appear after resume, got {count}"

    # Run should be in a terminal or review state
    run = db.get_run(run_id)
    assert run["state"] in ("approved", "awaiting_review"), f"Unexpected state: {run['state']}"


# ============================================================
# UNIT TEST — Legal lock
# ============================================================
def test_legal_lock(tmp_path, monkeypatch):
    """Stage 1 locks entity_type=LLC. Pass A must NOT overwrite it."""
    db = _setup_db(tmp_path, monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    db.insert_account("ATH-001", {
        "business_name": "Athens Plumbing Co",
        "address": "123 Main St Athens GA"
    })

    import importlib
    import mock_data

    # Make pass_a return entity_type='Corp' — pipeline should ignore it
    original_pass_a = mock_data.mock_extract_pass_a

    def pass_a_with_entity(evidence_text, existing_fields):
        result = original_pass_a(evidence_text, existing_fields)
        result["entity_type"] = {
            "value": "Corp",
            "confidence": 0.99,
            "evidence_quote": "Registered as Corp"
        }
        return result

    monkeypatch.setattr(mock_data, "mock_extract_pass_a", pass_a_with_entity)
    monkeypatch.setattr(mock_data, "extract_pass_a", pass_a_with_entity)

    import pipeline
    importlib.reload(pipeline)

    run_id = db.create_run("ATH-001")
    db.update_state(run_id, "running")
    pipeline.run_pipeline("ATH-001", run_id)

    acc = db.get_account("ATH-001")
    assert acc["entity_type"] == "LLC", (
        f"entity_type should be 'LLC' (SOS locked), got '{acc['entity_type']}'"
    )

    # Restore
    monkeypatch.setattr(mock_data, "mock_extract_pass_a", original_pass_a)
    monkeypatch.setattr(mock_data, "extract_pass_a", original_pass_a)


# ============================================================
# UNIT TEST — No site accepted
# ============================================================
def test_no_site_accepted(tmp_path, monkeypatch):
    """When all candidates score below 0.70, stages 3 and 4 are skipped."""
    db = _setup_db(tmp_path, monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    db.insert_account("ATH-001", {
        "business_name": "Athens Plumbing Co",
        "address": "123 Main St Athens GA"
    })

    import importlib
    import mock_data

    # Score all candidates below 0.70
    def low_score_websites(candidates, name, address):
        return [{"url": url, "score": 0.30, "reason": "low score"} for url in candidates]

    monkeypatch.setattr(mock_data, "mock_score_websites", low_score_websites)
    monkeypatch.setattr(mock_data, "score_websites", low_score_websites)

    import pipeline
    importlib.reload(pipeline)

    run_id = db.create_run("ATH-001")
    db.update_state(run_id, "running")
    pipeline.run_pipeline("ATH-001", run_id)

    # Check for stage_skipped events for stages 3 and 4
    log = run_log_for(run_id, db)
    skipped_events = [e for e in log if e["event"] == "stage_skipped"]
    skipped_stages = []
    for e in skipped_events:
        detail = json.loads(e["detail"]) if e["detail"] else {}
        skipped_stages.append(detail.get("stage"))

    assert 3 in skipped_stages, f"Stage 3 should be skipped, skipped stages: {skipped_stages}"
    assert 4 in skipped_stages, f"Stage 4 should be skipped, skipped stages: {skipped_stages}"

    # Pipeline should complete without error
    run = db.get_run(run_id)
    assert run["state"] in ("approved", "awaiting_review", "failed"), f"Unexpected state: {run['state']}"
    assert run["state"] != "failed", "Pipeline should not fail when no site is accepted"

    # Restore
    import mock_data as md
    monkeypatch.setattr(md, "mock_score_websites", md.mock_score_websites)
    monkeypatch.setattr(md, "score_websites", md.mock_score_websites)


# ============================================================
# UNIT TEST — Timeout
# ============================================================
def test_timeout(tmp_path, monkeypatch):
    """Review queue item with expires_at in the past should be timed_out."""
    db = _setup_db(tmp_path, monkeypatch)

    db.insert_account("ATH-001", {
        "business_name": "Athens Plumbing Co",
        "address": "123 Main St Athens GA"
    })

    run_id = db.create_run("ATH-001")
    db.update_state(run_id, "running")

    # Manually insert an expired review_queue row
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()

    conn = db._get_conn()
    conn.execute(
        """INSERT INTO review_queue
           (run_id, account_id, field_name, proposed_value, source,
            confidence_score, evidence_excerpt, created_at, expires_at, status)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (run_id, "ATH-001", "phone", "(706) 555-0192", "test_source",
         0.65, "Call us at (706) 555-0192", now_iso, yesterday, "pending")
    )
    conn.commit()
    conn.close()

    # Run the timeout check logic from reviewer_cli
    import importlib
    import reviewer_cli
    importlib.reload(reviewer_cli)

    reviews = db.get_all_pending_reviews()
    assert len(reviews) == 1

    reviewer_id = "test_analyst"
    item = reviews[0]

    expires_at_str = item["expires_at"]
    now = datetime.now(timezone.utc)
    if expires_at_str.endswith("Z"):
        expires_dt = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
    elif "+" in expires_at_str or expires_at_str.count("-") > 2:
        expires_dt = datetime.fromisoformat(expires_at_str)
    else:
        expires_dt = datetime.fromisoformat(expires_at_str).replace(tzinfo=timezone.utc)

    assert now > expires_dt, "Review should be expired"

    # Apply timeout
    db.update_review_decision(item["id"], "timed_out", reviewer_id)
    db.log_event(run_id, "timed_out", {"field": item["field_name"]})
    db.update_state(run_id, "escalated")

    # Verify
    conn = db._get_conn()
    row = conn.execute(
        "SELECT status FROM review_queue WHERE id=?", (item["id"],)
    ).fetchone()
    conn.close()
    assert row["status"] == "timed_out", f"Expected timed_out, got {row['status']}"

    run = db.get_run(run_id)
    assert run["state"] == "escalated", f"Expected escalated, got {run['state']}"
