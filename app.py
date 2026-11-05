"""
Streamlit UI for the Business Account Data Enrichment Pipeline.
Run with: streamlit run app.py
"""
import os
import sys
import json
import uuid
import datetime
import sqlite3

import streamlit as st

# ── make sure project modules are importable ──────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
import config
import db

# ── page config ───────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Enrichment Pipeline",
    page_icon="🏢",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── sidebar nav ───────────────────────────────────────────────────────────
st.sidebar.title("🏢 Enrichment Pipeline")
st.sidebar.caption("Client Pilot — Athens, GA")
page = st.sidebar.radio(
    "Navigation",
    ["📊 Dashboard", "🔍 Accounts", "▶️ Run Enrichment", "✅ Review Queue", "📋 Run Logs"],
)
st.sidebar.divider()
st.sidebar.caption(f"DB: `{config.DB_PATH}`")
st.sidebar.caption(f"Threshold: `{config.CONFIDENCE_THRESHOLD}`")
api_set = bool(os.environ.get("ANTHROPIC_API_KEY"))
st.sidebar.caption(f"API key: {'✅ set' if api_set else '⚠️ not set (mock mode)'}")

# ── ensure DB exists ──────────────────────────────────────────────────────
db.create_db()


# ══════════════════════════════════════════════════════════════════════════
# helpers
# ══════════════════════════════════════════════════════════════════════════
def _conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def get_all_accounts():
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM accounts ORDER BY account_id")]


def get_all_runs():
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM runs ORDER BY created_at DESC")]


def enqueue_all_pending():
    """Insert a pending_event for every account with enrichment_status='pending'."""
    aids = db.get_pending_accounts()
    if not aids:
        return 0
    with _conn() as c:
        inserted = 0
        for aid in aids:
            existing = c.execute(
                "SELECT 1 FROM pending_events WHERE account_id=? AND processed=0", (aid,)
            ).fetchone()
            if not existing:
                c.execute(
                    "INSERT INTO pending_events VALUES (?,?,?,?,0)",
                    [str(uuid.uuid4()), aid, "new_enrichment_request",
                     datetime.datetime.utcnow().isoformat()]
                )
                inserted += 1
        c.commit()
    return inserted


STATUS_COLOR = {
    "pending":        "🔵",
    "in_progress":    "🟡",
    "enriched":       "🟢",
    "needs_review":   "🟠",
    "failed":         "🔴",
}
RUN_COLOR = {
    "pending":        "🔵",
    "running":        "🟡",
    "awaiting_review":"🟠",
    "approved":       "🟢",
    "rejected":       "🔴",
    "escalated":      "🔴",
    "failed":         "🔴",
}


