import sqlite3
import datetime
import math
from collections import defaultdict

def run(con: sqlite3.Connection) -> list[dict]:
    # 1. Fetch tenants
    tenants = {}
    for tid, name, plan in con.execute("SELECT tenant_id, name, plan FROM tenant").fetchall():
        tenants[tid] = plan

    # 2. Fetch disabled items set
    disabled_items = set()
    for tid, icode in con.execute("SELECT tenant_id, item_code FROM item WHERE disabled = 1").fetchall():
        disabled_items.add((tid, icode))

    # 3. Fetch order lines for window 2026-05-01 to 2026-06-30
    # Map: line_id -> (tenant_id, channel, customer_id, day)
    order_lines = {}
    
    # Aggregation for (tenant_id, channel, day):
    # lines_total: set of line_ids
    # distinct_customers: set of customer_ids
    tcd_lines = defaultdict(set)
    tcd_customers = defaultdict(set)
    
    cur = con.execute("""
        SELECT line_id, tenant_id, customer_id, channel, substr(created_at, 1, 10)
        FROM order_line
        WHERE created_at >= '2026-05-01' AND created_at <= '2026-06-30T23:59:59'
    """)
    for lid, tid, cid, chan, day in cur.fetchall():
        order_lines[lid] = (tid, chan, day)
        key = (tid, chan, day)
        tcd_lines[key].add(lid)
        tcd_customers[key].add(cid)

    # 4. Process match_events
    # We need match_events from 2026-04-30 (for repeat items prev day on 2026-05-01) to 2026-06-30
    
    # Tenant-Day aggregations:
    # accept_scores: list of float
    # latencies: list of int
    # accepted_disabled_count: int
    # items_by_day: tenant_id -> day -> set of item_code
    td_scores = defaultdict(list)
    td_latencies = defaultdict(list)
    td_accepted_disabled = defaultdict(int)
    td_items = defaultdict(lambda: defaultdict(set))
    
    # Tenant-Channel-Day match event aggregations:
    # lines_accepted: set of line_ids
    # candidates_considered: int
    tcd_accepted_lines = defaultdict(set)
    tcd_candidates = defaultdict(int)

    cur = con.execute("""
        SELECT tenant_id, line_id, item_code, accepted, latency_ms, score, substr(created_at, 1, 10)
        FROM match_event
        WHERE created_at >= '2026-04-30' AND created_at <= '2026-06-30T23:59:59'
    """)
    
    for tid, lid, icode, accepted, lat, score, day in cur.fetchall():
        # Track items per tenant per day (for repeat_items)
        td_items[tid][day].add(icode)
        
        # Only aggregate window days (2026-05-01 to 2026-06-30) for the report metrics
        if day < '2026-05-01':
            continue
            
        td_key = (tid, day)
        td_latencies[td_key].append(lat)
        
        if accepted == 1:
            td_scores[td_key].append(score)
            if (tid, icode) in disabled_items:
                td_accepted_disabled[td_key] += 1
                
        # Link to order line for channel-level metrics
        ol_info = order_lines.get(lid)
        if ol_info:
            _, chan, ol_day = ol_info
            # Check if match_event belongs to same day/channel
            tcd_key = (tid, chan, day)
            tcd_candidates[tcd_key] += 1
            if accepted == 1:
                tcd_accepted_lines[tcd_key].add(lid)

    # 5. Pre-compute repeat items prev day per (tenant_id, day)
    # repeat_items: tenant_id -> day -> count
    td_repeat = {}
    for tid, days_dict in td_items.items():
        for day_str, items_today in days_dict.items():
            if day_str < '2026-05-01':
                continue
            # Calculate prev day string
            # day_str format: YYYY-MM-DD
            dt = datetime.date(int(day_str[:4]), int(day_str[5:7]), int(day_str[8:10]))
            prev_day_str = (dt - datetime.timedelta(days=1)).isoformat()
            items_prev = days_dict.get(prev_day_str, set())
            td_repeat[(tid, day_str)] = len(items_today & items_prev)

    # 6. Pre-compute tenant-day summary metrics (avg_accept_score, max_latency, avg_latency, p95_latency)
    td_summary = {}
    for td_key, lats in td_latencies.items():
        scores = td_scores.get(td_key, [])
        avg_score = sum(scores) / len(scores) if scores else None
        max_lat = max(lats) if lats else None
        avg_lat = sum(lats) / len(lats) if lats else None
        
        # Nearest-rank p95: index = ceil(0.95 * n) - 1 in 0-indexed sorted list
        lats_sorted = sorted(lats)
        n = len(lats_sorted)
        p95_idx = max(0, math.ceil(0.95 * n) - 1)
        p95_lat = lats_sorted[p95_idx] if n > 0 else None
        
        td_summary[td_key] = {
            "avg_accept_score": avg_score,
            "max_latency_ms": max_lat,
            "avg_latency_ms": avg_lat,
            "p95_latency_ms": p95_lat,
            "accepted_disabled": td_accepted_disabled.get(td_key, 0),
            "repeat_items_prev_day": td_repeat.get(td_key, 0)
        }

    # 7. Assemble final rows sorted by t.tenant_id, ol.channel, day
    results = []
    # Sorted unique keys from tcd_lines
    sorted_keys = sorted(tcd_lines.keys(), key=lambda k: (k[0], k[1], k[2]))
    
    for tid, chan, day in sorted_keys:
        tcd_key = (tid, chan, day)
        td_key = (tid, day)
        td_data = td_summary.get(td_key, {})
        
        row = {
            "tenant_id": tid,
            "plan": tenants.get(tid, ""),
            "channel": chan,
            "day": day,
            "lines_total": len(tcd_lines[tcd_key]),
            "lines_accepted": len(tcd_accepted_lines[tcd_key]),
            "candidates_considered": tcd_candidates[tcd_key],
            "avg_accept_score": td_data.get("avg_accept_score"),
            "max_latency_ms": td_data.get("max_latency_ms"),
            "avg_latency_ms": td_data.get("avg_latency_ms"),
            "distinct_customers": len(tcd_customers[tcd_key]),
            "repeat_items_prev_day": td_data.get("repeat_items_prev_day", 0),
            "accepted_disabled": td_data.get("accepted_disabled", 0),
            "p95_latency_ms": td_data.get("p95_latency_ms")
        }
        results.append(row)

    return results
