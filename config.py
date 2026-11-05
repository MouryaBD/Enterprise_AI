DB_PATH = "enrichment.db"
CONFIDENCE_THRESHOLD = 0.80
MAX_RETRIES = 3
CALL_TIMEOUT = 30
REVIEW_EXPIRY_HOURS = 24
MODEL = "claude-haiku-4-5-20251001"
LEGAL_FIELDS = ["entity_type", "date_established"]
ALL_FIELDS = ["business_name","phone","email","website","address","city",
              "state","zip","entity_type","date_established","principal_name",
              "principal_title","num_employees","business_description"]
