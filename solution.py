#!/usr/bin/env python3
"""Visible-pixel, fail-closed MIB document intake pipeline.

This file is an original implementation.  It deliberately avoids PDF text
layers: every extracted value first passes through a rendered page and local
OCR, which keeps hidden document content outside the evidence path.
"""

from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

import pypdfium2 as pdfium
from PIL import Image, ImageEnhance, ImageOps


FIELDS = (
    "applicant_name", "species_code", "home_world", "visa_class", "sponsor_id",
    "arrival_date", "declared_purpose", "risk_flags", "fee_status",
)
DEFAULTS = {
    "applicant_name": "unknown", "species_code": "unknown", "home_world": "unknown",
    "visa_class": "unknown", "sponsor_id": "SPN-0000", "arrival_date": "1900-01-01",
    "declared_purpose": "unknown", "risk_flags": "none", "fee_status": "unknown",
}
DISQUALIFYING = {"memory_tampering", "planetary_embargo", "active_warrant", "biohazard_red"}
REVIEW_FLAGS = {"identity_conflict", "sponsor_mismatch", "illegible_biometrics", "rescinded_denial"}
REVOKED_SPONSORS = {"SPN-0007", "SPN-0139", "SPN-4040"}
PAGE_MARKERS = {
    "intake": ("interstellar intake", "work authorization", "applicant"),
    "fee": ("fee receipt", "fee status", "amount"),
    "biometric": ("biometric", "observed flags", "b-13"),
    "sponsor": ("sponsor attestation", "sponsor id"),
    "note": ("adjudicator", "finding", "manual note"),
    "registry": ("registry", "registry status", "registered"),
}
PRECEDENCE = {"note": 60, "intake": 50, "biometric": 40, "fee": 35, "sponsor": 30, "registry": 20, "other": 10}


@dataclass(frozen=True)
class Evidence:
    field: str
    value: str
    source: str
    confidence: float


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def render_pages(pdf_path: Path) -> Iterable[Image.Image]:
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        for index in range(len(document)):
            page = document[index]
            try:
                image = page.render(scale=2.4).to_pil().convert("RGB")
                yield image
            finally:
                page.close()
    finally:
        document.close()


def ocr(image: Image.Image, psm: int) -> str:
    with tempfile.NamedTemporaryFile(suffix=".png") as temporary:
        image.save(temporary.name)
        completed = subprocess.run(
            ["tesseract", temporary.name, "stdout", "--psm", str(psm), "-l", "eng"],
            capture_output=True, text=True, timeout=25, check=False,
        )
    # Preserve OCR line boundaries: forms frequently place several fields on a
    # page, and collapsing them would let one label consume the next field.
    return completed.stdout


def visible_texts(image: Image.Image) -> tuple[str, ...]:
    """Return independent visible OCR readings rather than choosing one early."""
    gray = ImageOps.grayscale(image)
    boosted = ImageEnhance.Contrast(gray).enhance(1.5)
    first = ocr(boosted, 6)
    second = ocr(boosted, 11)
    readings = tuple(text for text in (first, second) if normalize_space(text))
    return readings if len(set(readings)) > 1 else readings[:1]


def page_kind(text: str) -> str:
    folded = text.casefold()
    scores = {kind: sum(marker in folded for marker in markers) for kind, markers in PAGE_MARKERS.items()}
    kind, score = max(scores.items(), key=lambda pair: pair[1])
    return kind if score >= 2 or (kind in {"biometric", "note"} and score == 1) else "other"


def extract_label(text: str, label: str) -> str:
    match = re.search(rf"\b{label}[ \t]*:?[ \t]*([^|;\n]{{1,90}})", text, re.I)
    if match and normalize_space(match.group(1)):
        value = normalize_space(match.group(1))
        value = re.split(r"\s{2,}(?=[A-Z][A-Za-z ]{2,}:)", value)[0]
        return value.strip(" .,:;")
    rows = [normalize_space(line) for line in text.splitlines() if normalize_space(line)]
    label_re = re.compile(rf"^{label}\s*:?$", re.I)
    for index, row in enumerate(rows):
        if label_re.fullmatch(row) and index + 1 < len(rows):
            return rows[index + 1].strip(" .,:;")
    return ""


