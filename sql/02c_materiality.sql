-- =====================================================================
-- 02c_materiality.sql  (DuckDB)
-- "If you don't handle X, this metric is wrong by roughly Y" -- a number
-- for every data-quality issue. Each query deliberately re-introduces one
-- defect and reports what the headline metric becomes.
-- Reads the raw CSV (most defects no longer exist downstream, which is the
-- point) plus the clean table. Each query is preceded by a short title header.
-- =====================================================================

CREATE OR REPLACE VIEW tickets AS
    SELECT * FROM read_parquet('data/clean/tickets_clean.parquet');

CREATE OR REPLACE VIEW raw AS
    SELECT * FROM read_csv_auto('data/raw/support_tickets.csv', all_varchar = true);


-- # Duplicates
-- Volume is the metric at risk.
SELECT
    (SELECT COUNT(*) FROM raw)                             AS volume_if_unhandled,
    (SELECT COUNT(*) FROM tickets)                         AS volume_correct,
    (SELECT COUNT(*) FROM raw) - (SELECT COUNT(*) FROM tickets)  AS overstated_by,
    ROUND(100.0 * ((SELECT COUNT(*) FROM raw) - (SELECT COUNT(*) FROM tickets))
                / (SELECT COUNT(*) FROM tickets), 2)       AS overstated_pct;


-- # Date format misparsed
-- 1,023 rows are DD/MM/YYYY. In 620 the first number is >12, which can only
-- be a day; the rest are inferred from those. This shows what parsing them as
-- MM/DD/YYYY would cost.
WITH mdy AS (
    SELECT
        try_strptime(created_at, '%Y-%m-%d %H:%M:%S')      AS iso_created,
        try_strptime(created_at, '%m/%d/%Y %H:%M')         AS mdy_created,
        try_strptime(resolved_at, '%Y-%m-%d %H:%M:%S')     AS resolved,
        created_at                                          AS raw_created
    FROM (SELECT DISTINCT * FROM raw)
),
scored AS (
    SELECT date_diff('minute', COALESCE(iso_created, mdy_created), resolved) / 60.0 AS hrs
    FROM mdy
    WHERE resolved IS NOT NULL AND COALESCE(iso_created, mdy_created) IS NOT NULL
)
SELECT
    (SELECT COUNT(*) FROM mdy WHERE raw_created LIKE '__/__/____%'
        AND mdy_created IS NULL)                            AS rows_lost_entirely,
    (SELECT COUNT(*) FROM mdy WHERE raw_created LIKE '__/__/____%'
        AND mdy_created IS NOT NULL)                        AS rows_silently_misparsed,
    ROUND((SELECT QUANTILE_CONT(hrs, 0.9) FROM scored WHERE hrs >= 0), 2) AS p90_if_unhandled,
    ROUND((SELECT QUANTILE_CONT(resolution_hours, 0.9)
           FROM tickets WHERE is_resolved AND NOT is_negative_duration), 2) AS p90_correct,
    (SELECT COUNT(*) FROM scored WHERE hrs < 0)             AS negatives_manufactured,
    (SELECT COUNT(*) FROM tickets WHERE is_negative_duration) AS negatives_real;


-- # Date format rows dropped
-- The lazy "just skip what won't parse" path. The speed metrics barely move;
-- the real damage is to volume -- worth knowing which metric an issue threatens.
SELECT
    COUNT(*)                                                          AS volume_if_dropped,
    (SELECT COUNT(*) FROM tickets) - COUNT(*)                         AS tickets_lost,
    ROUND(100.0 * ((SELECT COUNT(*) FROM tickets) - COUNT(*))
                / (SELECT COUNT(*) FROM tickets), 1)                  AS pct_of_dataset_lost,
    ROUND(MEDIAN(resolution_hours)
          FILTER (WHERE is_resolved AND NOT is_negative_duration), 2)  AS median_if_dropped,
    ROUND(QUANTILE_CONT(resolution_hours, 0.9)
          FILTER (WHERE is_resolved AND NOT is_negative_duration), 2)  AS p90_if_dropped
FROM tickets
WHERE ticket_id NOT IN (
    SELECT ticket_id FROM raw WHERE created_at LIKE '__/__/____%'
);


