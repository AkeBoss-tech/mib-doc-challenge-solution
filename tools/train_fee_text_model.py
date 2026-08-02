#!/usr/bin/env python3
"""Train and export the output-only visible-OCR fee model."""

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


def char_wb_counts(text: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for word in text.split():
        padded = f" {word} "
        for size in range(3, 6):
            for offset in range(len(padded) - size + 1):
                counts[padded[offset:offset + size]] += 1
    return counts


def exported_probabilities(
    text: str,
    features: dict[str, tuple[float, tuple[float, ...]]],
    intercepts: tuple[float, ...],
) -> np.ndarray:
    weighted = []
    for token, count in char_wb_counts(sanitize(text)).items():
        if token in features:
            idf, coefficients = features[token]
            weighted.append(((1.0 + math.log(count)) * idf, coefficients))
    norm = math.sqrt(sum(value * value for value, _ in weighted)) or 1.0
    scores = np.asarray([
        intercept + sum((value / norm) * coefficients[index] for value, coefficients in weighted)
        for index, intercept in enumerate(intercepts)
    ])
    sigmoid = 1.0 / (1.0 + np.exp(-np.clip(scores, -40.0, 40.0)))
    return sigmoid / np.sum(sigmoid)


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
    labels = [truth[case_id]["fee_status"] for case_id in ids]
    vectorizer = TfidfVectorizer(
        analyzer="char_wb", ngram_range=(3, 5), min_df=3,
        max_features=12000, sublinear_tf=True,
    )
    matrix = vectorizer.fit_transform(texts)
    classifier = LogisticRegression(
        C=0.5, class_weight="balanced", max_iter=1200,
        solver="liblinear", random_state=1,
    )
    classifier.fit(matrix, labels)
    names = vectorizer.get_feature_names_out()
    features = {
        name: (
            float(vectorizer.idf_[index]),
            tuple(float(value) for value in classifier.coef_[:, index]),
        )
        for index, name in enumerate(names)
    }
    intercepts = tuple(float(value) for value in classifier.intercept_)
    expected = classifier.predict_proba(matrix)
    observed = np.stack([
        exported_probabilities(text, features, intercepts) for text in texts
    ])
    maximum_error = float(np.max(np.abs(expected - observed)))
    if maximum_error > 1e-10:
        raise RuntimeError(f"exported inference mismatch: {maximum_error}")
    payload = {
        "schema": "akeboss-visible-ocr-fee/v1",
        "training_rows": len(ids),
        "sanitization": "casefold_digits_to_hash_collapse_space",
        "analyzer": "char_wb_3_5_sublinear_l2",
        "min_document_frequency": 3,
        "max_features": 12000,
        "logistic_c": 0.5,
        "classes": classifier.classes_.tolist(),
        "minimum_probability": 0.30,
        "minimum_margin": 0.05,
        "intercepts": list(intercepts),
        "features": {
            key: [idf, list(coefficients)]
            for key, (idf, coefficients) in features.items()
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    print("features", len(features))
    print("maximum_export_error", maximum_error)


if __name__ == "__main__":
    main()
