#!/usr/bin/env python3
"""Cross-fit a visible-OCR correctness estimator for confidence calibration."""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss
from sklearn.model_selection import StratifiedKFold


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--ocr-cache", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    args = parser.parse_args()
    truth = {row["case_id"]: row for row in csv.DictReader(args.truth.open())}
    cache = {row["case_id"]: row for row in map(json.loads, args.ocr_cache.open())}
    predictions = {row["case_id"]: row for row in map(json.loads, args.predictions.open())}
    ids = sorted(truth)
    labels = np.asarray([truth[x]["adjudication"] == predictions[x]["adjudication"] for x in ids])
    texts = []
    for case_id in ids:
        row = predictions[case_id]
        metadata = " ".join((
            f"pred_{row['adjudication'].casefold()}", f"visa_{row['visa_class'].casefold()}",
            f"fee_{row['fee_status'].casefold()}",
            "risk_clean" if row["risk_flags"] == "none" else "risk_adverse",
            "sponsor_missing" if row["sponsor_id"] == "SPN-0000" else "sponsor_present",
            "date_missing" if row["arrival_date"] == "1900-01-01" else "date_present",
        ))
        visible = re.sub(r"\s+", " ", re.sub(r"\d", "#", cache[case_id]["text"].casefold())).strip()
        texts.append(metadata + " " + visible)
    texts = np.asarray(texts, dtype=object)
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=8090)
    for c_value in (0.1, 0.5, 2.0, 8.0):
        probabilities = np.zeros(len(ids))
        for train, test in folds.split(texts, labels):
            vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=3,
                                         max_features=12000, sublinear_tf=True)
            train_matrix = vectorizer.fit_transform(texts[train])
            test_matrix = vectorizer.transform(texts[test])
            classifier = LogisticRegression(C=c_value, max_iter=1200, solver="liblinear", random_state=1)
            classifier.fit(train_matrix, labels[train])
            probabilities[test] = classifier.predict_proba(test_matrix)[:, 1]
        print(json.dumps({"c": c_value, "brier": round(brier_score_loss(labels, probabilities), 6),
                          "mean_probability": round(float(np.mean(probabilities)), 6),
                          "actual_correctness": round(float(np.mean(labels)), 6)}, separators=(",", ":")))


if __name__ == "__main__":
    main()
