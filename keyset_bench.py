"""Keyset vs OFFSET pagination benchmark on PostgreSQL.
Reproduce: point DSN at any scratch PostgreSQL 12+ database and run `python keyset_bench.py`.
"""
import json, os, statistics, psycopg2

DSN = os.environ.get("DSN", "host=/tmp port=54329 user=postgres dbname=postgres")
ROWS = 2_000_000
PAGE = 50
DEPTHS = [0, 1_000, 10_000, 100_000, 500_000, 1_000_000, 1_900_000]
RUNS = 7

c = psycopg2.connect(DSN); c.autocommit = True; cur = c.cursor()
cur.execute("SELECT version()"); version = cur.fetchone()[0]
cur.execute("DROP TABLE IF EXISTS orders")
cur.execute("""
CREATE TABLE orders (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  customer_id int         NOT NULL,
  created_at  timestamptz NOT NULL,
  total       numeric(10,2) NOT NULL
)""")
# created_at has deliberate ties: one timestamp per ~4 rows
cur.execute(f"""
INSERT INTO orders (customer_id, created_at, total)
SELECT (random()*50000)::int,
       timestamptz '2024-01-01' + (g/4) * interval '15 seconds',
       round((random()*500)::numeric, 2)
FROM generate_series(1, {ROWS}) g""")
cur.execute("CREATE INDEX orders_created_id ON orders (created_at, id)")
cur.execute("VACUUM ANALYZE orders")

def exec_ms(sql, params=()):
    cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, params)
    plan = cur.fetchone()[0][0]
    return plan["Execution Time"], plan["Plan"].get("Shared Hit Blocks", 0) + plan["Plan"].get("Shared Read Blocks", 0)

results = []
for depth in DEPTHS:
    off_sql = f"SELECT id, created_at, total FROM orders ORDER BY created_at, id LIMIT {PAGE} OFFSET {depth}"
    # cursor values = last row of the previous page, i.e. row number `depth`
    if depth == 0:
        key_sql, params = f"SELECT id, created_at, total FROM orders ORDER BY created_at, id LIMIT {PAGE}", ()
    else:
        cur.execute(f"SELECT created_at, id FROM orders ORDER BY created_at, id OFFSET {depth-1} LIMIT 1")
        params = cur.fetchone()
        key_sql = (f"SELECT id, created_at, total FROM orders WHERE (created_at, id) > (%s, %s) "
                   f"ORDER BY created_at, id LIMIT {PAGE}")
    # same rows?
    cur.execute(off_sql); a = cur.fetchall()
    cur.execute(key_sql, params); b = cur.fetchall()
    assert a == b, f"mismatch at depth {depth}"
    off = [exec_ms(off_sql) for _ in range(RUNS)]
    key = [exec_ms(key_sql, params) for _ in range(RUNS)]
    results.append({"offset_depth": depth,
                    "offset_ms_median": round(statistics.median(x[0] for x in off), 3),
                    "offset_buffers": off[-1][1],
                    "keyset_ms_median": round(statistics.median(x[0] for x in key), 3),
                    "keyset_buffers": key[-1][1]})
    print(results[-1])

# Correctness demo 1: tie-breaker. Filtering on created_at alone loses rows that share the boundary timestamp.
cur.execute(f"SELECT created_at, id FROM orders ORDER BY created_at, id OFFSET {PAGE*3-1} LIMIT 1")
last_ts, last_id = cur.fetchone()
cur.execute("SELECT count(*) FROM orders WHERE created_at = %s AND id > %s", (last_ts, last_id))
lost_by_ts_only = cur.fetchone()[0]

# Correctness demo 2: a new row inserted at the front between page requests.
cur.execute(f"SELECT id FROM orders ORDER BY created_at, id LIMIT {PAGE}")
page1 = [r[0] for r in cur.fetchall()]
cur.execute("INSERT INTO orders (customer_id, created_at, total) VALUES (1, timestamptz '2023-12-31', 9.99)")
cur.execute(f"SELECT id FROM orders ORDER BY created_at, id LIMIT {PAGE} OFFSET {PAGE}")
page2_offset = [r[0] for r in cur.fetchall()]
dupes_offset = len(set(page1) & set(page2_offset))
cur.execute("SELECT created_at, id FROM orders WHERE id = %s", (page1[-1],))
k = cur.fetchone()
cur.execute(f"SELECT id FROM orders WHERE (created_at, id) > (%s, %s) ORDER BY created_at, id LIMIT {PAGE}", k)
page2_keyset = [r[0] for r in cur.fetchall()]
dupes_keyset = len(set(page1) & set(page2_keyset))

# Plan text for the deepest page
cur.execute(f"EXPLAIN (ANALYZE, BUFFERS) SELECT id, created_at, total FROM orders ORDER BY created_at, id LIMIT {PAGE} OFFSET 1900000")
plan_offset = "\n".join(r[0] for r in cur.fetchall())
cur.execute(f"SELECT created_at, id FROM orders ORDER BY created_at, id OFFSET 1899999 LIMIT 1"); p = cur.fetchone()
cur.execute(f"EXPLAIN (ANALYZE, BUFFERS) SELECT id, created_at, total FROM orders WHERE (created_at, id) > (%s, %s) ORDER BY created_at, id LIMIT {PAGE}", p)
plan_keyset = "\n".join(r[0] for r in cur.fetchall())

json.dump({"version": version, "rows": ROWS, "page": PAGE, "runs": RUNS, "results": results,
           "rows_lost_if_cursor_uses_created_at_only": lost_by_ts_only,
           "duplicate_ids_offset_after_insert": dupes_offset,
           "duplicate_ids_keyset_after_insert": dupes_keyset,
           "plan_offset": plan_offset, "plan_keyset": plan_keyset},
          open("keyset_results.json", "w"), indent=2, default=str)
print("lost(ts only):", lost_by_ts_only, "dupes offset:", dupes_offset, "dupes keyset:", dupes_keyset)
print(plan_offset); print(plan_keyset)

# Extra check used in the article: the expanded OR form is applied as a Filter, not an Index Cond.
# cur.execute("EXPLAIN (ANALYZE, BUFFERS) SELECT id, created_at, total FROM orders "
#             "WHERE created_at > %s OR (created_at = %s AND id > %s) ORDER BY created_at, id LIMIT 50", (ts, ts, id_))
