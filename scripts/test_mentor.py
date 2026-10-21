#!/usr/bin/env python3
"""Behavior and corpus-boundary tests; not scientific validation."""
from copy import deepcopy
import json
from pathlib import Path
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET

import mentor
import search_workflow


class MentorChecks(unittest.TestCase):
    def setUp(self):
        self.example = json.loads((mentor.ROOT / "assets" / "task-example.json").read_text(encoding="utf-8"))

    def codes(self, task):
        out = mentor.audit(task)
        return {x["code"] for x in out["errors"] + out["review_items"]}

    def test_registry_count_is_identity_not_eligibility(self):
        records = mentor.load("corpus.json")["records"]
        self.assertEqual(len(records), 159)
        self.assertEqual(len({r["doi"] for r in records}), 159)
        self.assertTrue(all(not r["eligibility_certified"] for r in records))
        self.assertEqual({r["id"] for r in records if r["quarantine"]}, {"B22", "B32"})

    def test_rules_have_conditions_boundaries_and_safe_sources(self):
        rules = mentor.load("knowledge.json")["rules"]
        self.assertEqual(len(rules), 24)
        self.assertEqual(len({r["id"] for r in rules}), len(rules))
        known = {r["doi"] for r in mentor.load("corpus.json")["records"]}
        correction = {"10.1002/adma.202404532"}
        for rule in rules:
            for field in ("when", "action", "test", "limit", "figure_move", "dois"):
                self.assertTrue(rule[field])
            self.assertTrue(set(rule["dois"]) <= known | correction)
            self.assertFalse(any(mentor.source(d).get("quarantine") for d in rule["dois"]))

    def test_query_retrieves_applicable_rule_and_no_fake_fallback(self):
        self.assertEqual(mentor.query("第二相 物理混合", "innovation")["results"][0]["id"], "P04")
        self.assertEqual(mentor.query("ZZZ_UNRELATED_9281")["results"], [])
        self.assertEqual(mentor.query("同SOC 原位", "figures")["results"][0]["id"], "P08")

    def test_quarantine_source_returns_no_scientific_text(self):
        row = mentor.source("https://doi.org/10.1016/j.ensm.2024.103848")
        self.assertTrue(row["quarantine"])
        self.assertNotIn("title", row)
        self.assertFalse(row["usable_for_recommendation"])

    def test_technique_query_and_no_weak_quota_filling(self):
        results = mentor.query("NASICON 高速 GITT DOS 电导", "innovation")["results"]
        self.assertEqual({r["id"] for r in results}, {"P10", "P11"})
        self.assertEqual(mentor.query("GITT", "innovation")["results"][0]["id"], "P11")

    def test_custom_three_state_palette_no_invented_percentage(self):
        palette = mentor.palette_data("soc", "C-S1,C-S2,D-S2")
        rows = palette["role_map"]
        self.assertEqual([r["role"] for r in rows], ["C-S1", "C-S2", "D-S2"])
        self.assertEqual(len({r["hex"] for r in rows}), 3)
        self.assertFalse(palette["physical_state_order_verified"])
        self.assertEqual(palette["encoding"], "discrete_state_identity_not_quantitative_soc")
        self.assertEqual({r["hex"] for r in rows}, {r["hex"] for r in palette["numerical_preflight"]["colors"]})
        one = mentor.palette_data("soc", "充电S1")
        self.assertEqual(len(one["numerical_preflight"]["colors"]), 1)
        with self.assertRaises(ValueError):
            mentor.palette_data("soc", "S1,S1")
        with self.assertRaises(ValueError):
            mentor.palette_data("dual", "S1,S2")

    def test_example_does_not_claim_scientific_acceptance(self):
        out = mentor.audit(self.example)
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["review_items"], [])
        self.assertFalse(out["scientific_acceptance"])

    def test_same_origin_cannot_be_counted_twice(self):
        self.example["evidence"][1]["origin_id"] = "run-0"
        self.example["claims"][0]["asserted_independent_sources"] = 2
        self.assertIn("DEPENDENT_EVIDENCE", self.codes(self.example))

    def test_same_state_comparison_detects_temperature_and_mass(self):
        self.example["conditions"][1]["temperature_c"] = -20
        self.example["conditions"][1]["mass_basis"] = "active crystal only"
        out = mentor.audit(self.example)
        problems = [r for r in out["review_items"] if r["code"] == "CONDITION_MISMATCH"]
        self.assertEqual(len(problems), 2)

    def test_unknown_soc_does_not_pass_equivalence(self):
        self.example["conditions"][1]["soc"] = None
        self.assertIn("COMPARISON_UNKNOWN", self.codes(self.example))

    def test_colors_keep_semantic_role(self):
        self.example["figures"][0]["panels"][1]["hex"] = "#24526B"
        self.assertIn("COLOR_ROLE_CONFLICT", self.codes(self.example))

    def test_unverified_paper_hex_is_flagged(self):
        self.example["palette"]["exact_author_colors"] = True
        self.assertIn("UNVERIFIED_SOURCE_RGB", self.codes(self.example))

    def test_dos_does_not_certify_conductivity(self):
        for ev in self.example["evidence"]:
            ev["method"] = "DOS"
            ev["inspection"] = "model"
        self.example["claims"][0]["quantity"] = "intrinsic_electronic_conductivity"
        self.assertIn("PROXY_OVERREACH", self.codes(self.example))

    def test_caption_does_not_certify_layout(self):
        for ev in self.example["evidence"]:
            ev["inspection"] = "caption"
        self.example["claims"][0]["quantity"] = "visual_layout"
        self.assertIn("VISUAL_UNVERIFIED", self.codes(self.example))

    def test_quarantined_evidence_fails_closed(self):
        self.example["evidence"][0]["source_doi"] = "10.1021/acsnano.4c18614"
        self.assertIn("QUARANTINED_SOURCE", self.codes(self.example))

    def test_unknown_reference_and_duplicate_id(self):
        self.example["claims"][0]["evidence_ids"].append("invented")
        self.example["evidence"].append(deepcopy(self.example["evidence"][0]))
        self.assertTrue({"UNKNOWN_REFERENCE", "DUPLICATE_ID"} <= self.codes(self.example))

    def test_empty_and_bad_shape_not_passing_audit(self):
        self.assertIn("EMPTY_TASK", self.codes({}))
        self.assertIn("SHAPE", self.codes({"claims": {"x": 1}}))
        self.assertTrue(mentor.audit([])["errors"])

    def test_continuous_color_has_scientific_scale(self):
        self.example["palette"]["kind"] = "diverging"
        self.assertTrue({"COLORBAR_UNKNOWN", "COLORBAR_CENTER"} <= self.codes(self.example))

    def test_palette_provenance_and_luminance(self):
        colors = mentor.load("colors.json")
        self.assertEqual(len(colors["observed"]), 47)
        self.assertEqual(len({r["doi"] for r in colors["observed"]}), 47)
        self.assertEqual(len(colors["palettes"]), 5)
        for palette in colors["palettes"]:
            self.assertIn("original proposal", palette["provenance"])
            rows = palette.get("role_map", palette.get("stops", []))
            vals = [mentor.luminance(r["hex"]) for r in rows]
            if palette["id"] in ("soc", "sequential"):
                self.assertTrue(all(a > b for a, b in zip(vals, vals[1:])))

    def test_original_svg_is_valid_and_does_not_overwrite(self):
        # Keep the uniquely owned scratch directory inside the caller's workspace.
        with tempfile.TemporaryDirectory(prefix="polyanion-mentor-test-", dir=Path.cwd()) as folder:
            path = Path(folder) / "palette.svg"
            mentor.swatches(path)
            self.assertTrue(ET.parse(path).getroot().tag.endswith("svg"))
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("<image", text)
            self.assertNotIn("http://doi", text)
            with self.assertRaises(FileExistsError):
                mentor.swatches(path)

    def test_skill_references_exist_and_no_private_research_import(self):
        for path in [mentor.ROOT / "SKILL.md", *(mentor.ROOT / "references").glob("*.md")]:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("\ufffd", text)
            for link in re.findall(r"\]\(([^)]+)\)", text):
                if not link.startswith(("https:", "http:")):
                    self.assertTrue((path.parent / link).exists(), str(path) + " -> " + link)
            # Keep even the test fixtures free of private project identifiers.
            # These finite guards supplement, not replace, manual provenance review.
            for private_marker in ("用户现用", "user_private_research", "private_manuscript"):
                self.assertNotIn(private_marker, text)

    def test_search_plan_is_not_executed_search(self):
        p = search_workflow.plan(["nasicon"], "2024-01-01", "2026-09-23", ["Advanced Materials"])
        self.assertEqual(p["executed_queries"], 0)
        self.assertFalse(p["exhaustive_coverage_certified"])
        self.assertEqual(len(p["query_matrix"]), 3)
        self.assertEqual(p["new_papers_read"], 0)
        with self.assertRaises(ValueError):
            search_workflow.plan(["nfpp"], "2026-09-23", "2024-01-01")

    def test_candidate_dedup_preserves_dates_and_unknown(self):
        payload = {"records":[
            {"doi":"https://doi.org/10.1021/acsenergylett.1c02107", "title":"Known", "first_online":"2021-12-03", "source":"A"},
            {"doi":"10.1021/ACSENERGYLETT.1C02107", "title":"Known", "first_online":"2022-01-14", "source":"B"},
            {"doi":None,"title":"Unresolved"}
        ]}
        out = search_workflow.screen(payload, "2021-09-23", "2026-09-23")
        self.assertEqual(out["unique_valid_dois"], 1)
        self.assertEqual(out["duplicate_occurrences"], 1)
        self.assertEqual(len(out["missing_or_invalid_doi_records"]), 1)
        self.assertEqual(out["records"][0]["historical_id"], "B97")
        self.assertEqual(out["records"][0]["date_status"], "date_unresolved")
        self.assertEqual(len(out["records"][0]["origins"]), 2)

    def test_candidate_quarantine_and_no_fulltext_upgrade(self):
        out = search_workflow.screen({"records":[{"doi":"10.1021/acsnano.4c18614", "title":"not exported"}]})
        self.assertEqual(out["records"][0]["titles"], [])
        self.assertTrue(out["records"][0]["quarantine"])
        self.assertFalse(out["records"][0]["eligible_by_full_text"])
        self.assertEqual(out["new_papers_read"], 0)

    def test_evidence_dependent_claim_requires_doi_or_labeled_theory(self):
        claim = self.example["claims"][0]
        claim["requires_literature_support"] = True
        self.assertIn("MISSING_SUPPORTING_DOI", self.codes(self.example))
        claim["basis"] = "general_theory"
        claim["support_note"] = "Conditional theory; no direct literature support found."
        self.assertIn("THEORY_WITHOUT_DIRECT_PAPER", self.codes(self.example))
        self.example["evidence"][0]["source_doi"] = "10.1021/acsenergylett.1c02107"
        self.assertIn("DOI_SUPPORT_SCOPE", self.codes(self.example))
        claim["support_scope"] = "Method analogy only, not direct proof of this hypothetical material."
        self.assertNotIn("MISSING_SUPPORTING_DOI", self.codes(self.example))

    def test_latest_response_policy(self):
        p = mentor.load("response-policy.json")
        self.assertTrue(p["innovation"]["evidence_dependent_recommendations_require_matched_doi"])
        self.assertFalse(p["figures"]["paper_examples_default"])
        self.assertTrue(p["theory_fallback"]["label_no_direct_literature_support"])
        self.assertFalse(p["provide_paper_full_text"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
