-- Task 4 baseline: "Tenant Match Health" report, as it exists in production today.
--
-- Parameters are inlined for readability; the service binds :date_from / :date_to.
-- The report powers an internal dashboard that is loaded on every page view by
-- the ops team, one tenant at a time, plus a nightly all-tenant roll-up.
--
-- Contract you must preserve: same columns, same rows, same values.
-- Ordering is defined by the ORDER BY at the bottom.

SELECT
    t.tenant_id,
    t.plan,
    ol.channel,
    substr(ol.created_at, 1, 10)                                   AS day,
    COUNT(DISTINCT ol.line_id)                                     AS lines_total,

    -- lines where the matcher committed to an answer
    (SELECT COUNT(DISTINCT me2.line_id)
       FROM match_event me2
       JOIN order_line ol2 ON ol2.line_id = me2.line_id
      WHERE me2.tenant_id = t.tenant_id
        AND me2.accepted = 1
        AND ol2.channel = ol.channel
        AND substr(ol2.created_at, 1, 10) = substr(ol.created_at, 1, 10)
    )                                                              AS lines_accepted,

    -- how much work the matcher did per line
    (SELECT COUNT(*)
       FROM match_event me3
       JOIN order_line ol3 ON ol3.line_id = me3.line_id
      WHERE ol3.tenant_id = t.tenant_id
        AND ol3.channel = ol.channel
        AND substr(ol3.created_at, 1, 10) = substr(ol.created_at, 1, 10)
    )                                                              AS candidates_considered,

    -- winning-candidate score, averaged
    (SELECT AVG(me4.score)
       FROM match_event me4
      WHERE me4.tenant_id = t.tenant_id
        AND me4.accepted = 1
        AND substr(me4.created_at, 1, 10) = substr(ol.created_at, 1, 10)
    )                                                              AS avg_accept_score,

    -- latency, such as it is measured today (see Task 4: the report owner also
    -- wants a p95_latency_ms column, nearest-rank, over the same tenant-day set)
    (SELECT MAX(me5.latency_ms)
       FROM match_event me5
      WHERE me5.tenant_id = t.tenant_id
        AND substr(me5.created_at, 1, 10) = substr(ol.created_at, 1, 10)
    )                                                              AS max_latency_ms,

    (SELECT AVG(me6.latency_ms)
       FROM match_event me6
      WHERE me6.tenant_id = t.tenant_id
        AND substr(me6.created_at, 1, 10) = substr(ol.created_at, 1, 10)
    )                                                              AS avg_latency_ms,

    -- distinct buyers touched, the way the original author did it
    (SELECT COUNT(DISTINCT ol4.customer_id)
       FROM order_line ol4
      WHERE ol4.tenant_id = t.tenant_id
        AND ol4.channel = ol.channel
        AND substr(ol4.created_at, 1, 10) = substr(ol.created_at, 1, 10)
    )                                                              AS distinct_customers,

    -- items the matcher also considered for this tenant on the PREVIOUS day.
    -- Added last quarter "just as a sanity metric". It is the reason the report
    -- went from slow to unusable.
    (SELECT COUNT(DISTINCT me8.item_code)
       FROM match_event me8
      WHERE me8.tenant_id = t.tenant_id
        AND substr(me8.created_at, 1, 10) = substr(ol.created_at, 1, 10)
        AND EXISTS (SELECT 1
                      FROM match_event me9
                     WHERE me9.tenant_id = me8.tenant_id
                       AND me9.item_code = me8.item_code
                       AND substr(me9.created_at, 1, 10) =
                           date(substr(me8.created_at, 1, 10), '-1 day'))
    )                                                              AS repeat_items_prev_day,

    -- share of accepted answers that pointed at a disabled item
    (SELECT COUNT(*)
       FROM match_event me7
       JOIN item it ON it.tenant_id = me7.tenant_id AND it.item_code = me7.item_code
      WHERE me7.accepted = 1
        AND it.disabled = 1
        AND me7.tenant_id = t.tenant_id
        AND substr(me7.created_at, 1, 10) = substr(ol.created_at, 1, 10)
    )                                                              AS accepted_disabled

FROM order_line ol
JOIN tenant t ON t.tenant_id = ol.tenant_id
WHERE substr(ol.created_at, 1, 10) >= '2026-05-01'
  AND substr(ol.created_at, 1, 10) <= '2026-05-01'
GROUP BY t.tenant_id, t.plan, ol.channel, substr(ol.created_at, 1, 10)
ORDER BY t.tenant_id, ol.channel, day;
