import sqlite3

DB = "inference_data.db"
conn = sqlite3.connect(DB)
cur = conn.cursor()

print("alembic_version:", cur.execute("SELECT version_num FROM alembic_version").fetchall())

names = sorted(
    r[0]
    for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND (name LIKE 'analytic%' OR name LIKE 'zone%' "
        "OR name LIKE 'line%' OR name LIKE 'trajectory%')"
    ).fetchall()
)
print("analytics tables present:", names)
conn.close()
