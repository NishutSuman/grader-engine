import csv
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = BASE_DIR / "codejudge_raw.db"

if DB_PATH.exists():
    DB_PATH.unlink()

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

for csv_path in sorted(DATA_DIR.glob("*.csv")):
    table_name = csv_path.stem
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        headers = next(reader)
        safe_cols = [h.strip() for h in headers]
        col_defs = ", ".join([f'"{col}" TEXT' for col in safe_cols])
        cur.execute(f'DROP TABLE IF EXISTS "{table_name}"')
        cur.execute(f'CREATE TABLE "{table_name}" ({col_defs})')
        placeholders = ", ".join(["?"] * len(safe_cols))
        insert_sql = f'INSERT INTO "{table_name}" ({", ".join([f"\"{c}\"" for c in safe_cols])}) VALUES ({placeholders})'
        row_count = 0
        for row in reader:
            cur.execute(insert_sql, row)
            row_count += 1
        print(f"Imported {row_count:5d} rows into {table_name}")

conn.commit()
conn.close()
print(f"\nCreated raw SQLite database: {DB_PATH}")
