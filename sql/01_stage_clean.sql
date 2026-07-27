-- =====================================================================
-- 01_stage_clean.sql  (DuckDB)
-- Turn the two raw CSVs into one clean ticket table with derived flags.
-- All cleaning logic lives here; src/clean.py only runs it and reports.
-- =====================================================================

WITH raw_tickets AS (
    -- read as text so we control parsing; SELECT DISTINCT removes the
    -- 59 exact full-row duplicates
    SELECT DISTINCT *
    FROM read_csv_auto('data/raw/support_tickets.csv', all_varchar = true)
),

agents AS (
    SELECT
        agent_id,
        team,
        try_strptime(hire_date, '%Y-%m-%d')::DATE AS hire_date,
        location
    FROM read_csv_auto('data/raw/agents.csv', all_varchar = true)
),

parsed AS (
    SELECT
        ticket_id,
        brand,

        -- channel -> 4 canonical values; lower+trim collapses casing and
        -- folds 'e-mail' into 'email'
        CASE lower(trim(channel))
            WHEN 'e-mail' THEN 'email'
            ELSE lower(trim(channel))
        END AS channel,

        -- category -> 5 canonical values; 'Bill' is a truncation of 'Billing'
        CASE
            WHEN lower(trim(category)) IN ('bill', 'billing') THEN 'Billing'
            WHEN lower(trim(category)) = 'booking change'      THEN 'Booking Change'
            WHEN lower(trim(category)) = 'technical issue'     THEN 'Technical Issue'
            WHEN lower(trim(category)) = 'cancellation'        THEN 'Cancellation'
            WHEN lower(trim(category)) = 'general query'       THEN 'General Query'
            ELSE trim(category)  -- anything unexpected is surfaced, not hidden
        END AS category,

        priority,

        -- created_at appears in two formats: ISO and DD/MM/YYYY HH:MM
        COALESCE(
            try_strptime(created_at, '%Y-%m-%d %H:%M:%S'),
            try_strptime(created_at, '%d/%m/%Y %H:%M')
        ) AS created_at,

        -- resolved_at: NULL/blank means not yet resolved
        COALESCE(
            try_strptime(resolved_at, '%Y-%m-%d %H:%M:%S'),
            try_strptime(resolved_at, '%d/%m/%Y %H:%M')
        ) AS resolved_at,

        NULLIF(trim(agent_id), '') AS agent_id,       -- 40 blanks -> NULL
        TRY_CAST(satisfaction_score AS INTEGER) AS satisfaction_score
    FROM raw_tickets
),

id_counts AS (
    -- after dedup, any ticket_id still appearing >1 time has conflicting data
    SELECT ticket_id, COUNT(*) AS id_rows
    FROM parsed
    GROUP BY ticket_id
)

SELECT
    p.ticket_id,
    p.brand,
    p.channel,
    p.category,
    p.priority,
    p.created_at,
    p.resolved_at,
    p.agent_id,
    p.satisfaction_score,
    a.team,
    a.hire_date,
    a.location,

    -- ---- derived flags & measures ---------------------------------------
    (p.resolved_at IS NOT NULL) AS is_resolved,

    -- wall-clock resolution time in hours (raw; negatives kept but flagged)
    CASE
        WHEN p.resolved_at IS NOT NULL AND p.created_at IS NOT NULL
        THEN date_diff('minute', p.created_at, p.resolved_at) / 60.0
    END AS resolution_hours,

    -- resolved before created: physically impossible; excluded from metrics
    (p.resolved_at IS NOT NULL AND p.created_at IS NOT NULL
        AND p.resolved_at < p.created_at) AS is_negative_duration,

    -- longer than 30 days: suspected stale/never-closed; kept (median/p90 robust)
    (p.resolved_at IS NOT NULL AND p.created_at IS NOT NULL
        AND date_diff('day', p.created_at, p.resolved_at) > 30) AS is_extreme_duration,

    -- resolved after the extract. The threshold is the day AFTER the extract
    -- (2025-06-02), so the two genuine resolutions on the morning of 2025-06-02
    -- are not flagged; the count is 9.
    (p.resolved_at IS NOT NULL
        AND p.resolved_at >= DATE '2025-06-03') AS is_future_resolution,

    -- age of an OPEN ticket as of the extract date; NULL for resolved tickets
    CASE
        WHEN p.resolved_at IS NULL AND p.created_at IS NOT NULL
        THEN date_diff('day', p.created_at, TIMESTAMP '2025-06-02 00:00:00')
    END AS open_age_days,

    -- ticket predates the agent's hire date: unreliable for attribution
    (p.created_at IS NOT NULL AND a.hire_date IS NOT NULL
        AND p.created_at < a.hire_date) AS created_before_hire,

    (p.agent_id IS NULL) AS missing_agent,
    (p.satisfaction_score IS NOT NULL) AS has_satisfaction,
    (p.satisfaction_score IN (1, 2)) AS is_bottom_box,
    (date_trunc('month', p.created_at) = DATE '2025-06-01') AS is_partial_month,
    (ic.id_rows > 1) AS is_id_conflict

FROM parsed p
LEFT JOIN agents a   USING (agent_id)
LEFT JOIN id_counts ic USING (ticket_id);
