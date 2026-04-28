"""Regression tests for PDF → normalize → evidence → title/summary (no API)."""
from __future__ import annotations

import os
import unittest

from app import research_ai as ra
from app.pdf_reader import extract_text_from_pdf

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOADS = os.path.join(BASE, "data", "workspace_uploads")


class ResearchPipelineTests(unittest.TestCase):
    def test_publisher_licence_stripped_from_normalize(self) -> None:
        blob = (
            "This may be the author's version (2026) Real Article Title Here.\n"
            "[Article] Creative Commons Licence, you must assume that re-use is limited to personal use.\n"
            "Introduction\n"
            "The analysis argues for stronger governance of algorithmic systems in public agencies.\n"
        )
        n = ra._normalize_for_prompt(blob)
        self.assertNotIn("Creative Commons", n)
        self.assertNotIn("re-use is limited", n.lower())
        self.assertIn("Real Article Title", n)
        self.assertIn("governance", n.lower())

    def test_title_rejects_citation_byline(self) -> None:
        junk = (
            "Lastly. (2020, 6 September). Florida mom shocked after finding child sex doll sold on Amazon.\n\n"
            "This study examines platform governance and consent frameworks in digital marketplaces. "
            "The authors argue that policy design must balance vendor autonomy with safety obligations."
        )
        ordered = ra._normalize_for_prompt(junk)
        title = ra._infer_title_from_text(ordered)
        self.assertNotIn("Lastly", title)
        self.assertNotIn("Florida mom", title)
        self.assertIn("platform", title.lower())

    def test_evidence_pack_prefers_contiguous_body(self) -> None:
        """A long coherent body should win the window over a trailing unrelated sentence."""
        parts = [
            "Abstract\nWe study river flooding in the Midwest. Results show levee height predicts damage to infrastructure.\n",
            "Introduction\nFlooding costs billions annually and climate change increases variance in runoff.\n",
        ]
        body = []
        for i in range(14):
            body.append(
                f"Methods\nWe combine hydrological modeling with census tract exposure for scenario {i + 1}. "
                f"Levee failure probability correlates with maintenance spending index {i + 1}.\n"
            )
        noise = "I have classified three categories for Facial Identification (FI): Mask and Uncovered Face.\n"
        text = "".join(parts) + "".join(body) + noise
        pack = ra._build_evidence_pack(text, max_chars=2000)
        self.assertIn("flooding", pack.lower())
        self.assertNotIn("Facial Identification", pack)

    def test_real_pdf_c071_if_present(self) -> None:
        path = os.path.join(UPLOADS, "c071d055-c61d-4b2b-99a7-10fb168dcc97.pdf")
        if not os.path.isfile(path):
            self.skipTest("fixture PDF not in workspace")
        raw = extract_text_from_pdf(path)
        ordered = ra._normalize_for_prompt(raw)
        pack = ra._build_evidence_pack(raw, max_chars=5000)
        self.assertNotIn("Facial Identification", pack)
        title = ra._infer_title_from_text(ordered)
        self.assertIn("Online Sex Work", title)
        self.assertNotIn("disclaim", title.lower())
        out = ra.summarize_for_resource(raw, max_chars=8000)
        self.assertIn("camming", out["summary"].lower())


if __name__ == "__main__":
    unittest.main()
