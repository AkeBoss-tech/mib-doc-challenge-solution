#!/usr/bin/env python3
"""Compare generic visible-OCR approval routers with out-of-fold gates.

This is an analysis-only probe. It reads rendered OCR, public labels, and an
existing prediction file; it does not export or modify the runtime model.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

import numpy as np
from scipy.sparse import hstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold


def sanitize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d", "#", text.casefold())).strip()


def vectorizers(name: str):
    if name == "char35":
        return [TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=3,
                                max_features=12000, sublinear_tf=True)]
    if name == "char26":
        return [TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 6), min_df=2,
                                max_features=24000, sublinear_tf=True)]
    if name == "word12":
        return [TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=2,
                                max_features=18000, sublinear_tf=True)]
    if name == "hybrid":
        return [
            TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2,
                            max_features=18000, sublinear_tf=True),
            TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=2,
                            max_features=12000, sublinear_tf=True),
        ]
    raise ValueError(name)


def matrices(builders, train_texts, test_texts):
    train_parts = []
    test_parts = []
    for builder in builders:
        train_parts.append(builder.fit_transform(train_texts))
        test_parts.append(builder.transform(test_texts))
    return hstack(train_parts).tocsr(), hstack(test_parts).tocsr()


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
    texts = np.asarray([sanitize(cache[case_id]["text"]) for case_id in ids], dtype=object)
    labels = np.asarray([truth[case_id]["adjudication"] == "APPROVED" for case_id in ids])
    actual = np.asarray([truth[case_id]["adjudication"] for case_id in ids])
    baseline = np.asarray([predictions[case_id]["adjudication"] for case_id in ids])
    review_gate = baseline == "NEEDS_REVIEW"
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=8090)

    for feature_name in ("char35", "char26", "word12", "hybrid"):
        for c_value in (0.5, 2.0, 8.0, 32.0):
            probabilities = np.zeros(len(ids))
            for train, test in folds.split(texts, labels):
                train_matrix, test_matrix = matrices(
                    vectorizers(feature_name), texts[train], texts[test],
                )
                classifier = LogisticRegression(
                    C=c_value, class_weight="balanced", max_iter=1200,
                    solver="liblinear", random_state=1,
                )
                classifier.fit(train_matrix, labels[train])
                probabilities[test] = classifier.predict_proba(test_matrix)[:, 1]
            # Sweep observed OOF scores. Catastrophic approvals are forbidden;
            # ordinary review false approvals are reported rather than hidden.
            best = (0, 0, 0, float(np.max(probabilities[review_gate])) + 1e-9, 0)
            for threshold in sorted(set(probabilities[review_gate]), reverse=True):
                routed = review_gate & (probabilities >= threshold)
                catastrophic = int(np.sum(routed & (actual == "DENIED")))
                if catastrophic:
                    continue
                recovered = int(np.sum(routed & (actual == "APPROVED")))
                review_errors = int(np.sum(routed & (actual == "NEEDS_REVIEW")))
                net = recovered - review_errors
                candidate = (net, recovered, -review_errors, float(threshold), int(np.sum(routed)))
                if candidate > best:
                    best = candidate
            print(json.dumps({
                "features": feature_name,
                "c": c_value,
                "oof_net_correct": best[0],
                "approved_recovered": best[1],
                "review_false_approvals": -best[2],
                "threshold": round(best[3], 6),
                "routed": best[4],
                "catastrophic_false_approvals": 0,
            }, separators=(",", ":")))


if __name__ == "__main__":
    main()
