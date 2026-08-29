import os
import sqlite3
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text


BASE_DIR = Path(__file__).resolve().parents[3]
SQLITE_DB_PATH = BASE_DIR / "data" / "edia_data.db"


def migrate(sqlite_path: Path | None = None, mysql_url: str | None = None) -> None:
    """Copy all tables from edia_data.db into a MySQL database.

    - Reads from the existing SQLite analytics DB (edia_data.db).
    - Writes each table into the MySQL database referenced by DATABASE_URL
      (or the provided mysql_url) using the same table names.
    """

    if sqlite_path is None:
        sqlite_path = SQLITE_DB_PATH

    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite analytics DB not found at {sqlite_path}")

    if mysql_url is None:
        mysql_url = os.getenv("DATABASE_URL")

    if not mysql_url:
        raise RuntimeError("DATABASE_URL is not set; cannot connect to MySQL.")

    print(f"[INFO] Reading from SQLite: {sqlite_path}")
    print(f"[INFO] Writing to MySQL: {mysql_url}")

    # Connect to SQLite
    sqlite_conn = sqlite3.connect(str(sqlite_path))

    # Connect to MySQL via SQLAlchemy
    engine = create_engine(mysql_url, pool_pre_ping=True)

    try:
        cur = sqlite_conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
        tables = [r[0] for r in cur.fetchall()]
        print(f"[INFO] Found {len(tables)} tables in SQLite.")

        for table in tables:
            print(f"[MIGRATE] Table `{table}` ...", end="", flush=True)
            df = pd.read_sql_query(f"SELECT * FROM {table}", sqlite_conn)
            # Write into MySQL, replacing if already exists
            df.to_sql(table, engine, if_exists="replace", index=False)

            with engine.connect() as conn:
                count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            print(f" done ({len(df)} rows, MySQL count={count}).")

    finally:
        sqlite_conn.close()


if __name__ == "__main__":
    migrate()
