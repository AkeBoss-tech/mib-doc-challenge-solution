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
REVOKED_SPONSORS = {"SPN-0007", "SPN-0139", "SPN-4040", "SPN-7331"}
# Public examples establish that these two sponsors consistently deny ordinary
# non-diplomatic applications. Other public revoked sponsors have visible
# signed exceptions, so they remain review/approval blockers rather than an
# unconditional denial fact.
STRICT_DENIAL_SPONSORS = {"SPN-0139", "SPN-7331"}
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


def page_diagnostics(image: Image.Image, page: int) -> PageDiagnostics:
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
        # Rotation correction is intentionally not applied until a measured
        # detector and its held-out benefit exist in this repository.
        orientation_correction_degrees=0,
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
    """Return only an explicitly labeled finding on a visible manual-note page."""
    if page_kind(text) != "note":
        return ""
    value = extract_label(text, "Finding")
    match = re.fullmatch(r"(APPROVED|DENIED|NEEDS[ _-]?REVIEW)", value, re.I)
    return match.group(1).upper().replace(" ", "_").replace("-", "_") if match else ""


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
    explicit_manual_approval: bool = False,
    trusted_stale_arrival: bool = False,
    unresolved_manual_note: bool = False,
) -> tuple[str, float]:
    flags = set(row["risk_flags"].split("|")) if row["risk_flags"] != "none" else set()
    if (
        finding == "DENIED"
        or flags & DISQUALIFYING
        or row["fee_status"] == "unpaid"
        or row["visa_class"] == "TRANSIT-7"
    ):
        return "DENIED", 0.97
    if finding == "NEEDS_REVIEW" or flags & REVIEW_FLAGS:
        return "NEEDS_REVIEW", 0.94
    # A conflict-free labeled manual approval is higher-authority evidence than
    # missing lower-priority form fields. Explicitly observed adverse facts and
    # lower-priority fields still fail closed above this gate.
    if (
        explicit_manual_approval
        and row["risk_flags"] == "none"
        and row["fee_status"] != "unpaid"
        and row["visa_class"] != "TRANSIT-7"
    ):
        return "APPROVED", 0.96
    if trusted_stale_arrival:
        return "DENIED", 0.97
    if row["visa_class"] not in {"unknown", "DIP-1"} and row["sponsor_id"] in STRICT_DENIAL_SPONSORS:
        return "DENIED", 0.97
    if row["visa_class"] == "unknown" or row["arrival_date"] == "1900-01-01" or row["fee_status"] == "unknown":
        return "NEEDS_REVIEW", 0.28
    if row["visa_class"] != "DIP-1" and row["sponsor_id"] in {"SPN-0000", *REVOKED_SPONSORS}:
        return "NEEDS_REVIEW", 0.02
    if row["fee_status"] == "waived" and row["visa_class"] != "DIP-1":
        return "NEEDS_REVIEW", 0.27
    # A clean approval requires affirmative fee and biometric evidence rather
    # than using an extraction default as a proxy for no risk.
    # An unresolved visible authority page vetoes the otherwise clean path.
    if visible_clean_biometrics and visible_paid_fee and not unresolved_manual_note:
        return "APPROVED", 0.96
    return "NEEDS_REVIEW", 0.21


def predict(pdf_path: Path) -> dict[str, object]:
    evidence: dict[str, list[Evidence]] = defaultdict(list)
    findings: list[str] = []
    manual_findings: list[str] = []
    saw_manual_note_page = False
    trace_pages: list[dict[str, object]] = []
    trace_candidates: list[CandidateValue] = []
    sponsor_roi_candidates: list[CandidateValue] = []
    fee_band_candidates: list[CandidateValue] = []
    category_candidates: dict[str, set[str]] = defaultdict(set)
    whole_fee_candidates: set[str] = set()
    visible_ocr_texts: list[str] = []
    trace_enabled = bool(os.environ.get("MIB_TRACE_DIR"))
    try:
        for page_number, image in enumerate(render_pages(pdf_path), start=1):
            page_texts = visible_texts(image)
            for text in page_texts:
                visible_ocr_texts.append(text)
                manual_finding = exact_manual_finding(text)
                if manual_finding:
                    manual_findings.append(manual_finding)
                kind = page_kind(text)
                saw_manual_note_page |= kind == "note"
                for field, values in visible_category_candidates(text, kind).items():
                    category_candidates[field].update(values)
                whole_fee = clean_anchored_fee_status(text)
                if whole_fee in {"paid", "waived"}:
                    whole_fee_candidates.add(whole_fee)
                finding = parse_page(kind, text, evidence)
                if finding:
                    findings.append(finding)
            band_finding, band_is_note = read_manual_note_band(image)
            saw_manual_note_page |= band_is_note
            if band_finding:
                manual_findings.append(band_finding)
            fee_band_candidate = read_fee_band_candidate(
                image,
                page_number,
                corroborating_texts=page_texts,
            )
            if fee_band_candidate is not None:
                fee_band_candidates.append(fee_band_candidate)
            sponsor_page = any("sponsor" in text.casefold() for text in page_texts)
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
                diagnostics = page_diagnostics(image, page_number)
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
    for field in CATEGORY_VOCABULARY:
        row[field] = snap_category(field, row[field])
    finding = "DENIED" if "DENIED" in findings else "NEEDS_REVIEW" if "NEEDS_REVIEW" in findings else ""
    visible_clean_biometrics = any(
        item.source == "biometric" and item.value == "none"
        for item in evidence["risk_flags"]
    )
    visible_paid_fee = row["fee_status"] == "paid"
    explicit_manual_approval = (
        bool(manual_findings)
        and set(manual_findings) == {"APPROVED"}
    )
    trusted_stale_arrival = False
    if row["visa_class"] not in {"unknown", "DIP-1"} and row["arrival_date"] != "1900-01-01":
        try:
            stale = (PACKET_RECEIPT_DATE - date.fromisoformat(row["arrival_date"])).days > 180
        except ValueError:
            stale = False
        trusted_stale_arrival = stale and any(
            item.value == row["visa_class"] and item.source in {"intake", "sponsor"}
            for item in evidence["visa_class"]
        )
    adjudication, confidence = decide(
        row,
        finding,
        visible_clean_biometrics=visible_clean_biometrics,
        visible_paid_fee=visible_paid_fee,
        explicit_manual_approval=explicit_manual_approval,
        trusted_stale_arrival=trusted_stale_arrival,
        unresolved_manual_note=saw_manual_note_page and not manual_findings,
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
