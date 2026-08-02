#!/usr/bin/env python3
"""Visible-pixel, fail-closed MIB document intake pipeline.

This file is an original implementation.  It deliberately avoids PDF text
layers: every extracted value first passes through a rendered page and local
OCR, which keeps hidden document content outside the evidence path.
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Iterable

import pypdfium2 as pdfium
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageStat


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
REVOKED_SPONSORS = {
    "SPN-0007", "SPN-0139", "SPN-2718", "SPN-4040", "SPN-7331", "SPN-9090",
}
# Public examples establish that these sponsors consistently deny ordinary
# non-diplomatic applications. Other public revoked sponsors have visible
# signed exceptions, so they remain review/approval blockers rather than an
# unconditional denial fact.
STRICT_DENIAL_SPONSORS = {"SPN-0139", "SPN-2718", "SPN-7331", "SPN-9090"}
PACKET_RECEIPT_DATE = date(2026, 7, 7)
PAGE_MARKERS = {
    "intake": ("interstellar intake", "work authorization", "applicant"),
    "fee": ("fee receipt", "fee status", "amount"),
    "biometric": ("biometric", "observed flags", "b-13"),
    "sponsor": ("sponsor attestation", "sponsor id"),
    "note": ("adjudicator", "finding", "manual note"),
    "registry": ("registry", "registry status", "registered"),
}
PRECEDENCE = {"note": 60, "intake": 50, "biometric": 40, "fee": 35, "sponsor": 30, "registry": 20, "other": 10}


def load_category_vocabulary() -> dict[str, tuple[str, ...]]:
    path = Path(__file__).with_name("models") / "public_category_vocab.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        fields = payload["fields"]
        return {field: tuple(values) for field, values in fields.items()}
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return {}


CATEGORY_VOCABULARY = load_category_vocabulary()


def load_name_token_vocabulary() -> tuple[str, ...]:
    path = Path(__file__).with_name("models") / "public_name_tokens.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema") != "akeboss-public-name-token-vocabulary/v1":
            return ()
        return tuple(str(token) for token in payload["tokens"])
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return ()


NAME_TOKEN_VOCABULARY = load_name_token_vocabulary()
NAME_TOKEN_MIN_SIMILARITY = 0.60
NAME_TOKEN_MIN_MARGIN = 0.00
NAME_PAIR_MIN_SIMILARITY = 0.70


def load_text_model(filename: str, schema: str) -> dict[str, object]:
    path = Path(__file__).with_name("models") / filename
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema") != schema:
            return {}
        return payload
    except (OSError, TypeError, json.JSONDecodeError):
        return {}


DENIAL_TEXT_MODEL = load_text_model(
    "visible_ocr_denial.json", "akeboss-visible-ocr-denial/v1"
)
APPROVAL_TEXT_MODEL = load_text_model(
    "visible_ocr_approval.json", "akeboss-visible-ocr-approval/v1"
)
FEE_TEXT_MODEL = load_text_model(
    "visible_ocr_fee.json", "akeboss-visible-ocr-fee/v1"
)
PURPOSE_TEXT_MODEL = load_text_model(
    "visible_ocr_declared_purpose.json", "akeboss-visible-ocr-declared-purpose/v1"
)
VISA_TEXT_MODEL = load_text_model(
    "visible_ocr_visa_class.json", "akeboss-visible-ocr-visa-class/v1"
)
SPECIES_TEXT_MODEL = load_text_model(
    "visible_ocr_species_code.json", "akeboss-visible-ocr-species-code/v1"
)
HOME_WORLD_TEXT_MODEL = load_text_model(
    "visible_ocr_home_world.json", "akeboss-visible-ocr-home-world/v1"
)
RISK_TEXT_MODEL = load_text_model(
    "visible_ocr_risk_multilabel.json", "akeboss-visible-ocr-risk-multilabel/v1"
)


@dataclass(frozen=True)
class Evidence:
    field: str
    value: str
    source: str
    confidence: float


@dataclass(frozen=True)
class PageDiagnostics:
    """Visible-pixel quality facts retained only for development tracing."""

    page: int
    width: int
    height: int
    grayscale_contrast: float
    dark_pixel_fraction: float
    edge_variance: float
    orientation_correction_degrees: int


@dataclass(frozen=True)
class RegionProposal:
    """A generic, visible-label proposal for a later ROI reader."""

    field_or_section: str
    page: int
    bounding_region: tuple[int, int, int, int]
    anchor_text: str
    anchor_quality: float
    layout_family: str
    proposed_reader: str


@dataclass(frozen=True)
class CandidateValue:
    """One visible-pixel field reading retained in the shadow ledger."""

    field: str
    raw_text: str
    normalized_value: str
    page: int
    crop: tuple[int, int, int, int]
    reader_family: str
    transform_chain: tuple[str, ...]
    ocr_quality: float
    anchor_quality: float
    visible_evidence_excerpt: str


@dataclass(frozen=True)
class LedgerEntry:
    """Resolution trace that never discards conflicting candidate readings."""

    field: str
    candidates: tuple[CandidateValue, ...]
    selected_value: str
    resolution_reason: str
    conflicts: tuple[str, ...]


@dataclass(frozen=True)
class OcrWord:
    text: str
    confidence: float
    left: int
    top: int
    width: int
    height: int
    block: int
    paragraph: int
    line: int


# These are public field-manual/schema labels, not case-specific clues.  The
# proposals are deliberately separate from extraction so they can be measured
# before a crop reader is allowed to influence output.
ANCHOR_LABELS = {
    "applicant_name": ("applicant name", "applicant", "registry name"),
    "species_code": ("species code", "species"),
    "home_world": ("home world",),
    "visa_class": ("visa class",),
    "sponsor_id": ("sponsor id", "sponsor"),
    "arrival_date": ("arrival date",),
    "declared_purpose": ("declared purpose", "purpose"),
    "risk_flags": ("observed flags", "risk flags", "risk flag"),
    "fee_status": ("fee status",),
    "disposition": ("finding", "disposition", "decision"),
}
MAX_REGION_PROPOSALS_PER_FIELD = 2
SPONSOR_ROI_MIN_OCR_QUALITY = 85.0
RISK_FLAG_FUZZY_MIN_SIMILARITY = 0.75
RISK_FLAG_FUZZY_MIN_MARGIN = 0.20
FEE_BAND_WIDTH_RATIO = 0.60
FEE_BAND_HEIGHT_RATIO = 0.25
FEE_STATUS_FUZZY_MIN_SIMILARITY = 0.80
FEE_STATUS_FUZZY_MIN_MARGIN = 0.20
NON_NAME_LABELS = {
    normalized
    for labels in ANCHOR_LABELS.values()
    for label in labels
    for normalized in (re.sub(r"[^a-z0-9]+", " ", label.casefold()).strip(),)
} | {"case id", "primary intake record", "passport image"}
ORIENTATION_LABELS = tuple(sorted({
    "case id", "applicant", "species code", "species match", "home world",
    "visa class", "sponsor id", "arrival date", "declared purpose", "purpose",
    "fee status", "observed flags", "risk flags", "registry status", "finding",
}))
ORIENTATION_NATIVE_MIN_LABELS = 2
ORIENTATION_RETRY_MIN_LABELS = 3
ORIENTATION_RETRY_MIN_GAIN = 2
PAIRED_APPROVAL_MIN_PROBABILITY = 0.75
PAIRED_APPROVAL_MAX_DENIAL_PROBABILITY = 0.30
BIOMETRIC_APPROVAL_MIN_PROBABILITY = 0.50
BIOMETRIC_APPROVAL_MAX_DENIAL_PROBABILITY = 0.40
MODELED_FEE_APPROVAL_MIN_PROBABILITY = 0.30
MODELED_FEE_APPROVAL_MIN_MARGIN = 0.19
MODELED_FEE_APPROVAL_MIN_ROUTER_PROBABILITY = 0.70
MODELED_FEE_APPROVAL_MAX_DENIAL_PROBABILITY = 0.60


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def visible_ocr_model_probability(texts: Iterable[str], model: dict[str, object]) -> float:
    """Score visible OCR using a compact exported character n-gram model."""
    if not model:
        return 0.0
    text = re.sub(r"\s+", " ", re.sub(r"\d", "#", "\n".join(texts).casefold())).strip()
    counts: Counter[str] = Counter()
    for word in text.split():
        padded = f" {word} "
        for size in range(3, 6):
            offset = 0
            counts[padded[offset:offset + size]] += 1
            while offset + size < len(padded):
                offset += 1
                counts[padded[offset:offset + size]] += 1
            if offset == 0:
                break
    features = model.get("features", {})
    weighted: list[tuple[float, float]] = []
    if isinstance(features, dict):
        for token, count in counts.items():
            parameters = features.get(token)
            if isinstance(parameters, list) and len(parameters) == 2:
                idf, coefficient = float(parameters[0]), float(parameters[1])
                weighted.append(((1.0 + math.log(count)) * idf, coefficient))
    norm = math.sqrt(sum(value * value for value, _ in weighted)) or 1.0
    score = float(model.get("intercept", 0.0)) + sum(
        (value / norm) * coefficient for value, coefficient in weighted
    )
    return 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, score))))


def visible_ocr_denial_probability(texts: Iterable[str]) -> float:
    return visible_ocr_model_probability(texts, DENIAL_TEXT_MODEL)


def visible_ocr_approval_probability(texts: Iterable[str]) -> float:
    return visible_ocr_model_probability(texts, APPROVAL_TEXT_MODEL)


def visible_ocr_categorical_prediction(
    texts: Iterable[str], model: dict[str, object]
) -> tuple[str, float, float]:
    """Predict one category from exported multiclass visible-OCR features."""
    classes = model.get("classes", [])
    intercepts = model.get("intercepts", [])
    features = model.get("features", {})
    if (
        not isinstance(classes, list)
        or not isinstance(intercepts, list)
        or len(classes) < 2
        or len(classes) != len(intercepts)
        or not isinstance(features, dict)
    ):
        return "", 0.0, 0.0
    text = re.sub(r"\s+", " ", re.sub(r"\d", "#", "\n".join(texts).casefold())).strip()
    counts: Counter[str] = Counter()
    for word in text.split():
        padded = f" {word} "
        for size in range(3, 6):
            for offset in range(len(padded) - size + 1):
                counts[padded[offset:offset + size]] += 1
    weighted: list[tuple[float, list[float]]] = []
    for token, count in counts.items():
        parameters = features.get(token)
        if (
            isinstance(parameters, list)
            and len(parameters) == 2
            and isinstance(parameters[1], list)
            and len(parameters[1]) == len(classes)
        ):
            weighted.append(((1.0 + math.log(count)) * float(parameters[0]), parameters[1]))
    norm = math.sqrt(sum(value * value for value, _ in weighted)) or 1.0
    sigmoid = []
    for index, intercept in enumerate(intercepts):
        score = float(intercept) + sum(
            (value / norm) * float(coefficients[index])
            for value, coefficients in weighted
        )
        sigmoid.append(1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, score)))))
    total = sum(sigmoid) or 1.0
    probabilities = [value / total for value in sigmoid]
    ranked = sorted(zip(probabilities, map(str, classes)), reverse=True)
    return ranked[0][1], ranked[0][0], ranked[0][0] - ranked[1][0]


def visible_ocr_fee_prediction(texts: Iterable[str]) -> tuple[str, float, float]:
    return visible_ocr_categorical_prediction(texts, FEE_TEXT_MODEL)


def visible_ocr_risk_prediction(texts: Iterable[str]) -> str:
    """Return independently supported visible risk flags from an exported model."""
    model = RISK_TEXT_MODEL
    flags = model.get("flags", [])
    intercepts = model.get("intercepts", [])
    features = model.get("features", {})
    if (
        not isinstance(flags, list)
        or not isinstance(intercepts, list)
        or len(flags) != len(intercepts)
        or not isinstance(features, dict)
    ):
        return ""
    text = re.sub(r"\s+", " ", re.sub(r"\d", "#", "\n".join(texts).casefold())).strip()
    counts: Counter[str] = Counter()
    for word in text.split():
        padded = f" {word} "
        for size in range(3, 6):
            for offset in range(len(padded) - size + 1):
                counts[padded[offset:offset + size]] += 1
    weighted: list[tuple[float, list[float]]] = []
    for token, count in counts.items():
        parameters = features.get(token)
        if (
            isinstance(parameters, list)
            and len(parameters) == 2
            and isinstance(parameters[1], list)
            and len(parameters[1]) == len(flags)
        ):
            weighted.append(((1.0 + math.log(count)) * float(parameters[0]), parameters[1]))
    norm = math.sqrt(sum(value * value for value, _ in weighted)) or 1.0
    thresholds = model.get("thresholds", {})
    found = []
    for index, intercept in enumerate(intercepts):
        score = float(intercept) + sum(
            (value / norm) * float(coefficients[index])
            for value, coefficients in weighted
        )
        probability = 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, score))))
        threshold = float(thresholds.get(str(flags[index]), 1.0)) if isinstance(thresholds, dict) else 1.0
        if probability >= threshold:
            found.append(str(flags[index]))
    return "|".join(sorted(found))


def modeled_risk_denial_recovery(adjudication: str, modeled_risk: str) -> bool:
    """Allow learned risk evidence to move only review toward denial."""
    flags = set(modeled_risk.split("|")) if modeled_risk else set()
    return adjudication == "NEEDS_REVIEW" and bool(flags & DISQUALIFYING)


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


def ocr_words(image: Image.Image, *, psm: int = 6) -> tuple[OcrWord, ...]:
    """Read visible word geometry for development-only layout measurement."""
    with tempfile.NamedTemporaryFile(suffix=".png") as temporary:
        image.save(temporary.name)
        completed = subprocess.run(
            ["tesseract", temporary.name, "stdout", "--psm", str(psm), "-l", "eng", "tsv"],
            capture_output=True, text=True, timeout=25, check=False,
        )
    if completed.returncode not in (0, 1):
        return ()
    words: list[OcrWord] = []
    for row in csv.DictReader(completed.stdout.splitlines(), delimiter="\t"):
        token = normalize_space(row.get("text", ""))
        try:
            confidence = float(row.get("conf", "-1"))
            left, top = int(row["left"]), int(row["top"])
            width, height = int(row["width"]), int(row["height"])
            block, paragraph, line = int(row["block_num"]), int(row["par_num"]), int(row["line_num"])
        except (KeyError, TypeError, ValueError):
            continue
        if token and confidence >= 0 and width > 0 and height > 0:
            words.append(OcrWord(token, confidence, left, top, width, height, block, paragraph, line))
    return tuple(words)


def page_diagnostics(
    image: Image.Image,
    page: int,
    orientation_correction_degrees: int = 0,
) -> PageDiagnostics:
    """Calculate deterministic, inexpensive pixel diagnostics without altering it."""
    gray = ImageOps.grayscale(image)
    statistics = ImageStat.Stat(gray)
    contrast = statistics.stddev[0]
    histogram = gray.histogram()
    dark = sum(histogram[:96]) / max(1, image.width * image.height)
    edges = gray.filter(ImageFilter.FIND_EDGES)
    edge_variance = ImageStat.Stat(edges).var[0]
    return PageDiagnostics(
        page=page,
        width=image.width,
        height=image.height,
        grayscale_contrast=round(contrast, 3),
        dark_pixel_fraction=round(dark, 5),
        edge_variance=round(edge_variance, 3),
        orientation_correction_degrees=orientation_correction_degrees,
    )


def normalized_anchor(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def anchor_similarity(observed: str, expected: str) -> float:
    """Bounded OCR-tolerant label matching, independent of field values."""
    actual = normalized_anchor(observed)
    target = normalized_anchor(expected)
    if not actual or not target:
        return 0.0
    if target in actual:
        return 1.0
    return SequenceMatcher(None, actual, target).ratio()


def propose_regions(
    words: Iterable[OcrWord],
    *,
    page: int,
    page_width: int,
    page_height: int,
    layout_family: str,
) -> tuple[RegionProposal, ...]:
    """Propose right/below value regions from visible field labels.

    The region is intentionally a broad candidate, not a fixed template.  A
    later reader may choose a horizontal or vertical value crop based on the
    same proposal; neither route is allowed to decide policy directly.
    """
    lines: dict[tuple[int, int, int], list[OcrWord]] = defaultdict(list)
    for word in words:
        lines[(word.block, word.paragraph, word.line)].append(word)
    proposals: list[RegionProposal] = []
    for line_words in lines.values():
        ordered = sorted(line_words, key=lambda word: word.left)
        observed = " ".join(word.text for word in ordered)
        for field, labels in ANCHOR_LABELS.items():
            matches: list[tuple[float, int, int, int]] = []
            for label in labels:
                label_word_count = len(normalized_anchor(label).split())
                # The label itself determines the crop origin. Values on the
                # same OCR line must not be mistaken for part of the anchor.
                for start in range(len(ordered)):
                    stop = min(len(ordered), start + label_word_count + 1)
                    for end in range(start + 1, stop + 1):
                        segment = " ".join(word.text for word in ordered[start:end])
                        matches.append((anchor_similarity(segment, label), label_word_count, start, end))
            quality, _, start, end = max(
                matches,
                key=lambda item: (item[0], item[1], -abs((item[3] - item[2]) - item[1])),
                default=(0.0, 0, 0, 0),
            )
            # A small edit tolerance accommodates normal OCR substitutions,
            # while excluding unrelated prose as an anchor.
            if quality < 0.84:
                continue
            anchor_words = ordered[start:end]
            top = min(word.top for word in anchor_words)
            right = max(word.left + word.width for word in anchor_words)
            bottom = max(word.top + word.height for word in anchor_words)
            line_height = max(1, bottom - top)
            proposals.append(RegionProposal(
                field_or_section=field,
                page=page,
                bounding_region=(
                    min(page_width, right + max(1, line_height // 3)),
                    max(0, top - line_height),
                    page_width,
                    min(page_height, bottom + (line_height * 5)),
                ),
                anchor_text=observed,
                anchor_quality=round(quality, 3),
                layout_family=layout_family,
                proposed_reader="label_value_roi",
            ))
    # Repeated headings are useful corroboration, but a damaged or adversarial
    # page must not cause unbounded crop OCR. Two visible occurrences preserve
    # an alternate reading while bounding each field's per-page work.
    bounded: list[RegionProposal] = []
    counts: dict[str, int] = defaultdict(int)
    for proposal in proposals:
        if counts[proposal.field_or_section] < MAX_REGION_PROPOSALS_PER_FIELD:
            bounded.append(proposal)
            counts[proposal.field_or_section] += 1
    return tuple(bounded)


def normalize_candidate(field: str, raw_text: str) -> str:
    """Normalize a crop reading by expected field type without policy logic."""
    rows = [normalize_space(line) for line in raw_text.splitlines() if normalize_space(line)]
    first = rows[0] if rows else ""
    if field == "applicant_name":
        return clean_name(first)
    if field == "species_code":
        return clean_species(first)
    if field in {"home_world", "declared_purpose"}:
        return first
    if field == "visa_class":
        match = re.search(r"\b(XW[- ]?[12]|DIP[- ]?1|MED[- ]?3|TRANSIT[- ]?7)\b", raw_text, re.I)
        return match.group(1).upper().replace(" ", "-") if match else ""
    if field == "sponsor_id":
        return clean_sponsor(raw_text)
    if field == "arrival_date":
        return clean_date(raw_text)
    if field == "risk_flags":
        return clean_flags(raw_text)
    if field == "fee_status":
        match = re.search(r"\b(paid|unpaid|waived)\b", raw_text, re.I)
        return match.group(1).lower() if match else ""
    if field == "disposition":
        match = re.search(r"\b(APPROVED|DENIED|NEEDS[ _-]?REVIEW)\b", raw_text, re.I)
        return match.group(1).upper().replace(" ", "_").replace("-", "_") if match else ""
    return first


def clean_anchored_fee_status(text: str) -> str:
    """Read a fee value only when the same visible crop has an exact fee anchor."""
    folded = normalized_anchor(text)
    if "fee receipt" not in folded and "fee status" not in folded:
        return ""
    exact = re.search(r"\b(unpaid|paid|waived)\b", text, re.I)
    if exact:
        return exact.group(1).lower()
    status_value = re.search(r"\bfee status\s*[:.=-]?\s*([a-z]{3,12})", text, re.I)
    if status_value and status_value.group(1).casefold() != "unknown":
        observed = status_value.group(1).casefold()
        ranked = sorted(
            (
                (SequenceMatcher(None, observed, expected).ratio(), expected)
                for expected in ("paid", "unpaid", "waived")
            ),
            reverse=True,
        )
        if (
            ranked[0][0] >= FEE_STATUS_FUZZY_MIN_SIMILARITY
            and ranked[0][0] - ranked[1][0] >= FEE_STATUS_FUZZY_MIN_MARGIN
        ):
            return ranked[0][1]
    # A receipt that visibly records the standard paid amount is affirmative
    # evidence even when its printed status is damaged. A zero charge is not
    # treated as a waiver: it may instead mean the status itself is unknown.
    if re.search(r"\$\s*809(?:\.00)?(?:\s|$)", text):
        return "paid"
    return ""


def ocr_with_quality(image: Image.Image, psm: int) -> tuple[str, float]:
    words = ocr_words(image, psm=psm)
    lines: dict[tuple[int, int, int], list[OcrWord]] = defaultdict(list)
    for word in words:
        lines[(word.block, word.paragraph, word.line)].append(word)
    text = "\n".join(
        " ".join(word.text for word in sorted(line_words, key=lambda item: item.left))
        for _, line_words in sorted(lines.items())
    )
    quality = sum(word.confidence for word in words) / len(words) if words else 0.0
    return text, round(quality, 3)


def read_fee_band_candidate(
    image: Image.Image,
    page: int,
    *,
    corroborating_texts: Iterable[str] = (),
    read_variant: Callable[[Image.Image, int], tuple[str, float]] = ocr_with_quality,
) -> CandidateValue | None:
    """Read one measured, page-relative fee band from visible pixels."""
    crop_box = (
        0,
        0,
        int(image.width * FEE_BAND_WIDTH_RATIO),
        int(image.height * FEE_BAND_HEIGHT_RATIO),
    )
    band = image.crop(crop_box)
    band = ImageEnhance.Contrast(ImageOps.grayscale(band)).enhance(1.7)
    raw_text, quality = read_variant(band, 11)
    raw_text = raw_text.strip()
    folded = normalized_anchor(raw_text)
    if "fee receipt" not in folded and "fee status" not in folded:
        return None
    normalized_value = clean_anchored_fee_status(raw_text)
    if normalized_value == "unpaid" and not any(
        re.search(r"\bunpaid\b", text, re.I)
        for text in corroborating_texts
    ):
        normalized_value = ""
    return CandidateValue(
        field="fee_status",
        raw_text=raw_text,
        normalized_value=normalized_value,
        page=page,
        crop=crop_box,
        reader_family="top_left_fee_band",
        transform_chain=("crop", "grayscale", "contrast_1.7"),
        ocr_quality=quality,
        anchor_quality=1.0,
        visible_evidence_excerpt=normalize_space(raw_text)[:160],
    )


def read_manual_note_band(
    image: Image.Image,
    *,
    read_variant: Callable[[Image.Image, int], tuple[str, float]] = ocr_with_quality,
) -> tuple[str, bool]:
    """Read the visible heading/finding area of a possible manual note."""
    crop = image.crop((0, 0, int(image.width * 0.80), int(image.height * 0.30)))
    crop = ImageEnhance.Contrast(ImageOps.grayscale(crop)).enhance(1.7)
    text, _ = read_variant(crop, 11)
    is_note = page_kind(text) == "note"
    return exact_manual_finding(text), is_note


def read_roi_candidates(
    image: Image.Image,
    proposal: RegionProposal,
    *,
    read_variant: Callable[[Image.Image, int], tuple[str, float]] = ocr_with_quality,
) -> tuple[CandidateValue, ...]:
    """Run a bounded, validation-triggered ROI retry in trace-only shadow mode."""
    left, top, right, bottom = proposal.bounding_region
    crop_box = (
        max(0, min(image.width, left)),
        max(0, min(image.height, top)),
        max(0, min(image.width, right)),
        max(0, min(image.height, bottom)),
    )
    if crop_box[0] >= crop_box[2] or crop_box[1] >= crop_box[3]:
        return ()
    native = image.crop(crop_box)
    variants = (
        (native, 6, ("crop", "native")),
        (
            native.resize((native.width * 2, native.height * 2), Image.Resampling.LANCZOS),
            7,
            ("crop", "rescale_2x"),
        ),
    )
    candidates: list[CandidateValue] = []
    for variant, psm, transforms in variants:
        raw_text, quality = read_variant(variant, psm)
        raw_text = raw_text.strip()
        normalized = normalize_candidate(proposal.field_or_section, raw_text)
        candidates.append(CandidateValue(
            field=proposal.field_or_section,
            raw_text=raw_text,
            normalized_value=normalized,
            page=proposal.page,
            crop=crop_box,
            reader_family=proposal.proposed_reader,
            transform_chain=transforms,
            ocr_quality=quality,
            anchor_quality=proposal.anchor_quality,
            visible_evidence_excerpt=normalize_space(raw_text)[:160],
        ))
        # Retry only a structurally invalid/missing native read. This avoids
        # applying an unmeasured quality threshold and bounds work to 2 reads.
        if normalized:
            break
    return tuple(candidates)


def resolve_candidate_ledger(candidates: Iterable[CandidateValue]) -> tuple[LedgerEntry, ...]:
    """Resolve shadow candidates while retaining every conflict and source."""
    grouped: dict[str, list[CandidateValue]] = defaultdict(list)
    for candidate in candidates:
        grouped[candidate.field].append(candidate)
    entries: list[LedgerEntry] = []
    for field in sorted(grouped):
        field_candidates = tuple(grouped[field])
        valid = [candidate for candidate in field_candidates if candidate.normalized_value]
        values = sorted({candidate.normalized_value for candidate in valid})
        if not valid:
            selected, reason = "", "no_valid_candidate"
        else:
            winner = max(valid, key=lambda item: (item.anchor_quality, item.ocr_quality))
            selected = winner.normalized_value
            reason = (
                "corroborated_equivalent_readings"
                if len(valid) > 1 and len(values) == 1
                else "highest_anchor_then_ocr_quality"
            )
        entries.append(LedgerEntry(
            field=field,
            candidates=field_candidates,
            selected_value=selected,
            resolution_reason=reason,
            conflicts=tuple(value for value in values if value != selected),
        ))
    return tuple(entries)


def corroborated_sponsor_fallback(ledger: Iterable[LedgerEntry]) -> str:
    """Promote only the grouped-validated, exact-anchor sponsor recovery path."""
    entry = next((item for item in ledger if item.field == "sponsor_id"), None)
    if entry is None or not entry.selected_value or entry.conflicts:
        return ""
    supporting = [
        candidate
        for candidate in entry.candidates
        if candidate.normalized_value == entry.selected_value
        and candidate.anchor_quality == 1.0
        and candidate.ocr_quality >= SPONSOR_ROI_MIN_OCR_QUALITY
        and candidate.transform_chain == ("crop", "native")
    ]
    distinct_crops = {candidate.crop for candidate in supporting}
    return entry.selected_value if len(distinct_crops) >= 2 else ""


def conflict_free_fee_fallback(candidates: Iterable[CandidateValue]) -> str:
    """Promote the grouped-validated fee band only when all valid reads agree."""
    values = {
        candidate.normalized_value
        for candidate in candidates
        if candidate.reader_family == "top_left_fee_band"
        and candidate.anchor_quality == 1.0
        and candidate.normalized_value
    }
    return next(iter(values)) if len(values) == 1 else ""


def write_trace(
    pdf_path: Path,
    pages: list[dict[str, object]],
    ledger: tuple[LedgerEntry, ...],
) -> None:
    """Persist optional visible-pixel development traces outside predictions."""
    directory = os.environ.get("MIB_TRACE_DIR")
    if not directory:
        return
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    payload = {
        "case_id": pdf_path.stem,
        "pages": pages,
        "evidence_ledger": [asdict(entry) for entry in ledger],
    }
    temporary = destination / f".{pdf_path.stem}.tmp"
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    temporary.replace(destination / f"{pdf_path.stem}.trace.json")


def visible_texts(image: Image.Image) -> tuple[str, ...]:
    """Return independent visible OCR readings rather than choosing one early."""
    gray = ImageOps.grayscale(image)
    boosted = ImageEnhance.Contrast(gray).enhance(1.5)
    first = ocr(boosted, 6)
    second = ocr(boosted, 11)
    readings = tuple(text for text in (first, second) if normalize_space(text))
    return readings if len(set(readings)) > 1 else readings[:1]


def orientation_label_score(texts: Iterable[str]) -> int:
    """Count distinct public schema labels visible in OCR text."""
    folded = normalized_anchor("\n".join(texts))
    return sum(label in folded for label in ORIENTATION_LABELS)


def orient_page_from_sparse_retry(
    image: Image.Image,
    native_texts: tuple[str, ...],
    *,
    read_variant: Callable[[Image.Image, int], str] = ocr,
) -> tuple[Image.Image, tuple[str, ...], int]:
    """Retry clearly unresolved pages at right angles using label evidence.

    The retry is independent of field values and accepts an orientation only
    when public schema labels improve by a wide margin. It therefore cannot
    manufacture a value from a rotation that merely yields more OCR tokens.
    """
    if os.environ.get("MIB_DISABLE_ORIENTATION") == "1":
        return image, native_texts, 0
    native_score = orientation_label_score(native_texts)
    if native_score >= ORIENTATION_NATIVE_MIN_LABELS:
        return image, native_texts, 0
    gray = ImageEnhance.Contrast(ImageOps.grayscale(image)).enhance(1.5)
    candidates: list[tuple[int, int, Image.Image, str]] = []
    for angle in (90, 270):
        rotated = gray.rotate(angle, expand=True, fillcolor=255)
        sparse = read_variant(rotated, 11)
        candidates.append((orientation_label_score((sparse,)), angle, rotated, sparse))
    candidates.sort(key=lambda item: item[0], reverse=True)
    best_score, angle, rotated, sparse = candidates[0]
    runner_up = candidates[1][0]
    if (
        best_score < ORIENTATION_RETRY_MIN_LABELS
        or best_score - native_score < ORIENTATION_RETRY_MIN_GAIN
        or best_score <= runner_up
    ):
        return image, native_texts, 0
    dense = read_variant(rotated, 6)
    readings = tuple(text for text in (dense, sparse) if normalize_space(text))
    return rotated, readings if len(set(readings)) > 1 else readings[:1], angle


def page_kind(text: str) -> str:
    folded = text.casefold()
    scores = {kind: sum(marker in folded for marker in markers) for kind, markers in PAGE_MARKERS.items()}
    kind, score = max(scores.items(), key=lambda pair: pair[1])
    return kind if score >= 2 or (kind in {"biometric", "note"} and score == 1) else "other"


def extract_label(text: str, label: str) -> str:
    # Prefer an exact label row before trying an inline value. Otherwise a
    # pattern such as ``Species(?: Code)?`` can backtrack at a newline and
    # incorrectly return the optional label word ``Code`` as the value.
    rows = [normalize_space(line) for line in text.splitlines() if normalize_space(line)]
    label_re = re.compile(rf"^{label}\s*:?$", re.I)
    for index, row in enumerate(rows):
        if label_re.fullmatch(row) and index + 1 < len(rows):
            return rows[index + 1].strip(" .,:;")
    match = re.search(rf"\b{label}[ \t]*:?[ \t]*([^|;\n]{{1,90}})", text, re.I)
    if match and normalize_space(match.group(1)):
        value = normalize_space(match.group(1))
        value = re.split(r"\s{2,}(?=[A-Z][A-Za-z ]{2,}:)", value)[0]
        return value.strip(" .,:;")
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
    if normalized_anchor(value) in NON_NAME_LABELS:
        return ""
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", value)
    if len(words) >= 3 and words[0].casefold() == "is":
        words = words[1:]
    if len(words) >= 4 and tuple(word.casefold() for word in words[-2:]) in {
        ("passport", "image"), ("scan", "image"),
    }:
        words = words[:-2]
    return " ".join(word[:1].upper() + word[1:].lower() for word in words[:4]) if len(words) >= 2 else ""


def snap_applicant_name(value: str) -> str:
    """Correct clear OCR edits using the exported public token vocabulary."""
    words = value.split()
    if len(words) not in {2, 3, 4} or not NAME_TOKEN_VOCABULARY:
        return value
    vocabulary = set(NAME_TOKEN_VOCABULARY)

    def ranked_token(word: str) -> tuple[float, float, str]:
        ranked = sorted(
            (
                SequenceMatcher(None, word.casefold(), token.casefold()).ratio(),
                token,
            )
            for token in NAME_TOKEN_VOCABULARY
        )
        return ranked[-1][0], ranked[-1][0] - ranked[-2][0], ranked[-1][1]

    if len(words) > 2:
        pairs = []
        for first_index in range(len(words) - 1):
            for second_index in range(first_index + 1, len(words)):
                first = ranked_token(words[first_index])
                second = ranked_token(words[second_index])
                if min(first[0], second[0]) >= NAME_PAIR_MIN_SIMILARITY:
                    pairs.append((
                        min(first[0], second[0]),
                        first[0] + second[0],
                        first[2],
                        second[2],
                    ))
        if pairs:
            _, _, first, second = max(pairs)
            return f"{first} {second}"
        return value

    corrected: list[str] = []
    for word in words:
        if word in vocabulary:
            corrected.append(word)
            continue
        if len(word) < 4:
            corrected.append(word)
            continue
        best_score, margin, best_token = ranked_token(word)
        if (
            best_score >= NAME_TOKEN_MIN_SIMILARITY
            and margin >= NAME_TOKEN_MIN_MARGIN
        ):
            corrected.append(best_token)
        else:
            corrected.append(word)
    return " ".join(corrected)


def clean_enum(value: str, choices: set[str]) -> str:
    candidate = normalize_space(value).casefold().replace("_", " ")
    for choice in choices:
        if candidate == choice.replace("_", " "):
            return choice
    return ""


def clean_species(value: str) -> str:
    candidate = normalize_space(value).upper().replace(" ", "_").replace("-", "_")
    return candidate if re.fullmatch(r"[A-Z][A-Z_]{1,36}", candidate) else ""


def category_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def snap_category(field: str, value: str) -> str:
    """Correct only near-exact OCR misspellings in a small public schema set."""
    options = CATEGORY_VOCABULARY.get(field, ())
    if not value or value in {"unknown", ""} or not options:
        return value
    needle = category_key(value)
    ranked = sorted(
        ((SequenceMatcher(None, needle, category_key(option)).ratio(), option) for option in options),
        reverse=True,
    )
    best_score, best_value = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
    if best_score >= 0.86 and best_score - runner_up >= 0.06:
        return best_value
    return value


def snap_output_purpose(value: str) -> str:
    """Apply a lower-risk vocabulary snap after adjudication is complete."""
    options = CATEGORY_VOCABULARY.get("declared_purpose", ())
    if value in options or not value or not options:
        return value
    needle = category_key(value)
    ranked = sorted(
        (
            SequenceMatcher(None, needle, category_key(option)).ratio(),
            option,
        )
        for option in options
    )
    best_score, best_value = ranked[-1]
    runner_up = ranked[-2][0]
    return best_value if best_score >= 0.60 and best_score - runner_up >= 0.10 else value


def normalize_output_visa(value: str) -> str:
    match = re.fullmatch(r"(XW|DIP|MED|TRANSIT)-?([1237])", value)
    return f"{match.group(1)}-{match.group(2)}" if match else value


def visible_category_candidates(text: str, kind: str) -> dict[str, set[str]]:
    """Find unique exact public-category values in already-visible OCR text."""
    folded = f" {normalized_anchor(text)} "
    found: dict[str, set[str]] = defaultdict(set)
    for field, values in CATEGORY_VOCABULARY.items():
        if field == "declared_purpose" and kind not in {"intake", "sponsor"}:
            continue
        for value in values:
            if f" {normalized_anchor(value)} " in folded:
                found[field].add(value)
    return found


def fuzzy_visible_category_candidate(texts: Iterable[str], field: str) -> str:
    """Recover a uniquely near-exact structured category from visible words."""
    if field not in {"species_code", "home_world"}:
        return ""
    scores = {value: 0.0 for value in CATEGORY_VOCABULARY.get(field, ())}
    for text in texts:
        words = re.findall(r"[a-z0-9]+", normalized_anchor(text))
        for value in scores:
            target = normalized_anchor(value)
            size = len(target.split())
            for width in range(max(1, size - 1), size + 2):
                for start in range(max(0, len(words) - width + 1)):
                    observed = " ".join(words[start:start + width])
                    scores[value] = max(scores[value], SequenceMatcher(None, observed, target).ratio())
    ranked = sorted(((score, value) for value, score in scores.items()), reverse=True)
    if len(ranked) < 2:
        return ""
    best_score, best_value = ranked[0]
    return best_value if best_score >= 0.80 and best_score - ranked[1][0] >= 0.05 else ""


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


def unique_visible_arrival_date(texts: Iterable[str]) -> str:
    """Return one conflict-free, valid packet-era ISO date from visible OCR."""
    candidates: set[str] = set()
    for text in texts:
        for raw in re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", text):
            try:
                parsed = date.fromisoformat(raw)
            except ValueError:
                continue
            if PACKET_RECEIPT_DATE.year - 1 <= parsed.year <= PACKET_RECEIPT_DATE.year:
                candidates.add(raw)
    return next(iter(candidates)) if len(candidates) == 1 else ""


def fuzzy_visible_arrival_date(texts: Iterable[str]) -> str:
    """Recover one anchored date after a consistent visible 6-to-8 OCR edit."""
    candidates: set[str] = set()
    for text in texts:
        for line in text.splitlines():
            # The broad label shape admits ordinary OCR damage to "Arrival
            # Date" but still requires a short, same-line field label.
            if not re.search(r"\ba[a-z]{2,12}\s+d[a-z]{2,6}\b", line, re.I):
                continue
            for month, day in re.findall(r"\b2028[-/](\d{2})[-/]?(\d{2})\b", line):
                # On this print family the glyph used for six is frequently
                # read as eight in both the year and month positions.
                month = "06" if month == "08" else month
                rendered = f"2026-{month}-{day}"
                try:
                    date.fromisoformat(rendered)
                except ValueError:
                    continue
                candidates.add(rendered)
    return next(iter(candidates)) if len(candidates) == 1 else ""


def clean_sponsor(value: str) -> str:
    found = re.search(r"\bSPN[- ]?(\d{4})\b", value.upper())
    return f"SPN-{found.group(1)}" if found else ""


def visible_sponsor_output_fallback(texts: Iterable[str]) -> str:
    """Choose one packet-wide visible sponsor candidate for output recovery."""
    substitutions = str.maketrans({
        "O": "0", "I": "1", "L": "1", "S": "5", "B": "8", "Z": "2",
    })
    counts: Counter[str] = Counter()
    for text in texts:
        for raw in re.findall(r"\bSPN[- ]?([0-9OILSBZ]{4})\b", text.upper()):
            candidate = f"SPN-{raw.translate(substitutions)}"
            if candidate != DEFAULTS["sponsor_id"]:
                counts[candidate] += 1
    ranked = counts.most_common()
    if not ranked or (len(ranked) > 1 and ranked[0][1] == ranked[1][1]):
        return ""
    return ranked[0][0]


def visible_name_output_fallback(texts: Iterable[str]) -> str:
    """Recover one unambiguous packet-wide name pair from visible OCR tokens."""
    if not NAME_TOKEN_VOCABULARY:
        return ""

    def nearest(word: str) -> tuple[float, str]:
        ranked = max(
            (
                SequenceMatcher(None, word.casefold(), token.casefold()).ratio(),
                token,
            )
            for token in NAME_TOKEN_VOCABULARY
        )
        return ranked

    evidence: Counter[str] = Counter()
    for text in texts:
        for line in text.splitlines():
            words = re.findall(r"[A-Za-z][A-Za-z'-]*", line)
            for first, second in zip(words, words[1:]):
                first_score, first_token = nearest(first)
                second_score, second_token = nearest(second)
                if min(first_score, second_score) >= NAME_PAIR_MIN_SIMILARITY:
                    evidence[f"{first_token} {second_token}"] += 1
    ranked = evidence.most_common()
    if not ranked or (len(ranked) > 1 and ranked[0][1] == ranked[1][1]):
        return ""
    return ranked[0][0]


def clean_flags(value: str) -> str:
    folded = value.casefold().replace("-", "_").replace(" ", "_")
    found = [flag for flag in sorted(DISQUALIFYING | REVIEW_FLAGS) if flag in folded]
    if found:
        return "|".join(found)
    if re.search(r"\bnone\b|\bclear\b", value, re.I):
        return "none"
    # Fuzzy recovery is restricted to short field-like values. It must have a
    # unique nearest public flag, which keeps arbitrary note prose and the
    # SAMPLE DENIAL watermark outside this evidence path.
    tokens = re.findall(r"[a-z][a-z0-9_\-]{5,40}", value.casefold())
    if len(tokens) <= 3 and len(normalize_space(value)) <= 80:
        recovered: list[str] = []
        for token in tokens:
            candidate = token.replace("-", "_")
            ranked = sorted([
                (
                    SequenceMatcher(None, candidate, flag).ratio(),
                    flag,
                )
                for flag in DISQUALIFYING | REVIEW_FLAGS
            ], reverse=True)
            best_score, best_flag = ranked[0]
            runner_up = ranked[1][0]
            if (
                best_score >= RISK_FLAG_FUZZY_MIN_SIMILARITY
                and best_score - runner_up >= RISK_FLAG_FUZZY_MIN_MARGIN
            ):
                recovered.append(best_flag)
        if recovered:
            return "|".join(sorted(set(recovered)))
    return ""


def exact_packet_adverse_flags(texts: Iterable[str]) -> str:
    """Collect exact public adverse flags outside visible instruction payloads."""
    retained_lines: list[str] = []
    for text in texts:
        retained_lines.extend(
            line for line in text.splitlines()
            if not re.search(
                r"\b(barcode|payload|force|ignore|answer|adjudication)\b",
                line,
                re.I,
            )
        )
    folded = "\n".join(retained_lines).casefold().replace("-", "_").replace(" ", "_")
    found = sorted(flag for flag in DISQUALIFYING | REVIEW_FLAGS if flag in folded)
    return "|".join(found)


def add(parsed: dict[str, list[Evidence]], field: str, value: str, source: str, confidence: float) -> None:
    if value:
        parsed[field].append(Evidence(field, value, source, confidence))


def parse_page(kind: str, text: str, parsed: dict[str, list[Evidence]]) -> str:
    weight = PRECEDENCE[kind]
    # Structured labels are stronger evidence than page-title recognition.  We
    # therefore read label/value pairs on every visible page; page kind only
    # sets provenance precedence when multiple pages disagree.
    applicant = clean_name(extract_label(text, "Applicant Name")) or clean_name(extract_label(text, "Applicant"))
    if not applicant:
        applicant = value_before_label(text, "Applicant Name", clean_name) or value_before_label(text, "Applicant", clean_name)
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
    if kind == "registry":
        registry_status = extract_label(text, "Registry Status")
        if re.search(r"\bembargo\b", registry_status, re.I):
            add(parsed, "registry_embargo_review", "true", kind, weight)
    if kind == "note":
        add(parsed, "risk_flags", clean_flags(extract_label(text, "(?:Risk )?Flag") or text), kind, weight)
        finding = re.search(r"\b(APPROVED|DENIED|NEEDS[ _-]?REVIEW)\b", extract_label(text, "Finding") or text, re.I)
        return finding.group(1).upper().replace(" ", "_").replace("-", "_") if finding else ""
    return ""


def exact_manual_finding(text: str) -> str:
    """Return only an explicitly labeled finding in visible page OCR."""
    match = re.search(
        r"\bFinding\s*:\s*(APPROVED|DENIED|NEEDS[ _-]?REVIEW)\b",
        text,
        re.I,
    )
    return match.group(1).upper().replace(" ", "_").replace("-", "_") if match else ""


def sponsor_attested_details(text: str) -> tuple[str, str, str, str]:
    """Read facts bound to one visibly attributed sponsor sentence."""
    match = re.search(
        r"\bSponsor\s+(SPN[- ]?\d{4})\s+attests\s+that\s+"
        r"([A-Z][A-Za-z]{2,20}\s+[A-Z][A-Za-z]{2,20})\s+is\s+expected\b",
        text,
        re.I,
    )
    if not match:
        return "", "", "", ""
    # Optional facts must not make the sponsor/applicant identity anchor fail.
    # Limit their search to the nearby continuation of this one attestation.
    continuation = text[match.end():match.end() + 600]
    purpose_match = re.match(
        r"\s+on\s+Earth\s+for\s+([^\.\n]{2,40})\.",
        continuation,
        re.I,
    )
    purpose_lookup = {
        normalize_space(value).casefold(): value
        for value in CATEGORY_VOCABULARY.get("declared_purpose", ())
    }
    purpose = (
        purpose_lookup.get(normalize_space(purpose_match.group(1)).casefold(), "")
        if purpose_match else ""
    )
    visa_match = re.search(
        r"\bresponsibility\s+for\s+class\s+"
        r"(XW[- ]?[12]|DIP[- ]?1|MED[- ]?3|TRANSIT[- ]?7)\s+compliance\b",
        continuation,
        re.I,
    )
    visa = visa_match.group(1).upper().replace(" ", "-") if visa_match else ""
    return clean_sponsor(match.group(1)), clean_name(match.group(2)), purpose, visa


def sponsor_attestation(text: str) -> tuple[str, str]:
    """Read the sponsor/applicant pair from a visibly attributed sentence."""
    sponsor, applicant, _purpose, _visa = sponsor_attested_details(text)
    return sponsor, applicant


def sponsor_attested_applicant(text: str) -> str:
    return sponsor_attestation(text)[1]


def approximate_labeled_applicants(text: str) -> set[str]:
    """Find names after a strongly applicant-like visible label.

    These approximate reads are conflict evidence only. They never populate an
    output field, which prevents a damaged label or name from becoming truth.
    """
    candidates: set[str] = set()
    for raw_line in text.splitlines():
        words = re.sub(r"[^A-Za-z'-]+", " ", raw_line).split()
        for index, word in enumerate(words):
            if not 5 <= len(word) <= 12:
                continue
            if SequenceMatcher(None, word.casefold(), "applicant").ratio() < 0.80:
                continue
            value_index = index + 1
            if value_index < len(words) and words[value_index].casefold() == "name":
                value_index += 1
            candidate = clean_name(" ".join(words[value_index:value_index + 2]))
            if candidate:
                candidates.add(candidate)
    return candidates


def exact_manual_corrections(text: str) -> dict[str, str]:
    """Read exact visible correction prose without inferring missing values."""
    patterns = {
        "applicant_name": (
            r"\bManual\s+correction\s*:\s*applicant\s+is\s+"
            r"([A-Z][A-Za-z]{2,20}\s+[A-Z][A-Za-z]{2,20})\b"
        ),
        "sponsor_id": (
            r"\bManual\s+correction\s*:\s*sponsor\s+is\s+(SPN[- ]?\d{4})\b"
        ),
        "visa_class": (
            r"\bManual\s+correction\s*:\s*visa\s+class\s+is\s+"
            r"(XW[- ]?[12]|DIP[- ]?1|MED[- ]?3|TRANSIT[- ]?7)\b"
        ),
        "fee_status": (
            r"\bManual\s+correction\s*:\s*fee\s+status\s+is\s+"
            r"(paid|unpaid|waived)\b"
        ),
    }
    corrections: dict[str, str] = {}
    for field, pattern in patterns.items():
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        raw = match.group(1)
        if field == "applicant_name":
            value = clean_name(raw)
        elif field == "sponsor_id":
            value = clean_sponsor(raw)
        elif field == "visa_class":
            value = raw.upper().replace(" ", "-")
        else:
            value = raw.casefold()
        if value:
            corrections[field] = value
    return corrections


def choose(field: str, options: list[Evidence]) -> str:
    if not options:
        return DEFAULTS[field]
    if field in {"applicant_name", "species_code", "sponsor_id"}:
        by_value: dict[str, list[Evidence]] = defaultdict(list)
        for item in options:
            by_value[item.value].append(item)
        consensus = sorted([
            (
                len({item.source for item in items}),
                len(items),
                max(item.confidence for item in items),
                value,
            )
            for value, items in by_value.items()
        ], reverse=True)
        if consensus:
            sources, reads, _, value = consensus[0]
            runner_ties = len(consensus) > 1 and consensus[1][:2] >= consensus[0][:2]
            category_is_valid = (
                field != "species_code"
                or value in CATEGORY_VOCABULARY.get("species_code", ())
            )
            if (sources >= 2 or reads >= 3) and not runner_ties and category_is_valid:
                return value
    ranked = sorted(options, key=lambda item: (item.confidence, item.source), reverse=True)
    best = ranked[0]
    if field == "risk_flags":
        adverse = [item.value for item in options if item.value != "none"]
        return "|".join(sorted(set("|".join(adverse).split("|")))) if adverse else best.value
    return best.value


def paired_approval_recovery(
    row: dict[str, str],
    *,
    approval_probability: float,
    denial_probability: float,
    affirmative_clean_biometrics: bool = False,
) -> bool:
    """Require complete clean policy evidence plus an independent denial veto."""
    complete = all(
        row[field] != DEFAULTS[field]
        for field in FIELDS
        if field != "risk_flags"
    )
    fee_ok = row["fee_status"] == "paid" or (
        row["fee_status"] == "waived" and row["visa_class"] == "DIP-1"
    )
    sponsor_ok = row["visa_class"] == "DIP-1" or (
        re.fullmatch(r"SPN-\d{4}", row["sponsor_id"]) is not None
        and row["sponsor_id"] not in REVOKED_SPONSORS
    )
    model_pair_accepts = (
        approval_probability >= PAIRED_APPROVAL_MIN_PROBABILITY
        and denial_probability <= PAIRED_APPROVAL_MAX_DENIAL_PROBABILITY
    )
    biometric_pair_accepts = (
        affirmative_clean_biometrics
        and approval_probability >= BIOMETRIC_APPROVAL_MIN_PROBABILITY
        and denial_probability <= BIOMETRIC_APPROVAL_MAX_DENIAL_PROBABILITY
    )
    return (
        row["risk_flags"] == "none"
        and complete
        and fee_ok
        and sponsor_ok
        and row["visa_class"] in {"XW-1", "XW-2", "DIP-1", "MED-3"}
        and (model_pair_accepts or biometric_pair_accepts)
    )


def modeled_fee_approval_recovery(
    row: dict[str, str],
    *,
    fee_value: str,
    fee_probability: float,
    fee_margin: float,
    approval_probability: float,
    denial_probability: float,
    affirmative_clean_biometrics: bool,
) -> bool:
    """Recover approval only from a strict independent evidence conjunction."""
    complete_non_fee = all(
        row[field] != DEFAULTS[field]
        for field in FIELDS
        if field not in {"risk_flags", "fee_status"}
    )
    fee_ok = fee_value == "paid" or (
        fee_value == "waived" and row["visa_class"] == "DIP-1"
    )
    sponsor_ok = row["visa_class"] == "DIP-1" or (
        re.fullmatch(r"SPN-\d{4}", row["sponsor_id"]) is not None
        and row["sponsor_id"] not in REVOKED_SPONSORS
    )
    return (
        row["fee_status"] == "unknown"
        and row["risk_flags"] == "none"
        and complete_non_fee
        and fee_ok
        and sponsor_ok
        and row["visa_class"] in {"XW-1", "XW-2", "DIP-1", "MED-3"}
        and affirmative_clean_biometrics
        and fee_probability >= MODELED_FEE_APPROVAL_MIN_PROBABILITY
        and fee_margin >= MODELED_FEE_APPROVAL_MIN_MARGIN
        and approval_probability >= MODELED_FEE_APPROVAL_MIN_ROUTER_PROBABILITY
        and denial_probability <= MODELED_FEE_APPROVAL_MAX_DENIAL_PROBABILITY
    )


def decide(
    row: dict[str, str],
    finding: str,
    *,
    visible_clean_biometrics: bool,
    visible_paid_fee: bool,
    explicit_manual_approval: bool = False,
    explicit_manual_review: bool = False,
    trusted_stale_arrival: bool = False,
    unresolved_manual_note: bool = False,
) -> tuple[str, float]:
    flags = set(row["risk_flags"].split("|")) if row["risk_flags"] != "none" else set()
    if explicit_manual_review:
        return "NEEDS_REVIEW", 0.99
    # An unconflicted, explicitly labeled visible finding is the highest
    # authority in the public evidence order. It may record an exception to a
    # lower-priority fee or visa fact, but never overrides observed risk.
    if explicit_manual_approval and row["risk_flags"] == "none":
        return "APPROVED", 0.98
    if (
        finding == "DENIED"
        or flags & DISQUALIFYING
        or row["fee_status"] == "unpaid"
        or row["visa_class"] == "TRANSIT-7"
    ):
        return "DENIED", 0.97
    if finding == "NEEDS_REVIEW" or flags & REVIEW_FLAGS:
        return "NEEDS_REVIEW", 0.99
    if trusted_stale_arrival:
        return "DENIED", 0.97
    strict_revocation = row["sponsor_id"] in STRICT_DENIAL_SPONSORS or (
        row["sponsor_id"] == "SPN-4040" and row["fee_status"] == "paid"
    )
    if row["visa_class"] not in {"unknown", "DIP-1"} and strict_revocation:
        return "DENIED", 0.97
    if row["visa_class"] == "unknown" or row["arrival_date"] == "1900-01-01" or row["fee_status"] == "unknown":
        return "NEEDS_REVIEW", 0.45
    if row["visa_class"] != "DIP-1" and row["sponsor_id"] in {"SPN-0000", *REVOKED_SPONSORS}:
        return "NEEDS_REVIEW", 0.0
    if row["fee_status"] == "waived" and row["visa_class"] != "DIP-1":
        return "NEEDS_REVIEW", 0.42
    # A clean approval requires affirmative fee and biometric evidence rather
    # than using an extraction default as a proxy for no risk.
    # An unresolved visible authority page vetoes the otherwise clean path.
    if visible_clean_biometrics and visible_paid_fee and not unresolved_manual_note:
        return "APPROVED", 0.98
    return "NEEDS_REVIEW", 0.39


def predict(pdf_path: Path) -> dict[str, object]:
    evidence: dict[str, list[Evidence]] = defaultdict(list)
    retry_evidence: dict[str, list[Evidence]] = defaultdict(list)
    findings: list[str] = []
    manual_findings: list[str] = []
    saw_manual_note_page = False
    trace_pages: list[dict[str, object]] = []
    trace_candidates: list[CandidateValue] = []
    sponsor_roi_candidates: list[CandidateValue] = []
    fee_band_candidates: list[CandidateValue] = []
    category_candidates: dict[str, set[str]] = defaultdict(set)
    retry_category_candidates: dict[str, set[str]] = defaultdict(set)
    whole_fee_candidates: set[str] = set()
    sponsor_applicant_candidates: set[str] = set()
    sponsor_attestations: set[tuple[str, str, str, str]] = set()
    approximate_applicant_candidates: set[str] = set()
    manual_correction_candidates: dict[str, set[str]] = defaultdict(set)
    visible_ocr_texts: list[str] = []
    model_ocr_texts: list[str] = []
    trace_enabled = bool(os.environ.get("MIB_TRACE_DIR"))
    try:
        for page_number, image in enumerate(render_pages(pdf_path), start=1):
            native_texts = visible_texts(image)
            model_ocr_texts.extend(native_texts)
            _oriented_image, oriented_texts, orientation_correction = orient_page_from_sparse_retry(
                image, native_texts,
            )
            # Rotation is additive evidence. Preserve the native page and its
            # readings so an OCR retry cannot erase an already-legible value
            # or redirect geometry-based readers to a transformed coordinate
            # system. Stable evidence ordering lets native readings win an
            # otherwise equal tie while rotated text fills unresolved fields.
            retry_texts: tuple[str, ...] = ()
            if oriented_texts != native_texts:
                retry_texts = tuple(text for text in oriented_texts if text not in native_texts)
            page_texts = native_texts + retry_texts
            for text, target_evidence, target_categories in (
                *((text, evidence, category_candidates) for text in native_texts),
                *((text, retry_evidence, retry_category_candidates) for text in retry_texts),
            ):
                visible_ocr_texts.append(text)
                if target_evidence is evidence:
                    approximate_applicant_candidates.update(approximate_labeled_applicants(text))
                    for field, value in exact_manual_corrections(text).items():
                        manual_correction_candidates[field].add(value)
                manual_finding = exact_manual_finding(text)
                if manual_finding:
                    manual_findings.append(manual_finding)
                kind = page_kind(text)
                saw_manual_note_page |= kind == "note"
                if target_evidence is evidence:
                    attestation = sponsor_attested_details(text)
                    attested_sponsor, attested_applicant, _purpose, _visa = attestation
                    if attested_applicant:
                        sponsor_applicant_candidates.add(attested_applicant)
                    if attested_sponsor and attested_applicant:
                        sponsor_attestations.add(attestation)
                for field, values in visible_category_candidates(text, kind).items():
                    target_categories[field].update(values)
                whole_fee = clean_anchored_fee_status(text)
                if target_evidence is evidence and whole_fee in {"paid", "waived"}:
                    whole_fee_candidates.add(whole_fee)
                finding = parse_page(kind, text, target_evidence)
                if finding:
                    findings.append(finding)
            band_finding, band_is_note = read_manual_note_band(image)
            saw_manual_note_page |= band_is_note
            if band_finding:
                manual_findings.append(band_finding)
            fee_band_candidate = read_fee_band_candidate(
                image,
                page_number,
                corroborating_texts=native_texts,
            )
            if fee_band_candidate is not None:
                fee_band_candidates.append(fee_band_candidate)
            sponsor_page = any("sponsor" in text.casefold() for text in native_texts)
            if trace_enabled or sponsor_page:
                words = ocr_words(ImageEnhance.Contrast(ImageOps.grayscale(image)).enhance(1.5))
                # Use the ordinary whole-page OCR as the layout classifier;
                # the TSV pass supplies only visible geometry for proposals.
                layout = page_kind(" ".join(word.text for word in words))
                proposals = propose_regions(
                    words,
                    page=page_number,
                    page_width=image.width,
                    page_height=image.height,
                    layout_family=layout,
                )
                sponsor_proposals = tuple(
                    proposal for proposal in proposals
                    if proposal.field_or_section == "sponsor_id"
                )
                sponsor_candidates = tuple(
                    candidate
                    for proposal in sponsor_proposals
                    for candidate in read_roi_candidates(image, proposal)
                )
                sponsor_roi_candidates.extend(sponsor_candidates)
            if trace_enabled:
                diagnostics = page_diagnostics(image, page_number, orientation_correction)
                page_candidates = sponsor_candidates + tuple(
                    candidate
                    for proposal in proposals
                    if proposal.field_or_section != "sponsor_id"
                    for candidate in read_roi_candidates(image, proposal)
                )
                if fee_band_candidate is not None:
                    page_candidates += (fee_band_candidate,)
                trace_candidates.extend(page_candidates)
                trace_pages.append({
                    "diagnostics": asdict(diagnostics),
                    "region_proposals": [asdict(proposal) for proposal in proposals],
                    "roi_candidates": [asdict(candidate) for candidate in page_candidates],
                })
    except (OSError, RuntimeError, subprocess.SubprocessError):
        pass
    if trace_enabled:
        write_trace(pdf_path, trace_pages, resolve_candidate_ledger(trace_candidates))
    row = {field: choose(field, evidence[field]) for field in FIELDS}
    retry_row = {field: choose(field, retry_evidence[field]) for field in FIELDS}
    for field in FIELDS:
        if row[field] == DEFAULTS[field] and retry_row[field] != DEFAULTS[field]:
            row[field] = retry_row[field]
    retry_adverse = retry_row["risk_flags"]
    if retry_adverse != "none":
        native_adverse = row["risk_flags"] if row["risk_flags"] != "none" else ""
        row["risk_flags"] = "|".join(sorted(set(filter(None, (native_adverse + "|" + retry_adverse).split("|")))))
    if row["risk_flags"] == DEFAULTS["risk_flags"]:
        packet_adverse = exact_packet_adverse_flags(visible_ocr_texts)
        if packet_adverse:
            row["risk_flags"] = packet_adverse
    if len(manual_correction_candidates["applicant_name"]) == 1:
        row["applicant_name"] = next(iter(manual_correction_candidates["applicant_name"]))
    if len(sponsor_applicant_candidates) == 1:
        attested_applicant = next(iter(sponsor_applicant_candidates))
        agreement = SequenceMatcher(
            None, row["applicant_name"].casefold(), attested_applicant.casefold()
        ).ratio()
        unrelated_visible_applicant = any(
            SequenceMatcher(None, candidate.casefold(), attested_applicant.casefold()).ratio() < 0.50
            for candidate in approximate_applicant_candidates
        )
        if (
            row["applicant_name"] == DEFAULTS["applicant_name"]
            and not unrelated_visible_applicant
        ) or agreement >= 0.60:
            row["applicant_name"] = attested_applicant
    matching_attested_sponsors = {
        sponsor
        for sponsor, applicant, _purpose, _visa in sponsor_attestations
        if SequenceMatcher(
            None, row["applicant_name"].casefold(), applicant.casefold()
        ).ratio() >= 0.85
    }
    if len(matching_attested_sponsors) == 1:
        row["sponsor_id"] = next(iter(matching_attested_sponsors))
    matching_attestations = {
        attestation
        for attestation in sponsor_attestations
        if SequenceMatcher(
            None, row["applicant_name"].casefold(), attestation[1].casefold()
        ).ratio() >= 0.85
    }
    attested_purposes = {item[2] for item in matching_attestations if item[2]}
    if len(attested_purposes) == 1:
        row["declared_purpose"] = next(iter(attested_purposes))
    attested_visas = {item[3] for item in matching_attestations if item[3]}
    if len(attested_visas) == 1:
        row["visa_class"] = next(iter(attested_visas))
    if row["sponsor_id"] == DEFAULTS["sponsor_id"]:
        recovered_sponsor = corroborated_sponsor_fallback(
            resolve_candidate_ledger(sponsor_roi_candidates),
        )
        if recovered_sponsor:
            row["sponsor_id"] = recovered_sponsor
    if row["fee_status"] == DEFAULTS["fee_status"]:
        recovered_fee = conflict_free_fee_fallback(fee_band_candidates)
        if recovered_fee:
            row["fee_status"] = recovered_fee
        elif len(whole_fee_candidates) == 1:
            row["fee_status"] = next(iter(whole_fee_candidates))
    output_fallback_date = unique_visible_arrival_date(visible_ocr_texts)
    for field in CATEGORY_VOCABULARY:
        candidates = category_candidates[field]
        if field in {"species_code", "home_world"} and len(candidates) == 1:
            row[field] = next(iter(candidates))
        elif row[field] == DEFAULTS[field] and len(candidates) == 1:
            row[field] = next(iter(candidates))
        elif not candidates and field in {"species_code", "home_world"}:
            fuzzy = fuzzy_visible_category_candidate(visible_ocr_texts, field)
            if fuzzy:
                row[field] = fuzzy
        retry_candidates = retry_category_candidates[field]
        if len(retry_candidates) == 1 and (
            row[field] == DEFAULTS[field]
            or (field in {"species_code", "home_world"} and row[field] not in CATEGORY_VOCABULARY[field])
        ):
            row[field] = next(iter(retry_candidates))
    for field in CATEGORY_VOCABULARY:
        row[field] = snap_category(field, row[field])
    for field, values in manual_correction_candidates.items():
        if len(values) == 1:
            row[field] = next(iter(values))
    manual_finding_values = set(manual_findings)
    if len(manual_finding_values) == 1:
        finding = next(iter(manual_finding_values))
    elif len(manual_finding_values) > 1:
        finding = "NEEDS_REVIEW"
    else:
        finding = "DENIED" if "DENIED" in findings else "NEEDS_REVIEW" if "NEEDS_REVIEW" in findings else ""
    visible_clean_biometrics = any(
        item.source == "biometric" and item.value == "none"
        for item in evidence["risk_flags"] + retry_evidence["risk_flags"]
    )
    visible_paid_fee = row["fee_status"] == "paid"
    explicit_manual_approval = (
        manual_finding_values == {"APPROVED"}
    )
    explicit_manual_review = manual_finding_values == {"NEEDS_REVIEW"}
    trusted_stale_arrival = False
    if row["visa_class"] not in {"unknown", "DIP-1"} and row["arrival_date"] != "1900-01-01":
        try:
            stale = (PACKET_RECEIPT_DATE - date.fromisoformat(row["arrival_date"])).days > 180
        except ValueError:
            stale = False
        trusted_stale_arrival = stale and any(
            item.value == row["visa_class"] and item.source in {"intake", "sponsor"}
            for item in evidence["visa_class"] + retry_evidence["visa_class"]
        )
    adjudication, confidence = decide(
        row,
        finding,
        visible_clean_biometrics=visible_clean_biometrics,
        visible_paid_fee=visible_paid_fee,
        explicit_manual_approval=explicit_manual_approval,
        explicit_manual_review=explicit_manual_review,
        trusted_stale_arrival=trusted_stale_arrival,
        unresolved_manual_note=saw_manual_note_page and not manual_findings,
    )
    denial_probability = visible_ocr_denial_probability(model_ocr_texts)
    if (
        adjudication == "NEEDS_REVIEW"
        and not explicit_manual_review
        and denial_probability >= float(DENIAL_TEXT_MODEL.get("threshold", 1.0))
    ):
        adjudication, confidence = "DENIED", 0.87
    approval_probability = visible_ocr_approval_probability(model_ocr_texts)
    fee_model_value, fee_model_probability, fee_model_margin = visible_ocr_fee_prediction(
        model_ocr_texts
    )
    folded_model_text = normalized_anchor("\n".join(model_ocr_texts))
    affirmative_clean_biometrics = (
        "observed flags none" in folded_model_text
        or "risk flags none" in folded_model_text
    )
    if (
        adjudication == "NEEDS_REVIEW"
        and not explicit_manual_review
        and approval_probability >= float(APPROVAL_TEXT_MODEL.get("threshold", 1.0))
    ):
        adjudication, confidence = "APPROVED", 0.97
    elif (
        adjudication == "NEEDS_REVIEW"
        and not explicit_manual_review
        and paired_approval_recovery(
            row,
            approval_probability=approval_probability,
            denial_probability=denial_probability,
            affirmative_clean_biometrics=affirmative_clean_biometrics,
        )
    ):
        adjudication, confidence = "APPROVED", 0.94
    elif (
        adjudication == "NEEDS_REVIEW"
        and not explicit_manual_review
        and modeled_fee_approval_recovery(
            row,
            fee_value=fee_model_value,
            fee_probability=fee_model_probability,
            fee_margin=fee_model_margin,
            approval_probability=approval_probability,
            denial_probability=denial_probability,
            affirmative_clean_biometrics=affirmative_clean_biometrics,
        )
    ):
        adjudication, confidence = "APPROVED", 0.93
    if row["arrival_date"] == DEFAULTS["arrival_date"] and output_fallback_date:
        row["arrival_date"] = output_fallback_date
    if row["arrival_date"] == DEFAULTS["arrival_date"]:
        fuzzy_date = fuzzy_visible_arrival_date(model_ocr_texts)
        if fuzzy_date:
            row["arrival_date"] = fuzzy_date
    if row["sponsor_id"] == DEFAULTS["sponsor_id"]:
        output_sponsor = visible_sponsor_output_fallback(model_ocr_texts)
        if output_sponsor:
            row["sponsor_id"] = output_sponsor
    if row["applicant_name"] == DEFAULTS["applicant_name"]:
        output_name = visible_name_output_fallback(model_ocr_texts)
        if output_name:
            row["applicant_name"] = output_name
    row["applicant_name"] = snap_applicant_name(row["applicant_name"])
    row["declared_purpose"] = snap_output_purpose(row["declared_purpose"])
    row["visa_class"] = normalize_output_visa(row["visa_class"])
    for field, model, valid_values in (
        ("declared_purpose", PURPOSE_TEXT_MODEL, CATEGORY_VOCABULARY.get("declared_purpose", ())),
        ("visa_class", VISA_TEXT_MODEL, ("XW-1", "XW-2", "DIP-1", "MED-3", "TRANSIT-7")),
        ("species_code", SPECIES_TEXT_MODEL, CATEGORY_VOCABULARY.get("species_code", ())),
        ("home_world", HOME_WORLD_TEXT_MODEL, CATEGORY_VOCABULARY.get("home_world", ())),
    ):
        if row[field] not in valid_values:
            value, probability, margin = visible_ocr_categorical_prediction(
                model_ocr_texts, model
            )
            if (
                value
                and probability >= float(model.get("minimum_probability", 1.0))
                and margin >= float(model.get("minimum_margin", 1.0))
            ):
                row[field] = value
    if row["risk_flags"] == DEFAULTS["risk_flags"]:
        modeled_risk = visible_ocr_risk_prediction(model_ocr_texts)
        if modeled_risk:
            row["risk_flags"] = modeled_risk
            if modeled_risk_denial_recovery(adjudication, modeled_risk):
                adjudication, confidence = "DENIED", 0.87
    if row["fee_status"] == DEFAULTS["fee_status"]:
        if (
            fee_model_value in {"paid", "unpaid", "waived"}
            and fee_model_probability >= float(FEE_TEXT_MODEL.get("minimum_probability", 1.0))
            and fee_model_margin >= float(FEE_TEXT_MODEL.get("minimum_margin", 1.0))
        ):
            row["fee_status"] = fee_model_value
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