# ══════════════════════════════════════════════════════════════════════════
# PAGE 1 — DASHBOARD
# ══════════════════════════════════════════════════════════════════════════
if page == "📊 Dashboard":
    st.title("📊 Dashboard")

    accounts = get_all_accounts()
    runs     = get_all_runs()
    reviews  = db.get_all_pending_reviews()

    total    = len(accounts)
    enriched = sum(1 for a in accounts if a["enrichment_status"] == "enriched")
    pending  = sum(1 for a in accounts if a["enrichment_status"] == "pending")
    failed   = sum(1 for a in accounts if a["enrichment_status"] == "failed")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Accounts", total)
    c2.metric("Enriched", enriched, delta=f"{enriched/total*100:.0f}%" if total else "0%")
    c3.metric("Pending", pending)
    c4.metric("Need Review", len(reviews))
    c5.metric("Failed", failed)

    st.divider()
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Account Status Breakdown")
        from collections import Counter
        status_counts = Counter(a["enrichment_status"] or "pending" for a in accounts)
        if status_counts:
            import pandas as pd
            df = pd.DataFrame(list(status_counts.items()), columns=["Status", "Count"])
            st.bar_chart(df.set_index("Status"))

    with col2:
        st.subheader("Field Completeness (all accounts)")
        if accounts:
            import pandas as pd
            completeness = {}
            for f in config.ALL_FIELDS:
                filled = sum(1 for a in accounts if a.get(f))
                completeness[f] = filled
            df2 = pd.DataFrame(list(completeness.items()), columns=["Field", "Filled"])
            df2["Pct"] = (df2["Filled"] / total * 100).round(1)
            st.dataframe(df2, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Recent Runs")
    if runs:
        import pandas as pd
        df_runs = pd.DataFrame(runs[:10])[
            ["run_id", "account_id", "stage", "state", "created_at", "updated_at"]
        ]
        df_runs["state"] = df_runs["state"].apply(
            lambda s: f"{RUN_COLOR.get(s,'⚪')} {s}"
        )
        df_runs["run_id"] = df_runs["run_id"].str[:8] + "..."
        st.dataframe(df_runs, use_container_width=True, hide_index=True)
    else:
        st.info("No runs yet. Go to **▶️ Run Enrichment** to start.")


# ══════════════════════════════════════════════════════════════════════════
# PAGE 2 — ACCOUNTS
# ══════════════════════════════════════════════════════════════════════════
elif page == "🔍 Accounts":
    st.title("🔍 Accounts")

    accounts = get_all_accounts()
    if not accounts:
        st.warning("No accounts in database. Import an xlsx file first.")
        st.code('python import_accounts.py --file "Input example.xlsx"')
        st.stop()

    import pandas as pd
    # Search / filter
    col1, col2 = st.columns([2, 1])
    search = col1.text_input("Search by name or account ID", "")
    status_filter = col2.selectbox(
        "Filter by status", ["All", "pending", "enriched", "needs_review", "failed"]
    )

    filtered = accounts
    if search:
        filtered = [a for a in filtered if
                    search.lower() in (a.get("business_name") or "").lower() or
                    search.lower() in (a.get("account_id") or "").lower()]
    if status_filter != "All":
        filtered = [a for a in filtered if
                    (a.get("enrichment_status") or "pending") == status_filter]

    st.caption(f"Showing {len(filtered)} of {len(accounts)} accounts")

    # Summary table
    rows = []
    for a in filtered:
        filled = sum(1 for f in config.ALL_FIELDS if a.get(f))
        rows.append({
            "ID":     a["account_id"],
            "Name":   a.get("business_name") or "—",
            "Status": f"{STATUS_COLOR.get(a.get('enrichment_status','pending'),'⚪')} {a.get('enrichment_status','pending')}",
            "Fields": f"{filled}/14",
            "Enriched At": (a.get("enriched_at") or "—")[:19],
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.divider()
    # Detail view
    st.subheader("Account Detail")
    ids = [a["account_id"] for a in filtered]
    if ids:
        selected_id = st.selectbox("Select account", ids)
        acc = db.get_account(selected_id)
        if acc:
            col1, col2 = st.columns(2)
            half = len(config.ALL_FIELDS) // 2
            for i, field in enumerate(config.ALL_FIELDS):
                col = col1 if i < half else col2
                val = acc.get(field)
                col.text_input(field, value=val or "", disabled=True, key=f"field_{field}")

            # Show run history for this account
            with _conn() as c:
                runs = [dict(r) for r in c.execute(
                    "SELECT run_id, stage, state, created_at FROM runs WHERE account_id=? ORDER BY created_at DESC",
                    (selected_id,)
                )]
            if runs:
                st.caption("Run history")
                import pandas as pd
                df_r = pd.DataFrame(runs)
                df_r["run_id"] = df_r["run_id"].str[:8] + "..."
                df_r["state"] = df_r["state"].apply(lambda s: f"{RUN_COLOR.get(s,'⚪')} {s}")
                st.dataframe(df_r, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════
# PAGE 3 — RUN ENRICHMENT
# ══════════════════════════════════════════════════════════════════════════
elif page == "▶️ Run Enrichment":
    st.title("▶️ Run Enrichment")

    pending_accounts = db.get_pending_accounts()

    col1, col2 = st.columns(2)
    col1.metric("Accounts pending enrichment", len(pending_accounts))
    col2.metric("Mode", "🤖 Live API" if os.environ.get("ANTHROPIC_API_KEY") else "🧪 Mock (no API key)")

    if not pending_accounts:
        st.success("All accounts have been enriched.")
    else:
        st.write("**Accounts queued:**", ", ".join(pending_accounts[:10]),
                 "..." if len(pending_accounts) > 10 else "")

    st.divider()

    col_a, col_b = st.columns(2)

    # Run all pending
    with col_a:
        st.subheader("Run all pending accounts")
        st.caption("Inserts a pending_event for each account with status='pending' and runs the pipeline.")
        if st.button("▶️ Run All Pending", type="primary", disabled=not pending_accounts):
            with st.spinner("Running pipeline…"):
                n = enqueue_all_pending()
                from poll_events import poll_events
                poll_events()
            st.success(f"Done. {n} event(s) enqueued and processed.")
            st.rerun()

    # Run single account
    with col_b:
        st.subheader("Run a single account")
        all_accounts = get_all_accounts()
        aid_options = [a["account_id"] for a in all_accounts]
        selected = st.selectbox("Select account", aid_options, key="run_single")
        if st.button("▶️ Run Selected Account"):
            with st.spinner(f"Running pipeline for {selected}…"):
                # Check for active run
                existing = db.check_duplicate_run(selected)
                if existing:
                    st.warning(f"Active run already exists: {existing[:8]}…")
                else:
                    run_id = db.create_run(selected)
                    db.update_state(run_id, "running")
                    from pipeline import run_pipeline
                    try:
                        run_pipeline(selected, run_id)
                        final = db.get_run(run_id)
                        st.success(f"Run complete — state: **{final['state']}** | run_id: `{run_id[:8]}…`")
                    except Exception as e:
                        st.error(f"Run failed: {e}")
            st.rerun()

    st.divider()
    st.subheader("Active / Recent Runs")
    runs = get_all_runs()
    if runs:
        import pandas as pd
        df = pd.DataFrame(runs[:20])[["run_id","account_id","stage","state","created_at"]]
        df["state"] = df["state"].apply(lambda s: f"{RUN_COLOR.get(s,'⚪')} {s}")
        df["run_id"] = df["run_id"].str[:8] + "..."
        st.dataframe(df, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════
# PAGE 4 — REVIEW QUEUE
# ══════════════════════════════════════════════════════════════════════════
elif page == "✅ Review Queue":
    st.title("✅ Review Queue")

    reviewer_id = st.sidebar.text_input("Reviewer ID", value="analyst_01")

    reviews = db.get_all_pending_reviews()
    now = datetime.datetime.now(datetime.timezone.utc)

    # Separate expired from active
    active  = []
    expired = []
    for r in reviews:
        exp_str = r["expires_at"]
        try:
            if "+" in exp_str or exp_str.count("-") > 2:
                exp_dt = datetime.datetime.fromisoformat(exp_str)
            else:
                exp_dt = datetime.datetime.fromisoformat(exp_str).replace(
                    tzinfo=datetime.timezone.utc)
        except Exception:
            exp_dt = now
        if now > exp_dt:
            expired.append(r)
        else:
            active.append(r)

    col1, col2, col3 = st.columns(3)
    col1.metric("Pending Review", len(active))
    col2.metric("Timed Out", len(expired))
    with _conn() as c:
        done = c.execute(
            "SELECT COUNT(*) FROM review_queue WHERE status IN ('approved','rejected','escalated')"
        ).fetchone()[0]
    col3.metric("Decided", done)

    # Handle expired
    if expired:
        st.warning(f"{len(expired)} item(s) have timed out.")
        if st.button("Mark all timed-out as escalated"):
            for r in expired:
                db.update_review_decision(r["id"], "timed_out", reviewer_id)
                db.log_event(r["run_id"], "timed_out", {"field": r["field_name"]})
                db.update_state(r["run_id"], "escalated")
            st.success("Done.")
            st.rerun()

    if not active:
        st.info("No active items pending review." if not expired else "")
        st.stop()

    st.divider()

    for r in active:
        acc = db.get_account(r["account_id"])
        current = (acc.get(r["field_name"]) if acc else None) or "blank"
        exp_str = r["expires_at"][:19] if r["expires_at"] else "—"

        with st.expander(
            f"**{r['field_name']}** — {r['account_id']} "
            f"({acc.get('business_name','') if acc else ''}) "
            f"| confidence {r['confidence_score']:.2f}",
            expanded=True
        ):
            c1, c2 = st.columns(2)
            c1.markdown(f"**Field:** `{r['field_name']}`")
            c1.markdown(f"**Account:** {r['account_id']} — {acc.get('business_name','') if acc else ''}")
            c1.markdown(f"**Proposed value:** `{r['proposed_value']}`")
            c1.markdown(f"**Current value:** `{current}`")
            c2.markdown(f"**Source:** {r['source']}")
            c2.markdown(f"**Confidence:** `{r['confidence_score']:.2f}` _(threshold: {config.CONFIDENCE_THRESHOLD})_")
            c2.markdown(f"**Expires:** {exp_str} UTC")

            st.info(f"📄 **Evidence:** \"{r['evidence_excerpt']}\"")
            st.caption(f"Rule: confidence below threshold ({config.CONFIDENCE_THRESHOLD}); human review required")

            btn1, btn2, btn3 = st.columns(3)
            if btn1.button("✅ Approve", key=f"approve_{r['id']}"):
                db.write_field(r["account_id"], r["field_name"], r["proposed_value"])
                db.update_review_decision(r["id"], "approved", reviewer_id)
                db.log_event(r["run_id"], "approved",
                             {"field": r["field_name"], "reviewer": reviewer_id})
                # Finalize run state if no more pending
                pending_left = db.get_pending_reviews(r["run_id"])
                if not pending_left:
                    all_rev = db.get_all_reviews_for_run(r["run_id"])
                    if any(rv["status"] == "approved" for rv in all_rev):
                        db.update_state(r["run_id"], "approved")
                    else:
                        db.update_state(r["run_id"], "rejected")
                st.success(f"Approved — `{r['field_name']}` written.")
                st.rerun()

            if btn2.button("❌ Reject", key=f"reject_{r['id']}"):
                db.update_review_decision(r["id"], "rejected", reviewer_id)
                db.log_event(r["run_id"], "rejected", {"field": r["field_name"]})
                pending_left = db.get_pending_reviews(r["run_id"])
                if not pending_left:
                    all_rev = db.get_all_reviews_for_run(r["run_id"])
                    if any(rv["status"] == "approved" for rv in all_rev):
                        db.update_state(r["run_id"], "approved")
                    else:
                        db.update_state(r["run_id"], "rejected")
                st.warning(f"Rejected — `{r['field_name']}` discarded.")
                st.rerun()

            if btn3.button("⚠️ Escalate", key=f"escalate_{r['id']}"):
                db.update_review_decision(r["id"], "escalated", reviewer_id)
                db.update_state(r["run_id"], "escalated")
                st.error(f"Escalated.")
                st.rerun()


# ══════════════════════════════════════════════════════════════════════════
# PAGE 5 — RUN LOGS
# ══════════════════════════════════════════════════════════════════════════
elif page == "📋 Run Logs":
    st.title("📋 Run Logs")

    runs = get_all_runs()
    if not runs:
        st.info("No runs yet.")
        st.stop()

    import pandas as pd

    # Run selector
    run_options = {
        f"{r['run_id'][:8]}… — {r['account_id']} — {RUN_COLOR.get(r['state'],'⚪')} {r['state']} — {r['created_at'][:19]}": r["run_id"]
        for r in runs
    }
    selected_label = st.selectbox("Select run", list(run_options.keys()))
    selected_run_id = run_options[selected_label]

    run = db.get_run(selected_run_id)
    acc = db.get_account(run["account_id"])

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Account", run["account_id"])
    col2.metric("Stage", f"{run['stage']}/7")
    col3.metric("State", run["state"])
    col4.metric("Legal Locked", "Yes" if run["legal_locked"] else "No")

    st.divider()

    log = db.get_run_log(selected_run_id)
    if log:
        rows = []
        for e in log:
            detail = {}
            if e["detail"]:
                try:
                    detail = json.loads(e["detail"])
                except Exception:
                    detail = {"raw": e["detail"]}
            rows.append({
                "Timestamp": e["timestamp"][:19],
                "Event":     e["event"],
                "Detail":    json.dumps(detail) if detail else "",
            })
        df_log = pd.DataFrame(rows)

        # Color event types
        def color_event(val):
            if "failed" in val or "rejected" in val or "escalated" in val:
                return "🔴 " + val
            elif "approved" in val or "auto_applied" in val or "complete" in val:
                return "🟢 " + val
            elif "review" in val or "awaiting" in val:
                return "🟠 " + val
            elif "skipped" in val or "discarded" in val:
                return "⚫ " + val
            return "🔵 " + val

        df_log["Event"] = df_log["Event"].apply(color_event)
        st.dataframe(df_log, use_container_width=True, hide_index=True, height=500)
    else:
        st.info("No log events for this run.")

    st.divider()
    # Review queue for this run
    reviews = db.get_all_reviews_for_run(selected_run_id)
    if reviews:
        st.subheader("Review Queue for this Run")
        df_rev = pd.DataFrame(reviews)[
            ["field_name", "proposed_value", "confidence_score", "source", "status", "reviewer_id", "decided_at"]
        ]
        st.dataframe(df_rev, use_container_width=True, hide_index=True)
