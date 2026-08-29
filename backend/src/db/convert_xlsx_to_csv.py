import os
from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = BASE_DIR / "data"
INTERNAL_DIR = DATA_DIR / "internal"
EXTERNAL_DIR = DATA_DIR / "external"
NEW_ERP_DIR = DATA_DIR / "new_erp"


def convert_internal_xlsx_to_csv() -> None:
    """Convert all .xlsx files in data/internal to .csv with same stem.

    Example:
      erp_v_contract.xlsx -> erp_v_contract.csv
    """

    if not INTERNAL_DIR.exists():
        print(f"[WARN] Internal XLSX dir not found: {INTERNAL_DIR}")
        return

    for path in sorted(INTERNAL_DIR.glob("*.xlsx")):
        out_path = path.with_suffix(".csv")
        print(f"[CONVERT] {path.name} -> {out_path.name}")
        df = pd.read_excel(path)
        df.to_csv(out_path, index=False)


def convert_external_xlsx_to_csv() -> None:
    """Convert each sheet in DATA3.xlsx into its own CSV file.

    Example:
      Sheet "裂解C5"      -> 裂解C5.csv
      Sheet "异戊二烯"    -> 异戊二烯.csv
    """

    preferred_path = DATA_DIR / "DATA3.xlsx"
    legacy_path = EXTERNAL_DIR / "DATA3.xlsx"
    xlsx_path = preferred_path if preferred_path.exists() else legacy_path
    if not xlsx_path.exists():
        print(f"[WARN] External XLSX not found: {preferred_path} or {legacy_path}")
        return

    excel_file = pd.ExcelFile(xlsx_path)
    for sheet_name in excel_file.sheet_names:
        df = excel_file.parse(sheet_name)

        # Use the sheet name as the CSV filename; replace path separators
        safe_name = sheet_name.replace("/", "_") + ".csv"
        out_path = EXTERNAL_DIR / safe_name

        print(f"[CONVERT] {xlsx_path.name}:{sheet_name} -> {out_path.name}")
        df.to_csv(out_path, index=False)


def convert_new_erp_xlsx_to_csv() -> None:
    """Convert all .xlsx files in data/new_erp to .csv with same stem.

    Example:
      erp_t_sal_orderentry.xlsx -> erp_t_sal_orderentry.csv

    These CSVs can then be ingested into MySQL using the same
    ingest_csv_to_mysql script by adding them under data/internal
    or by extending the ingestion script if needed.
    """

    if not NEW_ERP_DIR.exists():
        print(f"[WARN] new_erp XLSX dir not found: {NEW_ERP_DIR}")
        return

    for path in sorted(NEW_ERP_DIR.glob("*.xlsx")):
        out_path = path.with_suffix(".csv")
        print(f"[CONVERT] {path.name} -> {out_path.name}")
        df = pd.read_excel(path)
        df.to_csv(out_path, index=False)


def convert_all() -> None:
    convert_internal_xlsx_to_csv()
    convert_external_xlsx_to_csv()
    convert_new_erp_xlsx_to_csv()


if __name__ == "__main__":
    convert_all()
