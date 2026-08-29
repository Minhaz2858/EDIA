import os
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text


BASE_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = BASE_DIR / "data"
INTERNAL_DIR = DATA_DIR / "internal"
EXTERNAL_DIR = DATA_DIR / "external"
NEW_ERP_DIR = DATA_DIR / "new_erp"


def _get_engine(mysql_url: str | None = None):
    if mysql_url is None:
        mysql_url = os.getenv("DATABASE_URL")
    if not mysql_url:
        raise RuntimeError("DATABASE_URL is not set; cannot connect to MySQL.")
    print(f"[INFO] Using MySQL URL: {mysql_url}")
    return create_engine(mysql_url, pool_pre_ping=True)


def ingest_internal_csv(engine) -> None:
    """Ingest all CSVs from data/internal into MySQL tables.

    Each file becomes a table named after the file stem, e.g.:
      erp_v_contract.csv -> table `erp_v_contract`
    """

    if not INTERNAL_DIR.exists():
        print(f"[WARN] Internal CSV dir not found: {INTERNAL_DIR}")
        return

    for path in sorted(INTERNAL_DIR.glob("*.csv")):
        table_name = path.stem.lower()
        print(f"[INGEST] {path.name} -> table `{table_name}`")
        df = pd.read_csv(path)
        df.to_sql(table_name, engine, if_exists="replace", index=False)

        with engine.connect() as conn:
            count = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()
        print(f"  -> {count} rows in `{table_name}`")


def ingest_external_csv(engine) -> None:
    """Ingest external CSVs into MySQL tables.

    Currently supports:
    - 裂解C5.csv      -> table `裂解C5`
    - 异戊二烯.csv    -> table `异戊二烯`
    - SIS.csv         -> table `SIS`
    - 丁二烯 SBS.csv  -> table `丁二烯_SBS`
    - 苯乙烯.csv      -> table `苯乙烯`
    - 间戊二烯.csv    -> table `间戊二烯`
    - 双环戊二烯.csv  -> table `双环戊二烯`
    - 发泡剂.csv      -> table `发泡剂`
    - c5_c9.csv       -> table `c5_c9`
    """

    # One table per sheet CSV
    sheet_files = [
        "裂解C5.csv",
        "异戊二烯.csv",
        "SIS.csv",
        "丁二烯 SBS.csv",
        "苯乙烯.csv",
        "间戊二烯.csv",
        "双环戊二烯.csv",
        "发泡剂.csv",
    ]

    for fname in sheet_files:
        path = EXTERNAL_DIR / fname
        if not path.exists():
            print(f"[WARN] External CSV not found: {path}")
            continue

        # Use file stem as table name, replace spaces with underscore
        table_name = path.stem.replace(" ", "_")
        print(f"[INGEST] {path.name} -> table `{table_name}`")
        df = pd.read_csv(path)
        df.to_sql(table_name, engine, if_exists="replace", index=False)

        with engine.connect() as conn:
            count = conn.execute(text(f"SELECT COUNT(*) FROM `{table_name}`")).scalar()
        print(f"  -> {count} rows in `{table_name}`")

    # c5_c9.csv -> c5_c9
    c5_c9_csv = EXTERNAL_DIR / "c5_c9.csv"
    if c5_c9_csv.exists():
        table_name = "c5_c9"
        print(f"[INGEST] {c5_c9_csv.name} -> table `{table_name}`")
        df = pd.read_csv(c5_c9_csv)
        df.to_sql(table_name, engine, if_exists="replace", index=False)

        with engine.connect() as conn:
            count = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()
        print(f"  -> {count} rows in `{table_name}`")
    else:
        print(f"[WARN] External CSV not found: {c5_c9_csv}")


def ingest_new_erp_csv(engine) -> None:
    """Ingest all CSVs from data/new_erp into MySQL tables.

    Each file becomes a table named after the file stem, e.g.:
      erp_t_sal_orderentry.csv -> table `erp_t_sal_orderentry`
    """

    if not NEW_ERP_DIR.exists():
        print(f"[WARN] new_erp CSV dir not found: {NEW_ERP_DIR}")
        return

    for path in sorted(NEW_ERP_DIR.glob("*.csv")):
        table_name = path.stem.lower()
        print(f"[INGEST] {path.name} -> table `{table_name}`")
        df = pd.read_csv(path)
        df.to_sql(table_name, engine, if_exists="replace", index=False)

        with engine.connect() as conn:
            count = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()
        print(f"  -> {count} rows in `{table_name}`")


def ingest_all(mysql_url: str | None = None) -> None:
    """Full ingestion entrypoint from CSVs into MySQL.

    - Reads all CSV files in data/internal
    - Reads all CSV files in data/new_erp
    - Reads DATA.csv in data/external (if present)
    """

    engine = _get_engine(mysql_url)
    ingest_internal_csv(engine)
    ingest_new_erp_csv(engine)
    ingest_external_csv(engine)


if __name__ == "__main__":
    ingest_all()
