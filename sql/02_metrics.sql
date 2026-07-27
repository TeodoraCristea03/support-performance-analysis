-- =====================================================================
-- 02_metrics.sql  (DuckDB)
-- Headline metric definitions. Every number the stakeholder sees traces
-- back to one of these queries. Reads data/clean/tickets_clean.parquet.
--
-- Each query is preceded by a short title header the Python runners read.
-- =====================================================================

CREATE OR REPLACE VIEW tickets AS
    SELECT * FROM read_parquet('data/clean/tickets_clean.parquet');


-- # Headline KPIs
-- Whole-period summary, one row. Resolution time excludes the impossible
-- negatives; median/p90 are robust to the extremes and future-dated rows,
-- which stay in. Speed within 24h is over ALL tickets, not just resolved
-- ones, so it can't be flattered by the tickets that never closed.
SELECT
    COUNT(*)                                                       AS total_tickets,
    COUNT(*) FILTER (WHERE is_resolved)                            AS resolved_tickets,
    COUNT(*) FILTER (WHERE NOT is_resolved)                        AS open_tickets,
    ROUND(100.0 * COUNT(*) FILTER (WHERE NOT is_resolved)
                / COUNT(*), 1)                                     AS backlog_percentage,

    ROUND(MEDIAN(resolution_hours)
          FILTER (WHERE is_resolved AND NOT is_negative_duration), 2)         AS median_res_hours,
    ROUND(QUANTILE_CONT(resolution_hours, 0.9)
          FILTER (WHERE is_resolved AND NOT is_negative_duration), 1)         AS p90_res_hours,

    ROUND(100.0 * COUNT(*) FILTER (WHERE is_resolved AND NOT is_negative_duration
                                     AND resolution_hours <= 24)
                / COUNT(*), 1)                                                AS pct_all_resolved_24h,
    ROUND(100.0 * COUNT(*) FILTER (WHERE is_resolved AND NOT is_negative_duration
                                     AND resolution_hours <= 24)
                / NULLIF(COUNT(*) FILTER (WHERE is_resolved), 0), 1)          AS pct_resolved_only_24h,

    -- age of the open backlog as of the extract date
    ROUND(MEDIAN(open_age_days) FILTER (WHERE NOT is_resolved))                AS median_open_age_days,
    COUNT(*) FILTER (WHERE NOT is_resolved AND open_age_days >= 180)            AS open_over_180d,

    COUNT(*) FILTER (WHERE is_resolved AND has_satisfaction)       AS surveyed_tickets,
    ROUND(100.0 * COUNT(*) FILTER (WHERE is_resolved AND has_satisfaction)
                / NULLIF(COUNT(*) FILTER (WHERE is_resolved), 0), 1)          AS response_rate_percentage,

    -- % of scored tickets rated 1-2; diagnostic only, not reported as a KPI
    ROUND(100.0 * COUNT(*) FILTER (WHERE is_bottom_box)
                / NULLIF(COUNT(*) FILTER (WHERE has_satisfaction), 0), 1)     AS low_score_percentage
FROM tickets;


-- # Satisfaction scale evidence
-- Scores above 5 can only come from the 1-10 scale. Their share is stable
-- every month, so the two scales were never separated in time (no cutover).
-- Excludes the partial June-2025 month.
SELECT
    date_trunc('month', created_at)                               AS month,
    COUNT(*) FILTER (WHERE has_satisfaction)                      AS scored,
    MIN(satisfaction_score)                                       AS min_score,
    MAX(satisfaction_score)                                       AS max_score,
    ROUND(100.0 * COUNT(*) FILTER (WHERE satisfaction_score > 5)
                / NULLIF(COUNT(*) FILTER (WHERE has_satisfaction), 0), 1)     AS score_above_5_percentage
FROM tickets
WHERE NOT is_partial_month
GROUP BY 1
ORDER BY 1;


-- # Satisfaction score histogram
-- Bin counts for the histogram. The cliff from score 5 to 6 is the signature
-- of two mixed populations (a 1-5 group plus a 1-10 group).
SELECT
    satisfaction_score,
    COUNT(*) AS n
FROM tickets
WHERE has_satisfaction
GROUP BY 1
ORDER BY 1;


-- # Resolution time distribution
-- Bin counts for the resolution-time histogram. Valid resolved tickets only.
SELECT
    CASE
        WHEN resolution_hours < 2  THEN '0: <2h'
        WHEN resolution_hours < 4  THEN '1: 2-4h'
        WHEN resolution_hours < 8  THEN '2: 4-8h'
        WHEN resolution_hours < 16 THEN '3: 8-16h'
        WHEN resolution_hours < 24 THEN '4: 16-24h'
        WHEN resolution_hours < 72 THEN '5: 1-3d'
        ELSE '6: 3d+'
    END AS resolution_bucket,
    COUNT(*) AS n
FROM tickets
WHERE is_resolved AND NOT is_negative_duration
GROUP BY 1
ORDER BY 1;


