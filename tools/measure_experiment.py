#!/usr/bin/env python3
"""Measure prediction and visible-pixel trace artifacts without altering them."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable


FIELDS = (
    "applicant_name", "species_code", "home_world", "visa_class", "sponsor_id",
    "arrival_date", "declared_purpose", "risk_flags", "fee_status",
)


def read_jsonl(path: Path) -> dict[str, dict[str, object]]:
    rows: dict[str, dict[str, object]] = {}
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            case_id = row.get("case_id")
            if not isinstance(case_id, str) or not case_id:
                raise ValueError(f"{path}:{line_number}: missing string case_id")
            if case_id in rows:
                raise ValueError(f"{path}:{line_number}: duplicate case_id {case_id}")
            rows[case_id] = row
    return rows


def read_groups(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    valid_items = all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in payload.items()
    ) if isinstance(payload, dict) else False
    if not valid_items:
        raise ValueError("group map must be a JSON object of case_id to group name")
    return payload


def ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def prediction_report(
    predictions: dict[str, dict[str, object]],
    *,
    references: dict[str, dict[str, object]] | None = None,
    baseline: dict[str, dict[str, object]] | None = None,
    groups: dict[str, str] | None = None,
) -> dict[str, object]:
    report: dict[str, object] = {"valid_rows": len(predictions)}
    if baseline is not None:
        shared = sorted(predictions.keys() & baseline.keys())
        report["change_vs_baseline"] = {
            "shared_rows": len(shared),
            "adjudication_changes": sum(
                predictions[case_id].get("adjudication") != baseline[case_id].get("adjudication")
                for case_id in shared
            ),
            "approval_additions": sum(
                predictions[case_id].get("adjudication") == "APPROVED"
                and baseline[case_id].get("adjudication") != "APPROVED"
                for case_id in shared
            ),
            "approval_removals": sum(
                predictions[case_id].get("adjudication") != "APPROVED"
                and baseline[case_id].get("adjudication") == "APPROVED"
                for case_id in shared
            ),
            "field_changes": {
                field: sum(predictions[case_id].get(field) != baseline[case_id].get(field) for case_id in shared)
                for field in FIELDS
            },
        }
    if references is None:
        return report

    comparable = sorted(predictions.keys() & references.keys())
    report["reference_coverage"] = {
        "comparable_rows": len(comparable),
        "missing_prediction_ids": len(references.keys() - predictions.keys()),
        "extra_prediction_ids": len(predictions.keys() - references.keys()),
    }
    report["per_field"] = {
        field: {
            "correct": sum(predictions[case_id].get(field) == references[case_id].get(field) for case_id in comparable),
            "total": len(comparable),
            "accuracy": ratio(
                sum(predictions[case_id].get(field) == references[case_id].get(field) for case_id in comparable),
                len(comparable),
            ),
        }
        for field in FIELDS
    }
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    correct_decisions = 0
    brier_terms: list[float] = []
    catastrophic_false_approvals = 0
    for case_id in comparable:
        predicted = predictions[case_id].get("adjudication")
        actual = references[case_id].get("adjudication")
        confusion[str(actual)][str(predicted)] += 1
        correct = predicted == actual
        correct_decisions += correct
        confidence = predictions[case_id].get("confidence")
        if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
            brier_terms.append((float(confidence) - float(correct)) ** 2)
        catastrophic_false_approvals += predicted == "APPROVED" and actual == "DENIED"
    report["decisions"] = {
        "correct": correct_decisions,
        "total": len(comparable),
        "accuracy": ratio(correct_decisions, len(comparable)),
        "confusion": {actual: dict(sorted(counts.items())) for actual, counts in sorted(confusion.items())},
        "brier": round(sum(brier_terms) / len(brier_terms), 6) if brier_terms else None,
        "catastrophic_false_approvals": catastrophic_false_approvals,
    }

    groups = groups or {}
    if groups:
        missing_groups = [case_id for case_id in comparable if case_id not in groups]
        if missing_groups:
            raise ValueError(f"group map is missing {len(missing_groups)} comparable case IDs")
        by_group: dict[str, list[str]] = defaultdict(list)
        for case_id in comparable:
            by_group[groups[case_id]].append(case_id)
        report["grouped"] = {
            group: {
                "rows": len(case_ids),
                "decision_accuracy": ratio(
                    sum(
                        predictions[case_id].get("adjudication")
                        == references[case_id].get("adjudication")
                        for case_id in case_ids
                    ),
                    len(case_ids),
                ),
                "per_field_accuracy": {
                    field: ratio(
                        sum(predictions[case_id].get(field) == references[case_id].get(field) for case_id in case_ids),
                        len(case_ids),
                    )
                    for field in FIELDS
                },
            }
            for group, case_ids in sorted(by_group.items())
        }
    return report


def trace_report(traces: Iterable[dict[str, object]]) -> dict[str, object]:
    cases = pages = proposals = candidates = valid_candidates = retries = conflicts = selected_fields = 0
    candidate_fields: Counter[str] = Counter()
    retry_recoveries = 0
    for trace in traces:
        cases += 1
        trace_pages = trace.get("pages", [])
        if not isinstance(trace_pages, list):
            raise ValueError("trace pages must be a list")
        pages += len(trace_pages)
        for page in trace_pages:
            if not isinstance(page, dict):
                raise ValueError("trace page must be an object")
            page_proposals = page.get("region_proposals", [])
            page_candidates = page.get("roi_candidates", [])
            if not isinstance(page_proposals, list) or not isinstance(page_candidates, list):
                raise ValueError("trace proposals and candidates must be lists")
            proposals += len(page_proposals)
            candidates += len(page_candidates)
            crop_native_valid: dict[tuple[object, ...], bool] = {}
            for candidate in page_candidates:
                if not isinstance(candidate, dict):
                    raise ValueError("trace candidate must be an object")
                field = str(candidate.get("field", ""))
                candidate_fields[field] += 1
                normalized = bool(candidate.get("normalized_value"))
                valid_candidates += normalized
                chain = candidate.get("transform_chain", [])
                is_retry = isinstance(chain, list) and "rescale_2x" in chain
                retries += is_retry
                key = (field, candidate.get("page"), tuple(candidate.get("crop", [])))
                if not is_retry:
                    crop_native_valid[key] = normalized
                elif normalized and not crop_native_valid.get(key, False):
                    retry_recoveries += 1
        ledger = trace.get("evidence_ledger", [])
        if not isinstance(ledger, list):
            raise ValueError("trace evidence_ledger must be a list")
        selected_fields += sum(bool(entry.get("selected_value")) for entry in ledger if isinstance(entry, dict))
        conflicts += sum(len(entry.get("conflicts", [])) for entry in ledger if isinstance(entry, dict))
    return {
        "cases": cases,
        "pages": pages,
        "region_proposals": proposals,
        "roi_candidates": candidates,
        "valid_candidates": valid_candidates,
        "retry_reads": retries,
        "retry_recoveries": retry_recoveries,
        "selected_ledger_fields": selected_fields,
        "conflicting_values": conflicts,
        "candidates_by_field": dict(sorted(candidate_fields.items())),
        "proposals_per_page": ratio(proposals, pages),
        "candidates_per_page": ratio(candidates, pages),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--groups", type=Path, help="JSON object mapping case_id to leakage-safe group")
    parser.add_argument("--trace-dir", type=Path)
    parser.add_argument("--runtime-seconds", type=float)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    predictions = read_jsonl(args.predictions)
    report = prediction_report(
        predictions,
        references=read_jsonl(args.reference) if args.reference else None,
        baseline=read_jsonl(args.baseline) if args.baseline else None,
        groups=read_groups(args.groups),
    )
    if args.trace_dir:
        trace_paths = sorted(args.trace_dir.glob("*.trace.json"))
        traces = [json.loads(path.read_text(encoding="utf-8")) for path in trace_paths]
        report["traces"] = trace_report(traces)
    if args.runtime_seconds is not None:
        report["runtime"] = {
            "total_seconds": round(args.runtime_seconds, 6),
            "seconds_per_pdf": ratio(args.runtime_seconds, len(predictions)),
        }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
