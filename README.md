# Backend Developer — Mindhive Technical Assessment

**Version:** 2026.1 · **Window:** 3 calendar days · **Expected effort:** 14–18 hours · **Format:** open book

---

## ⚡ How to Run Everything

Assume standard Python 3.10+ with `rapidfuzz` installed (`pip install rapidfuzz`).

### 1. Matcher Evaluation (Task 3)
Runs the evaluation harness against the labelled training set (`data/order_lines_train.csv`):
```bash
python evaluate.py
```
*Expected: 420 lines evaluated in <10s (~20ms/line), Precision@Auto ~96.7%, 0 cross-tenant violations.*

### 2. Generate Holdout Predictions (Task 2)
Generates `predictions.csv` for `data/order_lines_holdout.csv`:
```bash
python predict.py
```
*Output: `predictions.csv` (301 predictions with calibrated confidence, decision, reason_code, and top-3 candidates).*

### 3. Report Query Performance Benchmark (Task 4)
Builds the performance SQLite database and validates the optimized report against reference output:
```bash
cd starter
python make_perf_db.py --out ../data/perf.sqlite
python bench_report.py check --db ../data/perf.sqlite --module my_report:run --repeat 5 --budget-s 10
cd ..
```
*Expected: All 8,666 rows match reference on all 13 columns. Execution time ~6–8s (within ≤10s budget, 360×+ speedup vs 3,050s baseline).*

### 4. Sync Adapter Tests & Verification (Task 5)
Runs the isolated unit test suite covering all 6 sync defects, and verifies scenario invariants:
```bash
cd starter/sync
python -m unittest test_sync.py -v
python run_sync.py
cd ../..
```
*Expected: 6/6 tests PASS in <0.05s. `run_sync.py` passes all three invariants.*

---

## 📁 Repository Deliverables Map

| Deliverable | Task | Description |
|---|---|---|
| [`DESIGN.md`](DESIGN.md) | Task 1 | Objective function, pipeline architecture, no-embedding justification, 6 failure modes, boundaries. |
| [`DECISIONS.md`](DECISIONS.md) | Tasks 1–6 | 12 structured decision log entries (context, options, choice, evidence, reversal triggers). |
| [`matcher/`](matcher/) + [`predict.py`](predict.py) | Task 2 | Deterministic-first multi-lane matcher service + holdout generator. |
| [`predictions.csv`](predictions.csv) | Task 2 | Holdout predictions conforming to schema with top-3 candidates. |
| [`evaluate.py`](evaluate.py) + [`EVAL.md`](EVAL.md) | Task 3 | Evaluation harness, metric defense, 20-item hand error analysis, label trap analysis, CI gates. |
| [`starter/my_report.py`](starter/my_report.py) + [`PERF.md`](PERF.md) | Task 4 | Streaming aggregation rewrite, baseline extrapolation, ablation ranking, p95 column, scale ceiling. |
| [`starter/sync/sync_adapter.py`](starter/sync/sync_adapter.py) + [`SYNC.md`](SYNC.md) | Task 5 | Fixed adapter, isolated test suite (`test_sync.py`), defect analysis, vendor contract wishlist. |
| [`SCALE.md`](SCALE.md) | Task 6 | Scaling analysis for 500 tenants, 4M catalogue rows, feedback loop defense, shadow/canary rollouts. |

---

## 0. Read this first

This assessment is deliberately larger than you can perfect in 3 days. That is the
point. We are hiring someone who decides what matters, does that part properly, and can
defend what they left out. A partial submission with sharp reasoning beats a complete
submission that is uniformly shallow.

**You may use any tool, including LLMs and coding agents.** We assume you will. What we
grade is what a model cannot give you: whether the problem was framed correctly, whether
the numbers you report are real, whether the trade-offs were chosen or defaulted into,
and whether you can explain any line of your submission live without hesitation. Every
submission ends in a 60-minute walkthrough where we will ask you to change your code in
front of us.

Two things make copy-paste answers fail here:

1. **The holdout set is unlabelled and scored by us.** You cannot grade yourself against
   our key. Overconfident matchers score *worse* than cautious ones.
2. **You must keep a decision log.** Not a changelog — a record of choices, alternatives
   rejected, and the evidence that settled it. We read it before we read your code.

