#!/usr/bin/env python3
"""Cross-fit one generic visible-OCR categorical output model."""

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


DEFAULTS = {
    "species_code": "unknown", "home_world": "unknown", "visa_class": "unknown",
    "declared_purpose": "unknown",
}


def sanitize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d", "#", text.casefold())).strip()


def group(case_id: str) -> str:
    value = int(hashlib.sha256(case_id.encode()).hexdigest()[:8], 16) % 2
    return "development" if value == 0 else "holdout"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--field", choices=tuple(DEFAULTS), required=True)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--ocr-cache", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    args = parser.parse_args()
    truth = {row["case_id"]: row for row in csv.DictReader(args.truth.open())}
    cache = {row["case_id"]: row for row in map(json.loads, args.ocr_cache.open())}
    predictions = {row["case_id"]: row for row in map(json.loads, args.predictions.open())}
    ids = np.asarray(sorted(truth), dtype=object)
    texts = np.asarray([sanitize(cache[case_id]["text"]) for case_id in ids], dtype=object)
    actual = np.asarray([truth[case_id][args.field] for case_id in ids], dtype=object)
    current = np.asarray([predictions[case_id][args.field] for case_id in ids], dtype=object)
    classes = np.asarray(sorted(set(actual)), dtype=object)
    probabilities = np.zeros((len(ids), len(classes)))
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=8090)
    for train, test in folds.split(texts, actual):
        vectorizer = TfidfVectorizer(
            analyzer="char_wb", ngram_range=(3, 5), min_df=3,
            max_features=16000, sublinear_tf=True,
        )
        train_matrix = vectorizer.fit_transform(texts[train])
        test_matrix = vectorizer.transform(texts[test])
        model = LogisticRegression(
            C=0.5, class_weight="balanced", max_iter=1200,
            solver="liblinear", random_state=1,
        )
        model.fit(train_matrix, actual[train])
        fold_probabilities = model.predict_proba(test_matrix)
        for source, label in enumerate(model.classes_):
            probabilities[test, np.flatnonzero(classes == label)[0]] = fold_probabilities[:, source]
    order = np.argsort(probabilities, axis=1)
    proposed = classes[order[:, -1]]
    maximum = probabilities[np.arange(len(ids)), order[:, -1]]
    margin = maximum - probabilities[np.arange(len(ids)), order[:, -2]]
    group_masks = {
        name: np.asarray([group(case_id) == name for case_id in ids])
        for name in ("development", "holdout")
    }
    results = []
    for scope in ("default", "invalid", "all"):
        eligible = {
            "default": current == DEFAULTS[args.field],
            "invalid": ~np.isin(current, classes),
            "all": np.ones(len(ids), dtype=bool),
        }[scope]
        for probability in (0.08, 0.10, 0.12, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60):
            for margin_threshold in (0.00, 0.01, 0.02, 0.05, 0.10, 0.15, 0.20):
                changed = eligible & (proposed != current) & (maximum >= probability) & (margin >= margin_threshold)
                result = {
                    "field": args.field, "scope": scope, "probability": probability,
                    "margin": margin_threshold, "changed": int(np.sum(changed)),
                    "gains": int(np.sum(changed & (proposed == actual) & (current != actual))),
                    "losses": int(np.sum(changed & (current == actual) & (proposed != actual))),
                    "net": int(np.sum(changed & (proposed == actual)) - np.sum(changed & (current == actual))),
                    "groups": {},
                }
                for name, group_mask in group_masks.items():
                    selected = changed & group_mask
                    result["groups"][name] = {
                        "changed": int(np.sum(selected)),
                        "gains": int(np.sum(selected & (proposed == actual) & (current != actual))),
                        "losses": int(np.sum(selected & (current == actual) & (proposed != actual))),
                        "net": int(np.sum(selected & (proposed == actual)) - np.sum(selected & (current == actual))),
                    }
                results.append(result)
    results.sort(key=lambda row: (
        min(row["groups"]["development"]["net"], row["groups"]["holdout"]["net"]),
        row["net"], -row["losses"],
    ), reverse=True)
    for scope in ("default", "invalid", "all"):
        scoped = [row for row in results if row["scope"] == scope]
        for row in scoped[:8]:
            print(json.dumps(row, separators=(",", ":")))


if __name__ == "__main__":
    main()
