# Sync Adapter — Defect Report (Task 5)

## 1. Defect List

### Defect 1 — Pagination cursor skips tied records (→ MAIA-812)
- **Ticket:** MAIA-812 ("some items never appear on our side until someone edits them")
- **Mechanism:** `FakeErp.list_changes(since, limit)` returns records with `updated_at > since`. The cursor was stored simply as `page[-1].updated_at`. When multiple records shared the same second-resolution timestamp across a page boundary, all subsequent records with that same timestamp were permanently skipped because the strict `>` operator excluded them. They only became visible if someone updated them in the ERP (advancing their timestamp).
- **Failing Test:** `test_defect1_pagination_skips_tied_records_maia_812` in `starter/sync/test_sync.py`
- **Fix:** Implemented tie-aware pagination. When a page ends on a timestamp boundary, the adapter identifies the boundary timestamp and only advances the cursor up to the last distinct timestamp strictly before the boundary. If an entire page contains identical timestamps, the query limit is dynamically expanded to retrieve the entire tie group before advancing.
- **Invariant Restored:** **INV1** (after a full sync, every remote record exists locally at the remote version).

---

### Defect 2 — Non-deterministic idempotency key (→ MAIA-830)
- **Ticket:** MAIA-830 ("price history shows two updates a second apart, we only made one")
- **Mechanism:** `idempotency_key()` incorporated `time.time()` and `attempt` into its hash payload. When `FakeErp` returned an `ErpTimeout` (HTTP 504) *after* successfully committing the write, the adapter retried the push with a freshly generated idempotency key. The ERP received a different key and executed a second write, producing two historical entries a second apart.
- **Failing Test:** `test_defect2_non_deterministic_idempotency_key_maia_830` in `starter/sync/test_sync.py`
- **Fix:** Stripped `time.time()` and `attempt` from `idempotency_key()`. The hash is now strictly deterministic based on `(external_id, payload)`.
- **Invariant Restored:** **INV2** (one logical local edit produces at most one remote write).

---

### Defect 3 — Blind overwrite on conflict (→ MAIA-844)
- **Ticket:** MAIA-844 ("an edit made in the ERP was overwritten by our older value")
- **Mechanism:** When `push()` encountered an `ErpConflict` (HTTP 409), it caught the exception, fetched the current remote record version via `erp.get()`, and immediately retried writing the local payload using the new `base_version`. This forcefully clobbered legitimate human edits made directly in the ERP.
- **Failing Test:** `test_defect3_blind_overwrite_on_conflict_maia_844` in `starter/sync/test_sync.py`
- **Fix:** On `ErpConflict`, the adapter accepts the remote version as authoritative, updates the local record with the remote payload and version, and clears the local `dirty` flag.
- **Invariant Restored:** **INV3** (a remote edit made after our local edit must not be silently lost).

---

### Defect 4 — Timezone mismatch in pull comparison (→ MAIA-844)
- **Ticket:** MAIA-844 ("an edit made in the ERP was overwritten by our older value")
- **Mechanism:** During `pull()`, the conflict check evaluated `local.updated_at_utc > rec.updated_at`. `local.updated_at_utc` is formatted in UTC (`+00:00`), whereas `rec.updated_at` is generated in server local time (`+08:00`) without timezone offset indicators. Consequently, UTC timestamps are 8 hours behind local timestamps, causing the adapter to incorrectly conclude that remote records are always newer and discarding unpushed local changes.
- **Failing Test:** `test_defect4_timezone_mismatch_in_pull_comparison_maia_844` in `starter/sync/test_sync.py`
- **Fix:** Removed timestamp string comparison. The pull stage now relies on version monotonicity (`local.remote_version >= rec.version`), avoiding all timezone translation traps.
- **Invariant Restored:** **INV3** (preserves local edits correctly against un-advanced remote versions).

---

## 2. Latent Defects