---

## 1. Context

We turn messy real-world buying signals — WhatsApp messages, emailed PDFs, portal CSV
uploads, transcribed voice notes — into structured sales orders inside our customers'
ERPs. One customer is an industrial hardware distributor; another sells frozen food. Each
runs their own catalogue, their own SKU conventions, and their own buyers who type things
like `remax 4" grinding disc x24 ctn` and expect the right stock code on the order.

The core problem: **given a free-text order line, resolve it to exactly one `item_code`
in that tenant's catalogue — or refuse to answer.**

The economics of that refusal are the whole job.

- A **correct auto-match** saves an operator ~20 seconds.
- An **abstention** costs ~40 seconds: it goes to a human review queue.
- A **wrong auto-match** costs roughly **20× an abstention**: it ships the wrong goods,
  and we find out from an angry customer three days later, after picking, packing, and a
  credit note. Some are never caught and quietly corrupt the alias table that trains the
  next match.

So this is not an accuracy problem. It is a **precision-at-a-chosen-coverage** problem
with asymmetric costs, and the interesting engineering is in knowing when the system does
not know.

---

## 2. What ships with this assessment

```
data/
  catalogue_acme.csv          1,148 items   tenant "acme"    (industrial hardware)
  catalogue_nordic.csv          524 items   tenant "nordic"  (frozen food)
  customer_sku_map.csv          ~710 rows   buyer SKU aliases — dirty on purpose
  uom_reference.csv                         pack/UOM conversions per item
  order_lines_train.csv         420 rows    labelled (`gt_item_code`, blank = abstain)
  order_lines_holdout.csv       300 rows    unlabelled — you predict, we score
  report_reference.json.gz      8,666 rows  correct full-window output of the Task 4 report
starter/
  make_perf_db.py                           builds the Task 4 SQLite database
  report_query.sql                          the slow report (Task 4)
  bench_report.py                           timing + equivalence harness (Task 4)
  sync/fake_erp.py                          the third-party ERP you cannot patch
  sync/sync_adapter.py                      our sync code (Task 5)
  sync/run_sync.py                          symptom reporter (Task 5)
```

Everything is stdlib Python 3.10+ and SQLite. Nothing calls the network.

### Data notes you should verify rather than trust

- Catalogues contain **superseded items** (`*-OLD`, `disabled=1`), **active twins** with
  the same visible name and different pack sizes, and **non-item rows** (`DELIVERY FEE`,
  `OPENING BALANCE`).
- `customer_sku_map.csv` contains **expired mappings**, **the same buyer SKU pointing at
  two different codes**, mappings with `source=inferred_match` and `confidence` well
  below 1.0, and buyer SKUs that happen to look like *another tenant's* item codes.
- Order lines carry typos, trade abbreviations (`S/S`, `ZP`, `SDS`), Malay/English mixing
  (`skru`, `ayam`, `susu`), dropped tokens, pack/UOM confusion, embedded barcodes, and
  lines that are not items at all.
- Roughly a third of the labelled lines have **no correct answer**. `gt_item_code` is
  blank for those. Predicting anything for them is a false positive.
- Tenant isolation is a hard rule. An `acme` line may never resolve to an `NRD-*` code.

---

## 3. Deliverables

A single git repository (bundle or private repo link) containing:

| File | What it is |
|---|---|
| `README.md` | How to run everything. Assume a clean machine, `python3` only. |
| `DESIGN.md` | Task 1. ~1,500 words max, diagrams welcome. |
| `DECISIONS.md` | Decision log. One entry per real choice. Format in §9. |
| `predictions.csv` | Task 2 output over the holdout set. Schema in §5.3. |
| `EVAL.md` | Task 3. Your metrics, your numbers, your error analysis. |
| `PERF.md` | Task 4. Baseline, diagnosis, fix, measured result. |
| `SYNC.md` | Task 5. Defects found, evidence, fixes, what you would change in the contract. |
| `SCALE.md` | Task 6. ~800 words max. |
| source + tests | Whatever you actually built. |

Commit as you go. A repository with one commit called "solution" is a negative signal.

---

## 4. Task 1 — Problem framing and design *(design, 20%)*

Before code. In `DESIGN.md`:

