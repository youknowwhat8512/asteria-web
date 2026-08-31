#!/usr/bin/env python3
"""Contract test for the Shooting Star reinforcement tip card."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ShootingStarTipCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = (ROOT / "magazine/articles.js").read_text(encoding="utf-8")
        cls.renderer = (ROOT / "magazine/magazine.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "magazine/magazine.css").read_text(encoding="utf-8")

    def test_removes_unnecessary_photo_sentence(self):
        self.assertNotIn("엔진 장착부와 그 아래에 더한 보강 구조를 두 장의 사진에 담았습니다.", self.data)

    def test_reinforcement_tip_uses_shared_card_schema(self):
        for required in (
            'title: "엄주범 단장 팁"',
            "현장에서 ‘실리카’라고 부른 해양용 실리콘 실란트",
            "서로 다른 진동 특성",
            "필수 공정",
        ):
            self.assertIn(required, self.data)
        self.assertIn("const sectionTip", self.renderer)
        self.assertIn("sectionTip(section.tip)", self.renderer)
        static_renderer = (ROOT / "scripts/render_magazine_static.mjs").read_text(encoding="utf-8")
        self.assertIn("section.tip", static_renderer)
        self.assertIn("article-tip", static_renderer)
        self.assertIn(".article-tip{", self.css)

    def test_engine_power_is_corrected(self):
        self.assertIn("기존 4.9마력 엔진을 9.9마력으로 업그레이드", self.data)
        self.assertNotIn("5.5마력", self.data)

    def test_all_entry_points_use_current_magazine_cache_keys(self):
        pages = sorted((ROOT / "magazine").rglob("*.html"))
        self.assertEqual(len(pages), 8)
        for page in pages:
            html = page.read_text(encoding="utf-8")
            self.assertIn("articles.js?v=20260831-usage-title-r20", html, page)
            self.assertIn("magazine.js?v=20260830-body-media-r19", html, page)
            self.assertIn("magazine.css?v=20260830-photo-swap-r11", html, page)


if __name__ == "__main__":
    unittest.main()