### Defect 5 — Cursor advanced before page processing completes
- **Mechanism:** In `pull()`, `store.set_cursor()` was called at the start of the page loop before records were upserted. If a crash, exception, or network termination occurred during record processing, the cursor had already advanced in the store. On restart, un-upserted records on that page would be permanently missed.
- **Failing Test:** `test_defect5_cursor_advanced_before_page_processing` in `starter/sync/test_sync.py`
- **Fix:** Moved `store.set_cursor()` to occur only after all records within the batch have been successfully written to `store`.
- **Invariant Restored:** Crash durability and **INV1**.

---

### Defect 6 — ErpTimeout on push not verified before retry
- **Mechanism:** When `write()` timed out (`ErpTimeout`), the adapter continued directly to the next loop iteration. While fixing Defect 2 makes the retry idempotent, retrying across a transient connection drop unnecessarily burdens the server if the write had already committed.
- **Failing Test:** `test_defect6_erptimeout_on_push_not_handled_as_potential_success` in `starter/sync/test_sync.py`
- **Fix:** Upon catching `ErpTimeout`, the adapter calls `erp.get()` to inspect whether the remote version was incremented. If the write committed, it accepts the update as successful immediately without an extra write request.
- **Invariant Restored:** **INV2** and request minimization under high packet drop.

---

## 3. The Contract We Wish We Had

If negotiating with the ERP vendor, we would request the following improvements in priority order:

1. **Opaque Cursors or Monotonic Sequence IDs for Change Feeds:**
   - *Problem:* Second-resolution timestamp pagination with `>` comparisons inevitably creates ties across page boundaries.
   - *Request:* Provide an opaque cursor (`next_cursor_token`) or integer sequence IDs (`change_id > X`) so pagination is 100% deterministic and O(1).
   - *Coping strategy while vendor says no:* Use tie-aware boundary pagination with dynamic limit expansion.

2. **Explicit Timezone Offsets (ISO 8601):**
   - *Problem:* Timestamps returned as `YYYY-MM-DD HH:MM:SS` without offset create ambiguity across geographic deployments.
   - *Request:* Format timestamps as `YYYY-MM-DDTHH:MM:SS.sssZ` or include `+08:00`.
   - *Coping strategy while vendor says no:* Base concurrency and ordering strictly on monotonic integer version numbers (`version`), never raw timestamp strings.

3. **Status Confirmation Header on Idempotent Retries:**
   - *Problem:* When a 504 occurs after commit, the client cannot distinguish between a dropped request and a dropped response without an extra GET.
   - *Request:* Retries using the same `Idempotency-Key` should return a header such as `X-Idempotent-Replay: true` with a guaranteed 24-hour TTL (rather than 60 seconds).
   - *Coping strategy while vendor says no:* Check remote state via GET after catching 504s.

4. **Transactional Bulk Upsert Endpoint:**
   - *Problem:* Pushing 100 dirty records currently requires 100 individual HTTP roundtrips with non-atomic partial failures.
   - *Request:* `POST /items/batch` supporting up to 500 items per request in a single atomic transaction.
   - *Coping strategy while vendor says no:* Sequential per-record writes with fine-grained error capture and retry queues.

---

## 4. What Breaks at Scale

Running this adapter across **500 tenants every 5 minutes** generates **144,000 sync runs per day**. At this volume:

1. **ERP Rate Limiting & Concurrency Saturation:**
   - 500 tenants polling independently will generate bursts of hundreds of concurrent requests. Without rate limiting and exponential backoff on the client side, the ERP gateway will return 429 Too Many Requests.
2. **Timeout Cascades from Single-Record Pushes:**
   - With a 15% timeout rate on writes, pushing 1,000 dirty items generates ~150 timeouts, each triggering a follow-up `erp.get()` check. This multiplies API traffic by 1.3× to 1.5×.
3. **Detection & Monitoring (Before Customers Notice):**
   - **Metrics:** Track `sync_duration_seconds`, `records_pulled_per_run`, `records_pushed_per_run`, `conflict_count`, and `timeout_recovery_count` per tenant.
   - **Alerting:** Fire a P2 alert if a tenant's cursor does not advance for >3 consecutive sync cycles (15 minutes), or if the unpushed `dirty` record queue exceeds 50 items.
