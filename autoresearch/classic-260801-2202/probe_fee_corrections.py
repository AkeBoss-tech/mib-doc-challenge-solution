#!/usr/bin/env python3
"""Cross-fit generic OCR fee corrections against the current predictions."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold


def sanitize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d", "#", text.casefold())).strip()


def group(case_id: str) -> str:
    value = int(hashlib.sha256(case_id.encode()).hexdigest()[:8], 16) % 2
    return "development" if value == 0 else "holdout"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--ocr-cache", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    args = parser.parse_args()

    truth = {row["case_id"]: row for row in csv.DictReader(args.truth.open())}
    cache = {row["case_id"]: row for row in map(json.loads, args.ocr_cache.open())}
    predictions = {row["case_id"]: row for row in map(json.loads, args.predictions.open())}
    ids = np.asarray(sorted(truth), dtype=object)
    texts = np.asarray([sanitize(cache[case_id]["text"]) for case_id in ids], dtype=object)
    labels = np.asarray([truth[case_id]["fee_status"] for case_id in ids], dtype=object)
    current = np.asarray([predictions[case_id]["fee_status"] for case_id in ids], dtype=object)
    probabilities = np.zeros((len(ids), 4))
    classes = np.asarray(["paid", "unknown", "unpaid", "waived"], dtype=object)
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=8090)
    for train, test in folds.split(texts, labels):
        vectorizer = TfidfVectorizer(
            analyzer="char_wb", ngram_range=(3, 5), min_df=3,
            max_features=12000, sublinear_tf=True,
        )
        train_matrix = vectorizer.fit_transform(texts[train])
        test_matrix = vectorizer.transform(texts[test])
        model = LogisticRegression(
            C=0.5, class_weight="balanced", max_iter=1200,
            solver="liblinear", random_state=1,
        )
        model.fit(train_matrix, labels[train])
        fold_probabilities = model.predict_proba(test_matrix)
        for source, label in enumerate(model.classes_):
            probabilities[test, np.flatnonzero(classes == label)[0]] = fold_probabilities[:, source]

    order = np.argsort(probabilities, axis=1)
    proposed = classes[order[:, -1]]
    maximum = probabilities[np.arange(len(ids)), order[:, -1]]
    margin = maximum - probabilities[np.arange(len(ids)), order[:, -2]]
    results = []
    for scope in ("unknown", "known", "all"):
        eligible = {
            "unknown": current == "unknown",
            "known": current != "unknown",
            "all": np.ones(len(ids), dtype=bool),
        }[scope]
        for minimum_probability in (0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90):
            for minimum_margin in (0.05, 0.10, 0.20, 0.30, 0.40, 0.50):
                changed = eligible & (proposed != current) & (maximum >= minimum_probability) & (margin >= minimum_margin)
                row = {
                    "scope": scope,
                    "minimum_probability": minimum_probability,
                    "minimum_margin": minimum_margin,
                    "changed": int(np.sum(changed)),
                    "correct_gains": int(np.sum(changed & (proposed == labels) & (current != labels))),
                    "correct_losses": int(np.sum(changed & (current == labels) & (proposed != labels))),
                    "net": int(np.sum(changed & (proposed == labels)) - np.sum(changed & (current == labels))),
                    "groups": {},
                }
                for name in ("development", "holdout"):
                    selected = changed & np.asarray([group(case_id) == name for case_id in ids])
                    row["groups"][name] = {
                        "changed": int(np.sum(selected)),
                        "correct_gains": int(np.sum(selected & (proposed == labels) & (current != labels))),
                        "correct_losses": int(np.sum(selected & (current == labels) & (proposed != labels))),
                        "net": int(np.sum(selected & (proposed == labels)) - np.sum(selected & (current == labels))),
                    }
                results.append(row)
    results.sort(key=lambda row: (
        min(row["groups"]["development"]["net"], row["groups"]["holdout"]["net"]),
        row["net"], -row["correct_losses"],
    ), reverse=True)
    for scope in ("unknown", "known", "all"):
        scoped = [row for row in results if row["scope"] == scope]
        for row in scoped[:10]:
            print(json.dumps(row, separators=(",", ":")))


if __name__ == "__main__":
    main()
