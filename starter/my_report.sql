WITH
-- Base dates for order_line
ol_base AS (
    SELECT 
        tenant_id,
        channel,
        customer_id,
        line_id,
        substr(created_at, 1, 10) AS day
    FROM order_line
    WHERE substr(created_at, 1, 10) >= '2026-05-01'
      AND substr(created_at, 1, 10) <= '2026-06-30'
),
-- Base dates for match_event
me_base AS (
    SELECT
        tenant_id,
        line_id,
        item_code,
        score,
        accepted,
        latency_ms,
        substr(created_at, 1, 10) AS day
    FROM match_event
    WHERE substr(created_at, 1, 10) >= '2026-05-01'
      AND substr(created_at, 1, 10) <= '2026-06-30'
),
-- Tenant x Channel x Day level aggregations
agg_tenant_channel_day AS (
    SELECT
        ol.tenant_id,
        ol.channel,
        ol.day,
        COUNT(DISTINCT ol.line_id) AS lines_total,
        COUNT(DISTINCT ol.customer_id) AS distinct_customers,
        COUNT(DISTINCT CASE WHEN me.accepted = 1 THEN me.line_id END) AS lines_accepted,
        COUNT(me.line_id) AS candidates_considered
    FROM ol_base ol
    LEFT JOIN me_base me ON me.line_id = ol.line_id
    GROUP BY ol.tenant_id, ol.channel, ol.day
),
-- To compute repeat_items_prev_day efficiently:
-- We need distinct items for each tenant/day
tenant_day_items AS (
    SELECT DISTINCT tenant_id, substr(created_at, 1, 10) AS day, item_code
    FROM match_event
),
-- Join today's items with yesterday's items
repeat_items AS (
    SELECT 
        t1.tenant_id, 
        t1.day, 
        COUNT(t1.item_code) AS repeat_items_prev_day
    FROM tenant_day_items t1
    JOIN tenant_day_items t2 
      ON t1.tenant_id = t2.tenant_id 
     AND t1.item_code = t2.item_code
     AND t2.day = date(t1.day, '-1 day')
    GROUP BY t1.tenant_id, t1.day
),
-- Tenant x Day level aggregations
agg_tenant_day AS (
    SELECT
        me.tenant_id,
        me.day,
        AVG(CASE WHEN me.accepted = 1 THEN me.score END) AS avg_accept_score,
        MAX(me.latency_ms) AS max_latency_ms,
        AVG(me.latency_ms) AS avg_latency_ms,
        COUNT(CASE WHEN me.accepted = 1 AND it.disabled = 1 THEN 1 END) AS accepted_disabled
    FROM me_base me
    LEFT JOIN item it ON it.tenant_id = me.tenant_id AND it.item_code = me.item_code
    GROUP BY me.tenant_id, me.day
),
-- p95 latency requires a percentile. SQLite doesn't have percentile_cont built-in by default
-- BUT the prompt says: "nearest-rank p95 of latency_ms over the same tenant-day set"
-- We can compute this by assigning row numbers partitioned by tenant/day, 
-- and taking the row where row_number = CEIL(0.95 * count)
latency_ranked AS (
    SELECT 
        tenant_id,
        day,
        latency_ms,
        ROW_NUMBER() OVER(PARTITION BY tenant_id, day ORDER BY latency_ms ASC) AS rn,
        COUNT(*) OVER(PARTITION BY tenant_id, day) AS cnt
    FROM me_base
),
p95_calc AS (
    SELECT 
        tenant_id,
        day,
        MIN(latency_ms) AS p95_latency_ms
    FROM latency_ranked
    WHERE rn >= CAST((cnt * 95 + 99) / 100 AS INTEGER)
    GROUP BY tenant_id, day
)
-- Bring it all together
SELECT
    t.tenant_id,
    t.plan,
    tcd.channel,
    tcd.day,
    tcd.lines_total,
    tcd.lines_accepted,
    tcd.candidates_considered,
    td.avg_accept_score,
    td.max_latency_ms,
    td.avg_latency_ms,
    tcd.distinct_customers,
    COALESCE(ri.repeat_items_prev_day, 0) AS repeat_items_prev_day,
    td.accepted_disabled,
    p95.p95_latency_ms
FROM agg_tenant_channel_day tcd
JOIN tenant t ON t.tenant_id = tcd.tenant_id
LEFT JOIN agg_tenant_day td ON td.tenant_id = tcd.tenant_id AND td.day = tcd.day
LEFT JOIN repeat_items ri ON ri.tenant_id = tcd.tenant_id AND ri.day = tcd.day
LEFT JOIN p95_calc p95 ON p95.tenant_id = tcd.tenant_id AND p95.day = tcd.day
ORDER BY t.tenant_id, tcd.channel, tcd.day;