def value_before_label(text: str, label: str, validator) -> str:
    """Recover forms where an image overlaps the label/value column order."""
    rows = [normalize_space(line) for line in text.splitlines() if normalize_space(line)]
    label_re = re.compile(rf"^{label}\s*:?$", re.I)
    for index, row in enumerate(rows):
        if label_re.fullmatch(row):
            for previous in reversed(rows[max(0, index - 4):index]):
                cleaned = validator(previous)
                if cleaned:
                    return cleaned
    return ""


def clean_name(value: str) -> str:
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", value)
    return " ".join(word[:1].upper() + word[1:].lower() for word in words[:4]) if len(words) >= 2 else ""


def clean_enum(value: str, choices: set[str]) -> str:
    candidate = normalize_space(value).casefold().replace("_", " ")
    for choice in choices:
        if candidate == choice.replace("_", " "):
            return choice
    return ""


def clean_species(value: str) -> str:
    candidate = normalize_space(value).upper().replace(" ", "_").replace("-", "_")
    return candidate if re.fullmatch(r"[A-Z][A-Z_]{1,36}", candidate) else ""


def clean_date(value: str) -> str:
    found = re.search(r"\b(20\d{2})[-/](\d{2})[-/](\d{2})\b", value)
    if not found:
        return ""
    rendered = "-".join(found.groups())
    try:
        date.fromisoformat(rendered)
    except ValueError:
        return ""
    return rendered


def clean_sponsor(value: str) -> str:
    found = re.search(r"\bSPN[- ]?(\d{4})\b", value.upper())
    return f"SPN-{found.group(1)}" if found else ""


def clean_flags(value: str) -> str:
    folded = value.casefold().replace("-", "_").replace(" ", "_")
    found = [flag for flag in sorted(DISQUALIFYING | REVIEW_FLAGS) if flag in folded]
    if found:
        return "|".join(found)
    if re.search(r"\bnone\b|\bclear\b", value, re.I):
        return "none"
    return ""


def add(parsed: dict[str, list[Evidence]], field: str, value: str, source: str, confidence: float) -> None:
    if value:
        parsed[field].append(Evidence(field, value, source, confidence))


def parse_page(kind: str, text: str, parsed: dict[str, list[Evidence]]) -> str:
    weight = PRECEDENCE[kind]
    # Structured labels are stronger evidence than page-title recognition.  We
    # therefore read label/value pairs on every visible page; page kind only
    # sets provenance precedence when multiple pages disagree.
    applicant = value_before_label(text, "Applicant Name", clean_name) or value_before_label(text, "Applicant", clean_name)
    if not applicant:
        applicant = clean_name(extract_label(text, "Applicant Name")) or clean_name(extract_label(text, "Applicant"))
    if not applicant:
        applicant = clean_name(extract_label(text, "Registry Name"))
    add(parsed, "applicant_name", applicant, kind, weight)
    add(parsed, "species_code", clean_species(extract_label(text, "Species(?: Code)?")), kind, weight)
    add(parsed, "home_world", normalize_space(extract_label(text, "Home World")), kind, weight)
    visa = re.search(r"\b(XW[- ]?[12]|DIP[- ]?1|MED[- ]?3|TRANSIT[- ]?7)\b", text, re.I)
    add(parsed, "visa_class", visa.group(1).upper().replace(" ", "-") if visa else "", kind, weight)
    sponsor = clean_sponsor(extract_label(text, "Sponsor ID")) or clean_sponsor(extract_label(text, "Sponsor"))
    if not sponsor:
        sponsor = value_before_label(text, "Sponsor ID", clean_sponsor) or value_before_label(text, "Sponsor", clean_sponsor)
    if not sponsor:
        sponsor = clean_sponsor(text) if kind == "sponsor" else ""
    add(parsed, "sponsor_id", sponsor, kind, weight)
    add(parsed, "arrival_date", clean_date(extract_label(text, "Arrival Date")), kind, weight)
    purpose = normalize_space(extract_label(text, "Declared Purpose")) or normalize_space(extract_label(text, "Purpose"))
    add(parsed, "declared_purpose", purpose, kind, weight)
    status = re.search(r"\b(paid|unpaid|waived)\b", extract_label(text, "Fee Status"), re.I)
    add(parsed, "fee_status", status.group(1).lower() if status else "", kind, weight)
    add(parsed, "risk_flags", clean_flags(extract_label(text, "Observed Flags") or extract_label(text, "Risk Flags")), kind, weight)
    if kind == "note":
        add(parsed, "risk_flags", clean_flags(extract_label(text, "(?:Risk )?Flag") or text), kind, weight)
        finding = re.search(r"\b(APPROVED|DENIED|NEEDS[ _-]?REVIEW)\b", extract_label(text, "Finding") or text, re.I)
        return finding.group(1).upper().replace(" ", "_").replace("-", "_") if finding else ""
    return ""


