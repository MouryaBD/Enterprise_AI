"""
Resume a failed or interrupted pipeline run from where it left off.
"""
import db
from pipeline import run_pipeline


def resume_run(run_id: str):
    run = db.get_run(run_id)
    if run is None:
        raise ValueError(f"Run {run_id} not found")

    next_stage = run["stage"] + 1
    db.log_event(run_id, "resumed", {"from_stage": next_stage})
    db.update_state(run_id, "running")
    run_pipeline(run["account_id"], run_id, start_stage=next_stage)


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: python resume.py <run_id>")
        sys.exit(1)
    resume_run(sys.argv[1])