1. **State the objective function.** Turn §1's cost table into something a machine can
   optimise. What exactly are you maximising, subject to what constraint? Justify the
   operating point you chose — and say who in the business gets to move it.
2. **Decompose the pipeline.** What stages, in what order, with what contract between
   them? Be explicit about which stages are deterministic and which are probabilistic,
   and why that ordering is the way round you chose.
3. **Where would an LLM or embeddings actually earn their place?** For each place you use
   one, state what cheaper mechanism you tried first, and what the fallback is when the
   model is unavailable, slow, or wrong. If you use neither, defend that.
4. **Name your failure modes before you build.** List the six most expensive ways this
   system can be confidently wrong on *this data*, and the mechanism that catches each.
5. **Draw the boundary.** What is out of scope for 3 days, and what would you need to
   see in production before you built it?

We are reading for: whether the framing survives contact with the data, whether you
noticed that the expensive mistakes and the common mistakes are different populations,
and whether your design has a place for "I don't know" that is not an afterthought.

---

## 5. Task 2 — Build the matcher *(execution, 25%)*

### 5.1 Requirements

Build a service-shaped Python component that, for one order line, returns either a single
`item_code` or an abstention, plus the evidence for that decision.

Hard requirements:

- **Tenant-scoped.** Cross-tenant resolution is a correctness failure, not a bug.
- **Deterministic given the same inputs.** Same input, same output, run to run.
- **Explainable.** Every decision carries a machine-readable reason. An operator in the
  review queue must be able to see *why*, and an engineer must be able to grep for it.
- **Budgeted.** ≤ 250 ms p95 per line on a laptop, cold caches excluded, no network. Your
  harness must measure and report this, not assume it.
- **Cold-start safe.** A brand-new tenant has a catalogue and zero alias history. It must
  still work, and its behaviour must be different from a mature tenant's — say how.

Suggested (not mandated) shape: cheap exact/normalised identifier lanes → lexical
candidate generation → optional semantic lane → a scoring/arbitration step that decides
between *answer* and *abstain*. If you disagree with that shape, build yours and defend
it in `DESIGN.md`.

### 5.2 Constraints on dependencies

Standard library plus, if you want them: `rapidfuzz`, `scikit-learn`, `numpy`, `sqlite3`,
`sentence-transformers` (or any local embedding model), `duckdb`, `pytest`. Anything that
needs a network call at inference time is out. If a grader cannot run your code offline on
a laptop in under 10 minutes, it does not count as delivered.

If you use embeddings: pin the model, ship the build step, and report the marginal gain
they bought you over the non-semantic baseline. "It felt better" is not a number. Deleting
the embedding lane after measuring that it did not pay is a **positive** result and we will
grade it as one.

### 5.3 Output

`predictions.csv`, one row per holdout line, exactly these columns:

```
line_id,item_code,confidence,decision,reason_code,candidates
```

- `item_code` — blank when abstaining.
- `confidence` — float in [0,1], calibrated in the sense that it is comparable across
  lanes. Explain your calibration in `EVAL.md`.
- `decision` — one of `auto`, `review`, `reject`.
- `reason_code` — short stable token, e.g. `alias_exact`, `barcode_hit`,
  `lexical_unique`, `ambiguous_twins`, `no_candidate_above_floor`, `not_an_item`.
- `candidates` — up to 3 `item_code:score` pairs, `|`-separated, best first. Used to
  score recall@3 even when you abstain.

### 5.4 How we score it

On our labels, over the holdout:

- **Precision on `decision=auto`** — the number that matters most.
- **Coverage** — share of lines auto-answered.
- **Net value** using the §1 cost model, at your chosen operating point.
- **Abstention quality** — of lines with no correct answer, how many you correctly
  refused; of lines you abstained on, how often the right code was in `candidates`.
- **Cross-tenant violations** — any is a hard fail on this task.

A submission that answers everything at 88% precision will score below one that answers
70% of lines at 98%. Know which one you built, and say so.

---

## 6. Task 3 — Evaluation *(rigour, 20%)*

The JD says this is non-negotiable, and we mean it. In `EVAL.md`:

