"""
Interactive CLI for reviewing low-confidence enrichment fields.
Run as: python reviewer_cli.py [--reviewer-id name]
"""
import argparse
from datetime import datetime, timezone

import db


def main():
    parser = argparse.ArgumentParser(description="Review pending enrichment fields")
    parser.add_argument("--reviewer-id", default="analyst_01", help="Reviewer identifier")
    args = parser.parse_args()
    reviewer_id = args.reviewer_id

    reviews = db.get_all_pending_reviews()

    if not reviews:
        print("No items pending review.")
        return

    seen_run_ids = set()

    for item in reviews:
        run_id = item["run_id"]
        seen_run_ids.add(run_id)

        now = datetime.now(timezone.utc)
        expires_at_str = item["expires_at"]
        # Parse ISO format
        try:
            # Handle both with and without timezone info
            if expires_at_str.endswith("Z"):
                expires_dt = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
            elif "+" in expires_at_str or expires_at_str.count("-") > 2:
                expires_dt = datetime.fromisoformat(expires_at_str)
            else:
                expires_dt = datetime.fromisoformat(expires_at_str).replace(tzinfo=timezone.utc)
        except Exception:
            expires_dt = now  # Treat as expired if we can't parse

        if now > expires_dt:
            db.update_review_decision(item["id"], "timed_out", reviewer_id)
            db.log_event(run_id, "timed_out", {"field": item["field_name"]})
            db.update_state(run_id, "escalated")
            print(f"[TIMED OUT] {item['account_id']} — {item['field_name']} — expired {expires_at_str}")
            continue

        account = db.get_account(item["account_id"])
        current_val = account.get(item["field_name"]) if account else None
        current = current_val if current_val else "blank"

        # Format expires for display
        try:
            display_expires = expires_dt.strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            display_expires = expires_at_str

        try:
            from rich.panel import Panel
            from rich.console import Console
            from rich import print as rprint

            console = Console()
            panel_text = (
                f"[bold]FIELD:[/bold]       {item['field_name']}\n"
                f"[bold]ACCOUNT:[/bold]     {item['account_id']} — {account.get('business_name', 'Unknown') if account else 'Unknown'}\n"
                f"[bold]PROPOSED:[/bold]    {item['proposed_value']}\n"
                f"[bold]SOURCE:[/bold]      {item['source']}\n"
                f"[bold]CONFIDENCE:[/bold]  {item['confidence_score']}\n"
                f"[bold]EVIDENCE:[/bold]    \"{item['evidence_excerpt']}\"\n"
                f"[bold]CURRENT:[/bold]     {current}\n"
                f"[bold]RULE:[/bold]        Confidence below threshold (0.80); human review required\n"
                f"[bold]EXPIRES:[/bold]     {display_expires}"
            )
            console.print(Panel(panel_text, title="Review Item"))
        except ImportError:
            print(f"\n--- Review Item ---")
            print(f"FIELD:       {item['field_name']}")
            print(f"ACCOUNT:     {item['account_id']} — {account.get('business_name', 'Unknown') if account else 'Unknown'}")
            print(f"PROPOSED:    {item['proposed_value']}")
            print(f"SOURCE:      {item['source']}")
            print(f"CONFIDENCE:  {item['confidence_score']}")
            print(f"EVIDENCE:    \"{item['evidence_excerpt']}\"")
            print(f"CURRENT:     {current}")
            print(f"RULE:        Confidence below threshold (0.80); human review required")
            print(f"EXPIRES:     {display_expires}")

        choice = input("[a] Approve  [r] Reject  [e] Escalate  [s] Skip: ").strip().lower()

        if choice == "a":
            db.write_field(item["account_id"], item["field_name"], item["proposed_value"])
            db.update_review_decision(item["id"], "approved", reviewer_id)
            db.log_event(run_id, "approved", {"field": item["field_name"], "reviewer": reviewer_id})
        elif choice == "r":
            db.update_review_decision(item["id"], "rejected", reviewer_id)
            db.log_event(run_id, "rejected", {"field": item["field_name"]})
        elif choice == "e":
            db.update_review_decision(item["id"], "escalated", reviewer_id)
            db.update_state(run_id, "escalated")
        elif choice == "s":
            pass

    # After all items: finalize run states
    for run_id in seen_run_ids:
        pending = db.get_pending_reviews(run_id)
        all_reviews = db.get_all_reviews_for_run(run_id)
        if not pending:
            if any(r["status"] == "approved" for r in all_reviews):
                db.update_state(run_id, "approved")
            else:
                db.update_state(run_id, "rejected")


if __name__ == "__main__":
    main()
