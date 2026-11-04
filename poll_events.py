"""
Poll pending_events table and dispatch pipeline runs.
"""
import db
from pipeline import run_pipeline

# Track accounts that have been dispatched in this poll cycle
_dispatched_in_batch: set = set()


def poll_events():
    global _dispatched_in_batch
    _dispatched_in_batch = set()

    events = db.get_pending_events()

    for event in events:
        event_id = event["event_id"]
        account_id = event["account_id"]

        # Check both: (1) active run in DB, (2) already dispatched in this batch
        existing_run = db.check_duplicate_run(account_id)
        if not existing_run and account_id in _dispatched_in_batch:
            # Find the run created in this batch for this account
            existing_run = db.get_latest_run_for_account(account_id)

        if existing_run:
            db.log_event(existing_run, "duplicate_discarded", {"event_id": event_id})
            db.mark_event_processed(event_id)
            continue

        run_id = db.create_run(account_id)
        db.update_state(run_id, "running")
        _dispatched_in_batch.add(account_id)
        db.mark_event_processed(event_id)
        run_pipeline(account_id, run_id)


if __name__ == "__main__":
    poll_events()
