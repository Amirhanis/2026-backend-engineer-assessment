# Design Document (Task 1)

## 1. Objective Function

The core problem is an asymmetric cost minimization problem disguised as a matching task. According to the business rules:
- **Correct auto-match**: Saves 20 seconds
- **Abstention (Review/Reject)**: Costs 40 seconds
- **Wrong auto-match**: Costs ~800 seconds (20× an abstention)

This gives us the net value equation per order line:
`Net Value = (20 × Correct) - (40 × Abstain) - (800 × Wrong)`

To maximize Net Value, the model's operating point must heavily prioritize **Precision**. A single wrong auto-match wipes out the time saved from 40 correct matches. Therefore, the system is designed to maximize **Coverage** subject to the strict constraint that **Precision ≥ 96%**. 

**Who moves the operating point?** The operations lead or product manager. If the human review queue is overwhelmed, they can lower the confidence thresholds (increasing coverage but risking more costly mis-ships). If mis-ships spike, they can raise the thresholds (increasing the review queue).

## 2. Pipeline Decomposition

The pipeline is ordered from cheapest, most deterministic rules to more expensive probabilistic matching, enforcing early exits.

1. **Not-an-Item Filter (Deterministic)**: Regex and keyword heuristics quickly reject lines that are clearly not items (e.g., "subtotal", "deliver by friday", "cadbury"). This prevents the system from confidently matching noise.
2. **Barcode Exact Match (Deterministic)**: If a barcode is provided, lookup the exact active item. Uniquely matching barcodes skip all other processing.
3. **Alias Map Exact Match (Deterministic)**: Resolves buyer SKUs to our item codes using the provided `customer_sku_map`. Enforces strict filtering: ignores expired entries, discards mappings with confidence < 0.70, and enforces tenant isolation.
4. **Direct Code Match (Deterministic)**: Checks if the raw text or SKU is exactly our item code format (e.g., `ACM-HEXB...`), catching cases where buyers explicitly order by our internal ID.
5. **Lexical Scoring (Probabilistic)**: Normalizes text (expanding trade abbreviations like "S/S" to "Stainless", removing noise words) and uses fuzzy token sorting to generate candidate scores against the active catalogue.
6. **Twin Detection & Arbitration (Deterministic)**: Calculates score gaps. If the top candidate is not sufficiently distinct from the runner-up (the "twin" problem), the system abstains and sends it to human review.

This order ensures that cheap, perfectly accurate signals (barcodes, explicit SKUs) preempt the fuzzy lexical layer, acting as a fast path.

## 3. The Role of Embeddings / LLMs

No LLMs or dense embedding models were used in this pipeline. 

**Why?** The data consists of highly structured hardware and food commodities (Brand + Type + Size + Finish). The primary failure modes are not semantic understanding ("I want something to cut wood"), but rather missing structural information (e.g. "Tolsen Hex Bolt M8x50" without specifying the finish).
I initially considered an embedding lane (e.g., `sentence-transformers`), but local testing on the catalogue names showed that fuzzy lexical matching with `rapidfuzz` combined with strict heuristic penalties (e.g., explicitly penalizing for missing finishes or colors) achieved ~96% precision at ~44% coverage on the training set. Dense embeddings struggle with exact token matches (like "M8x50" vs "M8x30") and would likely introduce costly false positives on "twin" items. The fallback when dense retrieval is unavailable is lexical matching anyway, so optimizing the lexical layer first is strictly dominant. 

## 4. Failure Modes

1. **The Twin Problem**: A line specifies "Stallion Hose Clip" but omits the material (SS304 vs Zinc). 
   - *Guard*: Lexical scoring uses a `TWIN_GAP_MIN` threshold. If the top two scores are within the gap, the system abstains.
2. **Missing Crucial Specifications**: "Remax Safety Helmet Standard" omits the color.
   - *Guard*: A hardcoded `IMPORTANT_WORDS` penalty. If the catalogue item contains a key structural word (finishes, colors, "bulk") that the order line lacks, the candidate's score is heavily penalized.
3. **Cross-Tenant Contamination**: A Nordic buyer uses an Acme-like SKU, matching a real Acme item.
   - *Guard*: The entire pipeline is heavily tenant-scoped. Catalogues and alias maps are partitioned by tenant, and cross-tenant lookups are strictly prohibited.
4. **Obsolete Item Traps**: Buyers order an item that was recently superseded (`-OLD`).
   - *Guard*: The catalogue index filters out items with `disabled=1` or the `-OLD` suffix. The alias map lookup also validates that the target item is currently active.
5. **Noisy Text / Not-an-Item**: Lines like "subtotal" matching random items.
   - *Guard*: A deterministic pre-filter catches common accounting terms, instructions ("deliver by"), and known non-catalogue brands ("Cadbury").
6. **Malicious / Incorrect SKU Mapping**: A buyer SKU maps to a completely wrong item due to a bad inferred match.
   - *Guard*: The alias loader enforces a minimum confidence threshold (0.7) and ignores expired mappings.

## 5. System Boundary

**Out of Scope for 3 Days:**
- **Dynamic Alias Learning**: The system currently uses a static CSV. In production, we need a feedback loop where human reviewer decisions safely append new, high-confidence entries to the alias table.
- **Advanced Semantic Parsing**: Machine learning named-entity recognition (NER) to perfectly extract Brand, Size, and Material into a structured JSON prior to matching.

**Production Requirements:**
- Observability: We need a shadow-mode deployment to measure precision on live unseen data before flipping the switch to `auto`.
- Telemetry: Counters for every `reason_code` to monitor data drift (e.g., if `ambiguous_twins` spikes, we know buyers are dropping a specific new spec).
