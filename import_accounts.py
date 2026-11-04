"""
Import accounts from an Excel file into enrichment.db.
Run as: python import_accounts.py --file "Input example.xlsx"
"""
import argparse
import sys

import db


def main():
    parser = argparse.ArgumentParser(description="Import accounts from xlsx")
    parser.add_argument("--file", required=True, help="Path to the xlsx file")
    args = parser.parse_args()

    db.create_db()

    conn_before = db._get_conn()
    before_count = conn_before.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    conn_before.close()

    inserted = db.import_accounts(args.file)

    conn_after = db._get_conn()
    after_count = conn_after.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    conn_after.close()

    already_existed = (after_count - before_count - inserted) + (before_count - 0)
    # Simpler: total rows that were skipped
    skipped = before_count

    print(f"Imported {inserted} accounts. {skipped} already existed.")


if __name__ == "__main__":
    main()
