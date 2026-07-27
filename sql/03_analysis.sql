-- =====================================================================
-- 03_analysis.sql  (DuckDB)
-- The analytical cuts. Descriptive stats only; the significance tests live
-- in src/analysis.py. Reads data/clean/tickets_clean.parquet.
--
-- The per-segment metrics are defined once: the comparison dimensions are
-- stacked into (dimension, segment) rows, the metric block is computed a
-- single time, and materialised as segment_metrics. Each cut then reads
-- from that table. Each query is preceded by a short title header the runners read.
-- =====================================================================

CREATE OR REPLACE VIEW tickets AS
    SELECT * FROM read_parquet('data/clean/tickets_clean.parquet');


-- Stack the comparison dimensions, carrying only the columns the metrics need.
CREATE OR REPLACE TABLE segment_metrics AS
WITH stacked AS (
    SELECT 'category' AS dimension, category AS segment,
           is_resolved, resolution_hours, is_negative_duration, has_satisfaction, is_bottom_box
    FROM tickets
    UNION ALL
    SELECT 'channel', channel,
           is_resolved, resolution_hours, is_negative_duration, has_satisfaction, is_bottom_box
    FROM tickets
    UNION ALL
    SELECT 'brand', brand,
           is_resolved, resolution_hours, is_negative_duration, has_satisfaction, is_bottom_box
    FROM tickets
    UNION ALL
    SELECT 'priority', priority,
           is_resolved, resolution_hours, is_negative_duration, has_satisfaction, is_bottom_box
    FROM tickets
    UNION ALL
    SELECT 'team', COALESCE(team, '(no agent/team)'),
           is_resolved, resolution_hours, is_negative_duration, has_satisfaction, is_bottom_box
    FROM tickets
)
SELECT
    dimension,
    segment,
    COUNT(*)                                                        AS volume,
    ROUND(100.0 * COUNT(*) FILTER (WHERE NOT is_resolved) / COUNT(*), 1) AS backlog_percentage,
    ROUND(MEDIAN(resolution_hours)
          FILTER (WHERE is_resolved AND NOT is_negative_duration), 2)    AS median_hrs,
    ROUND(QUANTILE_CONT(resolution_hours, 0.9)
          FILTER (WHERE is_resolved AND NOT is_negative_duration), 1)    AS p90_hrs,
    ROUND(100.0 * COUNT(*) FILTER (WHERE is_resolved AND has_satisfaction)
                / NULLIF(COUNT(*) FILTER (WHERE is_resolved), 0), 1)     AS response_rate_percentage,
    ROUND(100.0 * COUNT(*) FILTER (WHERE is_bottom_box)
                / NULLIF(COUNT(*) FILTER (WHERE has_satisfaction), 0), 1) AS low_score_percentage
FROM stacked
GROUP BY dimension, segment;


-- # By category
SELECT segment, volume, backlog_percentage, median_hrs, p90_hrs, response_rate_percentage, low_score_percentage
FROM segment_metrics WHERE dimension = 'category' ORDER BY volume DESC;

-- # By channel
SELECT segment, volume, backlog_percentage, median_hrs, p90_hrs, response_rate_percentage, low_score_percentage
FROM segment_metrics WHERE dimension = 'channel' ORDER BY volume DESC;

-- # By brand
SELECT segment, volume, backlog_percentage, median_hrs, p90_hrs, response_rate_percentage, low_score_percentage
FROM segment_metrics WHERE dimension = 'brand' ORDER BY volume DESC;

-- # By priority
-- Urgent is resolved no faster than Low: priority isn't changing handling.
SELECT segment, volume, backlog_percentage, median_hrs, p90_hrs, response_rate_percentage, low_score_percentage
FROM segment_metrics WHERE dimension = 'priority'
ORDER BY CASE segment WHEN 'Urgent' THEN 1 WHEN 'High' THEN 2
                      WHEN 'Medium' THEN 3 WHEN 'Low' THEN 4 END;

-- # By team
SELECT segment, volume, median_hrs, low_score_percentage
FROM segment_metrics WHERE dimension = 'team' ORDER BY volume DESC;


-- # Monthly trend
-- Excludes the partial June-2025 month.
SELECT
    date_trunc('month', created_at)                                AS month,
    COUNT(*)                                                       AS volume,
    ROUND(MEDIAN(resolution_hours)
          FILTER (WHERE is_resolved AND NOT is_negative_duration), 2)    AS median_hrs,
    ROUND(100.0 * COUNT(*) FILTER (WHERE is_bottom_box)
                / NULLIF(COUNT(*) FILTER (WHERE has_satisfaction), 0), 1) AS low_score_percentage
