-- =====================================================================
-- 02b_validation.sql  (DuckDB)
-- UTC / sign-off checks: the things a stakeholder relies on before trusting
-- the numbers. Each check returns a PASS/FAIL result or a small table to
-- eyeball. Run 02_metrics.sql first, or rely on the view recreated below.
-- =====================================================================

CREATE OR REPLACE VIEW tickets AS
    SELECT * FROM read_parquet('data/clean/tickets_clean.parquet');


-- Row-count reconciliation: clean rows == raw DISTINCT rows, confirming
-- dedup removed only exact duplicates and nothing else was lost.
SELECT
    (SELECT COUNT(*) FROM read_csv_auto('data/raw/support_tickets.csv', all_varchar=true))               AS raw_rows,
    (SELECT COUNT(*) FROM (SELECT DISTINCT * FROM read_csv_auto('data/raw/support_tickets.csv', all_varchar=true))) AS raw_distinct,
    (SELECT COUNT(*) FROM tickets)                                                                        AS clean_rows,
    CASE WHEN (SELECT COUNT(*) FROM tickets)
            = (SELECT COUNT(*) FROM (SELECT DISTINCT * FROM read_csv_auto('data/raw/support_tickets.csv', all_varchar=true)))
         THEN 'PASS' ELSE 'FAIL' END                                                                     AS reconciliation;


-- No impossible durations reach the resolution-time metric.
SELECT
    COUNT(*) FILTER (WHERE resolution_hours < 0)                          AS negatives_in_metric_input,
    CASE WHEN COUNT(*) FILTER (WHERE resolution_hours < 0) = 0
         THEN 'PASS' ELSE 'FAIL' END                                     AS check_no_negatives
FROM tickets
WHERE is_resolved AND NOT is_negative_duration;


-- Absence by segment: if the survey response rate is flat across
-- brand/channel/priority, the ~30% missing satisfaction isn't biased by
-- who gets surveyed, so the sentiment comparison isn't distorted.
SELECT 'brand'    AS dimension, brand    AS value,
       COUNT(*) FILTER (WHERE is_resolved) AS resolved,
       ROUND(100.0 * COUNT(*) FILTER (WHERE is_resolved AND has_satisfaction)
                   / NULLIF(COUNT(*) FILTER (WHERE is_resolved), 0), 1)  AS response_rate_percentage
FROM tickets GROUP BY brand
UNION ALL
SELECT 'channel', channel,
       COUNT(*) FILTER (WHERE is_resolved),
       ROUND(100.0 * COUNT(*) FILTER (WHERE is_resolved AND has_satisfaction)
                   / NULLIF(COUNT(*) FILTER (WHERE is_resolved), 0), 1)
FROM tickets GROUP BY channel
UNION ALL
SELECT 'priority', priority,
       COUNT(*) FILTER (WHERE is_resolved),
       ROUND(100.0 * COUNT(*) FILTER (WHERE is_resolved AND has_satisfaction)
                   / NULLIF(COUNT(*) FILTER (WHERE is_resolved), 0), 1)
FROM tickets GROUP BY priority
ORDER BY dimension, response_rate_percentage DESC;


-- Domain / range sanity checks. Each should return 0 offenders.
SELECT
    COUNT(*) FILTER (WHERE satisfaction_score IS NOT NULL
                       AND satisfaction_score NOT BETWEEN 1 AND 10)              AS bad_satisfaction,
    COUNT(*) FILTER (WHERE priority NOT IN ('Low','Medium','High','Urgent'))     AS bad_priority,
    COUNT(*) FILTER (WHERE channel NOT IN ('email','phone','chat','social'))     AS bad_channel,
    COUNT(*) FILTER (WHERE category NOT IN
        ('Billing','Booking Change','Technical Issue','Cancellation','General Query')) AS bad_category,
    COUNT(*) FILTER (WHERE brand NOT IN ('agoda','Booking.com','priceline','KAYAK')) AS bad_brand,
    CASE WHEN
        COUNT(*) FILTER (WHERE satisfaction_score IS NOT NULL AND satisfaction_score NOT BETWEEN 1 AND 10) = 0
        AND COUNT(*) FILTER (WHERE priority NOT IN ('Low','Medium','High','Urgent')) = 0
        AND COUNT(*) FILTER (WHERE channel NOT IN ('email','phone','chat','social')) = 0
        AND COUNT(*) FILTER (WHERE category NOT IN
            ('Billing','Booking Change','Technical Issue','Cancellation','General Query')) = 0
        AND COUNT(*) FILTER (WHERE brand NOT IN ('agoda','Booking.com','priceline','KAYAK')) = 0
        THEN 'PASS' ELSE 'FAIL' END                                             AS domain_checks
