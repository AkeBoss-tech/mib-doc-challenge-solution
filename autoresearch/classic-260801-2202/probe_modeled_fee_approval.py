#!/usr/bin/env python3
"""Cross-fit strict approval recovery using visible-text and fee evidence."""

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


FIELDS = ("applicant_name", "species_code", "home_world", "visa_class", "sponsor_id", "arrival_date", "declared_purpose")
DEFAULTS = {"unknown", "SPN-0000", "1900-01-01"}
REVOKED = {"SPN-0007", "SPN-0139", "SPN-2718", "SPN-4040", "SPN-7331", "SPN-9090"}


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
    actual = np.asarray([truth[case_id]["adjudication"] for case_id in ids], dtype=object)
    fees = np.asarray([truth[case_id]["fee_status"] for case_id in ids], dtype=object)
    fee_classes = np.asarray(sorted(set(fees)), dtype=object)
    approval = np.zeros(len(ids)); denial = np.zeros(len(ids)); fee_probabilities = np.zeros((len(ids), len(fee_classes)))
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=8090)
    for train, test in folds.split(texts, actual):
        vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=3, max_features=12000, sublinear_tf=True)
        train_matrix = vectorizer.fit_transform(texts[train]); test_matrix = vectorizer.transform(texts[test])
        for target, destination, c_value in (("APPROVED", approval, 8.0), ("DENIED", denial, 2.0)):
            model = LogisticRegression(C=c_value, class_weight="balanced", max_iter=1200, solver="liblinear", random_state=1)
            model.fit(train_matrix, actual[train] == target)
            destination[test] = model.predict_proba(test_matrix)[:, 1]
        fee_model = LogisticRegression(C=0.5, class_weight="balanced", max_iter=1200, solver="liblinear", random_state=1)
        fee_model.fit(train_matrix, fees[train])
        fold_probabilities = fee_model.predict_proba(test_matrix)
        for source, label in enumerate(fee_model.classes_):
            fee_probabilities[test, np.flatnonzero(fee_classes == label)[0]] = fold_probabilities[:, source]
    order = np.argsort(fee_probabilities, axis=1)
    fee_value = fee_classes[order[:, -1]]
    fee_probability = fee_probabilities[np.arange(len(ids)), order[:, -1]]
    fee_margin = fee_probability - fee_probabilities[np.arange(len(ids)), order[:, -2]]
    policy = []
    for case_id in ids:
        row = predictions[case_id]
        folded = re.sub(r"[^a-z0-9]+", " ", cache[case_id]["text"].casefold())
        complete = all(str(row[field]) not in DEFAULTS for field in FIELDS)
        sponsor_ok = row["visa_class"] == "DIP-1" or (
            re.fullmatch(r"SPN-\d{4}", str(row["sponsor_id"])) is not None and row["sponsor_id"] not in REVOKED
        )
        policy.append(
            row["adjudication"] == "NEEDS_REVIEW" and row["fee_status"] == "unknown"
            and row["risk_flags"] == "none" and complete and sponsor_ok
            and row["visa_class"] in {"XW-1", "XW-2", "DIP-1", "MED-3"}
            and ("observed flags none" in folded or "risk flags none" in folded)
        )
    policy = np.asarray(policy)
    results = []
    for fee_min in (0.20, 0.25, 0.30, 0.35, 0.40, 0.50):
        for fee_margin_min in (0.00, 0.05, 0.10, 0.15, 0.19, 0.20, 0.25, 0.30):
            fee_ok = (fee_value == "paid") | ((fee_value == "waived") & np.asarray([predictions[x]["visa_class"] == "DIP-1" for x in ids]))
            for approval_min in (0.40, 0.50, 0.60, 0.65, 0.70, 0.75, 0.80):
                for denial_max in (0.20, 0.30, 0.40, 0.49, 0.60):
                    routed = policy & fee_ok & (fee_probability >= fee_min) & (fee_margin >= fee_margin_min) & (approval >= approval_min) & (denial <= denial_max)
                    counts = {label: int(np.sum(routed & (actual == label))) for label in ("APPROVED", "NEEDS_REVIEW", "DENIED")}
                    groups = {}
                    for name in ("development", "holdout"):
                        selected = routed & np.asarray([group(case_id) == name for case_id in ids])
                        group_counts = {label: int(np.sum(selected & (actual == label))) for label in ("APPROVED", "NEEDS_REVIEW", "DENIED")}
                        groups[name] = {"counts": group_counts, "raw_gain": 6 * group_counts["APPROVED"] - 7 * group_counts["NEEDS_REVIEW"]}
                    results.append({"fee_min": fee_min, "fee_margin": fee_margin_min, "approval_min": approval_min, "denial_max": denial_max, "routed": int(np.sum(routed)), "counts": counts, "raw_gain": 6 * counts["APPROVED"] - 7 * counts["NEEDS_REVIEW"], "groups": groups})
    valid = [row for row in results if row["counts"]["DENIED"] == 0 and min(item["raw_gain"] for item in row["groups"].values()) > 0]
    valid.sort(key=lambda row: (row["raw_gain"], min(item["raw_gain"] for item in row["groups"].values()), -row["counts"]["NEEDS_REVIEW"]), reverse=True)
    for row in valid[:40]:
        print(json.dumps(row, separators=(",", ":")))
    for row in results:
        if row["fee_margin"] == 0.19 and row["approval_min"] == 0.70 and row["denial_max"] == 0.40 and row["fee_min"] == 0.30:
            print(json.dumps({"target_candidate": row}, separators=(",", ":")))


if __name__ == "__main__":
    main()
