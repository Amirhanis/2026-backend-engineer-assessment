# Decision Log

## D-01 — Reject the dense retrieval (embedding) lane
**Context:** The brief suggested an optional semantic lane. Order lines contain structured hardware and food items (Brand + Size + Spec).
**Options:** (a) Add `sentence-transformers` semantic search, (b) Rely solely on normalized lexical matching.
**Chose:** (b).
**Evidence:** Dense embeddings generally struggle with exact numerical specs (e.g., confusing M8x50 with M8x30) and often score "twin" items (same name, different brand) identically. Lexical matching using `rapidfuzz.token_sort_ratio` handles typos well, and exact token matching is strictly required for this domain. Measuring the lexical baseline yielded ~96.7% precision, proving embeddings were an unnecessary complexity.
**Reversal trigger:** If the domain expands to highly descriptive, non-structured items (e.g., "the red thing for cutting wood"), revisit embeddings.

## D-02 — Explicit token penalty over token_set_ratio
**Context:** Fuzzy matching struggles with "subset" queries. E.g., "Tolsen Bolt M8x50" vs "Tolsen Hex Bolt M8x50 HDG".
**Options:** (a) Use `rapidfuzz.token_set_ratio`, (b) Hardcode penalties for missing critical tokens.
**Chose:** (b).
**Evidence:** Using `token_set_ratio` caused the score gap between "Zinc Plated" and "HDG" variants to collapse to zero, flooding the system with `ambiguous_twins` and crashing coverage to 23%. Instead, we explicitly penalize candidates if they contain critical distinguishing words (finishes, colors, "bulk") that the query omits. This restored coverage to ~44% while maintaining strict precision.
**Reversal trigger:** If the list of critical structural modifiers (colors/finishes) grows too large to maintain manually, we will need to extract these dynamically using NER.

## D-03 — Operating Point: Precision over Coverage
**Context:** Setting the fuzzy match thresholds (`AUTO_THRESHOLD`).
**Options:** (a) Lower threshold to hit >60% coverage, (b) Keep threshold high to guarantee >96% precision.
**Chose:** (b) `AUTO_THRESHOLD = 82.0`.
**Evidence:** The cost model is highly asymmetric: a wrong auto-match (-800s) costs 20 times more than an abstention (-40s). Earning 20 seconds on a correct match means a precision below 95% literally yields negative net value. We optimized entirely for precision and abstention quality.
**Reversal trigger:** If human reviewers cost drastically less, or mis-shipments become cheaper to handle (e.g., better logistics).

## D-04 — Hardcoded "Not-an-Item" heuristics
**Context:** 30% of the training data are abstentions, many of which are non-item text like "subtotal" or "deliver by friday".
**Options:** (a) Let lexical matching naturally yield low scores, (b) Build an explicit pre-filter.
**Chose:** (b).
**Evidence:** While lexical matching often scores these low, short non-item strings can occasionally match short item names purely by chance. An explicit pre-filter guarantees these are rejected with a specific `not_an_item` reason code, providing better analytics and safety.
**Reversal trigger:** If buyers start ordering actual items named "Subtotal" or "Discount".

## D-05 — Ignoring low-confidence and expired SKUs
**Context:** `customer_sku_map.csv` contains dirty data.
**Options:** (a) Trust all alias mappings, (b) Filter them on load.
**Chose:** (b).
**Evidence:** Analysis showed 349 entries with confidence < 0.8 and 26 expired entries. Loading these into the index caused false positive matches in the `alias_exact` lane. Filtering them out at load time guarantees the deterministic alias lane is perfectly accurate.
**Reversal trigger:** If valid, un-expired aliases regularly have confidence scores below 0.70 in the real production system.

## D-06 — Strict Twin Detection
**Context:** Deciding when an item is too ambiguous to auto-match.
**Options:** (a) Always pick the top score, (b) Abstain if the gap between candidate #1 and #2 is less than a threshold.
**Chose:** (b) `TWIN_GAP_MIN = 8.0`.
**Evidence:** Without twin detection, "Stallion Hose Clip Zinc" would blindly pick a random size variant just because its string length slightly favored the fuzzy ratio. Twin detection explicitly routes these to human review, saving the 800s penalty.
**Reversal trigger:** None. Twin detection is structurally required for this domain.

## D-07 — Temp tables + covering indexes over pure CTE rewrite (Task 4)
**Context:** The baseline report query uses 7 correlated subqueries, each rescanning `match_event` (~1.1M rows). Needed to bring the full 61-day window from ~45 minutes to ≤ 10 seconds.
**Options:** (a) Pure CTE-based rewrite using the existing schema, (b) Add persistent indexes and rewrite, (c) Materialize filtered data into temp tables with covering indexes, then query those.
**Chose:** (c).
**Evidence:** (a) still scans 1.1M rows per CTE pass with no index support — measured at ~40s for the full window. (b) works but permanently changes the schema; the ledger takes ~40 writes per order line at peak, and every persistent index costs insert throughput. (c) builds indexes only for the duration of the query, then drops them. Measured at ~4s full window: faster than (a), no ongoing write cost like (b).
**Reversal trigger:** If the report runs sub-second without temp tables (e.g., via persistent columnstore or DuckDB migration), the complexity is not worth it.

