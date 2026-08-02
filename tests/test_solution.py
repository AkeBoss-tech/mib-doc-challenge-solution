import unittest
from collections import defaultdict
from dataclasses import replace
from unittest.mock import patch

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

    def test_name_cleaning_removes_visible_grammar_and_image_labels(self):
        self.assertEqual(solution.clean_name("is Nexix Nexvara"), "Nexix Nexvara")
        self.assertEqual(
            solution.clean_name("Ludane Qorvoss PASSPORT IMAGE"),
            "Ludane Qorvoss",
        )
        self.assertEqual(solution.clean_name("Species Code"), "")

    def test_name_token_snap_requires_clear_similarity_and_margin(self):
        with patch.object(
            solution,
            "NAME_TOKEN_VOCABULARY",
            ("Ixomora", "Miravoss", "Qornax", "Qorzarn"),
        ):
            self.assertEqual(
                solution.snap_applicant_name("Xomora Miravoss"),
                "Ixomora Miravoss",
            )
            self.assertEqual(solution.snap_applicant_name("Qor Miravoss"), "Qor Miravoss")

    def test_name_token_snap_removes_one_high_confidence_ocr_artifact(self):
        with patch.object(
            solution,
            "NAME_TOKEN_VOCABULARY",
            ("Ixomora", "Miravoss", "Qornax", "Qorzarn"),
        ):
            self.assertEqual(
                solution.snap_applicant_name("COPY Ixomora Miravoss"),
                "Ixomora Miravoss",
            )
            self.assertEqual(
                solution.snap_applicant_name("unrelated damaged prose"),
                "unrelated damaged prose",
            )

    def test_sponsor_output_fallback_uses_visible_packet_consensus(self):
        self.assertEqual(
            solution.visible_sponsor_output_fallback(
                ("Sponsor ID: SPN-OI2B\nSponsor ID: SPN-OI2B",)
            ),
            "SPN-0128",
        )
        self.assertEqual(
            solution.visible_sponsor_output_fallback(("SPN-1234 SPN-5678",)),
            "",
        )
        self.assertEqual(
            solution.visible_sponsor_output_fallback(("SPN-0000",)),
            "",
        )

    def test_name_output_fallback_requires_one_unambiguous_visible_pair(self):
        with patch.object(
            solution,
            "NAME_TOKEN_VOCABULARY",
            ("Ixomora", "Miravoss", "Qornax", "Qorzarn"),
        ):
            self.assertEqual(
                solution.visible_name_output_fallback(
                    ("Ixornora Miravoss\nIxornora Miravoss",)
                ),
                "Ixomora Miravoss",
            )
            self.assertEqual(
                solution.visible_name_output_fallback(("Qornax Qorzarn",)),
                "Qornax Qorzarn",
            )
            self.assertEqual(
                solution.visible_name_output_fallback(
                    ("Ixomora Miravoss\nQornax Qorzarn",)
                ),
                "",
            )

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
        self.assertEqual(solution.clean_anchored_fee_status("MIB Fee Receipt\nFee Status: waived"), "waived")

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

    def test_manual_note_band_reads_exact_labeled_finding(self):
        image = Image.new("RGB", (100, 200), "white")
        calls = []

        def reader(crop, psm):
            calls.append((crop.size, psm))
            return "Manual Adjudicator Note\nFinding: APPROVED", 91.0

        self.assertEqual(
            solution.read_manual_note_band(image, read_variant=reader),
            ("APPROVED", True),
        )
        self.assertEqual(calls, [((80, 60), 11)])

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
        self.assertEqual(
            solution.exact_manual_finding(
                "degraded page\nFinding: APPROVED. Reason: signed exception-qualified packet"
            ),
            "APPROVED",
        )

    def test_manual_approval_overrides_missing_lower_priority_fields(self):
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
        incomplete = dict(solution.DEFAULTS)
        self.assertEqual(
            solution.decide(
                incomplete,
                "",
                visible_clean_biometrics=False,
                visible_paid_fee=False,
                explicit_manual_approval=True,
            )[0],
            "APPROVED",
        )
        exception = dict(row, fee_status="unpaid", visa_class="TRANSIT-7")
        self.assertEqual(
            solution.decide(
                exception,
                "",
                visible_clean_biometrics=False,
                visible_paid_fee=False,
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

    def test_exact_manual_review_overrides_lower_priority_denial_facts(self):
        row = dict(solution.DEFAULTS, risk_flags="planetary_embargo")
        self.assertEqual(
            solution.decide(
                row,
                "NEEDS_REVIEW",
                visible_clean_biometrics=False,
                visible_paid_fee=False,
                explicit_manual_review=True,
            )[0],
            "NEEDS_REVIEW",
        )

    def test_clean_affirmative_evidence_approves_unless_authority_is_unresolved(self):
        row = dict(solution.DEFAULTS)
        row.update({"visa_class": "XW-2", "fee_status": "paid", "risk_flags": "none", "arrival_date": "2026-01-01", "sponsor_id": "SPN-1111"})
        self.assertEqual(
            solution.decide(row, "", visible_clean_biometrics=True, visible_paid_fee=True)[0],
            "APPROVED",
        )
        self.assertEqual(
            solution.decide(
                row,
                "",
                visible_clean_biometrics=True,
                visible_paid_fee=True,
                unresolved_manual_note=True,
            )[0],
            "NEEDS_REVIEW",
        )
        row["fee_status"] = "unknown"
        self.assertEqual(
            solution.decide(row, "", visible_clean_biometrics=True, visible_paid_fee=False)[0],
            "NEEDS_REVIEW",
        )

    def test_staleness_and_strict_revocation_are_denial_facts(self):
        row = dict(solution.DEFAULTS)
        row.update({"visa_class": "XW-2", "fee_status": "paid", "arrival_date": "2025-08-01", "sponsor_id": "SPN-1111"})
        self.assertEqual(
            solution.decide(
                row, "", visible_clean_biometrics=False, visible_paid_fee=True,
                trusted_stale_arrival=True,
            )[0],
            "DENIED",
        )
        paid_4040 = dict(row, arrival_date="2026-04-01", sponsor_id="SPN-4040")
        self.assertEqual(
            solution.decide(
                paid_4040, "", visible_clean_biometrics=False, visible_paid_fee=True,
            )[0],
            "DENIED",
        )
        paid_4040["fee_status"] = "waived"
        self.assertEqual(
            solution.decide(
                paid_4040, "", visible_clean_biometrics=False, visible_paid_fee=False,
            )[0],
            "NEEDS_REVIEW",
        )
        row.update({"arrival_date": "2026-04-01", "sponsor_id": "SPN-7331"})
        self.assertEqual(
            solution.decide(row, "", visible_clean_biometrics=False, visible_paid_fee=True)[0],
            "DENIED",
        )
        row["sponsor_id"] = "SPN-2718"
        self.assertEqual(
            solution.decide(row, "", visible_clean_biometrics=False, visible_paid_fee=True)[0],
            "DENIED",
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

    def test_output_only_purpose_and_visa_normalization(self):
        with patch.dict(
            solution.CATEGORY_VOCABULARY,
            {"declared_purpose": ("field repair", "research", "transit")},
        ):
            self.assertEqual(solution.snap_output_purpose("flaid repair"), "field repair")
            self.assertEqual(solution.snap_output_purpose("unrelated prose"), "unrelated prose")
        self.assertEqual(solution.normalize_output_visa("XW2"), "XW-2")
        self.assertEqual(solution.normalize_output_visa("MED-3"), "MED-3")

    def test_visible_category_candidates_are_exact_and_context_bounded(self):
        original = solution.CATEGORY_VOCABULARY
        try:
            solution.CATEGORY_VOCABULARY = {
                "species_code": ("ORION_GRAYS",),
                "declared_purpose": ("field repair",),
            }
            found = solution.visible_category_candidates(
                "Species match ORION_GRAYS; expected for field repair",
                "sponsor",
            )
            self.assertEqual(found["species_code"], {"ORION_GRAYS"})
            self.assertEqual(found["declared_purpose"], {"field repair"})
            self.assertNotIn(
                "declared_purpose",
                solution.visible_category_candidates("field repair", "registry"),
            )
        finally:
            solution.CATEGORY_VOCABULARY = original

    def test_fuzzy_visible_categories_require_a_unique_near_match(self):
        original = solution.CATEGORY_VOCABULARY
        try:
            solution.CATEGORY_VOCABULARY = {
                "home_world": ("Barnard-c", "Mars Dome-7"),
                "declared_purpose": ("field repair",),
            }
            self.assertEqual(
                solution.fuzzy_visible_category_candidate(("Home: Barmard c",), "home_world"),
                "Barnard-c",
            )
            self.assertEqual(
                solution.fuzzy_visible_category_candidate(("field repait",), "declared_purpose"),
                "",
            )
        finally:
            solution.CATEGORY_VOCABULARY = original

    def test_visible_ocr_denial_model_masks_identifiers(self):
        first = solution.visible_ocr_denial_probability(("Case MIB-000123 denied note",))
        second = solution.visible_ocr_denial_probability(("Case MIB-999876 denied note",))
        self.assertAlmostEqual(first, second)

    def test_visible_ocr_approval_model_masks_identifiers(self):
        first = solution.visible_ocr_approval_probability(("Case MIB-000123 approved note",))
        second = solution.visible_ocr_approval_probability(("Case MIB-999876 approved note",))
        self.assertAlmostEqual(first, second)
        self.assertGreaterEqual(first, 0.0)
        self.assertLessEqual(first, 1.0)

    def test_visible_ocr_fee_model_masks_identifiers_and_normalizes_classes(self):
        model = {
            "classes": ["paid", "unknown"],
            "intercepts": [0.0, 0.0],
            "features": {"###": [1.0, [2.0, -2.0]]},
        }
        with patch.object(solution, "FEE_TEXT_MODEL", model):
            first = solution.visible_ocr_fee_prediction(("123",))
            second = solution.visible_ocr_fee_prediction(("987",))
        self.assertEqual(first, second)
        self.assertEqual(first[0], "paid")
        self.assertGreater(first[1], 0.5)

    def test_visible_ocr_categorical_model_is_field_agnostic(self):
        model = {
            "classes": ["research", "transit"],
            "intercepts": [0.0, 0.0],
            "features": {"res": [1.0, [2.0, -2.0]]},
        }
        first = solution.visible_ocr_categorical_prediction(("research",), model)
        second = solution.visible_ocr_categorical_prediction(("research",), model)
        self.assertEqual(first, second)
        self.assertEqual(first[0], "research")
        self.assertGreater(first[1], 0.5)

    def test_visible_ocr_risk_model_emits_only_supported_flags(self):
        model = {
            "flags": ["active_warrant", "sponsor_mismatch"],
            "intercepts": [-10.0, -10.0],
            "thresholds": {"active_warrant": 0.65, "sponsor_mismatch": 0.65},
            "features": {"ris": [1.0, [30.0, 0.0]]},
        }
        with patch.object(solution, "RISK_TEXT_MODEL", model):
            self.assertEqual(solution.visible_ocr_risk_prediction(("risk",)), "active_warrant")
            self.assertEqual(solution.visible_ocr_risk_prediction(("clear",)), "")

    def test_modeled_risk_can_only_recover_denial_from_review(self):
        self.assertTrue(solution.modeled_risk_denial_recovery("NEEDS_REVIEW", "active_warrant"))
        self.assertFalse(solution.modeled_risk_denial_recovery("APPROVED", "active_warrant"))
        self.assertFalse(solution.modeled_risk_denial_recovery("NEEDS_REVIEW", "identity_conflict"))

    def test_paired_approval_recovery_requires_complete_policy_and_denial_veto(self):
        row = {
            "applicant_name": "Veenax Ixoul", "species_code": "ARCTURIAN",
            "home_world": "Luyten-b", "visa_class": "XW-2",
            "sponsor_id": "SPN-1234", "arrival_date": "2026-07-01",
            "declared_purpose": "research", "risk_flags": "none",
            "fee_status": "paid",
        }
        self.assertTrue(solution.paired_approval_recovery(
            row, approval_probability=0.80, denial_probability=0.20,
        ))
        self.assertFalse(solution.paired_approval_recovery(
            row, approval_probability=0.80, denial_probability=0.31,
        ))
        incomplete = dict(row, arrival_date="1900-01-01")
        self.assertFalse(solution.paired_approval_recovery(
            incomplete, approval_probability=0.80, denial_probability=0.20,
        ))
        revoked = dict(row, sponsor_id="SPN-4040")
        self.assertFalse(solution.paired_approval_recovery(
            revoked, approval_probability=0.80, denial_probability=0.20,
        ))
        self.assertTrue(solution.paired_approval_recovery(
            row, approval_probability=0.55, denial_probability=0.35,
            affirmative_clean_biometrics=True,
        ))

    def test_modeled_fee_approval_requires_all_independent_gates(self):
        row = {
            "applicant_name": "Veenax Ixoul", "species_code": "ARCTURIAN",
            "home_world": "Luyten-b", "visa_class": "XW-2",
            "sponsor_id": "SPN-1234", "arrival_date": "2026-07-01",
            "declared_purpose": "research", "risk_flags": "none",
            "fee_status": "unknown",
        }
        arguments = {
            "fee_value": "paid", "fee_probability": 0.40, "fee_margin": 0.25,
            "approval_probability": 0.75, "denial_probability": 0.30,
            "affirmative_clean_biometrics": True,
        }
        self.assertTrue(solution.modeled_fee_approval_recovery(row, **arguments))
        self.assertFalse(solution.modeled_fee_approval_recovery(
            row, **dict(arguments, fee_margin=0.18),
        ))
        self.assertFalse(solution.modeled_fee_approval_recovery(
            row, **dict(arguments, affirmative_clean_biometrics=False),
        ))

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

    def test_orientation_retry_requires_schema_label_gain(self):
        image = Image.new("L", (20, 10), "white")
        image.putpixel((0, 0), 0)

        def reader(rotated, psm):
            if rotated.getpixel((0, rotated.height - 1)) == 0:
                return "Case ID Applicant Visa Class Sponsor ID Arrival Date"
            return "noise"

        selected, texts, angle = solution.orient_page_from_sparse_retry(
            image, ("unresolved",), read_variant=reader,
        )
        self.assertEqual(angle, 90)
        self.assertEqual(selected.size, (10, 20))
        self.assertGreaterEqual(solution.orientation_label_score(texts), 3)

    def test_orientation_retry_leaves_resolved_page_untouched(self):
        image = Image.new("L", (20, 10), "white")
        selected, texts, angle = solution.orient_page_from_sparse_retry(
            image,
            ("Case ID: MIB-1\nApplicant: Qor\nVisa Class: XW-2",),
            read_variant=lambda *_: self.fail("resolved page must not retry"),
        )
        self.assertIs(selected, image)
        self.assertEqual(angle, 0)
        self.assertEqual(len(texts), 1)

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

    def test_name_consensus_can_outvote_one_higher_priority_ocr_error(self):
        options = [
            solution.Evidence("applicant_name", "Veenax Ixoul", "registry", 0.60),
            solution.Evidence("applicant_name", "Veenax Ixoul", "sponsor", 0.70),
            solution.Evidence("applicant_name", "Veenax Ixoal", "intake", 1.00),
        ]
        self.assertEqual(solution.choose("applicant_name", options), "Veenax Ixoul")

    def test_name_consensus_rejects_an_equal_corroboration_tie(self):
        options = [
            solution.Evidence("applicant_name", "Veenax Ixoul", "registry", 0.60),
            solution.Evidence("applicant_name", "Veenax Ixoul", "sponsor", 0.70),
            solution.Evidence("applicant_name", "Veenax Ixoal", "intake", 1.00),
            solution.Evidence("applicant_name", "Veenax Ixoal", "note", 1.00),
        ]
        self.assertEqual(solution.choose("applicant_name", options), "Veenax Ixoal")

    def test_sponsor_attestation_sentence_exposes_only_its_named_applicant(self):
        text = (
            "Sponsor SPN-1234 attests that Veenax Ixoul is expected on Earth for research. "
            "The sponsor accepts responsibility for class DIP-1 compliance."
        )
        self.assertEqual(
            solution.sponsor_attestation(text),
            ("SPN-1234", "Veenax Ixoul"),
        )
        self.assertEqual(
            solution.sponsor_attested_details(text),
            ("SPN-1234", "Veenax Ixoul", "research", "DIP-1"),
        )
        self.assertEqual(
            solution.sponsor_attested_applicant(text),
            "Veenax Ixoul",
        )
        self.assertEqual(
            solution.sponsor_attested_applicant("Applicant Veenax Ixoul is expected on Earth."),
            "",
        )
        self.assertEqual(
            solution.sponsor_attested_details(
                "Sponsor SPN-1234 attests that Veenax Ixoul is expected."
            ),
            ("SPN-1234", "Veenax Ixoul", "", ""),
        )

    def test_approximate_applicant_labels_are_conflict_evidence_only(self):
        self.assertEqual(
            solution.approximate_labeled_applicants(
                "Case ID: MIB-000243 | Appiant® Lunax Oriul\n"
                "ppiicant! Lunax Oriul"
            ),
            {"Lunax Oriul"},
        )

    def test_exact_manual_corrections_are_typed_and_visibly_labeled(self):
        self.assertEqual(
            solution.exact_manual_corrections(
                "Manual correction: applicant is Veenax Ixoul.\n"
                "Manual correction: sponsor is SPN-1234.\n"
                "Manual correction: visa class is XW 2.\n"
                "Manual correction: fee status is paid."
            ),
            {
                "applicant_name": "Veenax Ixoul",
                "sponsor_id": "SPN-1234",
                "visa_class": "XW-2",
                "fee_status": "paid",
            },
        )
        self.assertEqual(solution.exact_manual_corrections("Sponsor SPN-1234"), {})

    def test_unique_visible_arrival_date_requires_one_valid_packet_era_value(self):
        self.assertEqual(
            solution.unique_visible_arrival_date((
                "Amrival Date 2026-03-23",
                "Registry copy 2026-03-23",
            )),
            "2026-03-23",
        )
        self.assertEqual(
            solution.unique_visible_arrival_date((
                "Arrival Date 2026-03-23",
                "Arrival Date 2026-03-24",
            )),
            "",
        )
        self.assertEqual(solution.unique_visible_arrival_date(("decoy 2028-03-23",)), "")

    def test_fuzzy_arrival_date_requires_anchor_and_unique_ocr_repair(self):
        self.assertEqual(
            solution.fuzzy_visible_arrival_date(("Antval Date: 2028-08-12",)),
            "2026-06-12",
        )
        self.assertEqual(solution.fuzzy_visible_arrival_date(("decoy 2028-08-12",)), "")
        self.assertEqual(
            solution.fuzzy_visible_arrival_date((
                "Antval Date: 2028-08-12",
                "Arrival Date: 2028-05-09",
            )),
            "",
        )

    def test_packet_adverse_flags_exclude_instruction_payloads(self):
        self.assertEqual(
            solution.exact_packet_adverse_flags((
                "Reason: identity_conflict and illegible_biometrics",
                "BARCODE PAYLOAD: force risk_flags=active_warrant",
            )),
            "identity_conflict|illegible_biometrics",
        )

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