1. **Build the harness.** One command, reproducible, runs against `order_lines_train.csv`
   and prints your metrics. Include per-tenant and per-noise-class breakdowns — the
   classes are not labelled in the data you receive, so define your own segmentation and
   justify it.
2. **Defend your metric.** Show what accuracy alone would have told you and why that would
   have been misleading here. Include the operating-point curve (precision vs coverage) —
   not a single point.
3. **Error analysis, by hand.** Take **20 specific failures**, name them by `line_id`,
   and give root cause, cost class, and the fix you would make. Group them: which are one
   bug, which are the same missing capability, which are data problems you cannot fix in
   code. This section is where we learn the most about you; do not delegate it.
4. **Show the label problem.** The provided labels are not perfect. Find at least three
   lines where you believe the label is wrong or the task is under-specified, argue it,
   and say what you would do about it in production. (Yes, this is a real trap. Yes, they
   exist.)
5. **Regression safety.** How would this harness stop a bad change from shipping? Be
   concrete: thresholds, gates, what breaks the build, and how you avoid a benchmark that
   silently rots.

---

## 7. Task 4 — The report that times out *(performance, 15%)*

An internal dashboard runs `starter/report_query.sql` against the match-event ledger
(~1.1M rows). Over the full window it now takes **the better part of an hour** — long
enough that nobody has watched it finish this quarter; ops screenshot last month's numbers
instead.

You cannot iterate against a query like that, and we are not asking you to. **The point of
this task is deciding what to measure when you cannot afford to measure everything.**

```bash
cd starter
python3 make_perf_db.py --out ../data/perf.sqlite     # ~1.1M events, ~20s, ~120 MB

# DO NOT run report_query.sql over the full window. Narrow it and measure a slice:
python3 bench_report.py baseline --db ../data/perf.sqlite --sql one_day.sql --out one_day.json

# check your rewrite against the shipped reference result, as often as you like:
python3 bench_report.py check --db ../data/perf.sqlite --sql my_report.sql \
       --repeat 5 --budget-s 10
```

`data/report_reference.json.gz` is the correct full-window result, 8,666 rows. It ships
with the assessment so equivalence checking costs you seconds instead of hours. It was
produced by an independently written query and verified row-for-row against
`report_query.sql` on slices — if you believe it is wrong, say so and show us.

**Budget: the full window in ≤ 10 seconds**, `check` passing, on a laptop.

Deliver in `PERF.md`:

1. **An estimated baseline, with your method shown.** Do not run the whole thing —
   estimate it. Measure slices, state what you are extrapolating *along*, and validate the
   assumption instead of asserting it: does the cost scale with rows in the window, with
   output groups, with tenants, with days? Two of those give answers that differ by 4×, and
   only one of them is right. Show the measurements that told you which.
2. **The diagnosis, ranked by cost, established empirically.** Ablate: remove one metric at
   a time and re-measure. Report the ranking you found, including the parts that surprised
   you. If the answer turns out to be "no single column dominates", that is a finding —
   say what it implies about where the fix has to happen, and prove it the same way.
3. **The fix.** Byte-identical results, inside the budget. State your target before you
   start, then report what you actually hit — including if you missed.
4. **One new column: `p95_latency_ms`** — nearest-rank p95 of `latency_ms` over the same
   tenant-day set as `max_latency_ms`. Adding it must not blow the budget.
5. **What you did not fix, and why.** Under a real deadline you fix the expensive thing
   and leave the rest. Name what you left, what it costs, and when you would come back.
6. **The trade-offs you accepted.** Indexes cost write throughput; this ledger takes ~40
   writes per order line at peak. Materialisation costs freshness; ops believe this
   dashboard is live. Schema changes cost a migration on a hot table. Say what you chose
   and what it costs.
7. **The honest ceiling.** At 50× today's volume, does your fix hold? What breaks first,
   what is the next architecture, and why is that not what you built today?

A correct rewrite that lands inside the budget is table stakes. What we are grading is
whether you found the dominant cost before touching anything, and whether you can prove
the ranking rather than assert it.

---

## 8. Task 5 — The sync that double-wrote *(debugging, 15%)*

`starter/sync/` contains a two-way ERP sync that is live and has three open tickets:

