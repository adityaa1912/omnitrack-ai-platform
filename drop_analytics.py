import sqlite3

DB = "inference_data.db"
TABLES = (
    "analytics_summaries",
    "zone_occupancy",
    "line_crossings",
    "trajectory_snapshots",
)

conn = sqlite3.connect(DB)
cur = conn.cursor()
for tbl in TABLES:
    cur.execute(f"DROP TABLE IF EXISTS {tbl}")
conn.commit()
cur.execute("SELECT version_num FROM alembic_version")
print("alembic_version after drop:", cur.fetchone())
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
print("Tables now:", [r[0] for r in cur.fetchall()])
conn.close()
