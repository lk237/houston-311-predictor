-- Exploration queries. Each query is named with a `-- name:` line;
-- src/eda.py runs them and saves the results to reports/.

-- name: overview
SELECT
    COUNT(*)                                        AS total_requests,
    MIN(created_at)                                 AS first_request,
    MAX(created_at)                                 AS last_request,
    ROUND(AVG(is_closed::INT) * 100, 1)             AS pct_closed,
    ROUND(MEDIAN(resolution_hours) / 24, 2)         AS median_days_to_close,
    ROUND(AVG(is_overdue::INT) * 100, 1)            AS pct_overdue
FROM requests;

-- name: top_case_types
SELECT
    case_type,
    COUNT(*)                                        AS requests,
    ROUND(MEDIAN(resolution_hours) / 24, 2)         AS median_days,
    ROUND(AVG(is_overdue::INT) * 100, 1)            AS pct_overdue
FROM requests
GROUP BY case_type
ORDER BY requests DESC
LIMIT 15;

-- name: by_department
SELECT
    department,
    COUNT(*)                                        AS requests,
    ROUND(MEDIAN(resolution_hours) / 24, 2)         AS median_days,
    ROUND(AVG(is_overdue::INT) * 100, 1)            AS pct_overdue
FROM requests
GROUP BY department
HAVING COUNT(*) >= 50
ORDER BY pct_overdue DESC;

-- name: by_district
SELECT
    district,
    COUNT(*)                                        AS requests,
    ROUND(MEDIAN(resolution_hours) / 24, 2)         AS median_days,
    ROUND(AVG(is_overdue::INT) * 100, 1)            AS pct_overdue
FROM requests
WHERE district IS NOT NULL
GROUP BY district
ORDER BY district;

-- name: monthly_trend
SELECT
    created_month,
    COUNT(*)                                        AS requests,
    ROUND(AVG(is_overdue::INT) * 100, 1)            AS pct_overdue
FROM requests
GROUP BY created_month
ORDER BY created_month;

-- name: by_channel
SELECT
    channel,
    COUNT(*)                                        AS requests,
    ROUND(AVG(is_overdue::INT) * 100, 1)            AS pct_overdue
FROM requests
GROUP BY channel
ORDER BY requests DESC;
