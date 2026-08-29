import sqlite3
from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = BASE_DIR / "data"
INTERNAL_DIR = DATA_DIR / "internal"
EXTERNAL_DIR = DATA_DIR / "external"


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Create a SQLite connection to the EDIA structured data DB.

    By default this lives at data/edia_data.db so it is easy to inspect.
    """

    if db_path is None:
        db_path = DATA_DIR / "edia_data.db"

    db_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(db_path))


def ingest_internal_excels(conn: sqlite3.Connection) -> None:
    """Ingest all ERP-style .xlsx files from data/internal into SQLite tables.

    Each file becomes one table named after the file stem, e.g.:
      erp_v_contract.xlsx -> table `erp_v_contract`
    Columns are taken directly from the Excel headers.
    """

    if not INTERNAL_DIR.exists():
        print(f"[WARN] Internal data dir not found: {INTERNAL_DIR}")
        return

    for path in sorted(INTERNAL_DIR.glob("*.xlsx")):
        table_name = path.stem.lower()
        print(f"[INGEST] {path.name} -> table `{table_name}`")
        df = pd.read_excel(path)
        df.to_sql(table_name, conn, if_exists="replace", index=False)


def ingest_external_data_excel(conn: sqlite3.Connection) -> None:
    """Ingest DATA.xlsx (multi-sheet) into per-sheet tables.

    Each sheet becomes a table named `data_<sheetname_lower>`, e.g.:
      sheet 'C5' -> table `data_c5`
    """

    data_xlsx = EXTERNAL_DIR / "DATA.xlsx"
    if not data_xlsx.exists():
        print(f"[WARN] External data file not found: {data_xlsx}")
        return

    print(f"[INGEST] {data_xlsx.name} (all sheets)")
    sheets = pd.read_excel(data_xlsx, sheet_name=None)

    for sheet_name, df in sheets.items():
        table_name = f"data_{sheet_name.strip().lower()}".replace(" ", "_")
        print(f"  - sheet '{sheet_name}' -> table `{table_name}`")
        df.to_sql(table_name, conn, if_exists="replace", index=False)


def ingest_all(db_path: Path | None = None) -> None:
    """Full ingestion entrypoint for step 1 (data foundation).

    - Creates / updates data/edia_data.db
    - Loads all ERP .xlsx files under data/internal
    - Loads all sheets from data/external/DATA.xlsx
    """

    conn = get_connection(db_path)
    try:
        ingest_internal_excels(conn)
        ingest_external_data_excel(conn)
        print("[DONE] Ingestion complete.")
    finally:
        conn.close()


if __name__ == "__main__":
    ingest_all()
