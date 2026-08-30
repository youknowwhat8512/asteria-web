#!/usr/bin/env python3
"""Contract tests for the Magazine membership-guide CTA."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
MAGAZINE_JS = ROOT / "magazine" / "magazine.js"
HOME = ROOT / "index.html"


class MagazineMembershipGuideTests(unittest.TestCase):
    def setUp(self):
        self.magazine_js = MAGAZINE_JS.read_text(encoding="utf-8")
        self.home = HOME.read_text(encoding="utf-8")

    def test_episode_cta_links_to_the_home_membership_guide(self):
        self.assertIn('href="/#club-guide">가입 안내 확인</a>', self.magazine_js)
        self.assertNotIn('data-copy>링크 복사</button>', self.magazine_js)
        self.assertNotIn("querySelector('[data-copy]')", self.magazine_js)

    def test_home_explains_membership_follows_open_sailing(self):
        self.assertIn("오픈 세일링 안내", self.home)
        self.assertIn("오픈 세일링 참여 방법", self.home)
        self.assertIn("오픈 세일링 참가 신청서 작성", self.home)
        self.assertIn("오픈 세일링 후 서로 잘 맞는다고 생각하면 클럽 가입을 진행합니다.", self.home)
        self.assertNotIn("가입 신청서를 작성해 아스테리아에 지원합니다.", self.home)
        self.assertNotIn("클럽 가입 안내", self.home)

    def test_episode_does_not_embed_the_application_modal(self):
        self.assertNotIn("membershipModal", self.magazine_js)
        self.assertNotIn("membership-form-embed", self.magazine_js)

    def test_all_magazine_entry_points_load_the_shared_membership_renderer(self):
        pages = sorted((ROOT / "magazine").rglob("*.html"))
        self.assertEqual(len(pages), 8)
        for page in pages:
            html = page.read_text(encoding="utf-8")
            self.assertIn("magazine.js?v=20260830-body-media-r19", html, page)
            self.assertIn("magazine.css?v=20260830-photo-swap-r11", html, page)


if __name__ == "__main__":
    unittest.main()