-- # Mean vs median
-- 12 tickets out of 5,001 are enough to quadruple the answer.
SELECT
    ROUND(AVG(resolution_hours), 1)                                   AS mean_hours,
    ROUND(MEDIAN(resolution_hours), 1)                                AS median_hours,
    ROUND(AVG(resolution_hours) / MEDIAN(resolution_hours), 1)        AS mean_is_x_times_median,
    COUNT(*) FILTER (WHERE is_extreme_duration)                       AS outliers_responsible
FROM tickets
WHERE is_resolved AND NOT is_negative_duration;


-- # Missing satisfaction as bad score
-- A common mistake: counting unsurveyed tickets into the denominator.
SELECT
    ROUND(100.0 * COUNT(*) FILTER (WHERE is_bottom_box)
                / NULLIF(COUNT(*) FILTER (WHERE has_satisfaction), 0), 1) AS low_score_correct,
    ROUND(100.0 * COUNT(*) FILTER (WHERE is_bottom_box OR
              (is_resolved AND NOT has_satisfaction))
                / NULLIF(COUNT(*) FILTER (WHERE is_resolved), 0), 1)      AS low_score_if_missing_counted_bad,
    COUNT(*) FILTER (WHERE is_resolved AND NOT has_satisfaction)          AS unsurveyed_resolved
FROM tickets;


-- # Category and channel not normalised
-- Every per-segment number fragments across casing variants.
SELECT
    (SELECT COUNT(DISTINCT category) FROM raw)      AS category_values_if_unhandled,
    (SELECT COUNT(DISTINCT category) FROM tickets)  AS category_values_correct,
    (SELECT COUNT(DISTINCT channel)  FROM raw)      AS channel_values_if_unhandled,
    (SELECT COUNT(DISTINCT channel)  FROM tickets)  AS channel_values_correct,
    (SELECT COUNT(*) FROM raw WHERE trim(category) = 'Bill')  AS bill_rows_orphaned;


-- # Survivorship
-- Excluding open tickets from the speed question. Same numerator, two denominators.
SELECT
    ROUND(100.0 * COUNT(*) FILTER (WHERE is_resolved AND NOT is_negative_duration
                                     AND resolution_hours <= 24)
                / NULLIF(COUNT(*) FILTER (WHERE is_resolved), 0), 1)  AS pct_24h_if_unhandled,
    ROUND(100.0 * COUNT(*) FILTER (WHERE is_resolved AND NOT is_negative_duration
                                     AND resolution_hours <= 24) / COUNT(*), 1) AS pct_24h_correct,
    COUNT(*) FILTER (WHERE NOT is_resolved)                            AS tickets_hidden_by_the_filter,
    ROUND(MEDIAN(open_age_days) FILTER (WHERE NOT is_resolved))        AS median_age_of_the_hidden
FROM tickets;


-- # Bill to Billing evidence
-- Billing was the only undersized category; merging 'Bill' into it closes the
-- gap to the other four. All figures on deduped rows so they are like-for-like.
WITH dedup AS (SELECT DISTINCT * FROM raw)
SELECT
    (SELECT COUNT(*) FROM dedup WHERE lower(trim(category)) = 'billing') AS billing_without_bill,
    (SELECT COUNT(*) FROM dedup WHERE trim(category) = 'Bill')           AS bill_rows,
    (SELECT COUNT(*) FROM tickets WHERE category = 'Billing')           AS billing_after_merge,
    (SELECT ROUND(AVG(n)) FROM (
        SELECT COUNT(*) AS n FROM tickets WHERE category <> 'Billing' GROUP BY category
     ))                                                                 AS avg_of_other_4_categories;


-- # Pre-hire tickets diagnosed
-- The pre-hire tickets belong to a handful of agents, all hired mid-period --
-- the signature of reassignment to new joiners, not random corruption.
SELECT
    COUNT(*)                                          AS pre_hire_tickets,
    COUNT(DISTINCT agent_id)                          AS agents_affected,
    (SELECT COUNT(*) FROM read_csv_auto('data/raw/agents.csv')) AS agents_total,
    ROUND(AVG(date_diff('day', created_at, hire_date)), 1)      AS avg_days_before_hire,
    MAX(date_diff('day', created_at, hire_date))                AS max_days_before_hire,
    MIN(hire_date)                                    AS earliest_hire_date_affected
FROM tickets
WHERE created_before_hire;
