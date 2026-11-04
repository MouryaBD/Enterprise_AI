# Business Account Data Enrichment Pipeline

A multi-source enrichment pipeline for business account data. Academic AI systems project.

## Requirements

- Python 3.11+

```bash
pip install -r requirements.txt
```

## Setup

### 1. Create the database

```bash
python -c "from db import create_db; create_db()"
```

### 2. Import accounts

```bash
python import_accounts.py --file "Input example.xlsx"
```

## Testing

### 4. Run tests (no API key needed)

```bash
python -m pytest test_m3.py -v
```

All 7 tests use mock data and require no Anthropic API key.

## Live Enrichment

### 5. Set API key for live enrichment

```bash
export ANTHROPIC_API_KEY=your_key_here
```

### 6. Enrich all pending accounts

Insert rows into `pending_events` for each account, then run:

```bash
python poll_events.py
```

Or run the pipeline directly:

```python
from db import create_run, update_state
from pipeline import run_pipeline

run_id = create_run("ACCOUNT-ID")
update_state(run_id, "running")
run_pipeline("ACCOUNT-ID", run_id)
```

## Review Low-Confidence Fields

### 7. Launch the reviewer CLI

```bash
python reviewer_cli.py --reviewer-id your_name
```

For each field below the confidence threshold (0.80), you will be prompted to:
- `[a]` Approve — write the value to the account
- `[r]` Reject — discard the proposed value
- `[e]` Escalate — flag for further review
- `[s]` Skip — leave pending for later

## Resume a Failed Run

### 8. Resume from where it left off

```bash
python -c "from resume import resume_run; resume_run('<run_id>')"
```

## Pipeline Stages

| Stage | Description |
|-------|-------------|
| 1 | GA SOS legal resolution (entity_type, date_established) |
| 2 | Profile lookup (name, address, city, state, zip) |
| 3 | Website discovery and scoring |
| 4 | Page fetch (/, /about, /contact, /team) |
| 5 | Pass A: extract fields from website evidence |
| 6 | Pass B: extract remaining fields from SOS + profile |
| 7 | Web consensus for phone, email, address |

## Database Schema

Five tables in `enrichment.db`:
- `accounts` — account records with enriched fields
- `runs` — pipeline run state and metadata
- `run_log` — event log for each run
- `pending_events` — incoming enrichment requests
- `review_queue` — fields awaiting human review

## Confidence Threshold

Fields with confidence >= 0.80 are written automatically.
Fields below threshold are queued for human review (expires after 24 hours).
Legal fields (entity_type, date_established) from SOS are always written without a confidence check.
