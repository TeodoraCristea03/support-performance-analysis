"""
clean.py — build the clean ticket table and print a data-quality report.

Runs sql/01_stage_clean.sql against DuckDB and writes the result to
data/clean/ (parquet + csv). All cleaning logic lives in the .sql file;
this script only executes it and prints the before/after report.
"""

from pathlib import Path
import os
import duckdb

# Resolve project root (parent of src/) and run from there so the
# relative data/ paths inside the .sql file always resolve.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.chdir(PROJECT_ROOT)

SQL_FILE = PROJECT_ROOT / "sql" / "01_stage_clean.sql"
OUT_DIR = PROJECT_ROOT / "data" / "clean"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def rule(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def main() -> None:
    con = duckdb.connect()

    # --- raw snapshot (before) -- all figures come from SQL ----------------
    con.execute(
        "CREATE TABLE raw AS "
        "SELECT * FROM read_csv_auto('data/raw/support_tickets.csv', all_varchar=true)"
    )
    raw_rows = con.sql("SELECT COUNT(*) FROM raw").fetchone()[0]
    raw_distinct = con.sql("SELECT COUNT(*) FROM (SELECT DISTINCT * FROM raw)").fetchone()[0]
    raw_channels = con.sql("SELECT COUNT(DISTINCT channel) FROM raw").fetchone()[0]
    raw_categories = con.sql("SELECT COUNT(DISTINCT category) FROM raw").fetchone()[0]

    # --- run the staging model (after) ------------------------------------
    stage_sql = SQL_FILE.read_text()
    con.execute(f"CREATE TABLE clean AS {stage_sql.rstrip().rstrip(';')}")

    clean_rows = con.sql("SELECT COUNT(*) FROM clean").fetchone()[0]
    clean_channels = con.sql("SELECT COUNT(DISTINCT channel) FROM clean").fetchone()[0]
    clean_categories = con.sql("SELECT COUNT(DISTINCT category) FROM clean").fetchone()[0]

    # --- report -----------------------------------------------------------
    rule("ROW COUNTS")
    print(f"raw rows ...................... {raw_rows}")
    print(f"raw distinct rows ............. {raw_distinct}  "
          f"(=> {raw_rows - raw_distinct} exact duplicates removed)")
    print(f"clean rows .................... {clean_rows}")

    rule("VALUE NORMALISATION (distinct values: before -> after)")
    print(f"channel ....................... {raw_channels} -> {clean_channels}")
    print(f"category ...................... {raw_categories} -> {clean_categories}")
    print("\nclean channel values:")
    print(con.sql("SELECT channel, COUNT(*) n FROM clean GROUP BY 1 ORDER BY 2 DESC").df().to_string(index=False))
    print("\nclean category values:")
    print(con.sql("SELECT category, COUNT(*) n FROM clean GROUP BY 1 ORDER BY 2 DESC").df().to_string(index=False))

    rule("DATA-QUALITY FLAGS (rows flagged / kept for handling downstream)")
    flags = con.sql("""
        SELECT
            SUM(CASE WHEN NOT is_resolved THEN 1 ELSE 0 END)         AS open_backlog,
            SUM(CASE WHEN is_negative_duration THEN 1 ELSE 0 END)    AS negative_duration,
            SUM(CASE WHEN is_extreme_duration THEN 1 ELSE 0 END)     AS extreme_gt_30d,
            SUM(CASE WHEN is_future_resolution THEN 1 ELSE 0 END)    AS resolved_after_extract,
            SUM(CASE WHEN created_before_hire THEN 1 ELSE 0 END)     AS created_before_hire,
            SUM(CASE WHEN missing_agent THEN 1 ELSE 0 END)           AS missing_agent,
            SUM(CASE WHEN is_id_conflict THEN 1 ELSE 0 END)          AS id_conflict_rows,
            SUM(CASE WHEN NOT has_satisfaction THEN 1 ELSE 0 END)    AS missing_satisfaction,
            SUM(CASE WHEN is_partial_month THEN 1 ELSE 0 END)        AS partial_month_jun25
        FROM clean
    """).df().T
    flags.columns = ["count"]
    print(flags.to_string())

    rule("PARSE / SANITY CHECKS")
    print("unparsed created_at:",
          con.sql("SELECT COUNT(*) FROM clean WHERE created_at IS NULL").fetchone()[0])
    print("(resolved_at blanks are legitimately NULL = unresolved, per README)")
    print("resolution_hours summary (resolved, positive, non-extreme):")
    print(con.sql("""
        SELECT ROUND(MEDIAN(resolution_hours),2) p50,
               ROUND(QUANTILE_CONT(resolution_hours,0.9),1) p90,
               ROUND(MIN(resolution_hours),2) min_h, ROUND(MAX(resolution_hours),1) max_h
        FROM clean
        WHERE is_resolved AND NOT is_negative_duration AND NOT is_extreme_duration
    """).df().to_string(index=False))

    print("\nage of the OPEN backlog as of the extract date:")
    print(con.sql("""
        SELECT COUNT(*) open_tickets,
               ROUND(MEDIAN(open_age_days)) median_age_days,
               MAX(open_age_days) max_age_days,
               SUM(CASE WHEN open_age_days >= 180 THEN 1 ELSE 0 END) older_than_180d
        FROM clean WHERE NOT is_resolved
    """).df().to_string(index=False))

    # --- write outputs ----------------------------------------------------
    pq = OUT_DIR / "tickets_clean.parquet"
    csv = OUT_DIR / "tickets_clean.csv"
    con.execute(f"COPY clean TO '{pq.as_posix()}' (FORMAT PARQUET)")
    con.execute(f"COPY clean TO '{csv.as_posix()}' (HEADER, DELIMITER ',')")

    rule("OUTPUTS WRITTEN")
    print(f"{pq.relative_to(PROJECT_ROOT)}")
    print(f"{csv.relative_to(PROJECT_ROOT)}")
    print(f"\nclean table: {clean_rows} rows x "
          f"{len(con.sql('SELECT * FROM clean LIMIT 0').columns)} columns")


if __name__ == "__main__":
    main()