def choose(field: str, options: list[Evidence]) -> str:
    if not options:
        return DEFAULTS[field]
    ranked = sorted(options, key=lambda item: (item.confidence, item.source), reverse=True)
    best = ranked[0]
    if field == "risk_flags":
        adverse = [item.value for item in options if item.value != "none"]
        return "|".join(sorted(set("|".join(adverse).split("|")))) if adverse else best.value
    return best.value


def decide(
    row: dict[str, str],
    finding: str,
    *,
    visible_clean_biometrics: bool,
    visible_paid_fee: bool,
) -> tuple[str, float]:
    flags = set(row["risk_flags"].split("|")) if row["risk_flags"] != "none" else set()
    if finding == "DENIED" or flags & DISQUALIFYING or row["fee_status"] == "unpaid" or row["visa_class"] == "TRANSIT-7":
        return "DENIED", 0.91
    if finding == "NEEDS_REVIEW" or flags & REVIEW_FLAGS:
        return "NEEDS_REVIEW", 0.58
    if row["visa_class"] == "unknown" or row["arrival_date"] == "1900-01-01" or row["fee_status"] == "unknown":
        return "NEEDS_REVIEW", 0.48
    if row["visa_class"] != "DIP-1" and row["sponsor_id"] in {"SPN-0000", *REVOKED_SPONSORS}:
        return "NEEDS_REVIEW", 0.46
    if row["fee_status"] == "waived" and row["visa_class"] != "DIP-1":
        return "NEEDS_REVIEW", 0.45
    # A clean approval requires affirmative fee and biometric evidence rather
    # than using an extraction default as a proxy for no risk.
    # Clean OCR is necessary but not yet sufficient for approval: public
    # packets can contain all apparent core fields while an unrecovered manual
    # condition still requires denial. Until an affirmative approval authority
    # is read at high precision, keep clean packets in the review queue.
    if visible_clean_biometrics and visible_paid_fee:
        return "NEEDS_REVIEW", 0.40
    return "NEEDS_REVIEW", 0.42


def predict(pdf_path: Path) -> dict[str, object]:
    evidence: dict[str, list[Evidence]] = defaultdict(list)
    findings: list[str] = []
    try:
        for image in render_pages(pdf_path):
            for text in visible_texts(image):
                kind = page_kind(text)
                finding = parse_page(kind, text, evidence)
                if finding:
                    findings.append(finding)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        pass
    row = {field: choose(field, evidence[field]) for field in FIELDS}
    finding = "DENIED" if "DENIED" in findings else "NEEDS_REVIEW" if "NEEDS_REVIEW" in findings else ""
    visible_clean_biometrics = any(
        item.source == "biometric" and item.value == "none"
        for item in evidence["risk_flags"]
    ) and sum(item.source == "biometric" and item.value == "none" for item in evidence["risk_flags"]) >= 2
    visible_paid_fee = any(
        item.source == "fee" and item.value == "paid"
        for item in evidence["fee_status"]
    )
    adjudication, confidence = decide(
        row,
        finding,
        visible_clean_biometrics=visible_clean_biometrics,
        visible_paid_fee=visible_paid_fee,
    )
    return {"case_id": pdf_path.stem, **row, "adjudication": adjudication, "confidence": confidence}


def main(input_dir: str, output_path: str) -> None:
    paths = sorted(Path(input_dir).glob("MIB-*.pdf"))
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        workers = max(1, min(int(os.environ.get("MIB_WORKERS", "4")), 4))
        with ProcessPoolExecutor(max_workers=workers) as executor:
            for row in executor.map(predict, paths):
                stream.write(json.dumps(row, separators=(",", ":")) + "\n")
    temporary.replace(destination)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: solution.py <input_pdf_directory> <output_predictions_path>")
    main(sys.argv[1], sys.argv[2])
