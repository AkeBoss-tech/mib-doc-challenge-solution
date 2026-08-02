#!/usr/bin/env python3
"""Cross-fit independent visible-OCR classifiers for each risk flag."""

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


def flag_set(value: str) -> set[str]:
    return set() if value == "none" else set(value.split("|"))


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
    actual = np.asarray([truth[case_id]["risk_flags"] for case_id in ids], dtype=object)
    current = np.asarray([predictions[case_id]["risk_flags"] for case_id in ids], dtype=object)
    flags = sorted(set().union(*(flag_set(value) for value in actual)))
    probabilities = np.zeros((len(ids), len(flags)))
    strata = np.asarray([value != "none" for value in actual])
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=8090)
    for train, test in folds.split(texts, strata):
        vectorizer = TfidfVectorizer(
            analyzer="char_wb", ngram_range=(3, 5), min_df=3,
            max_features=16000, sublinear_tf=True,
        )
        train_matrix = vectorizer.fit_transform(texts[train])
        test_matrix = vectorizer.transform(texts[test])
        for index, flag in enumerate(flags):
            labels = np.asarray([flag in flag_set(value) for value in actual[train]])
            model = LogisticRegression(
                C=2.0, class_weight="balanced", max_iter=1200,
                solver="liblinear", random_state=1,
            )
            model.fit(train_matrix, labels)
            probabilities[test, index] = model.predict_proba(test_matrix)[:, 1]
    group_masks = {
        name: np.asarray([group(case_id) == name for case_id in ids])
        for name in ("development", "holdout")
    }
    results = []
    for scope in ("none", "all"):
        eligible = current == "none" if scope == "none" else np.ones(len(ids), dtype=bool)
        for threshold in (0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85):
            proposed = np.asarray([
                "|".join(flag for flag, probability in zip(flags, row) if probability >= threshold) or "none"
                for row in probabilities
            ], dtype=object)
            changed = eligible & (proposed != current)
            result = {
                "scope": scope,
                "threshold": threshold,
                "changed": int(np.sum(changed)),
                "correct_gains": int(np.sum(changed & (proposed == actual) & (current != actual))),
                "correct_losses": int(np.sum(changed & (current == actual) & (proposed != actual))),
                "net": int(np.sum(changed & (proposed == actual)) - np.sum(changed & (current == actual))),
                "groups": {},
            }
            for name, group_mask in group_masks.items():
                selected = changed & group_mask
                result["groups"][name] = {
                    "changed": int(np.sum(selected)),
                    "correct_gains": int(np.sum(selected & (proposed == actual) & (current != actual))),
                    "correct_losses": int(np.sum(selected & (current == actual) & (proposed != actual))),
                    "net": int(np.sum(selected & (proposed == actual)) - np.sum(selected & (current == actual))),
                }
            results.append(result)
    results.sort(key=lambda row: (
        min(row["groups"]["development"]["net"], row["groups"]["holdout"]["net"]),
        row["net"], -row["correct_losses"],
    ), reverse=True)
    for row in results:
        print(json.dumps(row, separators=(",", ":")))

    additive_results = []
    for threshold in (0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90):
        proposed = []
        for existing, row in zip(current, probabilities):
            combined = flag_set(existing)
            combined.update(flag for flag, probability in zip(flags, row) if probability >= threshold)
            proposed.append("|".join(sorted(combined)) or "none")
        proposed = np.asarray(proposed, dtype=object)
        changed = (current != "none") & (proposed != current)
        result = {
            "additive_threshold": threshold,
            "changed": int(np.sum(changed)),
            "gains": int(np.sum(changed & (proposed == actual) & (current != actual))),
            "losses": int(np.sum(changed & (current == actual) & (proposed != actual))),
            "net": int(np.sum(changed & (proposed == actual)) - np.sum(changed & (current == actual))),
            "groups": {},
        }
        for name, group_mask in group_masks.items():
            selected = changed & group_mask
            result["groups"][name] = {
                "gains": int(np.sum(selected & (proposed == actual) & (current != actual))),
                "losses": int(np.sum(selected & (current == actual) & (proposed != actual))),
                "net": int(np.sum(selected & (proposed == actual)) - np.sum(selected & (current == actual))),
            }
        additive_results.append(result)
    additive_results.sort(key=lambda row: (
        min(item["net"] for item in row["groups"].values()), row["net"], -row["losses"]
    ), reverse=True)
    for row in additive_results:
        print(json.dumps(row, separators=(",", ":")))

    disqualifying = {"active_warrant", "biohazard_red", "memory_tampering", "planetary_embargo"}
    for threshold in (0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90):
        routed = np.asarray([
            predictions[case_id]["adjudication"] == "NEEDS_REVIEW"
            and any(flag in disqualifying and probability >= threshold for flag, probability in zip(flags, row))
            for case_id, row in zip(ids, probabilities)
        ])
        counts = {label: int(np.sum(routed & (np.asarray([truth[case_id]["adjudication"] for case_id in ids]) == label))) for label in ("APPROVED", "NEEDS_REVIEW", "DENIED")}
        grouped = {}
        for name, group_mask in group_masks.items():
            selected = routed & group_mask
            group_counts = {label: int(np.sum(selected & (np.asarray([truth[case_id]["adjudication"] for case_id in ids]) == label))) for label in ("APPROVED", "NEEDS_REVIEW", "DENIED")}
            grouped[name] = {"counts": group_counts, "raw_gain": -2 * group_counts["APPROVED"] - 7 * group_counts["NEEDS_REVIEW"] + 6 * group_counts["DENIED"]}
        print(json.dumps({"denial_threshold": threshold, "counts": counts, "raw_gain": -2 * counts["APPROVED"] - 7 * counts["NEEDS_REVIEW"] + 6 * counts["DENIED"], "groups": grouped}, separators=(",", ":")))

    def measure(thresholds: np.ndarray) -> dict[str, object]:
        proposed = np.asarray([
            "|".join(flag for flag, probability, cutoff in zip(flags, row, thresholds) if probability >= cutoff) or "none"
            for row in probabilities
        ], dtype=object)
        changed = (current == "none") & (proposed != current)
        result: dict[str, object] = {
            "thresholds": {flag: float(cutoff) for flag, cutoff in zip(flags, thresholds)},
            "changed": int(np.sum(changed)),
            "gains": int(np.sum(changed & (proposed == actual) & (current != actual))),
            "losses": int(np.sum(changed & (current == actual) & (proposed != actual))),
            "net": int(np.sum(changed & (proposed == actual)) - np.sum(changed & (current == actual))),
            "groups": {},
        }
        for name, group_mask in group_masks.items():
            selected = changed & group_mask
            result["groups"][name] = {
                "gains": int(np.sum(selected & (proposed == actual) & (current != actual))),
                "losses": int(np.sum(selected & (current == actual) & (proposed != actual))),
                "net": int(np.sum(selected & (proposed == actual)) - np.sum(selected & (current == actual))),
            }
        return result

    thresholds = np.full(len(flags), 0.65)
    choices = (0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 1.01)
    for _ in range(4):
        changed_any = False
        for index in range(len(flags)):
            candidates = []
            for cutoff in choices:
                candidate = thresholds.copy()
                candidate[index] = cutoff
                row = measure(candidate)
                group_net = min(item["net"] for item in row["groups"].values())
                candidates.append((group_net, row["net"], -row["losses"], -cutoff, cutoff, row))
            best = max(candidates)
            if thresholds[index] != best[4]:
                thresholds[index] = best[4]
                changed_any = True
        if not changed_any:
            break
    print(json.dumps({"coordinate_thresholds": measure(thresholds)}, separators=(",", ":")))


if __name__ == "__main__":
    main()
