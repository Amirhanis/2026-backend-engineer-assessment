#!/usr/bin/env python3
"""Evaluation harness for Task 3.

Runs the matcher against the training set, computes metrics, and prints
a detailed report including the net value based on the cost model.
"""

import csv
import sys
import time
from collections import defaultdict
from matcher import build_pipeline
from matcher.config import COST_CORRECT_AUTO, COST_ABSTENTION, COST_WRONG_AUTO


def evaluate():
    print("Loading pipeline...")
    pipeline = build_pipeline()

    lines = []
    with open("data/order_lines_train.csv", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            lines.append(row)

    print(f"Loaded {len(lines)} training lines. Evaluating...")

    results = []
    start_t = time.perf_counter()
    for row in lines:
        res = pipeline.match(
            line_id=row["line_id"],
            tenant=row["tenant"],
            raw_text=row["raw_text"],
            customer_id=row["customer_id"],
            buyer_sku=row["buyer_sku"],
            raw_barcode=row["raw_barcode"],
        )
        results.append((row, res))
    elapsed = time.perf_counter() - start_t
    
    print(f"Evaluation finished in {elapsed:.3f}s ({elapsed/len(lines)*1000:.1f}ms/line)\n")

    # Metrics
    total = len(lines)
    auto_count = 0
    correct_auto = 0
    wrong_auto = 0
    abstain_count = 0  # review or reject
    
    gt_blank_count = 0
    gt_blank_abstained = 0
    gt_present_abstained = 0
    gt_in_candidates = 0
    
    cross_tenant_violations = 0

    tenant_stats = defaultdict(lambda: {"total": 0, "auto": 0, "correct": 0, "wrong": 0})
    reason_stats = defaultdict(lambda: {"auto_correct": 0, "auto_wrong": 0, "abstain": 0})

    errors = []

    for row, res in results:
        tenant = row["tenant"]
        gt = row["gt_item_code"].strip()
        is_auto = res.decision == "auto"
        pred = res.item_code
        
        tenant_stats[tenant]["total"] += 1
        
        if not gt:
            gt_blank_count += 1
            
        if is_auto:
            auto_count += 1
            tenant_stats[tenant]["auto"] += 1
            
            # Cross-tenant check
            if pred and ((tenant == "acme" and pred.startswith("NRD-")) or 
                         (tenant == "nordic" and pred.startswith("ACM-"))):
                cross_tenant_violations += 1
                
            if pred == gt:
                correct_auto += 1
                tenant_stats[tenant]["correct"] += 1
                reason_stats[res.reason_code]["auto_correct"] += 1
            else:
                wrong_auto += 1
                tenant_stats[tenant]["wrong"] += 1
                reason_stats[res.reason_code]["auto_wrong"] += 1
                errors.append({
                    "line_id": row["line_id"],
                    "text": row["raw_text"],
                    "pred": pred,
                    "gt": gt,
                    "reason": res.reason_code,
                    "candidates": [c.item_code for c in res.candidates]
                })
        else:
            abstain_count += 1
            reason_stats[res.reason_code]["abstain"] += 1
            if not gt:
                gt_blank_abstained += 1
            else:
                gt_present_abstained += 1
                # Was the GT in the candidates?
                if any(c.item_code == gt for c in res.candidates):
                    gt_in_candidates += 1

    precision = correct_auto / auto_count if auto_count else 0.0
    coverage = auto_count / total if total else 0.0
    net_value = (correct_auto * COST_CORRECT_AUTO) + (abstain_count * COST_ABSTENTION) + (wrong_auto * COST_WRONG_AUTO)
    
    print("=== OVERALL METRICS ===")
    print(f"Total Lines:     {total}")
    print(f"Coverage:        {coverage:.1%} ({auto_count}/{total} auto-answered)")
    print(f"Precision@Auto:  {precision:.1%} ({correct_auto}/{auto_count} correct)")
    print(f"Wrong Autos:     {wrong_auto} (Costly!)")
    print(f"Abstentions:     {abstain_count}")
    print(f"Net Value:       {net_value} seconds")
    print(f"Cross-Tenant:    {cross_tenant_violations}")

    print("\n=== ABSTENTION QUALITY ===")
    print(f"Lines with no GT (should abstain): {gt_blank_count}")
    print(f"Correctly abstained on no-GT:      {gt_blank_abstained} / {gt_blank_count} ({gt_blank_abstained/gt_blank_count if gt_blank_count else 0:.1%})")
    print(f"Abstained but GT existed:          {gt_present_abstained}")
    print(f"  ...and GT was in candidates:     {gt_in_candidates} / {gt_present_abstained} ({gt_in_candidates/gt_present_abstained if gt_present_abstained else 0:.1%})")

    print("\n=== PER-TENANT BREAKDOWN ===")
    for t, s in tenant_stats.items():
        cov = s["auto"] / s["total"] if s["total"] else 0
        prec = s["correct"] / s["auto"] if s["auto"] else 0
        print(f"  {t.upper():<7} - Coverage: {cov:.1%} ({s['auto']}/{s['total']}), Precision: {prec:.1%} ({s['correct']}/{s['auto']}), Wrong: {s['wrong']}")

    print("\n=== DECISION REASONS ===")
    for r, s in sorted(reason_stats.items(), key=lambda x: x[1]["auto_correct"] + x[1]["abstain"], reverse=True):
        print(f"  {r:<25} Auto Correct: {s['auto_correct']:<3}  Auto Wrong: {s['auto_wrong']:<3}  Abstain: {s['abstain']:<3}")

    if errors:
        print("\n=== AUTO ERRORS ===")
        for e in errors:
            print(f"  {e['line_id']} [{e['reason']}] Text: '{e['text']}'")
            print(f"    Pred: {e['pred']}")
            print(f"    GT:   {e['gt']}")
            print(f"    Cands: {e['candidates']}")
            print()


if __name__ == "__main__":
    evaluate()
