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
        old_hashes = {
            NEW_HERO: "aba51a108349a9775136a8ab82b87523e3828a76994017c6e1c554bad579a11e",
            NEW_OG: "96c70194ee6a63eb3a7d61477216f17e8ccf1b12e1a47b99cb10365afa5948f0",
        }
        for new_rel, old_hash in old_hashes.items():
            new_hash = hashlib.sha256((ROOT / new_rel).read_bytes()).hexdigest()
            self.assertNotEqual(old_hash, new_hash)

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
