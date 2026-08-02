#!/usr/bin/env python3
"""Train independent output-only classifiers for visible risk flags."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import Counter
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression


def sanitize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d", "#", text.casefold())).strip()


def flag_set(value: str) -> set[str]:
    return set() if value == "none" else set(value.split("|"))


def char_wb_counts(text: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for word in text.split():
        padded = f" {word} "
        for size in range(3, 6):
            for offset in range(len(padded) - size + 1):
                counts[padded[offset:offset + size]] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--ocr-cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    truth = {row["case_id"]: row for row in csv.DictReader(args.truth.open())}
    cache = {row["case_id"]: row for row in map(json.loads, args.ocr_cache.open())}
    ids = sorted(truth)
    texts = [sanitize(cache[case_id]["text"]) for case_id in ids]
    labels = [truth[case_id]["risk_flags"] for case_id in ids]
    flags = sorted(set().union(*(flag_set(value) for value in labels)))
    vectorizer = TfidfVectorizer(
        analyzer="char_wb", ngram_range=(3, 5), min_df=3,
        max_features=16000, sublinear_tf=True,
    )
    matrix = vectorizer.fit_transform(texts)
    classifiers = []
    for flag in flags:
        target = [flag in flag_set(value) for value in labels]
        classifier = LogisticRegression(
            C=2.0, class_weight="balanced", max_iter=1200,
            solver="liblinear", random_state=1,
        )
        classifier.fit(matrix, target)
        classifiers.append(classifier)
    names = vectorizer.get_feature_names_out()
    intercepts = [float(model.intercept_[0]) for model in classifiers]
    features = {
        name: [
            float(vectorizer.idf_[index]),
            [float(model.coef_[0, index]) for model in classifiers],
        ]
        for index, name in enumerate(names)
    }
    observed = []
    for text in texts:
        weighted = []
        for token, count in char_wb_counts(text).items():
            if token in features:
                idf, coefficients = features[token]
                weighted.append(((1.0 + math.log(count)) * idf, coefficients))
        norm = math.sqrt(sum(value * value for value, _ in weighted)) or 1.0
        scores = np.asarray([
            intercept + sum((value / norm) * coefficients[index] for value, coefficients in weighted)
            for index, intercept in enumerate(intercepts)
        ])
        observed.append(1.0 / (1.0 + np.exp(-np.clip(scores, -40.0, 40.0))))
    expected = np.stack([model.predict_proba(matrix)[:, 1] for model in classifiers], axis=1)
    maximum_error = float(np.max(np.abs(expected - np.stack(observed))))
    if maximum_error > 1e-10:
        raise RuntimeError(f"exported inference mismatch: {maximum_error}")
    payload = {
        "schema": "akeboss-visible-ocr-risk-multilabel/v1",
        "field": "risk_flags",
        "training_rows": len(ids),
        "sanitization": "casefold_digits_to_hash_collapse_space",
        "analyzer": "char_wb_3_5_sublinear_l2",
        "min_document_frequency": 3,
        "max_features": 16000,
        "logistic_c": 2.0,
        "thresholds": {
            "active_warrant": 0.65,
            "biohazard_red": 0.65,
            "identity_conflict": 0.65,
            "illegible_biometrics": 0.65,
            "memory_tampering": 0.65,
            "planetary_embargo": 0.65,
            "rescinded_denial": 0.65,
            "sponsor_mismatch": 0.65,
        },
        "flags": flags,
        "intercepts": intercepts,
        "features": features,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    print("flags", len(flags), "features", len(names), "maximum_export_error", maximum_error)


if __name__ == "__main__":
    main()
