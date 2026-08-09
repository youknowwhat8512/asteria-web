#!/usr/bin/env python3
"""Focused privacy contract for the Shooting Star representative images."""
from pathlib import Path
import hashlib
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
OLD_HERO = "images/mag-shootingstar-tuning-2026-hardstand-hero.jpeg"
OLD_OG = "images/og-shootingstar-tuning-2026.jpg"
NEW_HERO = "images/mag-shootingstar-tuning-2026-hardstand-hero-anonymized.jpeg"
NEW_OG = "images/og-shootingstar-tuning-2026-anonymized.jpg"


class ShootingStarPhotoPrivacyTests(unittest.TestCase):
    def test_old_public_assets_are_removed(self):
        self.assertFalse((ROOT / OLD_HERO).exists())
        self.assertFalse((ROOT / OLD_OG).exists())

    def test_anonymized_assets_exist_with_expected_dimensions(self):
        expected = {NEW_HERO: (2160, 2880), NEW_OG: (1200, 630)}
        for rel, (width, height) in expected.items():
            path = ROOT / rel
            self.assertTrue(path.is_file(), path)
            output = subprocess.check_output(
                ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
                text=True,
            )
            self.assertIn(f"pixelWidth: {width}", output)
            self.assertIn(f"pixelHeight: {height}", output)

    def test_anonymized_assets_differ_from_last_public_versions(self):
        for old_rel, new_rel in ((OLD_HERO, NEW_HERO), (OLD_OG, NEW_OG)):
            old = subprocess.check_output(["git", "show", f"HEAD:{old_rel}"], cwd=ROOT)
            new = (ROOT / new_rel).read_bytes()
            self.assertNotEqual(hashlib.sha256(old).digest(), hashlib.sha256(new).digest())

    def test_all_public_references_use_anonymized_names(self):
        article_data = (ROOT / "magazine/articles.js").read_text(encoding="utf-8")
        detail = (ROOT / "magazine/shootingstar-club-tuning-2026/index.html").read_text(encoding="utf-8")
        self.assertIn(f'/{NEW_HERO}', article_data)
        self.assertIn(f'/{NEW_OG}', article_data)
        self.assertIn(f'/{NEW_HERO}', detail)
        self.assertIn(f'/{NEW_OG}', detail)
        self.assertNotIn(OLD_HERO.split("/", 1)[1], article_data + detail)
        self.assertNotIn(OLD_OG.split("/", 1)[1], article_data + detail)


if __name__ == "__main__":
    unittest.main()
