import unittest

from tools import measure_experiment


def row(case_id, adjudication, confidence, **fields):
    return {
        "case_id": case_id,
        "adjudication": adjudication,
        "confidence": confidence,
        **{field: fields.get(field, "same") for field in measure_experiment.FIELDS},
    }


class ExperimentMeasurementTests(unittest.TestCase):
    def test_prediction_report_measures_safety_calibration_groups_and_changes(self):
        predictions = {
            "a": row("a", "APPROVED", 0.8),
            "b": row("b", "NEEDS_REVIEW", 0.6, fee_status="paid"),
        }
        references = {
            "a": row("a", "DENIED", 1.0),
            "b": row("b", "NEEDS_REVIEW", 1.0, fee_status="unpaid"),
        }
        baseline = {
            "a": row("a", "NEEDS_REVIEW", 0.4),
            "b": row("b", "NEEDS_REVIEW", 0.6, fee_status="paid"),
        }
        report = measure_experiment.prediction_report(
            predictions, references=references, baseline=baseline, groups={"a": "g1", "b": "g2"},
        )
        self.assertEqual(report["decisions"]["catastrophic_false_approvals"], 1)
        self.assertEqual(report["decisions"]["confusion"]["DENIED"]["APPROVED"], 1)
        self.assertAlmostEqual(report["decisions"]["brier"], 0.4)
        self.assertEqual(report["per_field"]["fee_status"]["correct"], 1)
        self.assertEqual(report["change_vs_baseline"]["approval_additions"], 1)
        self.assertEqual(report["grouped"]["g2"]["decision_accuracy"], 1.0)

    def test_trace_report_counts_retry_recovery_and_conflicts(self):
        trace = {
            "pages": [{
                "region_proposals": [{"field_or_section": "fee_status"}],
                "roi_candidates": [
                    {
                        "field": "fee_status", "page": 1, "crop": [1, 2, 3, 4],
                        "normalized_value": "", "transform_chain": ["crop", "native"],
                    },
                    {
                        "field": "fee_status", "page": 1, "crop": [1, 2, 3, 4],
                        "normalized_value": "paid", "transform_chain": ["crop", "rescale_2x"],
                    },
                ],
            }],
            "evidence_ledger": [{"field": "fee_status", "selected_value": "paid", "conflicts": ["unpaid"]}],
        }
        report = measure_experiment.trace_report([trace])
        self.assertEqual(report["retry_reads"], 1)
        self.assertEqual(report["retry_recoveries"], 1)
        self.assertEqual(report["conflicting_values"], 1)
        self.assertEqual(report["selected_ledger_fields"], 1)
        self.assertEqual(report["candidates_per_page"], 2.0)


if __name__ == "__main__":
    unittest.main()
