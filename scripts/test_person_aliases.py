#!/usr/bin/env python3
"""Contract test: person-alias substitution in magazine/articles.js.

Verifies:
- All 17 non-pro real names are absent (0 occurrences)
- Each activity alias appears at least once in the article data
- 정성안 (coach, real name retained) still appears
- 김지아 (athlete, real name retained) still appears
- 스키퍼 XX form is used (no bare 'Skipper XX' left in data)
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Non-pro real names that must be absent
REAL_NAMES_ABSENT = [
    "이동준", "엄주범", "조윤호", "유진욱", "고재영", "박정수",
    "김종명", "이주환", "채승범", "최중삼", "이안", "이원희",
    "박정일", "김동천", "김영철", "김정철", "김은진",
]

# Public activity names that must appear at least once
ALIASES_PRESENT = [
    "회장 DJ", "단장 JB", "국장 YH", "스키퍼 JW", "스키퍼 JY", "스키퍼 JS",
    "Crew JM", "Crew JH", "Crew SB", "Crew JS", "Crew A", "Crew WH",
    "Crew JI", "Crew DC", "Crew YC", "Crew JC", "Crew EJ",
]

LEADERSHIP_SKIPPER_NAMES_ABSENT = ["스키퍼 DJ", "스키퍼 JB", "스키퍼 YH"]

# Real names that must be retained
REAL_NAMES_RETAINED = ["정성안", "김지아"]


class PersonAliasContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = (ROOT / "magazine/articles.js").read_text(encoding="utf-8")

    def test_nonpro_real_names_are_absent(self):
        """All 17 non-pro real names must not appear in articles.js."""
        found = []
        for name in REAL_NAMES_ABSENT:
            count = self.data.count(name)
            if count > 0:
                found.append(f"{name}({count}회)")
        self.assertEqual(found, [], f"비프로 실명 발견: {found}")

    def test_each_alias_appears_at_least_once(self):
        """Every activity alias must appear at least once in articles.js."""
        missing = [alias for alias in ALIASES_PRESENT if alias not in self.data]
        self.assertEqual(missing, [], f"활동명 없음: {missing}")

    def test_leadership_names_do_not_use_skipper_prefix(self):
        """The three club leaders use their organizational activity names."""
        found = [
            f"{name}({self.data.count(name)}회)"
            for name in LEADERSHIP_SKIPPER_NAMES_ABSENT
            if name in self.data
        ]
        self.assertEqual(found, [], f"이전 운영진 활동명 발견: {found}")

    def test_retained_real_names_still_present(self):
        """정성안 and 김지아 must still appear (real names retained by rule)."""
        for name in REAL_NAMES_RETAINED:
            self.assertIn(name, self.data, f"{name} 본명이 사라졌습니다")

    def test_no_bare_english_skipper_xx_in_data(self):
        """Skipper XX (English) must not appear — only 스키퍼 XX form is valid."""
        # Exclude results.skipper field (inaccessible by line content alone,
        # but "Skipper XX" in normal prose means the pattern is wrong)
        # The badge value "Skipper" as a standalone quoted string is OK
        matches = re.findall(r'Skipper [A-Z]{2}', self.data)
        self.assertEqual(matches, [], f"영문 Skipper XX 잔존: {matches}")

    def test_cache_key_is_r26(self):
        """articles.js cache key in test_wind_data_photo_contract must be r26."""
        contract = (ROOT / "scripts/test_wind_data_photo_contract.py").read_text(encoding="utf-8")
        self.assertIn("20260907-leadership-aliases-r26", contract)

    def test_tip_title_uses_alias(self):
        """Reinforcement tip title must use alias form."""
        self.assertIn('title: "단장 JB 팁"', self.data)
        self.assertNotIn('title: "스키퍼 JB 팁"', self.data)
        self.assertNotIn('title: "엄주범 단장 팁"', self.data)

    def test_club_cup_leadership_names_are_not_duplicated_by_badges(self):
        """Club Cup results use leadership names without a duplicate title badge."""
        self.assertIn(
            '{ rank: "02", skipper: "회장 DJ", badges: [], '
            'detail: "단장 JB · Crew JM · Crew WH · Crew JI · 게스트 정성안" }',
            self.data,
        )
        self.assertIn(
            'detail: "국장 YH · Crew YC · Crew DC · 게스트 Crew EJ(레이디스)"',
            self.data,
        )
        self.assertNotIn('badges: ["클럽 회장"]', self.data)


if __name__ == "__main__":
    unittest.main()
