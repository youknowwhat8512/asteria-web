#!/usr/bin/env python3
"""Contract test: fixed-initial person aliases in magazine/articles.js."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Non-pro real names that must be absent
REAL_NAMES_ABSENT = [
    "이동준", "엄주범", "조윤호", "유진욱", "고재영", "박정수",
    "김종명", "이주환", "채승범", "최중삼", "이안", "이원희",
    "박정일", "김동천", "김영철", "김정철", "김은진",
]

# Public fixed initials that must appear at least once
MEMBER_INITIALS_PRESENT = [
    "YJW", "GJY", "PJS", "KJM", "LJH", "CSB", "CJS", "LA", "LWH",
    "PJI", "KDC", "KYC", "KJC", "KEJ",
]

LEADERSHIP_NAMES_PRESENT = ["회장 DJ", "단장 JB", "국장 YH"]

LEGACY_ACTIVITY_NAMES_ABSENT = [
    "스키퍼 JW", "스키퍼 JY", "스키퍼 JS", "Crew JM", "Crew JH", "Crew SB",
    "Crew JS", "Crew A", "Crew WH", "Crew JI", "Crew DC", "Crew YC",
    "Crew JC", "Crew EJ",
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

    def test_each_member_initial_appears_at_least_once(self):
        """Every fixed member initial must appear at least once."""
        missing = [alias for alias in MEMBER_INITIALS_PRESENT if alias not in self.data]
        self.assertEqual(missing, [], f"고정 이니셜 없음: {missing}")

    def test_leadership_names_are_retained(self):
        """The three organizational activity names remain unchanged."""
        missing = [name for name in LEADERSHIP_NAMES_PRESENT if name not in self.data]
        self.assertEqual(missing, [], f"운영진 활동명 없음: {missing}")

    def test_legacy_activity_names_are_absent(self):
        """All prefixed member activity names must have zero occurrences."""
        found = [name for name in LEGACY_ACTIVITY_NAMES_ABSENT if name in self.data]
        self.assertEqual(found, [], f"구 활동명 발견: {found}")

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

    def test_cache_key_is_r27(self):
        """The cache contract pins the full-initial r27 key."""
        contract = (ROOT / "scripts/test_wind_data_photo_contract.py").read_text(encoding="utf-8")
        self.assertIn("20260908-full-initial-aliases-r27", contract)

    def test_tip_title_uses_alias(self):
        """Reinforcement tip title must use alias form."""
        self.assertIn('title: "단장 JB 팁"', self.data)
        self.assertNotIn('title: "스키퍼 JB 팁"', self.data)
        self.assertNotIn('title: "엄주범 단장 팁"', self.data)

    def test_club_cup_leadership_names_are_not_duplicated_by_badges(self):
        """Club Cup results use leadership names without a duplicate title badge."""
        self.assertIn(
            '{ rank: "02", skipper: "회장 DJ", badges: [], '
            'detail: "단장 JB · KJM · LWH · PJI · 게스트 정성안" }',
            self.data,
        )
        self.assertIn(
            'detail: "국장 YH · KYC · KDC · 게스트 KEJ(레이디스)"',
            self.data,
        )
        self.assertNotIn('badges: ["클럽 회장"]', self.data)


if __name__ == "__main__":
    unittest.main()