FROM tickets
WHERE NOT is_partial_month
GROUP BY 1
ORDER BY 1;


-- # Agent low-score spread
-- Spread of the per-agent low-score rate (agents with >=40 scored tickets).
-- Consistent with sampling noise; the formal test is in Python.
WITH per_agent AS (
    SELECT agent_id,
           COUNT(*) FILTER (WHERE has_satisfaction) AS n_scored,
           100.0 * COUNT(*) FILTER (WHERE is_bottom_box)
                 / NULLIF(COUNT(*) FILTER (WHERE has_satisfaction), 0) AS low_score_percentage
    FROM tickets
    WHERE agent_id IS NOT NULL
    GROUP BY agent_id
)
SELECT
    COUNT(*)                                    AS agents,
    ROUND(MIN(low_score_percentage), 1)                   AS min_percentage,
    ROUND(QUANTILE_CONT(low_score_percentage, 0.25), 1)   AS q25_percentage,
    ROUND(MEDIAN(low_score_percentage), 1)                AS median_percentage,
    ROUND(QUANTILE_CONT(low_score_percentage, 0.75), 1)   AS q75_percentage,
    ROUND(MAX(low_score_percentage), 1)                   AS max_percentage
FROM per_agent
WHERE n_scored >= 40;


-- # Resolution vs satisfaction correlation
-- Pearson correlation, overall and per priority. Near zero everywhere.
SELECT 'ALL' AS priority,
       COUNT(*)                                        AS n,
       ROUND(CORR(resolution_hours, satisfaction_score), 3) AS corr_res_sat
FROM tickets
WHERE is_resolved AND NOT is_negative_duration AND has_satisfaction
UNION ALL
SELECT priority,
       COUNT(*),
       ROUND(CORR(resolution_hours, satisfaction_score), 3)
FROM tickets
WHERE is_resolved AND NOT is_negative_duration AND has_satisfaction
GROUP BY priority
ORDER BY priority;


-- # Backlog age by priority
-- How long the OPEN work has been sitting, by priority. Urgent has waited as
-- long as Low, in months rather than the hours it takes to close a ticket.
SELECT
    priority,
    COUNT(*) FILTER (WHERE NOT is_resolved)                          AS open_tickets,
    ROUND(MEDIAN(open_age_days) FILTER (WHERE NOT is_resolved))      AS median_age_days,
    MAX(open_age_days) FILTER (WHERE NOT is_resolved)                AS max_age_days,
    COUNT(*) FILTER (WHERE NOT is_resolved AND open_age_days >= 180)  AS open_over_180d,
    ROUND(MEDIAN(resolution_hours)
          FILTER (WHERE is_resolved AND NOT is_negative_duration), 2) AS median_close_hours
FROM tickets
GROUP BY 1
ORDER BY CASE priority WHEN 'Urgent' THEN 1 WHEN 'High' THEN 2
                       WHEN 'Medium' THEN 3 WHEN 'Low' THEN 4 END;


-- # Backlog by creation month
-- Each monthly cohort leaves roughly the same residue open, so old work is
-- never revisited. (May-2025 sits higher partly because it is the newest.)
SELECT
    date_trunc('month', created_at)                                  AS month,
    COUNT(*)                                                         AS created,
    COUNT(*) FILTER (WHERE NOT is_resolved)                          AS still_open,
    ROUND(100.0 * COUNT(*) FILTER (WHERE NOT is_resolved) / COUNT(*), 1) AS pct_still_open
FROM tickets
WHERE NOT is_partial_month
GROUP BY 1
ORDER BY 1;


-- # Arrival pattern by weekday
-- Checked for a staffing lever. Real support traffic dips at the weekend;
-- this is perfectly flat, so there is no staffing lever to pull here.
SELECT
    dayofweek(created_at)                                            AS day_of_week,
    dayname(created_at)                                              AS day_name,
    COUNT(*)                                                         AS volume,
    ROUND(MEDIAN(resolution_hours)
          FILTER (WHERE is_resolved AND NOT is_negative_duration), 2) AS median_hrs
FROM tickets
GROUP BY 1, 2
ORDER BY 1;
