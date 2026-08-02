#!/usr/bin/env python3
"""Train and export the compact visible-OCR denial model."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import Counter
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression


def sanitize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d", "#", text.casefold())).strip()


def char_wb_counts(text: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for word in re.sub(r"\s+", " ", text).split():
        padded = f" {word} "
        for size in range(3, 6):
            offset = 0
            counts[padded[offset:offset + size]] += 1
            while offset + size < len(padded):
                offset += 1
                counts[padded[offset:offset + size]] += 1
            if offset == 0:
                break
    return counts


def exported_probability(text: str, features: dict[str, tuple[float, float]], intercept: float) -> float:
    weighted: list[tuple[float, float]] = []
    for token, count in char_wb_counts(sanitize(text)).items():
        if token in features:
            idf, coefficient = features[token]
            weighted.append(((1.0 + math.log(count)) * idf, coefficient))
    norm = math.sqrt(sum(value * value for value, _ in weighted)) or 1.0
    score = intercept + sum((value / norm) * coefficient for value, coefficient in weighted)
    return 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, score))))


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
    labels = [truth[case_id]["adjudication"] == "DENIED" for case_id in ids]
    vectorizer = TfidfVectorizer(
        analyzer="char_wb", ngram_range=(3, 5), min_df=4,
        max_features=8000, sublinear_tf=True,
    )
    matrix = vectorizer.fit_transform(texts)
    classifier = LogisticRegression(
        C=2.0, class_weight="balanced", max_iter=1000,
        solver="liblinear", random_state=1,
    )
    classifier.fit(matrix, labels)
    names = vectorizer.get_feature_names_out()
    features = {
        name: (float(vectorizer.idf_[index]), float(classifier.coef_[0, index]))
        for index, name in enumerate(names)
    }
    expected = classifier.predict_proba(matrix)[:, 1]
    observed = [exported_probability(text, features, float(classifier.intercept_[0])) for text in texts]
    maximum_error = max(abs(float(left) - right) for left, right in zip(expected, observed))
    if maximum_error > 1e-10:
        raise RuntimeError(f"exported inference mismatch: {maximum_error}")
    payload = {
        "schema": "akeboss-visible-ocr-denial/v1",
        "training_rows": len(ids),
        "sanitization": "casefold_digits_to_hash_collapse_space",
        "analyzer": "char_wb_3_5_sublinear_l2",
        "min_document_frequency": 4,
        "max_features": 8000,
        "logistic_c": 2.0,
        "threshold": 0.50,
        "intercept": float(classifier.intercept_[0]),
        "features": {key: list(value) for key, value in features.items()},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    print("features", len(features))
    print("maximum_export_error", maximum_error)


if __name__ == "__main__":
    main()
