#!/usr/bin/env python3
"""Focused privacy contract for the Shooting Star representative images."""
from pathlib import Path
import hashlib
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
OLD_HERO = "images/mag-shootingstar-tuning-2026-hardstand-hero.jpeg"
MISALIGNED_HERO = "images/mag-shootingstar-tuning-2026-hardstand-hero-anonymized.jpeg"
OLD_OG = "images/og-shootingstar-tuning-2026.jpg"
NEW_HERO = "images/mag-shootingstar-tuning-2026-hardstand-hero-face-blurred-v2.jpeg"
NEW_OG = "images/og-shootingstar-tuning-2026-anonymized.jpg"


class ShootingStarPhotoPrivacyTests(unittest.TestCase):
    def test_old_public_assets_are_removed(self):
        self.assertFalse((ROOT / OLD_HERO).exists())
        self.assertFalse((ROOT / MISALIGNED_HERO).exists())
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

    def test_anonymized_assets_match_reviewed_versions(self):
        reviewed_hashes = {
            NEW_HERO: "73754ce3446e3c8d77f56f52e31a5f9917a6738e00283aae9d4972cf9b56e306",
            NEW_OG: "27240299ddefb513cf9a5e506ae5114088c1eac3036cf28de641a692eeeef50d",
        }
        for rel, expected_hash in reviewed_hashes.items():
            actual_hash = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
            self.assertEqual(expected_hash, actual_hash)

    def test_all_public_references_use_anonymized_names(self):
        article_data = (ROOT / "magazine/articles.js").read_text(encoding="utf-8")
        detail = (ROOT / "magazine/shootingstar-club-tuning-2026/index.html").read_text(encoding="utf-8")
        self.assertIn(f'/{NEW_HERO}', article_data)
        self.assertIn(f'/{NEW_OG}', article_data)
        self.assertIn(f'/{NEW_HERO}', detail)
        self.assertIn(f'/{NEW_OG}', detail)
        self.assertNotIn(OLD_HERO.split("/", 1)[1], article_data + detail)
        self.assertNotIn(MISALIGNED_HERO.split("/", 1)[1], article_data + detail)
        self.assertNotIn(OLD_OG.split("/", 1)[1], article_data + detail)


if __name__ == "__main__":
    unittest.main()
