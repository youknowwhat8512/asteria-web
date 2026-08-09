#!/usr/bin/env python3
"""Contract tests for the shared Magazine membership CTA and form modal."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
MAGAZINE_JS = ROOT / "magazine" / "magazine.js"
HOME = ROOT / "index.html"


class MagazineMembershipCtaTests(unittest.TestCase):
    def setUp(self):
        self.magazine_js = MAGAZINE_JS.read_text(encoding="utf-8")
        self.home = HOME.read_text(encoding="utf-8")

    def test_copy_button_is_replaced_by_shared_membership_cta(self):
        self.assertIn('data-apply>클럽 가입 신청</button>', self.magazine_js)
        self.assertNotIn('data-copy>링크 복사</button>', self.magazine_js)
        self.assertNotIn("querySelector('[data-copy]')", self.magazine_js)

    def test_magazine_uses_the_existing_home_application_form(self):
        embedded = re.search(r'data-src="([^"]+viewform\?embedded=true)"', self.home)
        public = re.search(r'href="(https://forms\.gle/[^"]+)"', self.home)
        if embedded is None or public is None:
            self.fail("home application form URLs are missing")
        self.assertIn(embedded.group(1), self.magazine_js)
        self.assertIn(public.group(1), self.magazine_js)

    def test_modal_has_accessible_open_close_contract(self):
        for required in (
            'aria-modal="true"',
            'aria-labelledby="membershipModalTitle"',
            'aria-hidden="true"',
            "membershipModal.classList.add('open')",
            "membershipModal.classList.remove('open')",
            "event.key === 'Escape'",
            "if(event.target === membershipModal)",
        ):
            self.assertIn(required, self.magazine_js)

    def test_all_magazine_entry_points_load_the_shared_membership_renderer(self):
        pages = sorted((ROOT / "magazine").rglob("*.html"))
        self.assertEqual(len(pages), 7)
        for page in pages:
            html = page.read_text(encoding="utf-8")
            self.assertIn("magazine.js?v=20260809-skipper-tip-r1", html, page)
            self.assertIn("magazine.css?v=20260809-skipper-tip-r1", html, page)


if __name__ == "__main__":
    unittest.main()