- **MAIA-812** — "some items never appear on our side until someone edits them"
- **MAIA-830** — "price history shows two updates a second apart, we only made one"
- **MAIA-844** — "an edit made in the ERP was overwritten by our older value"

```bash
cd starter/sync && python3 run_sync.py
```

`run_sync.py` reports symptoms. It does not diagnose, and it is not a test suite.
`fake_erp.py` is the vendor's system: you may **not** modify it, and its awkward semantics
(second-resolution cursors, non-transactional batches, 504s after commit, 60-second exact-
match idempotency, timestamps in the server's local zone) are the environment, not bugs to
report.

Deliver in `SYNC.md`, plus code and tests:

1. **A defect list.** For each: the ticket it explains, the exact mechanism, and a
   **failing test that isolates it and passes after your fix**. One test per defect — a
   single test that goes green because you fixed four things is worth much less.
2. **There are more defects than there are tickets.** Some are latent: they have not
   produced a ticket yet because the conditions have not lined up. Find them, and say what
   would have to happen in production for each to surface.
3. **Fixes**, with the invariant each one restores stated explicitly.
4. **The contract you wish you had.** What would you ask the vendor for, in priority
   order, and how do you stay correct while they say no?
5. **What breaks at scale.** This runs per-tenant every 5 minutes across 500 tenants. Say
   what falls over first and how you would know before a customer tells you.

Assume the process can be killed at any moment. Crash-safety is part of correctness here.

---

## 9. Task 6 — Scale and rollout *(judgement, 5%)*

`SCALE.md`, 800 words. You are now running this for 500 tenants, 4M catalogue rows, ~150k
order lines a day, and the alias table grows from every confirmed order.

- What is the first thing to break, and what is your evidence?
- If you used embeddings: what does re-indexing cost when a tenant edits 40k items at 2am,
  and how does a tenant with a stale index behave in the meantime?
- The alias table learns from operator confirmations — including their mistakes. How do
  you stop a feedback loop that entrenches a wrong match, given that a wrong match is 20x
  the cost of an abstention?
- How do you ship a matcher change safely? Shadow, canary, both, neither — and how you
  decide it worked when the ground truth arrives days later.

---

## 10. The decision log

`DECISIONS.md`. One entry per decision that could reasonably have gone another way:

```markdown
## D-04 — Reject the dense retrieval lane
**Context:** lexical lane already at 96.2% precision / 61% coverage on train.
**Options:** (a) MiniLM over item_name+description, (b) char n-gram TF-IDF, (c) nothing.
**Chose:** (b), then (c) for the second pass.
**Evidence:** dense added +3.1pt coverage but cost 4 FPs on twin items (train, n=420);
              at 20x FP cost that is net negative. See EVAL.md §4.
**Reversal trigger:** if buyer-typed lines get longer than ~8 tokens, revisit.
```

Eight to fifteen entries is a healthy range. We will pick three at random in the
walkthrough and ask you to re-argue them. An entry you cannot defend is worse than an
entry you never wrote.

---

## 11. Rules, scope, and honesty

- **Timebox is yours to manage.** If you spend all 3 days on Task 2 and stub the rest,
  say so in `README.md` and tell us why that was the right call. We will grade the
  argument.
- **Do not hand-tune to the holdout.** You cannot score it anyway, and we can see it.
- **Attribute your tools.** If a section is substantially model-generated, say so — we do
  not mind, and it costs nothing. Claiming otherwise, when the walkthrough disagrees, ends
  the process.
- **If something in this brief is wrong, ambiguous, or contradicts the data, say so** in
  `README.md` and proceed under a stated assumption. Finding the ambiguities is part of
  the assessment.
- **Questions are allowed** at any point during the 3 days. Asking a sharp one is a
  positive signal; we will note it.

## 12. Walkthrough (60 minutes, after submission)

- 10 min — you present the problem as you framed it. Not your code: the problem.
- 20 min — we drive: pick failures from your own error analysis, ask why, ask what you'd
  do differently, ask what happens if the cost ratio changes from 20× to 3×.
- 20 min — **live change.** We give you a new noise pattern or a shifted constraint and
  you modify your code with us watching. Small, scoped, and in your own codebase. Tools
  allowed; explanation required.
- 10 min — your questions.

Good luck. Build the part that matters and tell us what you skipped.
