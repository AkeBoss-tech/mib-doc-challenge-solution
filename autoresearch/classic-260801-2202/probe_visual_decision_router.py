#!/usr/bin/env python3
"""Cross-fit a compact visible-thumbnail decision router.

This analysis is independent of field OCR and participant implementations. It
uses only low-resolution rendered pixels and public labels, then measures
whether probabilities add value behind the existing fail-closed policy gate.
"""

from __future__ import annotations

import argparse
import csv
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageOps
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold


MAX_PAGES = 6
THUMBNAIL = (24, 32)
CLASSES = ("APPROVED", "DENIED", "NEEDS_REVIEW")


def page_features(image: Image.Image) -> np.ndarray:
    gray = ImageOps.grayscale(image)
    thumb = np.asarray(
        gray.resize(THUMBNAIL, Image.Resampling.BILINEAR), dtype=np.float32
    ) / 255.0
    horizontal = np.mean(thumb, axis=1)
    vertical = np.mean(thumb, axis=0)
    gradients = np.asarray([
        np.mean(np.abs(np.diff(thumb, axis=0))),
        np.mean(np.abs(np.diff(thumb, axis=1))),
        np.std(thumb),
        np.mean(thumb),
    ], dtype=np.float32)
    return np.concatenate((thumb.ravel(), horizontal, vertical, gradients))


def extract_one(path_text: str) -> tuple[str, np.ndarray]:
    path = Path(path_text)
    document = pdfium.PdfDocument(str(path))
    page_count = len(document)
    per_page = []
    try:
        for index in range(min(page_count, MAX_PAGES)):
            page = document[index]
            try:
                image = page.render(scale=0.6).to_pil().convert("RGB")
            finally:
                page.close()
            per_page.append(page_features(image))
    finally:
        document.close()
    width = len(page_features(Image.new("L", THUMBNAIL, 255)))
    while len(per_page) < MAX_PAGES:
        per_page.append(np.zeros(width, dtype=np.float32))
    page_presence = np.asarray(
        [1.0 if index < page_count else 0.0 for index in range(MAX_PAGES)],
        dtype=np.float32,
    )
    return path.stem, np.concatenate((*per_page, page_presence))


def policy_complete(row: dict[str, object]) -> bool:
    defaults = {"unknown", "SPN-0000", "1900-01-01"}
    fields = (
        "applicant_name", "species_code", "home_world", "visa_class",
        "sponsor_id", "arrival_date", "declared_purpose", "fee_status",
    )
    fee_ok = row["fee_status"] == "paid" or (
        row["fee_status"] == "waived" and row["visa_class"] == "DIP-1"
    )
    return (
        row["risk_flags"] == "none"
        and all(str(row[field]) not in defaults for field in fields)
        and fee_ok
        and row["visa_class"] in {"XW-1", "XW-2", "DIP-1", "MED-3"}
    )


def report_routes(
    probabilities: np.ndarray,
    labels: np.ndarray,
    predictions: list[dict[str, object]],
    model_name: str,
) -> None:
    review = np.asarray([row["adjudication"] == "NEEDS_REVIEW" for row in predictions])
    complete = np.asarray([policy_complete(row) for row in predictions])
    for target in ("APPROVED", "DENIED"):
        column = CLASSES.index(target)
        candidates = []
        for threshold in sorted(set(probabilities[review, column]), reverse=True):
            routed = review & (probabilities[:, column] >= threshold)
            if target == "APPROVED":
                routed &= complete
            counts = {
                value: int(np.sum(routed & (labels == value))) for value in CLASSES
            }
            if target == "APPROVED" and counts["DENIED"]:
                continue
            gain = (
                6 * counts[target]
                - (7 if target == "APPROVED" else 2) * counts["NEEDS_REVIEW"]
                - (2 if target == "DENIED" else 0) * counts["APPROVED"]
            )
            candidates.append((gain, counts[target], -sum(counts.values()), threshold, counts))
        if not candidates:
            continue
        best = max(candidates)
        print(json.dumps({
            "model": model_name,
            "target": target,
            "classification_raw_gain": best[0],
            "target_correct": best[1],
            "routed": -best[2],
            "threshold": round(float(best[3]), 6),
            "counts": best[4],
        }, separators=(",", ":")))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--pdf-dir", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    truth = {row["case_id"]: row for row in csv.DictReader(args.truth.open())}
    prediction_map = {
        row["case_id"]: row for row in map(json.loads, args.predictions.open())
    }
    ids = sorted(truth)
    if args.cache.exists():
        payload = np.load(args.cache)
        cached_ids = payload["ids"].astype(str).tolist()
        if cached_ids != ids:
            raise SystemExit("visual cache case ids do not match truth")
        features = payload["features"]
    else:
        paths = [str(args.pdf_dir / f"{case_id}.pdf") for case_id in ids]
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            extracted = dict(executor.map(extract_one, paths))
        features = np.stack([extracted[case_id] for case_id in ids])
        args.cache.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(args.cache, ids=np.asarray(ids), features=features)
    labels = np.asarray([truth[case_id]["adjudication"] for case_id in ids])
    predictions = [prediction_map[case_id] for case_id in ids]
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=8090)
    for name, builder in (
        ("thumbnail_logistic_c0.5", lambda: LogisticRegression(
            C=0.5, class_weight="balanced", max_iter=1200,
            solver="liblinear", random_state=1,
        )),
        ("thumbnail_logistic_c2", lambda: LogisticRegression(
            C=2.0, class_weight="balanced", max_iter=1200,
            solver="liblinear", random_state=1,
        )),
        ("thumbnail_extra_trees", lambda: ExtraTreesClassifier(
            n_estimators=300, min_samples_leaf=3, max_features="sqrt",
            class_weight="balanced", n_jobs=1, random_state=8090,
        )),
    ):
        probabilities = np.zeros((len(ids), len(CLASSES)), dtype=np.float64)
        for train, test in folds.split(features, labels):
            model = builder()
            model.fit(features[train], labels[train])
            fold_probabilities = model.predict_proba(features[test])
            for source, label in enumerate(model.classes_):
                probabilities[test, CLASSES.index(label)] = fold_probabilities[:, source]
        report_routes(probabilities, labels, predictions, name)


if __name__ == "__main__":
    main()
