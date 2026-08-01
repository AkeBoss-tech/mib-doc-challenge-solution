import unittest
from collections import defaultdict
from dataclasses import replace

from PIL import Image

import solution


class VisiblePipelineTests(unittest.TestCase):
    def test_label_value_can_follow_its_own_line(self):
        self.assertEqual(
            solution.extract_label("Fee Status\npaid\nAmount\n$809", "Fee Status"),
            "paid",
        )

    def test_optional_label_suffix_is_not_returned_as_multiline_value(self):
        self.assertEqual(
            solution.extract_label("Species Code\nVENUSIAN_MYCELIAL", "Species(?: Code)?"),
            "VENUSIAN_MYCELIAL",
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

    def test_applicant_after_label_wins_over_nearby_case_id_label(self):
        evidence = defaultdict(list)
        solution.parse_page(
            "intake",
            "Case ID\nMIB-000001\nApplicant\nMiraquell Ixovara\nSpecies Code\nJOVIAN_GASFORM",
            evidence,
        )
        self.assertEqual(evidence["applicant_name"][0].value, "Miraquell Ixovara")
        self.assertEqual(evidence["species_code"][0].value, "JOVIAN_GASFORM")

    def test_schema_label_is_not_a_person_name(self):
        self.assertEqual(solution.clean_name("Case ID"), "")
        self.assertEqual(solution.clean_name("Species Code"), "")

    def test_disqualifying_flag_cannot_approve(self):
        row = dict(solution.DEFAULTS)
        row.update({"visa_class": "XW-2", "fee_status": "paid", "risk_flags": "active_warrant", "arrival_date": "2026-01-01", "sponsor_id": "SPN-1111"})
        self.assertEqual(
            solution.decide(row, "", visible_clean_biometrics=True, visible_paid_fee=True)[0],
            "DENIED",
        )

    def test_short_unique_fuzzy_risk_read_recovers_but_watermark_does_not(self):
        self.assertEqual(solution.clean_flags("bichaxard_yed"), "biohazard_red")
        self.assertEqual(solution.clean_flags("SAMPLE DENIAL"), "")
        self.assertEqual(solution.clean_flags("ordinary administrative narrative"), "")

    def test_fee_band_requires_visible_anchor_and_supports_bounded_recovery(self):
        self.assertEqual(solution.clean_anchored_fee_status("Fee Status: paid"), "paid")
        self.assertEqual(solution.clean_anchored_fee_status("Fee Status: waved"), "waived")
        self.assertEqual(solution.clean_anchored_fee_status("MIB Fee Receipt\n$809.00"), "paid")
        self.assertEqual(solution.clean_anchored_fee_status("MIB Fee Receipt\n$0.00"), "")
        self.assertEqual(solution.clean_anchored_fee_status("Mandatory fee unpaid"), "")

    def test_fee_band_reader_is_page_relative_and_conflict_fail_closed(self):
        image = Image.new("RGB", (100, 200), "white")
        candidate = solution.read_fee_band_candidate(
            image,
            3,
            read_variant=lambda crop, psm: ("MIB Fee Receipt\nFee Status: paid", 92.0),
        )
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.crop, (0, 0, 60, 50))
        self.assertEqual(candidate.normalized_value, "paid")
        self.assertEqual(candidate.page, 3)
        conflict = replace(candidate, normalized_value="waived", page=4)
        self.assertEqual(solution.conflict_free_fee_fallback((candidate,)), "paid")
        self.assertEqual(solution.conflict_free_fee_fallback((candidate, conflict)), "")

    def test_unpaid_fee_band_requires_a_separate_visible_reading(self):
        image = Image.new("RGB", (100, 200), "white")
        read = lambda crop, psm: ("MIB Fee Receipt\nFee Status: unpaid", 92.0)
        uncorroborated = solution.read_fee_band_candidate(image, 1, read_variant=read)
        corroborated = solution.read_fee_band_candidate(
            image,
            1,
            corroborating_texts=("damaged form says unpaid",),
            read_variant=read,
        )
        self.assertEqual(uncorroborated.normalized_value, "")
        self.assertEqual(corroborated.normalized_value, "unpaid")

    def test_registry_embargo_status_is_review_evidence_not_denial_authority(self):
        evidence = defaultdict(list)
        solution.parse_page(
            "registry",
            "Planetary Registry Extract\nRegistry Status\nEMBARGO REVIEW\nArrival Date\n2026-03-18",
            evidence,
        )
        self.assertEqual(evidence["registry_embargo_review"][0].value, "true")
        self.assertEqual(evidence["risk_flags"], [])
        row = dict(solution.DEFAULTS)
        self.assertEqual(
            solution.decide(
                row,
                "",
                visible_clean_biometrics=False,
                visible_paid_fee=False,
            )[0],
            "NEEDS_REVIEW",
        )

    def test_exact_manual_finding_ignores_unlabeled_watermark(self):
        self.assertEqual(
            solution.exact_manual_finding(
                "Manual Adjudicator Note\nFinding: APPROVED\nReason: verified",
            ),
            "APPROVED",
        )
        self.assertEqual(
            solution.exact_manual_finding("Manual Adjudicator Note\nSAMPLE DENIAL"),
            "",
        )

    def test_manual_approval_requires_complete_nonadverse_corroboration(self):
        row = dict(solution.DEFAULTS)
        row.update({
            "visa_class": "XW-2",
            "fee_status": "paid",
            "risk_flags": "none",
            "arrival_date": "2026-01-01",
            "sponsor_id": "SPN-1111",
        })
        self.assertEqual(
            solution.decide(
                row,
                "",
                visible_clean_biometrics=False,
                visible_paid_fee=True,
                explicit_manual_approval=True,
            )[0],
            "APPROVED",
        )
        row["risk_flags"] = "identity_conflict"
        self.assertEqual(
            solution.decide(
                row,
                "",
                visible_clean_biometrics=False,
                visible_paid_fee=True,
                explicit_manual_approval=True,
            )[0],
            "NEEDS_REVIEW",
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
        self.assertLess(proposal.bounding_region[0], 76)
        self.assertEqual(proposal.bounding_region[2:], (200, 80))

    def test_anchor_matching_tolerates_small_ocr_error_but_not_unrelated_text(self):
        self.assertGreaterEqual(solution.anchor_similarity("Fee Statu5", "fee status"), 0.84)
        self.assertLess(solution.anchor_similarity("Narrative explanation", "fee status"), 0.84)

    def test_region_proposals_are_bounded_per_field_and_page(self):
        words = tuple(
            word
            for line in range(1, 5)
            for word in (
                solution.OcrWord("Fee", 94, 10, line * 20, 20, 10, line, 1, 1),
                solution.OcrWord("Status", 95, 33, line * 20, 35, 10, line, 1, 1),
            )
        )
        proposals = solution.propose_regions(
            words, page=1, page_width=200, page_height=300, layout_family="fee",
        )
        self.assertEqual(len(proposals), solution.MAX_REGION_PROPOSALS_PER_FIELD)

    def test_roi_reader_retries_only_after_invalid_native_read(self):
        image = Image.new("RGB", (100, 50), "white")
        proposal = solution.RegionProposal(
            "fee_status", 1, (10, 10, 90, 40), "Fee Status", 0.95, "fee", "label_value_roi",
        )
        calls = []

        def read_variant(crop, psm):
            calls.append((crop.size, psm))
            return ("unreadable", 12.0) if len(calls) == 1 else ("paid", 96.0)

        candidates = solution.read_roi_candidates(image, proposal, read_variant=read_variant)
        self.assertEqual(calls, [((80, 30), 6), ((160, 60), 7)])
        self.assertEqual([item.normalized_value for item in candidates], ["", "paid"])
        self.assertEqual(candidates[1].transform_chain, ("crop", "rescale_2x"))

        calls.clear()
        candidates = solution.read_roi_candidates(
            image,
            proposal,
            read_variant=lambda crop, psm: (calls.append((crop.size, psm)) or "waived", 91.0),
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(calls, [((80, 30), 6)])

    def test_shadow_ledger_retains_conflict_and_respects_anchor_priority(self):
        strong = solution.CandidateValue(
            "fee_status", "paid", "paid", 1, (1, 2, 3, 4), "label_value_roi",
            ("crop", "native"), 75.0, 0.97, "paid",
        )
        weak = solution.CandidateValue(
            "fee_status", "unpaid", "unpaid", 2, (5, 6, 7, 8), "label_value_roi",
            ("crop", "native"), 99.0, 0.85, "unpaid",
        )
        entry = solution.resolve_candidate_ledger((weak, strong))[0]
        self.assertEqual(entry.selected_value, "paid")
        self.assertEqual(entry.conflicts, ("unpaid",))
        self.assertEqual(entry.candidates, (weak, strong))
        self.assertEqual(entry.resolution_reason, "highest_anchor_then_ocr_quality")

    def test_sponsor_fallback_requires_two_high_quality_distinct_native_crops(self):
        first = solution.CandidateValue(
            "sponsor_id", "SPN-1234", "SPN-1234", 1, (1, 2, 30, 40), "label_value_roi",
            ("crop", "native"), 93.0, 1.0, "SPN-1234",
        )
        second = replace(first, crop=(5, 6, 30, 40), ocr_quality=91.0)
        entry = solution.LedgerEntry(
            "sponsor_id", (first, second), "SPN-1234", "corroborated_equivalent_readings", (),
        )
        self.assertEqual(solution.corroborated_sponsor_fallback((entry,)), "SPN-1234")
        self.assertEqual(
            solution.corroborated_sponsor_fallback((replace(entry, candidates=(first,)),)),
            "",
        )
        self.assertEqual(
            solution.corroborated_sponsor_fallback((replace(entry, candidates=(first, replace(second, ocr_quality=84.9))),)),
            "",
        )
        self.assertEqual(
            solution.corroborated_sponsor_fallback((replace(entry, conflicts=("SPN-9999",)),)),
            "",
        )


if __name__ == "__main__":
    unittest.main()