## D-08 — Accept remote on conflict instead of 3-way merge (Task 5)
**Context:** `sync_adapter.py` on `ErpConflict` was blindly overwriting remote changes. Need a conflict resolution strategy.
**Options:** (a) 3-way merge (store base snapshot, diff both sides, merge), (b) Accept remote and re-pull, (c) Accept local and force-write.
**Chose:** (b).
**Evidence:** (c) is the existing bug (MAIA-844). (a) is the correct long-term answer but requires storing base snapshots and a per-field merge policy — too much machinery for a 3-day fix. (b) is safe: the remote edit was made by a human in the ERP, so accepting it respects their intent. The local edit goes to the next sync cycle, where the operator can re-apply it if needed. In the worst case, we lose a local edit rather than silently clobber a remote one — matching the cost asymmetry (clobbered remote edits are invisible and unrecoverable; lost local edits are visible and retryable).
**Reversal trigger:** When we have a proper CRDT or OT layer with field-level merge.

## D-09 — Deterministic idempotency key (Task 5)
**Context:** The ERP's idempotency window is 60 seconds, exact string match. The adapter's key included `time.time()` and `attempt`, making every retry unique — defeating idempotency.
**Options:** (a) Key = hash(external_id + payload + time.time()), (b) Key = hash(external_id + payload), (c) Key = hash(external_id + payload + version).
**Chose:** (b).
**Evidence:** The purpose of the idempotency key is to ensure that *the same logical write* is not applied twice. Including `time.time()` makes each retry a different logical write from the ERP's perspective, causing double-writes (MAIA-830). Including version is marginally safer but unnecessary — if the payload and ID are identical, it's the same write. Measured: with (b), zero duplicate writes across 100 runs with `timeout_rate=0.25`.
**Reversal trigger:** If the system needs to write the same payload to the same ID multiple times within 60 seconds (e.g., a rapid undo-redo cycle). In that case, add a logical sequence number.

## D-10 — Version-based conflict detection over timestamp comparison (Task 5)
**Context:** `pull()` compared `local.updated_at_utc > rec.updated_at` to decide whether to keep local edits. But UTC and server-local (+08:00) times are never comparable.
**Options:** (a) Convert timestamps to the same timezone, (b) Use version numbers for comparison, (c) Always accept remote during pull.
**Chose:** (b).
**Evidence:** (a) requires knowing the server's timezone offset, which is not in the API response (timestamps are rendered without offset). Hardcoding +08:00 works today but is fragile. (b) version numbers are monotonic and timezone-agnostic: if `local.remote_version >= rec.version`, we already have this version or newer. This is the ERP's own concurrency primitive and the most reliable signal.
**Reversal trigger:** If the ERP changes its versioning scheme (e.g., to UUIDs or non-monotonic versions).

## D-11 — Cursor as (timestamp, external_id) tuple (Task 5)
**Context:** The ERP's `list_changes()` uses strict `>` on timestamp strings at second resolution. Multiple records can share the same timestamp, so a timestamp-only cursor skips records.
**Options:** (a) Timestamp-only cursor with overlap window, (b) (timestamp, external_id) cursor with deduplication, (c) Request a server-side cursor or offset parameter from the vendor.
**Chose:** (b).
**Evidence:** (c) is the right answer but requires a vendor change we don't control. (a) requires re-fetching overlapping records and is wasteful. (b) exploits the fact that `list_changes()` sorts by `(updated_at, external_id)`, so we can use the last record's `(timestamp, id)` pair to precisely resume. Any records with `updated_at == cursor_ts` and `external_id <= cursor_id` are already processed.
**Reversal trigger:** If the vendor provides a proper opaque cursor or sequence-based pagination.

## D-12 — Shadow + canary deployment over A/B testing (Task 6)
**Context:** Deploying matcher changes safely when ground truth arrives 3–7 days after the order.
**Options:** (a) A/B test with random traffic split, (b) Shadow mode (dual-run, single decision) followed by canary.
**Chose:** (b).
**Evidence:** A/B testing splits the ground-truth signal — each variant sees only half the confirmations, doubling the time to detect regressions. Shadow mode runs both versions on 100% of traffic, generating full evaluation data for both. The canary phase then validates operational behavior (latency, error rates) on live traffic at 5% before full rollout. The 48-hour shadow window followed by 72-hour canary matches the ~5-day ground-truth lag.
**Reversal trigger:** If ground truth becomes near-real-time (e.g., instant barcode verification at the warehouse), A/B testing becomes viable because regressions are detected in hours, not days.
