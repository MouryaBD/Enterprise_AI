"""
Import accounts from an Excel file into enrichment.db.
Run as: python import_accounts.py --file "Input example.xlsx"
"""
import argparse
import openpyxl

import db


def main():
    parser = argparse.ArgumentParser(description="Import accounts from xlsx")
    parser.add_argument("--file", required=True, help="Path to the xlsx file")
    args = parser.parse_args()

    db.create_db()

    # Count total data rows in xlsx to report how many already existed
    wb = openpyxl.load_workbook(args.file)
    ws = wb.active
    total_data_rows = sum(
        1 for row in ws.iter_rows(min_row=2) if any(cell.value for cell in row)
    )
    wb.close()

    inserted = db.import_accounts(args.file)
    skipped = total_data_rows - inserted

    print(f"Imported {inserted} accounts. {skipped} already existed.")


if __name__ == "__main__":
    main()
