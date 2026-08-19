#!/usr/bin/env python3
"""Generate predictions for the holdout set."""

import csv
from matcher import build_pipeline


def predict():
    print("Loading pipeline...")
    pipeline = build_pipeline()

    print("Processing holdout set...")
    with open("data/order_lines_holdout.csv", encoding="utf-8") as f_in, \
         open("predictions.csv", "w", encoding="utf-8", newline="") as f_out:
        
        reader = csv.DictReader(f_in)
        fieldnames = ["line_id", "item_code", "confidence", "decision", "reason_code", "candidates"]
        writer = csv.DictWriter(f_out, fieldnames=fieldnames)
        writer.writeheader()

        for row in reader:
            res = pipeline.match(
                line_id=row["line_id"],
                tenant=row["tenant"],
                raw_text=row["raw_text"],
                customer_id=row["customer_id"],
                buyer_sku=row["buyer_sku"],
                raw_barcode=row["raw_barcode"],
            )
            
            writer.writerow({
                "line_id": res.line_id,
                "item_code": res.item_code,
                "confidence": f"{res.confidence:.4f}",
                "decision": res.decision,
                "reason_code": res.reason_code,
                "candidates": res.candidates_str(),
            })

    print("Done. Wrote predictions.csv")


if __name__ == "__main__":
    predict()