-- # Satisfaction scale
-- The scales can't be split per ticket, but they can for the population.
-- Scores 6-10 only exist on the 1-10 scale, so their average height is that
-- group's per-score height (h); ten such scores give its total size.
-- Subtracting h from each of scores 1-5 leaves the 1-5 group on its own.
-- Both groups come out evenly spread, i.e. sitting on their midpoint.
WITH scored AS (
    SELECT satisfaction_score AS score, COUNT(*) AS observed
    FROM tickets WHERE has_satisfaction GROUP BY 1
),
model AS (
    SELECT
        (SELECT AVG(observed) FROM scored WHERE score > 5) AS h,
        (SELECT SUM(observed) FROM scored)                 AS total
)
SELECT
    s.score,
    s.observed,
    ROUND(m.h, 1)                                                  AS from_1_10_scale,
    CASE WHEN s.score <= 5
         THEN ROUND(s.observed - m.h, 1) END                       AS from_1_5_scale,
    ROUND(CASE WHEN s.score <= 5 THEN m.total / 5.0 - m.h
               ELSE m.h END, 1)                                    AS expected_if_random
FROM scored s CROSS JOIN model m
ORDER BY s.score;


-- # Satisfaction methodology swing
-- Nine defensible ways to answer "how satisfied are customers?" on the same
-- 3,071 scores. Only the scale assumption changes between rows, so the spread
-- between the smallest and largest answer is the cost of the unflagged scale
-- migration. Row J is the mixture-model answer: both scales on their midpoint
-- = 50% = no signal.
-- Keep no ';' or '--' inside these string literals: the runners split on ';'
-- and strip '--' comments, so either would truncate the statement.
WITH s AS (SELECT satisfaction_score AS x FROM tickets WHERE has_satisfaction)
SELECT * FROM (
    SELECT 'A' AS variant, 'Naive mean, reported as "out of 10"'      AS approach,
           ROUND(100.0 * AVG(x) / 10.0, 1) AS satisfaction_percentage FROM s
    UNION ALL SELECT 'B', 'Naive mean, reported as "out of 5"',
           ROUND(100.0 * AVG(x) / 5.0, 1) FROM s
    UNION ALL SELECT 'C', 'Normalise everything as 1-10: (x-1)/9',
           ROUND(100.0 * AVG((x - 1) / 9.0), 1) FROM s
    UNION ALL SELECT 'D', 'Normalise everything as 1-5: (x-1)/4',
           ROUND(100.0 * AVG((x - 1) / 4.0), 1) FROM s
    UNION ALL SELECT 'E', 'Rule: score >5 came from 1-10, else 1-5',
           ROUND(100.0 * AVG(CASE WHEN x > 5 THEN (x - 1) / 9.0
                                  ELSE (x - 1) / 4.0 END), 1) FROM s
    UNION ALL SELECT 'F', 'Top-box, scale-aware (4-5 or 8-10)',
           ROUND(100.0 * COUNT(*) FILTER (WHERE x IN (4,5,8,9,10)) / COUNT(*), 1) FROM s
    UNION ALL SELECT 'G', 'Top-box, naive (>=4)',
           ROUND(100.0 * COUNT(*) FILTER (WHERE x >= 4) / COUNT(*), 1) FROM s
    UNION ALL SELECT 'H', '100 - bottom-box(1-2)  [the old instrument]',
           ROUND(100.0 - 100.0 * COUNT(*) FILTER (WHERE x <= 2) / COUNT(*), 1) FROM s
    UNION ALL SELECT 'I', '100 - bottom(1-3)',
           ROUND(100.0 - 100.0 * COUNT(*) FILTER (WHERE x <= 3) / COUNT(*), 1) FROM s
    UNION ALL SELECT 'J', 'Mixture model: the defensible answer', 50.0
) ORDER BY variant;


-- # Backlog age profile
-- 11.6% open looks like a healthy queue; the age profile shows it isn't.
-- The median open ticket has been sitting ~169 days and nearly half are over
-- six months old, while tickets that do close take a median of ~6 hours.
SELECT
    CASE
        WHEN open_age_days < 7   THEN '0: <7 days'
        WHEN open_age_days < 30  THEN '1: 7-30 days'
        WHEN open_age_days < 90  THEN '2: 30-90 days'
        WHEN open_age_days < 180 THEN '3: 90-180 days'
        ELSE                          '4: over 180 days'
    END AS age_bucket,
    COUNT(*)                                                   AS open_tickets,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)         AS pct_of_backlog
FROM tickets
WHERE NOT is_resolved
GROUP BY 1
ORDER BY 1;


-- # Speed by denominator
-- Same numerator, two denominators. The gap between them is the survivorship
-- bias in a resolved-only "median 5.9h".
SELECT
    'resolved tickets only'  AS population,
    COUNT(*) FILTER (WHERE is_resolved)                                  AS denominator,
    ROUND(100.0 * COUNT(*) FILTER (WHERE is_resolved AND NOT is_negative_duration
                                     AND resolution_hours <= 24)
                / NULLIF(COUNT(*) FILTER (WHERE is_resolved), 0), 1)     AS pct_within_24h
FROM tickets
UNION ALL
SELECT
    'ALL tickets (honest)',
    COUNT(*),
    ROUND(100.0 * COUNT(*) FILTER (WHERE is_resolved AND NOT is_negative_duration
                                     AND resolution_hours <= 24) / COUNT(*), 1)
FROM tickets;
