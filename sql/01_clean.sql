-- Build the clean `requests` table from the staged `raw_311` table.
-- All timestamps in raw_311 are already UTC (converted in src/load_db.py).
-- {NOT_FINISHED_STATUSES} is filled in from config.yaml by load_db.py.
--
-- The query is built in steps using CTEs (WITH ... AS): each step takes the
-- previous one's result, like a small pipeline inside SQL.

CREATE OR REPLACE TABLE requests AS

-- Step 1: remove duplicates and rows that can't be used.
WITH deduped AS (
    -- the yearly files can repeat a case; keep its latest record
    -- ROW_NUMBER numbers each case's copies (1 = most recently closed);
    -- QUALIFY keeps only copy number 1.
    SELECT *
    FROM raw_311
    WHERE case_id IS NOT NULL AND created_at IS NOT NULL
    QUALIFY ROW_NUMBER() OVER (PARTITION BY case_id ORDER BY closed_at DESC NULLS LAST) = 1
),

-- Step 2: keep the useful columns and add time-based features.
with_times AS (
    SELECT
        case_id, status, title, case_type, department, division,
        district, neighborhood, channel, sla, sla_days, sla_unit,
        created_at, due_at,
        -- when the city exported this file (falls back to now if the banner had no date)
        COALESCE(extract_at, CURRENT_TIMESTAMP::TIMESTAMP) AS extract_at,
        -- a closed date only counts if the status says the work is finished
        -- ("Routed" requests sometimes have a closed date while still open)
        CASE WHEN status IN ({NOT_FINISHED_STATUSES}) THEN NULL ELSE closed_at END AS closed_at,
        DATE_TRUNC('month', created_at)  AS created_month,   -- e.g. 2025-03-01, for monthly charts
        DAYOFWEEK(created_at)            AS created_dow,     -- 0 = Sunday ... 6 = Saturday
        HOUR(created_at)                 AS created_hour     -- 0-23, in UTC
    FROM deduped
)

-- Step 3: compute how long each request took and whether it was late.
SELECT
    *,
    closed_at IS NOT NULL                                        AS is_closed,
    DATE_DIFF('minute', created_at, closed_at) / 60.0            AS resolution_hours,
    -- Target: was the request finished after its due date?
    --   closed after due                           -> TRUE
    --   closed on time                             -> FALSE
    --   still open when exported, already past due -> TRUE
    --   still open when exported, not yet due      -> NULL (outcome unknown, excluded)
    -- "Open" is judged at the file's export time, not today: the file can't tell us
    -- what happened to a request after it was exported.
    CASE
        WHEN due_at IS NULL                     THEN NULL
        WHEN closed_at IS NOT NULL              THEN closed_at > due_at
        WHEN extract_at > due_at                THEN TRUE
        ELSE NULL
    END                                                          AS is_overdue
FROM with_times
WHERE closed_at IS NULL OR closed_at >= created_at;              -- drop impossible rows (closed before created)
