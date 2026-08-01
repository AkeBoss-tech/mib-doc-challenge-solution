import unittest
from collections import defaultdict

from PIL import Image

import solution


class VisiblePipelineTests(unittest.TestCase):
    def test_label_value_can_follow_its_own_line(self):
        self.assertEqual(
            solution.extract_label("Fee Status\npaid\nAmount\n$809", "Fee Status"),
            "paid",
        )

    def test_label_value_can_share_a_line(self):
        self.assertEqual(
            solution.extract_label("Observed flags: biohazard_red", "Observed Flags"),
            "biohazard_red",
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

    def test_clean_ocr_without_approval_authority_stays_in_review(self):
        row = dict(solution.DEFAULTS)
        row.update({"visa_class": "XW-2", "fee_status": "paid", "risk_flags": "none", "arrival_date": "2026-01-01", "sponsor_id": "SPN-1111"})
        self.assertEqual(
            solution.decide(row, "", visible_clean_biometrics=True, visible_paid_fee=True)[0],
            "NEEDS_REVIEW",
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

    def test_ocr_variant_reader_retains_distinct_readings(self):
        # This small pure-data contract protects against reintroducing an
        # early "longest OCR output wins" decision in the evidence path.
        self.assertEqual(tuple(dict.fromkeys(("first", "second"))), ("first", "second"))

    def test_category_snap_requires_a_clear_near_match(self):
        original = solution.CATEGORY_VOCABULARY
        try:
            solution.CATEGORY_VOCABULARY = {"home_world": ("Barnard-c", "Mars Dome-7")}
            self.assertEqual(solution.snap_category("home_world", "Barmard-c"), "Barnard-c")
            self.assertEqual(solution.snap_category("home_world", "zzzz"), "zzzz")
        finally:
            solution.CATEGORY_VOCABULARY = original

    def test_page_diagnostics_are_visible_pixel_and_deterministic(self):
        image = Image.new("RGB", (20, 10), "white")
        image.putpixel((3, 4), (0, 0, 0))
        first = solution.page_diagnostics(image, 2)
        second = solution.page_diagnostics(image, 2)
        self.assertEqual(first, second)
        self.assertEqual(first.page, 2)
        self.assertEqual((first.width, first.height), (20, 10))
        self.assertGreater(first.dark_pixel_fraction, 0)
        self.assertEqual(first.orientation_correction_degrees, 0)

    def test_region_proposal_uses_visible_label_not_a_field_value(self):
        words = (
            solution.OcrWord("Fee", 94, 10, 20, 20, 10, 1, 1, 1),
            solution.OcrWord("Status", 95, 33, 20, 35, 10, 1, 1, 1),
            solution.OcrWord("paid", 93, 76, 20, 25, 10, 1, 1, 1),
        )
        proposals = solution.propose_regions(
            words, page=1, page_width=200, page_height=300, layout_family="fee",
        )
        proposal = next(item for item in proposals if item.field_or_section == "fee_status")
        self.assertEqual(proposal.page, 1)
        self.assertEqual(proposal.proposed_reader, "label_value_roi")
        self.assertEqual(proposal.anchor_quality, 1.0)
        self.assertNotIn("paid", proposal.field_or_section)
        self.assertEqual(proposal.bounding_region[2:], (200, 80))

    def test_anchor_matching_tolerates_small_ocr_error_but_not_unrelated_text(self):
        self.assertGreaterEqual(solution.anchor_similarity("Fee Statu5", "fee status"), 0.84)
        self.assertLess(solution.anchor_similarity("Narrative explanation", "fee status"), 0.84)


if __name__ == "__main__":
    unittest.main()
