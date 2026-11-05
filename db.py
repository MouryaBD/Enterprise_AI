import sqlite3
import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

import config

_active_run_id: Optional[str] = None


def _get_conn():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_db():
    conn = _get_conn()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            account_id           TEXT PRIMARY KEY,
            business_name        TEXT,
            phone                TEXT,
            email                TEXT,
            website              TEXT,
            address              TEXT,
            city                 TEXT,
            state                TEXT,
            zip                  TEXT,
            entity_type          TEXT,
            date_established     TEXT,
            principal_name       TEXT,
            principal_title      TEXT,
            num_employees        TEXT,
            business_description TEXT,
            enriched_at          TEXT,
            enrichment_status    TEXT DEFAULT 'pending'
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            run_id            TEXT PRIMARY KEY,
            account_id        TEXT NOT NULL,
            stage             INTEGER DEFAULT 0,
            state             TEXT NOT NULL,
            legal_locked      INTEGER DEFAULT 0,
            fields_written    TEXT,
            confidence_scores TEXT,
            evidence_excerpts TEXT,
            reviewer_id       TEXT,
            reviewer_decision TEXT,
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS run_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id    TEXT NOT NULL,
            event     TEXT NOT NULL,
            detail    TEXT,
            timestamp TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS pending_events (
            event_id    TEXT PRIMARY KEY,
            account_id  TEXT NOT NULL,
            event_type  TEXT NOT NULL,
            received_at TEXT NOT NULL,
            processed   INTEGER DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS review_queue (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id           TEXT NOT NULL,
            account_id       TEXT NOT NULL,
            field_name       TEXT NOT NULL,
            proposed_value   TEXT,
            source           TEXT,
            confidence_score REAL,
            evidence_excerpt TEXT,
            created_at       TEXT NOT NULL,
            expires_at       TEXT NOT NULL,
            status           TEXT DEFAULT 'pending',
            reviewer_id      TEXT,
            decided_at       TEXT
        )
    """)

    conn.commit()
    conn.close()


def import_accounts(xlsx_path: str) -> int:
    import openpyxl
    wb = openpyxl.load_workbook(xlsx_path)
    ws = wb.active

    headers = []
    for cell in ws[1]:
        val = cell.value
        headers.append(val.strip().lower() if val else "")

    # Map header names to field names (including xlsx-specific aliases)
    field_map = {f.lower(): f for f in config.ALL_FIELDS}
    field_map["account_id"] = "account_id"
    field_map["id"] = "account_id"
    # xlsx aliases
    field_map["account id"] = "account_id"
    field_map["business name"] = "business_name"
    field_map["primary email"] = "email"
    field_map["primary phone"] = "phone"
    field_map["billing street"] = "address"
    field_map["billing city"] = "city"
    field_map["billing state"] = "state"
    field_map["billing zip"] = "zip"
    field_map["date established"] = "date_established"
    field_map["entity type"] = "entity_type"
    field_map["type of business"] = "business_description"

    # Track columns that feed into principal_name (first + last combined)
    principal_first_col = None
    principal_last_col = None

    col_to_field = {}
    for i, h in enumerate(headers):
        if h == "principal first name":
            principal_first_col = i
        elif h == "principal last name":
            principal_last_col = i
        elif h in field_map:
            col_to_field[i] = field_map[h]

    conn = _get_conn()
    c = conn.cursor()
    inserted = 0

    for row in ws.iter_rows(min_row=2, values_only=True):
        if not any(row):
            continue
        row_dict = {}
        for i, val in enumerate(row):
            if i in col_to_field:
                row_dict[col_to_field[i]] = val

        # Combine principal first + last name
        first = row[principal_first_col] if principal_first_col is not None and principal_first_col < len(row) else None
        last = row[principal_last_col] if principal_last_col is not None and principal_last_col < len(row) else None
        if first or last:
            row_dict["principal_name"] = " ".join(p for p in [first, last] if p)

        account_id = row_dict.get("account_id")
        if not account_id:
            continue

        # Check if exists
        existing = c.execute(
            "SELECT account_id FROM accounts WHERE account_id=?", (account_id,)
        ).fetchone()
        if existing:
            continue

        fields = ["account_id"] + [f for f in config.ALL_FIELDS]
        values = [row_dict.get("account_id")] + [row_dict.get(f) for f in config.ALL_FIELDS]
        placeholders = ",".join("?" * len(fields))
        col_names = ",".join(fields)
        c.execute(f"INSERT INTO accounts ({col_names}) VALUES ({placeholders})", values)
        inserted += 1

    conn.commit()
    conn.close()
    return inserted


def get_account(account_id: str) -> Optional[dict]:
    conn = _get_conn()
    c = conn.cursor()
    row = c.execute(
        "SELECT * FROM accounts WHERE account_id=?", (account_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return dict(row)


def get_pending_accounts() -> list:
    conn = _get_conn()
    c = conn.cursor()
    rows = c.execute(
        "SELECT account_id FROM accounts WHERE enrichment_status='pending'"
    ).fetchall()
    conn.close()
    return [r["account_id"] for r in rows]


def write_field(account_id: str, field_name: str, value: str):
    if field_name not in config.ALL_FIELDS:
        raise ValueError(f"Field '{field_name}' not in ALL_FIELDS")

    now = _now_iso()
    conn = _get_conn()
    c = conn.cursor()
    c.execute(
        f"UPDATE accounts SET {field_name}=?, enriched_at=? WHERE account_id=?",
        (value, now, account_id)
    )
    conn.commit()
    conn.close()

    if _active_run_id:
        log_event(_active_run_id, "field_written", {"field": field_name, "account_id": account_id})


def set_enrichment_status(account_id: str, status: str):
    conn = _get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE accounts SET enrichment_status=? WHERE account_id=?",
        (status, account_id)
    )
    conn.commit()
    conn.close()


def create_run(account_id: str) -> str:
    run_id = str(uuid.uuid4())
    now = _now_iso()
    conn = _get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO runs (run_id, account_id, state, created_at, updated_at) VALUES (?,?,?,?,?)",
        (run_id, account_id, "pending", now, now)
    )
    conn.commit()
    conn.close()
    return run_id


def get_run(run_id: str) -> Optional[dict]:
    conn = _get_conn()
    c = conn.cursor()
    row = c.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    return dict(row)


def update_stage(run_id: str, stage: int):
    now = _now_iso()
    conn = _get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE runs SET stage=?, updated_at=? WHERE run_id=?",
        (stage, now, run_id)
    )
    conn.commit()
    conn.close()


def update_state(run_id: str, state: str):
    now = _now_iso()
    conn = _get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE runs SET state=?, updated_at=? WHERE run_id=?",
        (state, now, run_id)
    )
    conn.commit()
    conn.close()


def log_event(run_id: str, event: str, detail: dict = None):
    now = _now_iso()
    detail_json = json.dumps(detail) if detail is not None else None
    conn = _get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO run_log (run_id, event, detail, timestamp) VALUES (?,?,?,?)",
        (run_id, event, detail_json, now)
    )
    conn.commit()
    conn.close()


def enqueue_review(run_id: str, account_id: str, field_name: str,
                   proposed_value: str, source: str, confidence_score: float,
                   evidence_excerpt: str):
    now = _now_iso()
    expires_dt = datetime.now(timezone.utc) + timedelta(hours=config.REVIEW_EXPIRY_HOURS)
    expires_at = expires_dt.isoformat()

    conn = _get_conn()
    c = conn.cursor()
    c.execute(
        """INSERT INTO review_queue
           (run_id, account_id, field_name, proposed_value, source,
            confidence_score, evidence_excerpt, created_at, expires_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (run_id, account_id, field_name, proposed_value, source,
         confidence_score, evidence_excerpt, now, expires_at)
    )
    conn.commit()
    conn.close()


def get_pending_reviews(run_id: str) -> list:
    conn = _get_conn()
    c = conn.cursor()
    rows = c.execute(
        "SELECT * FROM review_queue WHERE run_id=? AND status='pending'",
        (run_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_pending_reviews() -> list:
    conn = _get_conn()
    c = conn.cursor()
    rows = c.execute(
        "SELECT * FROM review_queue WHERE status='pending' ORDER BY created_at ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_reviews_for_run(run_id: str) -> list:
    conn = _get_conn()
    c = conn.cursor()
    rows = c.execute(
        "SELECT * FROM review_queue WHERE run_id=?", (run_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_review_decision(review_id: int, status: str, reviewer_id: str):
    now = _now_iso()
    conn = _get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE review_queue SET status=?, reviewer_id=?, decided_at=? WHERE id=?",
        (status, reviewer_id, now, review_id)
    )
    conn.commit()
    conn.close()


def set_legal_locked(run_id: str):
    now = _now_iso()
    conn = _get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE runs SET legal_locked=1, updated_at=? WHERE run_id=?",
        (now, run_id)
    )
    conn.commit()
    conn.close()


def check_duplicate_run(account_id: str) -> Optional[str]:
    """
    Returns run_id if a run exists for this account in a non-terminal state
    (pending, running, awaiting_review).
    Returns None otherwise.
    """
    conn = _get_conn()
    c = conn.cursor()
    row = c.execute(
        """SELECT run_id FROM runs
           WHERE account_id=? AND state IN ('pending','running','awaiting_review')
           LIMIT 1""",
        (account_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return row["run_id"]


def get_latest_run_for_account(account_id: str) -> Optional[str]:
    """Returns the most recent run_id for an account, regardless of state."""
    conn = _get_conn()
    c = conn.cursor()
    row = c.execute(
        """SELECT run_id FROM runs
           WHERE account_id=?
           ORDER BY created_at DESC
           LIMIT 1""",
        (account_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return row["run_id"]


def mark_event_processed(event_id: str):
    conn = _get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE pending_events SET processed=1 WHERE event_id=?", (event_id,)
    )
    conn.commit()
    conn.close()


def get_pending_events() -> list:
    conn = _get_conn()
    c = conn.cursor()
    rows = c.execute(
        "SELECT * FROM pending_events WHERE processed=0 ORDER BY received_at ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def insert_account(account_id: str, fields: dict):
    """Insert a single account row (used in tests)."""
    all_fields = ["account_id"] + config.ALL_FIELDS
    values = [account_id] + [fields.get(f) for f in config.ALL_FIELDS]
    placeholders = ",".join("?" * len(all_fields))
    col_names = ",".join(all_fields)
    conn = _get_conn()
    c = conn.cursor()
    c.execute(f"INSERT OR IGNORE INTO accounts ({col_names}) VALUES ({placeholders})", values)
    conn.commit()
    conn.close()


def insert_pending_event(event_id: str, account_id: str, event_type: str):
    now = _now_iso()
    conn = _get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO pending_events (event_id, account_id, event_type, received_at) VALUES (?,?,?,?)",
        (event_id, account_id, event_type, now)
    )
    conn.commit()
    conn.close()


def get_run_log(run_id: str) -> list:
    conn = _get_conn()
    c = conn.cursor()
    rows = c.execute(
        "SELECT * FROM run_log WHERE run_id=? ORDER BY id ASC", (run_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
