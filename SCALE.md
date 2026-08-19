# Scale and Rollout (Task 6)

## The First Thing to Break

At 500 tenants, 4M catalogue rows, and ~150k order lines/day, the **lexical scoring stage** is the first bottleneck. Today, each order line scans the tenant's entire active catalogue computing fuzzy scores. The median tenant has ~8,000 active items, meaning each line evaluates ~8,000 `token_sort_ratio` calls. At 150k lines/day, that is 1.2 billion fuzzy comparisons per day.

**Evidence:** On the current single-tenant benchmark (1,148 items, Acme), lexical scoring takes ~40ms per line. At 8,000 items it extrapolates to ~280ms, which already pushes against the 250ms p95 budget. At 15,000 items (a large distributor), the budget is blown.

**Fix:** Pre-filter candidates before fuzzy scoring. A cheap trigram or character n-gram inverted index (built at catalogue load time) reduces the candidate set from thousands to ~20–50. This is the standard information-retrieval pattern: cheap recall stage → expensive precision stage. At 50 candidates, the fuzzy scoring cost drops to ~2ms/line regardless of catalogue size.

The second bottleneck is alias table size. At 500 tenants with growing alias history, the in-memory dictionary becomes multi-gigabyte. The fix is straightforward: move aliases into a per-tenant SQLite database with indexed lookups. The current CSV-in-memory approach was correct for single-tenant cold-start but does not survive 500 tenants loaded simultaneously.

## Alias Feedback Loop: Stopping Entrenched Wrong Matches

The alias table learns from operator confirmations, including their mistakes. A wrong confirmation (cost: 20× an abstention) enters the alias table and causes every future occurrence of that buyer SKU to auto-match incorrectly — compounding the damage.

**Defence in depth:**

1. **Provisional period.** New aliases from operator confirmations enter a `provisional` state for 7 days. During this window, they are used only for the `review` lane (not `auto`), giving other operators a chance to catch errors. Only after N independent confirmations (N ≥ 2) does an alias graduate to `confirmed`.

2. **Contradiction detection.** If an operator maps buyer SKU `X → item A` but another operator later maps `X → item B`, both entries are frozen and escalated. The system reverts to abstention for that SKU until a senior operator resolves it.

3. **Confidence decay.** Alias confidence decays over time without re-confirmation. An alias unused for 90 days drops below the auto-match threshold and requires human review. This prevents stale mappings from silently going wrong after catalogue changes (e.g., item supersession).

4. **Cost-aware audit sampling.** Randomly sample 2–5% of auto-matched lines for human review, weighted by the economic cost of the item (high-value items get audited more). This provides an ongoing ground-truth signal that catches systematic alias errors before they compound.

## Shipping a Matcher Change Safely

**Shadow mode first, canary second.**

**Shadow deployment:** The new matcher version runs in parallel on 100% of live traffic but its decisions are not acted upon. Both versions' predictions are logged. After 48 hours, we compare:
- Precision on the overlapping `auto` decisions (both versions agree)
- Disagreement rate (where they differ, and which was right — checked against operator confirmations that trickle in over the next 3 days)
- Coverage delta (did the new version expand or contract the auto-match set?)

The challenge is that ground truth arrives days later (when wrong shipments are caught). This means shadow evaluation uses a **lagged precision metric**: we only score decisions where the ground truth has resolved (typically 5–7 days post-order). We accept this delay because a bad rollout costs far more than a slow rollout.

**Canary deployment:** If shadow results are positive (precision ≥ baseline, no new cross-tenant violations), we route 5% of live traffic to the new version for 72 hours. The canary is auto-rolled-back if:
- Precision drops below the baseline minus 1 percentage point
- Any cross-tenant violation is detected
- The abstention rate spikes by more than 10 percentage points (indicates a regression in coverage)

Only after the canary holds for 72 hours do we proceed to full rollout.

**Decision:** We use shadow + canary (not A/B testing) because A/B splits the ground-truth signal and doubles the time to detect regressions. With shadow, 100% of traffic generates evaluation data for both versions.

## Embedding Re-indexing (If Used)

If embeddings were used, re-indexing 40k items at 2am (a tenant's bulk catalogue edit) would take ~8 minutes on a single GPU (at ~80 items/sec for a 384-dim model) or ~25 minutes on CPU. During re-indexing, the tenant operates on the **stale index**: queries still match against the pre-edit catalogue. This means:
- New items are invisible until re-indexing completes (same symptom as MAIA-812 but at a different layer)
- Deleted items remain matchable, causing potential false positives
- Edited item descriptions may match against stale embeddings

**Mitigation:** Use a double-buffer index. The new index builds in the background; the stale index serves traffic until the new one is ready. Swap is atomic. For the ~25 minute gap, the lexical fallback lane (which uses the live catalogue, not the embedding index) catches new and edited items, so the system degrades to lexical-only rather than failing. This is why the lexical lane must always be present, even with embeddings: it is the fallback for index staleness, model unavailability, and cold-start tenants.
