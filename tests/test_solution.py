import unittest
from collections import defaultdict

import solution


class VisiblePipelineTests(unittest.TestCase):
    def test_label_value_can_follow_its_own_line(self):
        self.assertEqual(
            solution.extract_label("Fee Status\npaid\nAmount\n$809", "Fee Status"),
            "paid",
        )

    def test_name_can_precede_applicant_label(self):
        text = "Case ID\nMIB-000001\nIxodane Luzarn\nApplicant\nSpecies Code"
        self.assertEqual(
            solution.value_before_label(text, "Applicant(?: Name)?", solution.clean_name),
            "Ixodane Luzarn",
        )

    def test_disqualifying_flag_cannot_approve(self):
        row = dict(solution.DEFAULTS)
        row.update({"visa_class": "XW-2", "fee_status": "paid", "risk_flags": "active_warrant", "arrival_date": "2026-01-01", "sponsor_id": "SPN-1111"})
        self.assertEqual(
            solution.decide(row, "", visible_clean_biometrics=True, visible_paid_fee=True)[0],
            "DENIED",
        )

    def test_approval_requires_observed_clean_fee_and_risk(self):
        row = dict(solution.DEFAULTS)
        row.update({"visa_class": "XW-2", "fee_status": "paid", "risk_flags": "none", "arrival_date": "2026-01-01", "sponsor_id": "SPN-1111"})
        self.assertEqual(
            solution.decide(row, "", visible_clean_biometrics=True, visible_paid_fee=True)[0],
            "APPROVED",
        )
        row["fee_status"] = "unknown"
        self.assertEqual(
            solution.decide(row, "", visible_clean_biometrics=True, visible_paid_fee=False)[0],
            "NEEDS_REVIEW",
        )

    def test_default_none_cannot_create_an_approval(self):
        row = dict(solution.DEFAULTS)
        row.update({"visa_class": "XW-2", "fee_status": "paid", "risk_flags": "none", "arrival_date": "2026-01-01", "sponsor_id": "SPN-1111"})
        self.assertEqual(
            solution.decide(row, "", visible_clean_biometrics=False, visible_paid_fee=True)[0],
            "NEEDS_REVIEW",
        )

    def test_structured_labels_are_read_even_when_page_typing_is_wrong(self):
        evidence = defaultdict(list)
        solution.parse_page(
            "other",
            "Registry Name\nIxodane Luzarn\nHome World\nBarnard-c\nArrival Date\n2026-02-10",
            evidence,
        )
        self.assertEqual(evidence["applicant_name"][0].value, "Ixodane Luzarn")
        self.assertEqual(evidence["home_world"][0].value, "Barnard-c")
        self.assertEqual(evidence["arrival_date"][0].value, "2026-02-10")


if __name__ == "__main__":
    unittest.main()
