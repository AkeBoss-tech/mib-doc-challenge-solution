#!/usr/bin/env python3
"""Cross-fit diverse original text routers and test consensus safety gates."""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

import numpy as np
from scipy.stats import rankdata
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
from sklearn.naive_bayes import ComplementNB
from sklearn.ensemble import ExtraTreesClassifier


DEFAULTS = {"unknown", "SPN-0000", "1900-01-01"}
REVOKED = {"SPN-0007", "SPN-0139", "SPN-4040", "SPN-7331"}
FIELDS = ("applicant_name", "species_code", "home_world", "visa_class", "sponsor_id",
          "arrival_date", "declared_purpose", "risk_flags", "fee_status")


def sanitize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d", "#", text.casefold())).strip()


def vectorizer(name: str) -> TfidfVectorizer:
    if name == "char":
        return TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=3,
                               max_features=12000, sublinear_tf=True)
    return TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=2,
                           max_features=18000, sublinear_tf=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--ocr-cache", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    args = parser.parse_args()
    truth = {row["case_id"]: row for row in csv.DictReader(args.truth.open())}
    cache = {row["case_id"]: row for row in map(json.loads, args.ocr_cache.open())}
    pred = {row["case_id"]: row for row in map(json.loads, args.predictions.open())}
    ids = sorted(truth)
    texts = np.asarray([sanitize(cache[x]["text"]) for x in ids], dtype=object)
    actual = np.asarray([truth[x]["adjudication"] for x in ids])
    strata = np.asarray([{"APPROVED": 0, "DENIED": 1, "NEEDS_REVIEW": 2}[value] for value in actual])
    folds = list(StratifiedKFold(n_splits=5, shuffle=True, random_state=8090).split(texts, strata))
    scores: dict[str, dict[str, np.ndarray]] = {}
    for family in ("char", "word"):
        for learner in ("logistic", "complement_nb"):
            name = f"{family}_{learner}"
            scores[name] = {target: np.zeros(len(ids)) for target in ("APPROVED", "DENIED")}
            for train, test in folds:
                builder = vectorizer(family)
                train_matrix = builder.fit_transform(texts[train])
                test_matrix = builder.transform(texts[test])
                for target in ("APPROVED", "DENIED"):
                    labels = actual[train] == target
                    if learner == "logistic":
                        c_value = 8.0 if target == "APPROVED" else 2.0
                        classifier = LogisticRegression(C=c_value, class_weight="balanced", max_iter=1200,
                                                        solver="liblinear", random_state=1)
                    else:
                        classifier = ComplementNB(alpha=0.5)
                    classifier.fit(train_matrix, labels)
                    scores[name][target][test] = classifier.predict_proba(test_matrix)[:, 1]

    policy_ok = []
    for case_id in ids:
        row = pred[case_id]
        complete = sum(str(row[field]) not in DEFAULTS for field in FIELDS)
        fee_ok = row["fee_status"] == "paid" or (row["fee_status"] == "waived" and row["visa_class"] == "DIP-1")
        sponsor_ok = row["visa_class"] == "DIP-1" or (
            re.fullmatch(r"SPN-\d{4}", str(row["sponsor_id"])) is not None and row["sponsor_id"] not in REVOKED
        )
        policy_ok.append(row["adjudication"] == "NEEDS_REVIEW" and row["risk_flags"] == "none"
                         and complete >= 9 and fee_ok and sponsor_ok
                         and row["visa_class"] in {"XW-1", "XW-2", "DIP-1", "MED-3"})
    policy_ok = np.asarray(policy_ok)
    approval_ranks = np.vstack([rankdata(value["APPROVED"]) / len(ids) for value in scores.values()])
    denial_ranks = np.vstack([rankdata(value["DENIED"]) / len(ids) for value in scores.values()])
    aggregations = {
        "mean": (np.mean(approval_ranks, axis=0), np.mean(denial_ranks, axis=0)),
        "unanimous": (np.min(approval_ranks, axis=0), np.max(denial_ranks, axis=0)),
        "median": (np.median(approval_ranks, axis=0), np.median(denial_ranks, axis=0)),
    }
    candidates = []
    for name, (approval, denial) in aggregations.items():
        for approval_min in np.arange(0.50, 0.96, 0.025):
            for denial_max in np.arange(0.30, 0.81, 0.025):
                routed = policy_ok & (approval >= approval_min) & (denial <= denial_max)
                counts = {value: int(np.sum(routed & (actual == value))) for value in ("APPROVED", "NEEDS_REVIEW", "DENIED")}
                candidates.append({"aggregation": name, "approval_rank_min": round(float(approval_min), 3),
                                   "denial_rank_max": round(float(denial_max), 3), "routed": int(np.sum(routed)),
                                   "counts": counts, "classification_raw_gain": 6 * counts["APPROVED"] - 7 * counts["NEEDS_REVIEW"]})
    candidates.sort(key=lambda row: (row["counts"]["DENIED"] == 0, row["classification_raw_gain"], row["counts"]["APPROVED"]), reverse=True)
    for row in candidates[:30]:
        print(json.dumps(row, separators=(",", ":")))
    paired = (
        policy_ok
        & (scores["char_logistic"]["APPROVED"] >= 0.75)
        & (scores["char_logistic"]["DENIED"] <= 0.30)
    )
    ensemble = (
        policy_ok
        & (aggregations["mean"][0] >= 0.875)
        & (aggregations["mean"][1] <= 0.425)
    )
    for name, routed in (("paired_union_ensemble", paired | ensemble),
                         ("paired_intersection_ensemble", paired & ensemble)):
        counts = {value: int(np.sum(routed & (actual == value))) for value in ("APPROVED", "NEEDS_REVIEW", "DENIED")}
        print(json.dumps({"aggregation": name, "routed": int(np.sum(routed)), "counts": counts,
                          "classification_raw_gain": 6 * counts["APPROVED"] - 7 * counts["NEEDS_REVIEW"]},
                         separators=(",", ":")))
    # Flatten diverse OOF probabilities with generic document-composition
    # features for a nonlinear meta-router. Each probability was generated by
    # a base model that did not train on that row.
    marker_features = np.asarray([
        [float(marker in text) for marker in (
            "fee receipt", "registry extract", "work authorization intake",
            "biometric", "sponsor attestation", "manual note",
        )] + [min(len(text) / 20000.0, 1.0)]
        for text in texts
    ])
    meta_matrix = np.column_stack([
        *[value[target] for value in scores.values() for target in ("APPROVED", "DENIED")],
        marker_features,
    ])
    for minimum_leaf in (5, 10, 20, 35):
        meta_probabilities = np.zeros((len(ids), 3))
        for train, test in folds:
            classifier = ExtraTreesClassifier(
                n_estimators=300, min_samples_leaf=minimum_leaf,
                max_features="sqrt", class_weight="balanced", random_state=8090,
                n_jobs=1,
            )
            classifier.fit(meta_matrix[train], actual[train])
            predicted = classifier.predict_proba(meta_matrix[test])
            for column, label in enumerate(classifier.classes_):
                meta_probabilities[test, ("APPROVED", "DENIED", "NEEDS_REVIEW").index(label)] = predicted[:, column]
        best = None
        for approval_min in np.arange(0.30, 0.91, 0.025):
            for denial_max in np.arange(0.05, 0.51, 0.025):
                routed = policy_ok & (meta_probabilities[:, 0] >= approval_min) & (meta_probabilities[:, 1] <= denial_max)
                counts = {value: int(np.sum(routed & (actual == value))) for value in ("APPROVED", "NEEDS_REVIEW", "DENIED")}
                candidate = (counts["DENIED"] == 0, 6 * counts["APPROVED"] - 7 * counts["NEEDS_REVIEW"], counts["APPROVED"],
                             {"aggregation": "extra_trees_meta", "minimum_leaf": minimum_leaf,
                              "approval_min": round(float(approval_min), 3), "denial_max": round(float(denial_max), 3),
                              "routed": int(np.sum(routed)), "counts": counts,
                              "classification_raw_gain": 6 * counts["APPROVED"] - 7 * counts["NEEDS_REVIEW"]})
                if best is None or candidate[:3] > best[:3]:
                    best = candidate
        print(json.dumps(best[-1], separators=(",", ":")))


if __name__ == "__main__":
    main()