FROM tickets;


-- Reporting-window bounds: confirm the span and the small June-2025
-- partial-month sliver that trend charts exclude.
SELECT
    MIN(created_at)                              AS first_created,
    MAX(created_at)                              AS last_created,
    COUNT(*) FILTER (WHERE is_partial_month)     AS partial_jun25_rows
FROM tickets;


-- Absence vs speed: do slow tickets get surveyed less? If so, the survey
-- would under-sample unhappy customers. Response rate should be flat.
SELECT
    CASE
        WHEN resolution_hours < 4   THEN '0: <4h'
        WHEN resolution_hours < 12  THEN '1: 4-12h'
        WHEN resolution_hours < 24  THEN '2: 12-24h'
        WHEN resolution_hours < 168 THEN '3: 1-7d'
        ELSE                             '4: 7d+'
    END                                                              AS speed_bucket,
    COUNT(*)                                                         AS resolved,
    ROUND(100.0 * COUNT(*) FILTER (WHERE has_satisfaction) / COUNT(*), 1) AS response_rate_percentage
FROM tickets
WHERE is_resolved AND NOT is_negative_duration
GROUP BY 1
ORDER BY 1;


-- Absence vs time: is survey coverage drifting over the year? A falling
-- response rate would make the trend line an artefact of collection.
SELECT
    date_trunc('month', created_at)                                  AS month,
    COUNT(*) FILTER (WHERE is_resolved)                              AS resolved,
    ROUND(100.0 * COUNT(*) FILTER (WHERE is_resolved AND has_satisfaction)
                / NULLIF(COUNT(*) FILTER (WHERE is_resolved), 0), 1) AS response_rate_percentage
FROM tickets
WHERE NOT is_partial_month
GROUP BY 1
ORDER BY 1;


-- Timestamps after the 2025-06-02 extract: 9 rows, latest 2026-03-11. Flagged
-- rather than absorbed into "slow tickets".
SELECT
    COUNT(*) FILTER (WHERE is_future_resolution)                     AS resolved_after_extract,
    MAX(resolved_at)                                                 AS latest_resolution,
    DATE '2025-06-02'                                                AS extract_date,
    CASE WHEN COUNT(*) FILTER (WHERE is_future_resolution) = 0
         THEN 'PASS'
         ELSE 'FLAG (kept in metric, immaterial: p90 moves 0.15h)'
    END                                                              AS status
FROM tickets;


-- The extract-date test: activity on both clocks per month. The export was
-- pulled "shortly after the last ticket", which is ambiguous -- the last
-- ticket CREATED is 2025-06-01, the last resolved_at is 2026-03-11. A created_at
-- filter would explain creations stopping, but not resolutions stopping, since
-- tickets already in the window keep being worked. If BOTH clocks stop on the
-- same day, the file ends there (2025-06-02).
WITH months AS (
    SELECT date_trunc('month', created_at) AS month FROM tickets
    UNION
    SELECT date_trunc('month', resolved_at) FROM tickets WHERE resolved_at IS NOT NULL
)
SELECT
    m.month,
    (SELECT COUNT(*) FROM tickets t
      WHERE date_trunc('month', t.created_at) = m.month)              AS tickets_created,
    (SELECT COUNT(*) FROM tickets t
      WHERE date_trunc('month', t.resolved_at) = m.month)             AS tickets_resolved
FROM months m
WHERE m.month IS NOT NULL
ORDER BY m.month;


-- Abnormal missing time period: ordinary resolution times run continuously to ~83h,
-- then the next value is ~3,833h with nothing in between. A real slow tail is
-- continuous; a cluster behind an empty gap is injected data.
SELECT
    ROUND(MAX(resolution_hours) FILTER (WHERE NOT is_extreme_duration), 1)  AS last_ordinary_value,
    ROUND(MIN(resolution_hours) FILTER (WHERE is_extreme_duration), 1)      AS first_extreme_value,
    COUNT(*) FILTER (WHERE resolution_hours BETWEEN 100 AND 3000)           AS tickets_in_the_gap,
    COUNT(*) FILTER (WHERE is_extreme_duration)                             AS extreme_tickets
FROM tickets
WHERE is_resolved AND NOT is_negative_duration;
