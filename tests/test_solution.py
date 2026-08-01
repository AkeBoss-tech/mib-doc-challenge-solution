import unittest

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
        self.assertEqual(solution.decide(row, "")[0], "DENIED")

    def test_approval_requires_observed_clean_fee_and_risk(self):
        row = dict(solution.DEFAULTS)
        row.update({"visa_class": "XW-2", "fee_status": "paid", "risk_flags": "none", "arrival_date": "2026-01-01", "sponsor_id": "SPN-1111"})
        self.assertEqual(solution.decide(row, "")[0], "APPROVED")
        row["fee_status"] = "unknown"
        self.assertEqual(solution.decide(row, "")[0], "NEEDS_REVIEW")


if __name__ == "__main__":
    unittest.main()
