#!/usr/bin/env python3
"""Photo contract for the 2026-08-29 Shooting Star wind-data episode.

Every one of the seven source scenes must appear exactly once in the DOM that
magazine.js actually renders, the hero must not be re-used in the body, and the
OG card must be its own 1200x630 file. The seventh scene — the 18:13 tablet
frame — is represented by its tutorial crop, so the full 1536x2048 original
must not also be rendered. The DOM is produced by running the real renderer
over a minimal document shim, so a slot moving in magazine.js is caught here
rather than in a browser.

The same rendered DOM also guards the screen tutorial: one card inside the
third and final article section, after both narrative sections and every
paragraph and photo in them, holding one central close-up of the screen carrying five numbered hotspots and two
hollow colour rings over the instrument's own A/T arrows, five numbered
callouts beside it with the same markers and readings, two colour-toned legend
rows (A is the yellow arrow, T the red one) and a five-card "how to use"
reading map, one card per reading, each tying the screen's acronym to its
colour/category, the action it drives and one plain sentence, and under that a
four-step scenario that walks the same readings once as a decision: check T,
compare A, adjust a little, read the boat speed back.
The card carries no scope note at all. The crawler-facing twin is generated into a
throwaway build root and held to the same contract, so the no-JS body and the
hydrated DOM cannot drift apart.
"""
from pathlib import Path
import json
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SLUG = "shootingstar-wind-data-sail-2026"
PREFIX = "/images/mag-shootingstar-wind-data-2026-"
OG = "/images/og-shootingstar-wind-data-2026-tablet.jpg"
HERO = f"{PREFIX}data-awa-103.jpeg"
OLD_HERO = f"{PREFIX}crew-hero.jpeg"
OLD_OG = "/images/og-shootingstar-wind-data-2026.jpg"
OLD_HERO_SHA256 = "03638ba59fd0bf237a0b8a83c44b68ec5a2830b72eb81de7f229dc18cf90ce0c"
OLD_OG_SHA256 = "822f4203a77a9fb9d6ca45faf5a60babe7a0d9a3316790f2d2dfd9cf16219bb4"
# The 18:13 frame reaches the reader as one tight close-up of the screen. The
# full-size original and the earlier, wider crop both stay on disk as history
# and must never be rendered as well.
TUTORIAL_CLOSEUP = f"{PREFIX}tablet-dial-closeup.jpeg"
OLD_CROP = f"{PREFIX}tablet-screen-7-4kt.jpeg"
CROP_SOURCE = f"{PREFIX}upwind-7-4kt.jpeg"
# The close-up is cut from the source with a fixed rect and stripped, so the
# source itself must survive this revision byte-for-byte.
CROP_SOURCE_SHA256 = "638a5aa99cdf478283b31e10c88edf336d2f53d5641c6670ffb6466c55ad3923"
# Five scenes carry the story in the body. The former AWA 103 body scene is now
# the hero, so it must not appear again below the fold.
BODY_SCENES = (
    f"{PREFIX}cockpit-tablet.jpeg",
    f"{PREFIX}tablet-under-sail.jpeg",
    f"{PREFIX}helm-underway.jpeg",
    TUTORIAL_CLOSEUP,
    f"{PREFIX}data-awa-41.jpeg",
)

# Each shared asset keeps its latest compatible cache key. This copy-only
# revision advances the article data while retaining the verified renderer and
# natural-size portrait stylesheet bundles.
CACHE_KEYS = {
    "articles.js": "20260830-body-media-r19",
    "magazine.js": "20260830-body-media-r19",
    "magazine.css": "20260830-photo-swap-r11",
}
CARD = '<aside class="article-explainer explainer-winddata explainer-layout-device-tutorial"'
SECTION_OPEN = '<section class="article-section">'
# The summary tells the reader why the two arrows differ and what to do with
# them; it no longer just recites where each number sits on the screen.
SUMMARY = ("배가 움직이면 선상에서 느끼는 바람의 방향과 세기가 달라집니다. "
           "노란 A와 빨간 T를 함께 보며 세일과 진로 조정에 참고하고, "
           "가운데 BOAT SPEED로 조정 전후 결과를 비교합니다.")
OLD_SUMMARY = ("위 숫자 두 개는 바람이 오는 방향, 아래 숫자 두 개는 바람의 세기입니다. "
               "가운데 7.4는 배의 속도입니다. 01부터 차례로 보면 됩니다.")
# marker -> (label, reading, a distinctive slice of the beginner explanation,
# the acronym spelled out under the label). The explanations are deliberately
# jargon-free: no 겉보기/실제 풍향각 wording. BOAT SPEED spells out to None —
# it is already the whole words, so it carries no second line.
TUTORIAL_ITEMS = (
    ("01", "AWA", "64°", "배 위에서 느끼는 바람의 방향입니다. 뱃머리를 0°로 봤을 때",
     "Apparent Wind Angle"),
    ("02", "TWA", "98°", "배가 움직이는 영향을 빼고 계산한 바람의 방향입니다.",
     "True Wind Angle"),
    ("03", "BOAT SPEED", "7.4노트", "그 순간 배가 달리던 속도입니다. 자동차의 속도계처럼",
     None),
    ("04", "AWS", "13.0노트", "달리는 배 위에서 몸으로 느끼는 바람의 세기입니다.",
     "Apparent Wind Speed"),
    ("05", "TWS", "11.9노트", "배가 움직이는 영향을 빼고 계산한 바람의 세기입니다.",
     "True Wind Speed"),
)
# tone -> (label, value). The tone drives the accent colour and mirrors the two
# pointers on the instrument screen itself.
LEGEND = (("yellow", "A · 노란색 화살", "지금 배 위에서 느끼는 값"),
          ("red", "T · 빨간색 화살", "배가 움직이는 영향을 빼고 계산한 값"))
# The two arrows the instrument draws itself, ringed on the photo in the same
# colours the legend uses: (tone, label, left%, top%, ring size as a % of the
# 412px-wide close-up). Fixed coordinates — never re-guessed from a thumbnail.
ANNOTATIONS = (("yellow", "A", "61.2", "44.8", "11.17"),
               ("red", "T", "72.8", "52.9", "11.17"))
# The "how to use" panel is a reading map, not a list of generic tips: one card
# per reading, each carrying the screen's own acronym, the colour/category it
# belongs to, the action it drives and one plain sentence.
USAGE = {
    "layout": "reading-map",
    "kicker": "HOW TO USE · 활용 팁",
    "title": "약자에서 용도로 바로 연결합니다",
    "summary": "앞글자 A는 노란 화살, T는 빨간 화살입니다. WA는 바람 방향, WS는 바람 세기입니다.",
}
# marker, code, tone, metric chip, action, sentence — in the same 01-05 order as
# the hotspots and the callouts.
USAGE_STEPS = (
    ("01", "AWA", "yellow", "노란 A · WA 방향", "세일 줄 조정",
     "지금 배에서 느끼는 바람 방향을 보고 세일 줄을 당기거나 풀 때 참고합니다."),
    ("02", "TWA", "red", "빨간 T · WA 방향", "진로 판단",
     "장비가 계산한 바람 방향을 보고 배가 바람을 앞·옆·뒤 어디에서 받는지 확인합니다."),
    ("03", "BOAT SPEED", "cyan", "배 속도", "조정 결과 확인",
     "세일 줄이나 진로를 바꾸기 전후의 속도를 비교합니다."),
    ("04", "AWS", "yellow", "노란 A · WS 세기", "센 바람 대비",
     "지금 배에서 느끼는 바람 세기가 갑자기 오르는지 확인합니다."),
    ("05", "TWS", "red", "빨간 T · WS 세기", "바람 변화 확인",
     "장비가 계산한 바람 세기가 시간에 따라 오르내리는지 확인합니다."),
)
# Every mapping card resolves to one accent, and the A/T accents are the same
# hexes the rings and the legend already use, so the panel and the photo agree.
USAGE_ACCENTS = {"yellow": "#ffcc33", "red": "#ff6a5a", "cyan": "#7adef0"}
# The rendered mapping card, slot by slot.
# The scenario panel walks the same readings once as a numbered decision.
# It is an optional sibling of the usage panel and must sit after it.
SCENARIO = {
    "kicker": "SCENARIO · 이렇게 써봅니다",
    "title": "이 화면 숫자로 한 번 따라가 봅니다",
    "summary": "빨간 T로 기준 바람을 확인하고, 노란 A와 비교한 뒤, 조금 조정하고 보트 속도로 결과를 봅니다.",
}
# marker, tone, title, chips in order, sentence. Step 3 is the adjustment: it
# belongs to neither arrow, so it declares no tone and falls back to neutral.
SCENARIO_STEPS = (
    ("1", "red", "기본 바람 확인", ("TWA 98°", "TWS 11.9노트"),
     "빨간 T를 보고 장비가 배의 움직임 영향을 보정해 계산한 바람의 방향과 세기를 먼저 확인합니다."),
    ("2", "yellow", "배 위 느낌과 비교", ("AWA 64°", "AWS 13.0노트"),
     "노란 A를 보면 이 순간에는 바람이 더 앞쪽에서, 조금 더 강하게 느껴집니다."),
    ("3", None, "조금 조정해 보기", ("세일 줄", "진로"),
     "A와 T의 차이를 참고해 세일 줄이나 진로를 한 번에 크게 바꾸지 않고 조금씩 조정해 봅니다."),
    ("4", "cyan", "속도로 결과 확인", ("BOAT SPEED 7.4노트", "조정 뒤 새 숫자"),
     "7.4노트를 비교 기준으로 기억하고 조정 뒤 숫자가 어떻게 바뀌는지 봅니다. "
     "더 나아지면 유지하고, 나빠지면 이전 설정으로 돌아가거나 다시 미세 조정합니다."),
)
# The rendered scenario step, slot by slot: tone is optional, chips are a list.
SCENARIO_STEP_RE = (r'<li class="explainer-scenario-step"(?: data-tone="([a-z]+)")?>'
                    r'<b class="scenario-marker">(.*?)</b>'
                    r'<b class="scenario-title">(.*?)</b>'
                    r'(.*?)<span class="scenario-body">(.*?)</span></li>')
USAGE_STEP_RE = (r'<li class="explainer-usage-step" data-tone="([a-z]+)">'
                 r'<b class="usage-marker">(.*?)</b><b class="usage-code">(.*?)</b>'
                 r'<span class="usage-metric">(.*?)</span>'
                 r'<b class="usage-action">(.*?)</b>'
                 r'<span class="usage-body">(.*?)</span></li>')
# Superseded panels: the r3 reading order and the r4 generic tip titles, which
# made the reader re-connect an acronym to its use in their head. Each was a
# whole slot value, so they are hunted as whole slot values — as a quoted
# string in the data and as a complete element text in either rendered body.
# Matching them as bare substrings would also condemn ordinary prose: the r6
# scenario summary legitimately ends "…보트 속도로 결과를 봅니다."
OLD_USAGE_STEPS = ("빨간 T부터 봅니다", "노란 A를 확인합니다", "보트 속도로 결과를 봅니다",
                   "T → A → 보트 속도 순서로 봅니다", "한 숫자만 보지 않고 세 가지를 함께 비교합니다.",
                   "세일 각도 조정에 참고", "바람을 받는 방향 확인", "조정 전후 속도 비교",
                   "갑자기 센 바람에 대비", "바람 자체의 변화 확인")
# Withdrawn on 2026-08-30: the scope note was deleted outright, not relocated,
# so it must be absent from the data and from both rendered bodies.
DELETED_NOTE = ("7.4노트와 네 가지 바람 값은 오후 6시 13분 사진에 잡힌 한 순간입니다. "
                "이날의 최고속도나 전체 항해 평균을 뜻하지 않습니다.")
# Terms the card must never teach a beginner with. "Apparent Wind"/"True Wind"
# left this list on 2026-08-30: each acronym now spells itself out under its own
# label, so the English names are the card's own vocabulary rather than jargon
# smuggled into the prose. They are pinned verbatim in TUTORIAL_ITEMS instead.
JARGON = ("겉보기 풍향각", "겉보기 풍속", "실제 풍향각", "실제 풍속")

def scenario_steps(card):
    """The scenario cards as (marker, tone, title, chips, body), in source order."""
    return [(marker, tone or None, title,
             tuple(re.findall(r'<span class="scenario-chip">(.*?)</span>', chips, re.S)), body)
            for tone, marker, title, chips, body in re.findall(SCENARIO_STEP_RE, card, re.S)]


# Minimal document shim: enough of the DOM for magazine.js to render one
# article and hand back the HTML it assigned to #articleRoot.
RENDER = r"""
const fs = require('fs'), vm = require('vm'), path = require('path');
const [root, slug] = process.argv.slice(1);
const stub = () => ({
  dataset: {}, classList: { add() {}, toggle() {} }, style: {},
  setAttribute() {}, appendChild(child) { return child; }, addEventListener() {},
  querySelector: () => stub(), querySelectorAll: () => [], remove() {}, select() {},
});
const articleRoot = stub();
articleRoot.dataset.articleSlug = slug;
const sandbox = {
  window: {}, console,
  location: { search: '', origin: 'https://asteria.club' },
  URL, URLSearchParams, Intl, setTimeout, Promise,
  navigator: {},
  matchMedia: () => ({ matches: true }),
  document: {
    title: '', head: Object.assign(stub(), { querySelector: () => null }),
    body: stub(), createElement: () => stub(), querySelector: () => null,
    getElementById: id => (id === 'articleRoot' ? articleRoot : id === 'magazineMasonry' ? null : stub()),
  },
};
vm.createContext(sandbox);
for (const file of ['articles.js', 'magazine.js']) {
  new vm.Script(fs.readFileSync(path.join(root, 'magazine', file), 'utf8'), { filename: file })
    .runInContext(sandbox, { timeout: 5000 });
}
process.stdout.write(JSON.stringify({ html: articleRoot.innerHTML || '', title: sandbox.document.title }));
"""


def static_build(node, mutate=None):
    """Run the real static renderer over a clean copy and hand back the page.

    Source pages ship with empty hydration containers on purpose, so the
    crawler-facing HTML only exists after a build. Generating it here keeps
    this contract honest without writing into the repository tree. `mutate`
    may rewrite articles.js inside the throwaway copy first.
    """
    with tempfile.TemporaryDirectory(prefix="asteria-windcard-") as tmp:
        build_root = Path(tmp)
        shutil.copytree(ROOT / "magazine", build_root / "magazine")
        if mutate:
            data = build_root / "magazine" / "articles.js"
            data.write_text(mutate(data.read_text(encoding="utf-8")), encoding="utf-8")
        subprocess.run([node, str(ROOT / "scripts/render_magazine_static.mjs"), str(build_root)],
                       check=True, capture_output=True, text=True)
        page = (build_root / "magazine" / SLUG / "index.html").read_text(encoding="utf-8")
    opened = re.search(r'<div[^>]*id="articleRoot"[^>]*>', page)
    assert opened, "articleRoot container missing from the generated page"
    return page[opened.end():page.index("</article>")]


class WindDataPhotoContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        node = shutil.which("node")
        assert node, "node is required to render magazine.js"
        cls.node = node
        result = subprocess.run(
            [node, "-e", RENDER, str(ROOT), SLUG], check=True, capture_output=True, text=True
        )
        rendered = json.loads(result.stdout)
        cls.dom = rendered["html"]
        cls.title = rendered["title"]
        cls.detail = (ROOT / "magazine" / SLUG / "index.html").read_text(encoding="utf-8")
        cls.data = (ROOT / "magazine/articles.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "magazine/magazine.css").read_text(encoding="utf-8")
        sections = cls.dom.split('<section class="article-section">')
        assert len(sections) == 4, f"expected 3 rendered sections, got {len(sections) - 1}"
        cls.first_section = sections[1]
        cls.third_section = sections[3]
        cls.static_detail = static_build(node)
        static_sections = cls.static_detail.split('<section class="article-section">')
        assert len(static_sections) == 4, "expected 3 static sections"
        cls.static_first_section = static_sections[1]
        cls.static_third_section = static_sections[3]

    @staticmethod
    def card_of(markup):
        return markup.partition(CARD)[2].partition("</aside>")[0]

    def assert_card_is_the_third_section(self, markup, where):
        """The card belongs to the titled third and final article section."""
        body = markup.partition('<div class="article-body">')[2]
        self.assertEqual(body.count(CARD), 1, where)
        sections = body.split(SECTION_OPEN)
        self.assertEqual(len(sections), 4, where)
        first_two = SECTION_OPEN.join(sections[:3])
        third = sections[3]
        self.assertNotIn(CARD, first_two, where)
        self.assertEqual(third.count(CARD), 1, where)
        kicker = "03 / How to use Tablet Data"
        heading = "태블릿 데이터 활용 팁"
        self.assertLess(third.index(kicker), third.index(heading), where)
        self.assertLess(third.index(heading), third.index(CARD), where)
        self.assertEqual(body.count(SECTION_OPEN), 3, where)

    def test_the_episode_renders(self):
        self.assertIn("테선장님과 함께한 하루", self.title)
        self.assertIn("테선장님과 함께한 하루", self.dom)

    def test_removed_measurement_and_external_weather_copy_stays_absent(self):
        removed = (
            "같은 화면에는 AWA 64°, TWA 98°, AWS 13.0노트, TWS 11.9노트가 함께 떠 있었습니다.",
            "배 위의 센서와 별개로, 같은 시간대의 외부 자료도 남아 있습니다.",
            "Iowa Environmental Mesonet",
            "Open-Meteo",
        )
        for markup in (self.data, self.dom, self.static_detail):
            for text in removed:
                self.assertNotIn(text, markup)

    def test_skipper_effect_copy_is_restored_once(self):
        copy = ("스키퍼 경험이 많은 사람에게 데이터는 더 높은 속도를 시도해 볼 목표가 됐고, "
                "경험이 적은 사람에게는 감각을 보완할 기준이 됐습니다. 세일과 진로를 조정한 뒤 "
                "BOAT SPEED를 다시 확인할 수 있다는 점이 감각에만 의존할 때와 가장 달랐습니다.")
        for markup in (self.data, self.dom, self.static_detail):
            self.assertEqual(markup.count(copy), 1)

    def test_every_source_scene_appears_exactly_once(self):
        for scene in (HERO, *BODY_SCENES):
            self.assertEqual(self.dom.count(f'src="{scene}"'), 1, scene)

    def test_only_the_closeup_represents_the_1813_frame(self):
        # One 18:13 frame, one reader-facing derivative: neither the full-size
        # original nor the superseded wider crop may render alongside it.
        for markup, where in ((self.dom, "hydrated DOM"), (self.static_detail, "static fallback")):
            self.assertEqual(markup.count(f'src="{TUTORIAL_CLOSEUP}"'), 1, where)
            self.assertEqual(markup.count(CROP_SOURCE), 0, where)
            self.assertEqual(markup.count(OLD_CROP), 0, where)
        self.assertNotIn(CROP_SOURCE, self.data)
        self.assertNotIn(OLD_CROP, self.data)

    def test_the_superseded_sources_are_still_on_disk_untouched(self):
        # Unreferenced is not deleted: both stay put, and the crop source must
        # still hash to the exact bytes the close-up rect was measured against.
        source = ROOT / CROP_SOURCE.lstrip("/")
        self.assertTrue(source.is_file(), "the crop source must never be deleted")
        digest = subprocess.check_output(["shasum", "-a", "256", str(source)], text=True).split()[0]
        self.assertEqual(digest, CROP_SOURCE_SHA256, "the crop source was modified")
        output = subprocess.check_output(
            ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(source)], text=True
        )
        self.assertIn("pixelWidth: 1536", output)
        self.assertIn("pixelHeight: 2048", output)
        self.assertTrue((ROOT / OLD_CROP.lstrip("/")).is_file(),
                        "the superseded crop must be left on disk, only unreferenced")

    def test_hero_is_not_reused_in_the_body(self):
        head, _, body = self.dom.partition("article-content")
        self.assertIn(HERO, head)
        self.assertNotIn(HERO, body)

    def test_retired_crew_hero_and_og_are_unreferenced_but_preserved(self):
        for retired, digest in ((OLD_HERO, OLD_HERO_SHA256), (OLD_OG, OLD_OG_SHA256)):
            for markup in (self.data, self.detail, self.dom, self.static_detail):
                self.assertNotIn(retired, markup)
            path = ROOT / retired.lstrip("/")
            self.assertTrue(path.is_file(), f"retired asset was deleted: {retired}")
            actual = subprocess.check_output(["shasum", "-a", "256", str(path)], text=True).split()[0]
            self.assertEqual(actual, digest, f"retired asset was modified: {retired}")

    def test_portrait_hero_uses_its_natural_width_in_both_renderers(self):
        for markup in (self.dom, self.static_detail):
            self.assertIn('class="article-hero hero-natural-portrait"', markup)
            self.assertIn('style="--hero-natural-width:360px"', markup)
        self.assertIn('.article-hero.hero-natural-portrait{display:grid;place-items:center;background:var(--ink)}', self.css)
        self.assertIn('.article-hero.hero-natural-portrait img{position:absolute;inset:0;margin:auto;width:min(100%,var(--hero-natural-width,100%));max-width:none;height:100%;max-height:none;object-fit:contain}', self.css)

    def test_og_card_is_a_separate_file_never_shown_in_the_body(self):
        self.assertNotIn(OG, self.dom)
        self.assertIn(OG, self.detail)
        self.assertIn('<meta property="og:image:width" content="1200">', self.detail)
        self.assertIn('<meta property="og:image:height" content="630">', self.detail)

    def test_published_files_decode_at_their_declared_size(self):
        expected = {
            HERO: (360, 480),
            OG: (1200, 630),
            f"{PREFIX}cockpit-tablet.jpeg": (360, 480),
            f"{PREFIX}tablet-under-sail.jpeg": (360, 480),
            f"{PREFIX}helm-underway.jpeg": (360, 480),
            TUTORIAL_CLOSEUP: (412, 565),
            f"{PREFIX}data-awa-41.jpeg": (360, 480),
        }
        for rel, (width, height) in expected.items():
            path = ROOT / rel.lstrip("/")
            self.assertTrue(path.is_file(), path)
            output = subprocess.check_output(
                ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)], text=True
            )
            self.assertIn(f"pixelWidth: {width}", output, rel)
            self.assertIn(f"pixelHeight: {height}", output, rel)

    def test_the_closeup_is_srgb_and_never_upscaled_on_screen(self):
        path = ROOT / TUTORIAL_CLOSEUP.lstrip("/")
        profile = subprocess.check_output(["sips", "-g", "space", "-g", "profile", str(path)], text=True)
        self.assertIn("space: RGB", profile)
        self.assertIn("sRGB", profile)
        # Nothing but the pixel segments survived the metadata strip.
        data = path.read_bytes()
        markers = []
        index = 2
        while index < len(data) and data[index] == 0xFF:
            marker = data[index + 1]
            if marker == 0xDA:
                break
            markers.append(marker)
            index += 2 + int.from_bytes(data[index + 2:index + 4], "big")
        self.assertFalse([m for m in markers if 0xE0 <= m <= 0xEF or m == 0xFE],
                         f"APPn/COM segment survived: {[hex(m) for m in markers]}")
        # The device column is 225px on desktop and capped at 260px on mobile,
        # both well inside the 412px the file actually carries.
        self.assertIn("minmax(0,1fr) 225px minmax(0,1fr)", self.css)
        self.assertIn(".explainer-device{width:min(100%,260px)}", self.css)

    def test_published_files_carry_no_exif_gps_or_xmp(self):
        for rel in (HERO, OG, *BODY_SCENES):
            data = (ROOT / rel.lstrip("/")).read_bytes()
            for marker in (b"Exif\x00\x00", b"http://ns.adobe.com", b"GPS"):
                self.assertNotIn(marker, data[:65536], f"{rel}: {marker!r}")

    def test_scenes_declare_their_natural_size(self):
        # width/height + the compact layout are what stop a 360px original from
        # being upscaled past its natural width; the crop declares its own.
        block = self.data[self.data.index(f'slug: "{SLUG}"'):self.data.index('slug: "shootingstar-club-tuning-2026"')]
        self.assertEqual(block.count("width: 360,"), 4)
        self.assertEqual(block.count('layout: "compact-portrait"'), 4)
        self.assertIn('heroLayout: "natural-portrait"', block)
        self.assertIn("heroNaturalWidth: 360,", block)
        self.assertIn('shape: "tall"', block)
        self.assertIn("width: 412,", block)
        self.assertIn("height: 565,", block)

    def test_one_explainer_is_declared_once_in_the_third_section(self):
        block = self.data[self.data.index(f'slug: "{SLUG}"'):self.data.index('slug: "shootingstar-club-tuning-2026"')]
        self.assertEqual(block.count("explainer: {"), 1)
        self.assertEqual(block.count('theme: "winddata"'), 1)
        self.assertEqual(block.count('layout: "device-tutorial"'), 1)
        self.assertNotIn('explainerPlacement: "article-end"', block)
        self.assertNotIn('kicker: "03 / Captain Tablet"', block)
        second = block.index('kicker: "02 / 7.4 Knots"')
        third = block.index('kicker: "03 / How to use Tablet Data"')
        heading = block.index('heading: "태블릿 데이터 활용 팁"')
        explainer = block.index("explainer: {")
        self.assertLess(second, third)
        self.assertLess(third, heading)
        self.assertLess(heading, explainer)
        # Both renderers use the generic section explainer path, not a hard-coded slug.
        for path in ("magazine/magazine.js", "scripts/render_magazine_static.mjs"):
            source = (ROOT / path).read_text(encoding="utf-8")
            self.assertNotIn(SLUG, source, path)

    def test_tutorial_card_renders_once_inside_the_third_section(self):
        self.assertEqual(self.dom.count(CARD), 1)
        self.assertNotIn(CARD, self.first_section)
        self.assertIn(CARD, self.third_section)
        self.assert_card_is_the_third_section(self.dom, "hydrated DOM")

    def test_the_first_section_reads_straight_through_without_the_card(self):
        section = self.first_section
        opening = section.index("오후 5시 30분, 왕산마리나에서 슈팅스타가 출항했습니다")
        second = section.index("처음에는 어색했습니다. 반사방지 필름을 붙인 태블릿은")
        photo = section.index(f"{PREFIX}tablet-under-sail.jpeg")
        third = section.index("세 사람은 이날 스키퍼와 지브 트리머")
        self.assertLess(opening, second)
        self.assertLess(second, photo)
        self.assertLess(photo, third)

    def test_card_follows_every_section_paragraph_and_photo(self):
        # The last words and the last body photo of merged section 02 land above section 03.
        for markup, where in ((self.dom, "hydrated DOM"), (self.static_detail, "static fallback")):
            self.assertLess(markup.index("그날부터 팀이 함께 실력을 키워 갈 기준이 하나 더 생겼습니다"),
                            markup.index(CARD), where)
        self.assertLess(self.dom.rindex(f"{PREFIX}data-awa-41.jpeg"), self.dom.index(CARD))

    def test_helm_photo_follows_the_adaptation_paragraph(self):
        target = ("처음 시도한 태블릿 기반 세일링이었지만 적응은 빨랐습니다. 문제가 생기면 "
                  "스키퍼의 표정보다 태블릿의 수치를 먼저 확인했고, 누구의 감이 맞는지를 두고 "
                  "옥신각신하기보다 같은 데이터를 놓고 다음 조정을 이야기했습니다.")
        following = "엄주범 단장이 장비를 마련한 이유도 여기에 있었습니다."
        photo = f"{PREFIX}helm-underway.jpeg"
        for markup, where in ((self.dom, "hydrated DOM"), (self.static_detail, "static fallback")):
            self.assertEqual(markup.count(photo), 1, where)
            self.assertLess(markup.index(target), markup.index(photo), where)
            self.assertLess(markup.index(photo), markup.index(following), where)

    def test_card_is_a_device_board_not_a_text_grid(self):
        card = self.card_of(self.dom)
        self.assertIn("<h4>태블릿 화면, 이렇게 읽습니다</h4>", card)
        self.assertIn(f'<p class="explainer-summary">{SUMMARY}</p>', card)
        # One central device image, and it is the screen close-up.
        self.assertEqual(card.count('<figure class="explainer-device">'), 1)
        self.assertEqual(card.count("<img"), 1)
        self.assertIn(f'<img src="{TUTORIAL_CLOSEUP}"', card)
        self.assertIn('width="412" height="565"', card)
        # Alt text and caption both have to say what the reader is looking at:
        # a close-up of the real screen, with A ringed yellow and T ringed red.
        alt = re.search(r'<img src="[^"]*" alt="([^"]*)"', card).group(1)
        caption = re.search(r"<figcaption>(.*?)</figcaption>", card, re.S).group(1)
        for text in (alt, caption):
            self.assertIn("확대", text)
            self.assertIn("노란색 원", text)
            self.assertIn("빨간색 원", text)
        # Five hotspots on the image, five callouts beside it, markers matching.
        self.assertEqual(card.count('class="explainer-hotspot"'), len(TUTORIAL_ITEMS))
        self.assertEqual(card.count('class="explainer-callout"'), len(TUTORIAL_ITEMS))
        self.assertEqual(card.count("<dt>"), len(TUTORIAL_ITEMS))
        self.assertEqual(card.count("<dd>"), len(TUTORIAL_ITEMS))
        hotspots = re.findall(r'class="explainer-hotspot" data-marker="(\d+)"', card)
        callouts = re.findall(r'class="explainer-callout" data-anchor="([a-z-]+)" data-marker="(\d+)"', card)
        self.assertEqual(hotspots, [item[0] for item in TUTORIAL_ITEMS])
        self.assertEqual([marker for _, marker in callouts], [item[0] for item in TUTORIAL_ITEMS])
        self.assertEqual([anchor for anchor, _ in callouts],
                         ["top-left", "top-right", "center", "bottom-left", "bottom-right"])
        # Each acronym is spelled out on its own line directly under the label,
        # between the label and the reading; BOAT SPEED has no line at all.
        named = [item for item in TUTORIAL_ITEMS if item[4]]
        self.assertEqual(card.count('class="explainer-fullname"'), len(named))
        for marker, label, reading, value, full_name in TUTORIAL_ITEMS:
            self.assertIn(f'<b class="explainer-marker">{marker}</b>', card)
            self.assertIn(f'<span class="explainer-label">{label}</span>', card)
            self.assertIn(f'<span class="explainer-reading">{reading}</span>', card)
            self.assertIn(value, card)
            after_label = (f'<span class="explainer-label">{label}</span>'
                           f'<span class="explainer-fullname">{full_name}</span>' if full_name
                           else f'<span class="explainer-label">{label}</span>'
                                f'<span class="explainer-reading">{reading}</span>')
            self.assertIn(after_label, card, label)
        # Two colour-toned legend rows; the old text-grid row is gone.
        self.assertEqual(card.count("explainer-legend-item"), len(LEGEND))
        for tone, label, value in LEGEND:
            self.assertIn(f'<li class="explainer-legend-item" data-tone="{tone}">'
                          f"<b>{label}</b><span>{value}</span></li>", card)
        self.assertNotIn("READ TOGETHER", card)

    def test_the_a_and_t_arrows_are_ringed_on_the_photo(self):
        for markup in (self.dom, self.static_detail):
            card = self.card_of(markup)
            frame = card.partition('<span class="explainer-device-frame">')[2] \
                        .partition("</span><figcaption>")[0]
            # Exactly two rings, inside the image frame — not loose in the card.
            self.assertEqual(card.count('class="explainer-annotation"'), len(ANNOTATIONS))
            self.assertEqual(frame.count('class="explainer-annotation"'), len(ANNOTATIONS))
            rings = re.findall(r'<span class="explainer-annotation" data-tone="([a-z]+)"'
                               r' style="left:([\d.]+)%;top:([\d.]+)%;--ring-size:([\d.]+)%"'
                               r' role="img" aria-label="([^"]+)">'
                               r'<b aria-hidden="true">([^<]+)</b></span>', card)
            self.assertEqual([(tone, label, x, y, size) for tone, x, y, size, _, label in rings],
                             [tuple(entry) for entry in ANNOTATIONS])
            # Each ring is announced by something more useful than its glyph.
            for (_, _, _, _, aria, label), (_, _, _, _, _) in zip(rings, ANNOTATIONS):
                self.assertNotEqual(aria, label)
                self.assertIn(label, aria)
            # Rings and hotspots share the frame but never land on each other.
            hotspots = [(float(x), float(y)) for x, y in re.findall(
                r'class="explainer-hotspot"[^>]*style="left:([\d.]+)%;top:([\d.]+)%"', frame)]
            self.assertEqual(len(hotspots), len(TUTORIAL_ITEMS))
            # Compared in the 225px-wide desktop rendering of the 412x565 file,
            # where a hotspot is 26px across and a ring 11.17% of the width.
            width, height = 225.0, 225.0 * 565 / 412
            for tone, label, x, y, size in ANNOTATIONS:
                ring = (float(x) / 100 * width, float(y) / 100 * height)
                clearance = 13 + float(size) / 100 * width / 2
                for spot in hotspots:
                    gap = ((ring[0] - spot[0] * width / 100) ** 2
                           + (ring[1] - spot[1] * height / 100) ** 2) ** 0.5
                    self.assertGreater(gap, clearance, f"{label} ring overlaps a hotspot")
            first, second = [(float(e[2]) / 100 * width, float(e[3]) / 100 * height) for e in ANNOTATIONS]
            self.assertGreater(((first[0] - second[0]) ** 2 + (first[1] - second[1]) ** 2) ** 0.5,
                               float(ANNOTATIONS[0][4]) / 100 * width, "the two rings overlap")

    def test_ring_tones_are_hollow_and_use_the_legend_colours(self):
        # Hollow: the ring only ever sets a border, never a background, so the
        # instrument's own arrow stays visible inside it.
        rule = self.css[self.css.index(".explainer-annotation{"):]
        rule = rule[:rule.index("}")]
        self.assertIn("border:2px solid", rule)
        self.assertIn("border-radius:50%", rule)
        self.assertNotIn("background", rule)
        self.assertIn("width:var(--ring-size)", rule)
        self.assertIn("aspect-ratio:1", rule)
        # A yellow, T red — the same two accents the legend rows carry.
        for tone, colour in (("yellow", "#ffcc33"), ("red", "#ff6a5a")):
            self.assertIn(f'.explainer-annotation[data-tone="{tone}"]{{border-color:{colour}}}', self.css)
            self.assertIn(f'.explainer-annotation[data-tone="{tone}"] b{{background:{colour}}}', self.css)
        self.assertEqual(set(re.findall(r'\.explainer-annotation\[data-tone="([a-z]+)"\]', self.css)),
                         {"yellow", "red"}, "only the yellow/red tones may be styled")
        # The label hangs off the ring rather than sitting inside it.
        tag = self.css[self.css.index(".explainer-annotation b{"):]
        tag = tag[:tag.index("}")]
        self.assertIn("position:absolute", tag)
        self.assertIn("left:calc(100% + 3px)", tag)

    def test_the_summary_compares_a_and_t_instead_of_reciting_positions(self):
        # Replaced, not amended: the position-recital sentence must be gone and
        # the new one must land exactly once in the data and in both bodies.
        for haystack, where in ((self.data, "articles.js"), (self.dom, "hydrated DOM"),
                                (self.static_detail, "static fallback")):
            self.assertEqual(haystack.count(OLD_SUMMARY), 0, where)
            self.assertEqual(haystack.count(SUMMARY), 1, where)
        for markup in (self.dom, self.static_detail):
            self.assertEqual(self.card_of(markup).count(SUMMARY), 1)

    def test_the_withdrawn_scope_note_is_gone_everywhere(self):
        # Deleted, not moved: the sentence must not survive anywhere, and the
        # card must carry no note slot at all.
        for haystack, where in ((self.data, "articles.js"), (self.dom, "hydrated DOM"),
                                (self.static_detail, "static fallback")):
            self.assertEqual(haystack.count(DELETED_NOTE), 0, where)
            self.assertEqual(haystack.count("이날의 최고속도나 전체 항해 평균"), 0, where)
        for markup in (self.dom, self.static_detail):
            self.assertEqual(self.card_of(markup).count("explainer-note"), 0)
        block = self.data[self.data.index(f'slug: "{SLUG}"'):self.data.index('slug: "shootingstar-club-tuning-2026"')]
        self.assertEqual(block.count("note:"), 0)

    def test_the_a_and_t_legends_are_the_yellow_and_red_arrows(self):
        for markup in (self.dom, self.static_detail):
            card = self.card_of(markup)
            rows = re.findall(r'<li class="explainer-legend-item" data-tone="([a-z]+)">'
                              r"<b>(.*?)</b><span>(.*?)</span></li>", card)
            self.assertEqual(rows, [tuple(entry) for entry in LEGEND])
        # Only the allowlisted tones exist, and each one carries a real accent.
        for tone, colour in (("yellow", "#ffcc33"), ("red", "#ff6a5a")):
            rule = f'.explainer-legend-item[data-tone="{tone}"]:before,' \
                   f'.explainer-legend-item[data-tone="{tone}"] b{{color:{colour}}}'
            self.assertIn(rule, self.css)
        self.assertEqual(set(re.findall(r'\.explainer-legend-item\[data-tone="([a-z]+)"\]', self.css)),
                         {"yellow", "red"}, "only the yellow/red tones may be styled")

    def test_the_card_maps_every_reading_to_a_colour_and_an_action(self):
        for markup in (self.dom, self.static_detail):
            card = self.card_of(markup)
            self.assertEqual(card.count('<section class="explainer-usage"'), 1)
            self.assertIn(f'<section class="explainer-usage" aria-label="{USAGE["title"]}">', card)
            self.assertIn(f'<div class="explainer-usage-kicker">{USAGE["kicker"]}</div>', card)
            self.assertEqual(card.count(f'<h5>{USAGE["title"]}</h5>'), 1)
            self.assertIn(f'<p class="explainer-usage-summary">{USAGE["summary"]}</p>', card)
            # The summary itself spells out both halves of every acronym, so a
            # first-timer can decode AWA/TWS without leaving the panel.
            for cue in ("A는 노란 화살", "T는 빨간 화살", "WA는 바람 방향", "WS는 바람 세기"):
                self.assertIn(cue, USAGE["summary"], cue)
            # An ordered list, still — the map keeps the 01-05 reading order.
            self.assertEqual(card.count(f'<ol class="explainer-usage-steps" data-layout="{USAGE["layout"]}">'), 1)
            steps = [(marker, code, tone, metric, action, body) for
                     tone, marker, code, metric, action, body in re.findall(USAGE_STEP_RE, card, re.S)]
            self.assertEqual(steps, [tuple(step) for step in USAGE_STEPS])
            # The panel is inside the card and after the legend, not a sibling.
            self.assertLess(card.index("explainer-legend"), card.index("explainer-usage"))
            self.assertEqual(len(steps), len(TUTORIAL_ITEMS), "one card per 01-05 reading")
            # Markers and codes are the ones the photo and the callouts use.
            self.assertEqual([step[0] for step in steps], [item[0] for item in TUTORIAL_ITEMS])
            self.assertEqual([step[1] for step in steps], [item[1] for item in TUTORIAL_ITEMS])
        # Neither superseded panel may survive as a slot anywhere.
        for haystack, pattern, where in ((self.data, '"{}"', "articles.js"),
                                         (self.dom, ">{}<", "hydrated DOM"),
                                         (self.static_detail, ">{}<", "static fallback")):
            for old in OLD_USAGE_STEPS:
                self.assertEqual(haystack.count(pattern.format(old)), 0, f"{where}: {old}")

    def test_each_mapping_card_resolves_to_the_photo_accent_for_its_arrow(self):
        # A is yellow and T is red on the photo, so they must be yellow and red
        # in the map too; the boat's own speed belongs to neither arrow.
        expected = {"AWA": "yellow", "AWS": "yellow", "TWA": "red", "TWS": "red", "BOAT SPEED": "cyan"}
        self.assertEqual({code: tone for _, code, tone, *_ in USAGE_STEPS}, expected)
        # Every tone the data uses resolves to an accent, and the two arrow
        # accents are the same hexes the rings and the legend already carry.
        self.assertEqual(".explainer-usage-step{--usage-accent:#7adef0}" in self.css, True)
        for tone, accent in USAGE_ACCENTS.items():
            self.assertIn(f'.explainer-usage-step[data-tone="{tone}"]{{--usage-accent:{accent}}}', self.css)
        for tone, accent in (("yellow", USAGE_ACCENTS["yellow"]), ("red", USAGE_ACCENTS["red"])):
            self.assertIn(f'.explainer-annotation[data-tone="{tone}"]{{border-color:{accent}}}', self.css)
        self.assertEqual(set(re.findall(r'\.explainer-usage-step\[data-tone="([a-z]+)"\]', self.css)),
                         set(USAGE_ACCENTS), "only allowlisted mapping tones may be styled")
        # Colour never carries a meaning on its own: the chip says which arrow
        # and which quantity in words, and the marker/code stay text.
        for _, _, _, metric, _, _ in USAGE_STEPS:
            self.assertTrue(metric.startswith(("노란 A · ", "빨간 T · ")) or metric == "배 속도", metric)
        self.assertEqual(sum("WA 방향" in step[3] for step in USAGE_STEPS), 2)
        self.assertEqual(sum("WS 세기" in step[3] for step in USAGE_STEPS), 2)

    def test_mapping_card_reads_code_then_category_then_action(self):
        # Source order is the reading order, and each slot is styled: the code
        # is the largest thing on the card, the chip is outlined in the accent,
        # the action is the second emphasis and carries the arrow, the sentence
        # is the quiet line under it.
        for markup in (self.dom, self.static_detail):
            card = self.card_of(markup)
            first = re.search(USAGE_STEP_RE, card, re.S).group(0)
            self.assertLess(first.index("usage-code"), first.index("usage-metric"))
            self.assertLess(first.index("usage-metric"), first.index("usage-action"))
            self.assertLess(first.index("usage-action"), first.index("usage-body"))
        self.assertIn('.explainer-usage-step .usage-code{grid-column:2;grid-row:1;'
                      'color:var(--usage-accent);font-family:"Archivo Black",sans-serif;'
                      "font-size:clamp(18px,2vw,22px)", self.css)
        self.assertIn(".explainer-usage-step .usage-metric{grid-column:2;grid-row:2;justify-self:start;"
                      "margin:0;padding:3px 9px;border:1px solid var(--usage-accent);"
                      "border-radius:999px;color:var(--usage-accent);font-size:10px", self.css)
        self.assertIn(".explainer-usage-step .usage-action{grid-column:2;grid-row:3;margin-top:2px;"
                      "color:var(--white);font-size:13px;font-weight:900", self.css)
        self.assertIn('.explainer-usage-step .usage-action:before{content:"→ ";'
                      "color:var(--usage-accent)}", self.css)
        self.assertIn(".explainer-usage-step .usage-body{grid-column:2;grid-row:4;", self.css)
        # The marker circle replaces the generic counter, never doubles it.
        self.assertIn('.explainer-usage-steps[data-layout="reading-map"] '
                      ".explainer-usage-step:before{content:none}", self.css)
        self.assertIn(".explainer-usage-step .usage-marker{grid-column:1;grid-row:1;", self.css)

    def test_the_card_walks_the_readings_once_as_a_scenario(self):
        for markup in (self.dom, self.static_detail):
            card = self.card_of(markup)
            self.assertEqual(card.count('<section class="explainer-scenario"'), 1)
            self.assertIn(f'<section class="explainer-scenario" aria-label="{SCENARIO["title"]}">', card)
            self.assertIn(f'<div class="explainer-scenario-kicker">{SCENARIO["kicker"]}</div>', card)
            self.assertEqual(card.count(f'<h5>{SCENARIO["title"]}</h5>'), 1)
            self.assertIn(f'<p class="explainer-scenario-summary">{SCENARIO["summary"]}</p>', card)
            self.assertEqual(card.count('<ol class="explainer-scenario-steps">'), 1)
            # Four steps, in order, with their chips in the order the data lists.
            self.assertEqual(scenario_steps(card), [tuple(step) for step in SCENARIO_STEPS])
            # Under the reading map, and this card still carries no note slot.
            self.assertLess(card.index("explainer-usage-steps"), card.index("explainer-scenario"))
            self.assertEqual(card.count("explainer-note"), 0)
        # Exactly one scenario is declared, and it is a sibling of usage.
        block = self.data[self.data.index(f'slug: "{SLUG}"'):self.data.index('slug: "shootingstar-club-tuning-2026"')]
        self.assertEqual(block.count("scenario: {"), 1)
        self.assertLess(block.index("usage: {"), block.index("scenario: {"))

    def test_a_note_would_still_come_after_the_scenario(self):
        # The scenario sits after the reading map and before any note slot.
        # This card declares no note, so the ordering is proven on a throwaway
        # copy that does — the real data must stay note-free (asserted above).
        def add_note(source):
            source, hits = re.subn(r"( +)(scenario: \{)", r'\1note: "임시 각주",\n\1\2', source, count=1)
            assert hits == 1, "expected the scenario fixture to sit next to a note slot"
            return source
        card = self.card_of(static_build(self.node, mutate=add_note))
        self.assertLess(card.index("explainer-usage-steps"), card.index("explainer-scenario"))
        self.assertLess(card.index("explainer-scenario"), card.index("explainer-note"))

    def test_the_scenario_teaches_comparing_values_not_a_rule(self):
        # It is a worked example, not a claim: nothing may say the adjustment
        # produced the 7.4, and nothing may state an absolute sailing rule.
        prose = SCENARIO["summary"] + " " + " ".join(step[4] for step in SCENARIO_STEPS)
        for claim in ("때문에", "덕분에", "빨라집니다", "올라갑니다", "항상", "반드시", "무조건"):
            self.assertNotIn(claim, prose, claim)
        # The last step frames 7.4 as the comparison baseline and allows both
        # outcomes, including reverting.
        self.assertIn("비교 기준으로 기억하고", SCENARIO_STEPS[3][4])
        self.assertIn("나빠지면 이전 설정으로 돌아가거나", SCENARIO_STEPS[3][4])

    def test_scenario_tones_are_allowlisted_and_never_the_only_signal(self):
        # The panel reuses the reading map's three accents on its own selectors,
        # so a scenario tone can never change what the rings on the photo claim.
        self.assertIn(".explainer-scenario-step{--scenario-accent:#7adef0", self.css)
        for tone, accent in USAGE_ACCENTS.items():
            self.assertIn(f'.explainer-scenario-step[data-tone="{tone}"]{{--scenario-accent:{accent}}}', self.css)
        self.assertEqual(set(re.findall(r'\.explainer-scenario-step\[data-tone="([a-z]+)"\]', self.css)),
                         set(USAGE_ACCENTS), "only allowlisted scenario tones may be styled")
        # Order lives in the marker text and subject in the title and chips, so
        # the tint carries nothing on its own.
        self.assertEqual([step[0] for step in SCENARIO_STEPS], ["1", "2", "3", "4"])
        for _, _, title, chips, body in SCENARIO_STEPS:
            self.assertTrue(title and chips and body)
        # The adjustment step is neutral and must not borrow either arrow.
        neutral = [step for step in SCENARIO_STEPS if step[1] is None]
        self.assertEqual(len(neutral), 1)
        for text in (neutral[0][2], *neutral[0][3]):
            self.assertNotIn("노란", text)
            self.assertNotIn("빨간", text)
        # The A/T steps do spell their arrow out in words, not only in colour.
        self.assertIn("빨간 T", SCENARIO_STEPS[0][4])
        self.assertIn("노란 A", SCENARIO_STEPS[1][4])

    def test_a_hostile_scenario_tone_never_becomes_an_attribute(self):
        def poison(source):
            source, hits = re.subn(r'(scenario: \{[\s\S]*?)tone: "red"',
                                   r'\1tone: "red\\" onload=alert(1) x=\\""', source, count=1)
            assert hits == 1, "expected a scenario tone fixture to poison"
            return source
        card = self.card_of(static_build(self.node, mutate=poison))
        steps = scenario_steps(card)
        # The attribute is dropped; the step, its title, chips and body survive.
        self.assertEqual([step[1] for step in steps], [None, "yellow", None, "cyan"])
        self.assertEqual([step[0] for step in steps], [step[0] for step in SCENARIO_STEPS])
        self.assertEqual([step[3] for step in steps], [step[3] for step in SCENARIO_STEPS])
        self.assertNotIn("onload", card)

    def test_scenario_is_four_across_only_where_it_stays_readable(self):
        # Base rule: one column, order 1-4, at 390 and anywhere below 761.
        self.assertIn(".explainer-scenario-steps{display:grid;grid-template-columns:1fr;", self.css)
        desktop_open = self.css.index("@media(min-width:761px)")
        desktop_close = self.css.index("\n}", desktop_open)
        desktop = self.css[desktop_open:desktop_close]
        # Four tracks would leave ~130px per step at the real 660px body width,
        # so the desktop flow is an explicit 2x2 instead.
        self.assertIn(".explainer-scenario-steps{grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 26px}",
                      desktop)
        # Exactly two rules touch the scenario track list — the 1fr base and
        # that single desktop override — so nothing can re-column it at 390px.
        rules = [m.start() for m in re.finditer(r"\.explainer-scenario-steps\{", self.css)]
        self.assertEqual(len(rules), 2)
        self.assertLess(rules[0], desktop_open)
        self.assertTrue(desktop_open < rules[1] < desktop_close)
        # The connector lives in the widened column gap, never over the text,
        # and only exists at the width that has a gap to put it in.
        self.assertIn('.explainer-scenario-step:nth-child(odd):after{content:"→";position:absolute;'
                      "right:-26px;top:50%;width:26px;", desktop)
        self.assertEqual(len(re.findall(r"\.explainer-scenario-step:nth-child", self.css)), 1)
        # Long Korean copy must wrap rather than push a step sideways.
        self.assertIn(".explainer-scenario-step{--scenario-accent:#7adef0;position:relative;min-width:0;",
                      self.css)
        for selector in (".scenario-title{", ".scenario-chip{", ".scenario-body{"):
            rule = self.css[self.css.index(selector):]
            self.assertIn("overflow-wrap:break-word", rule[:rule.index("}")])

    def test_usage_steps_fill_the_desktop_grid_and_stack_on_mobile(self):
        # Desktop reads 3 + 2: the two direction readings and the boat's speed
        # on top, the two strength readings under them — which is source order.
        self.assertEqual([step[1] for step in USAGE_STEPS[:3]], ["AWA", "TWA", "BOAT SPEED"])
        self.assertEqual([step[1] for step in USAGE_STEPS[3:]], ["AWS", "TWS"])
        self.assertIn(".explainer-usage-steps{display:grid;grid-template-columns:1fr;", self.css)
        desktop_open = self.css.index("@media(min-width:761px)")
        desktop_close = self.css.index("\n}", desktop_open)
        desktop = self.css[desktop_open:desktop_close]
        # Six tracks: three double-width cards, then the rest split the row so
        # an odd step count leaves no dead cell.
        self.assertIn(".explainer-usage-steps{grid-template-columns:repeat(6,minmax(0,1fr))", desktop)
        self.assertIn(".explainer-usage-step{grid-column:span 2}", desktop)
        self.assertIn(".explainer-usage-step:nth-child(n+4){grid-column:span 3}", desktop)
        # Nothing outside the desktop query gives a step a column span, so 390px
        # keeps the plain single-column stack.
        self.assertEqual(len(re.findall(r"grid-column:span", self.css)), 2)
        # Exactly two rules touch the track list — the 1fr base and that single
        # desktop override — so nothing can re-column the steps at 390px.
        rules = [m.start() for m in re.finditer(r"\.explainer-usage-steps\{", self.css)]
        self.assertEqual(len(rules), 2)
        self.assertLess(rules[0], desktop_open)
        self.assertTrue(desktop_open < rules[1] < desktop_close)
        # Long Korean step copy must wrap rather than push the card sideways.
        self.assertIn(".explainer-usage-step{position:relative;min-width:0;", self.css)
        for selector in (".explainer-usage-step b{", ".explainer-usage-step span{"):
            rule = self.css[self.css.index(selector):]
            self.assertIn("overflow-wrap:break-word", rule[:rule.index("}")])

    def test_usage_is_generic_data_not_a_hardcoded_story(self):
        for source in ("magazine/magazine.js", "scripts/render_magazine_static.mjs"):
            text = (ROOT / source).read_text(encoding="utf-8")
            self.assertNotIn(SLUG, text, source)
            self.assertNotIn("winddata", text, source)
            # Tones reach the DOM through an allowlist, never straight from data.
            self.assertIn("ACCENT_TONES = ['yellow', 'red']", text, source)
            self.assertIn("ACCENT_TONES.includes(tone)", text, source)
            self.assertIn("ACCENT_TONES.includes(entry.tone)", text, source)
            # The reading map is a layout any explainer can ask for, with its
            # own tone allowlist — the rings keep theirs, so a mapping tone can
            # never change what the photo claims.
            self.assertIn("const READING_MAP = 'reading-map'", text, source)
            self.assertIn("USAGE_TONES = ['yellow', 'red', 'cyan']", text, source)
            self.assertIn("USAGE_TONES.includes(tone)", text, source)
            # The scenario panel is the same deal: optional data, no branch of
            # its own for tones, and no copy from this episode baked in.
            self.assertIn("usageTone(step.tone)", text, source)
            self.assertNotIn("scenarioTone", text, source)
            for copy in (SCENARIO["kicker"], SCENARIO["title"], SCENARIO["summary"],
                         *(step[2] for step in SCENARIO_STEPS),
                         *(chip for step in SCENARIO_STEPS for chip in step[3])):
                self.assertNotIn(copy, text, source)
            for code in ("AWA", "TWA", "BOAT SPEED", "AWS", "TWS"):
                self.assertNotIn(code, text, source)

    def test_explainers_without_usage_render_no_panel(self):
        for slug in ("wangsan-to-yeosu-island-delivery-2026", "shootingstar-club-tuning-2026"):
            rendered = json.loads(subprocess.run(
                [self.node, "-e", RENDER, str(ROOT), slug], check=True, capture_output=True, text=True
            ).stdout)["html"]
            self.assertNotIn("explainer-usage", rendered, slug)
            self.assertNotIn("explainer-scenario", rendered, slug)
            self.assertNotIn("data-tone", rendered, slug)
            self.assertNotIn("explainer-annotation", rendered, slug)

    def test_the_card_explains_without_jargon(self):
        # The readings keep their instrument labels (AWA/TWA/AWS/TWS); the
        # sentences that explain them to a first-timer must not.
        for markup in (self.dom, self.static_detail):
            card = self.card_of(markup)
            summary = re.search(r'<p class="explainer-summary">(.*?)</p>', card, re.S).group(1)
            prose = summary + "".join(re.findall(r"<dd>(.*?)</dd>", card, re.S)) \
                + "".join(re.findall(r'<li class="explainer-legend-item"[^>]*>.*?<span>(.*?)</span>', card, re.S)) \
                + card.partition('<section class="explainer-usage"')[2]
            for term in JARGON:
                self.assertNotIn(term, prose, term)

    def test_hotspot_coordinates_are_plain_clamped_percentages(self):
        for markup in (self.dom, self.static_detail):
            styles = re.findall(r'class="explainer-hotspot"[^>]*style="([^"]*)"', self.card_of(markup))
            self.assertEqual(len(styles), len(TUTORIAL_ITEMS))
            for style in styles:
                match = re.fullmatch(r"left:(\d+(?:\.\d+)?)%;top:(\d+(?:\.\d+)?)%", style)
                self.assertIsNotNone(match, style)
                for value in match.groups():
                    self.assertGreaterEqual(float(value), 0)
                    self.assertLessEqual(float(value), 100)

    def test_out_of_range_hotspots_cannot_escape_into_the_style_attribute(self):
        def poison(source):
            # First marker gets a string that would break out of style="…";
            # second gets numbers past both ends of the range.
            source, hits = re.subn(r"hotspot: \{ x: [\d.]+, y: [\d.]+ \}",
                                   'hotspot: { x: "999\\" onload=alert(1) x=\\"", y: -40 }',
                                   source, count=1)
            assert hits == 1, "expected a hotspot fixture to poison"
            source, hits = re.subn(r"hotspot: \{ x: [\d.]+, y: [\d.]+ \}",
                                   "hotspot: { x: 999, y: -40 }", source, count=1)
            assert hits == 1, "expected a second hotspot fixture to poison"
            return source
        card = self.card_of(static_build(self.node, mutate=poison))
        styles = re.findall(r'class="explainer-hotspot"[^>]*style="([^"]*)"', card)
        self.assertEqual(styles[0], "left:0%;top:0%")
        self.assertEqual(styles[1], "left:100%;top:0%")
        self.assertNotIn("onload", card)

    def test_tutorial_layout_is_styled_and_keeps_the_theme_decoration(self):
        self.assertIn(".article-explainer.explainer-winddata{", self.css)
        self.assertIn('.article-explainer.explainer-winddata:before{content:"WIND\\A DATA"', self.css)
        self.assertIn(".explainer-layout-device-tutorial .explainer-tutorial{", self.css)
        self.assertIn(".explainer-hotspot{", self.css)
        self.assertIn(".explainer-annotation{", self.css)
        # Korean headings must not strand a lone syllable on the last line.
        self.assertIn(".explainer-layout-device-tutorial h4{text-wrap:balance;word-break:keep-all}", self.css)
        self.assertIn(".explainer-device{", self.css)
        # The name under an acronym takes its own flex line in the callout, so
        # it never squeezes the reading out of the narrow side cards.
        self.assertIn(".explainer-fullname{box-sizing:border-box;flex:0 0 100%;padding-left:28px", self.css)
        self.assertIn('.explainer-callout[data-anchor$="-right"] .explainer-fullname{padding-right:28px;padding-left:0}', self.css)
        self.assertIn(".explainer-legend{", self.css)
        # Desktop places the callouts around the device; mobile stacks them.
        desktop = self.css[self.css.index("@media(min-width:761px)"):]
        for anchor in ("top-left", "bottom-left", "top-right", "bottom-right", "center"):
            self.assertIn(f'.explainer-callout[data-anchor="{anchor}"]', desktop)
        mobile = self.css[self.css.index("@media(max-width:760px)"):]
        self.assertIn(".article-explainer dl{grid-template-columns:1fr", mobile)
        self.assertIn(".article-explainer.explainer-winddata:before{", mobile)

    def test_every_reader_shares_one_cache_key_per_asset(self):
        pages = sorted(p for p in ROOT.rglob("*.html")
                       if not {".wrangler", "prototypes", ".git"} & set(p.relative_to(ROOT).parts))
        keys = {asset: set() for asset in CACHE_KEYS}
        readers = {asset: 0 for asset in CACHE_KEYS}
        for page in pages:
            html = page.read_text(encoding="utf-8")
            for asset in keys:
                for found in re.findall(rf'{re.escape(asset)}\?v=([^"\']+)', html):
                    keys[asset].add(found)
                    readers[asset] += 1
        self.assertEqual(readers, {"articles.js": 9, "magazine.js": 8, "magazine.css": 8})
        self.assertEqual(keys, {asset: {key} for asset, key in CACHE_KEYS.items()})

    def test_generated_no_js_body_carries_the_card_exactly_once(self):
        self.assertEqual(self.static_detail.count(CARD), 1)
        self.assertNotIn(CARD, self.static_first_section)
        self.assertIn(CARD, self.static_third_section)
        card = self.card_of(self.static_detail)
        self.assertEqual(card.count("<dt>"), len(TUTORIAL_ITEMS))
        self.assertEqual(card.count("<dd>"), len(TUTORIAL_ITEMS))
        self.assertEqual(card.count('class="explainer-hotspot"'), len(TUTORIAL_ITEMS))

    def test_generated_card_sits_in_the_third_and_final_section(self):
        section = self.static_first_section
        # The static renderer emits no body photos, so section 01 is just its
        # paragraphs — and they now run on without the card between them.
        self.assertNotIn("tablet-under-sail", section)
        self.assertLess(section.index("오후 5시 30분, 왕산마리나에서 슈팅스타가 출항했습니다"),
                        section.index("처음에는 어색했습니다. 반사방지 필름을 붙인 태블릿은"))
        self.assert_card_is_the_third_section(self.static_detail, "static fallback")

    def test_generated_card_matches_the_hydrated_card_slot_for_slot(self):
        def slots(markup):
            card = self.card_of(markup)
            return {
                "kicker": re.search(r'<div class="explainer-kicker">(.*?)</div>', card).group(1),
                "title": re.search(r"<h4>(.*?)</h4>", card).group(1),
                "summary": re.search(r'<p class="explainer-summary">(.*?)</p>', card, re.S).group(1),
                "image": re.search(r'<figure class="explainer-device"><span class="explainer-device-frame">'
                                   r'<img src="(.*?)" alt="(.*?)" width="(\d+)" height="(\d+)"', card).groups(),
                "caption": re.search(r"<figcaption>(.*?)</figcaption>", card, re.S).group(1),
                "hotspots": re.findall(r'class="explainer-hotspot" data-marker="(\d+)" style="([^"]*)"', card),
                "annotations": re.findall(r'class="explainer-annotation" data-tone="([a-z]+)"'
                                          r' style="([^"]*)" role="img" aria-label="([^"]*)">'
                                          r'<b aria-hidden="true">([^<]*)</b>', card),
                # The name line is optional, so it is captured as an optional
                # group: a renderer that drops or adds one shows up as drift.
                "callouts": re.findall(r'data-anchor="([a-z-]+)" data-marker="(\d+)"><dt>'
                                       r'<b class="explainer-marker">(.*?)</b>'
                                       r'<span class="explainer-label">(.*?)</span>'
                                       r'(?:<span class="explainer-fullname">(.*?)</span>)?'
                                       r'<span class="explainer-reading">(.*?)</span></dt><dd>(.*?)</dd>', card, re.S),
                "legend": re.findall(r'<li class="explainer-legend-item" data-tone="([a-z]+)">'
                                     r"<b>(.*?)</b><span>(.*?)</span></li>", card),
                "usage": re.search(r'<section class="explainer-usage" aria-label="(.*?)">\s*'
                                   r'<div class="explainer-usage-kicker">(.*?)</div>\s*'
                                   r"<h5>(.*?)</h5>\s*"
                                   r'<p class="explainer-usage-summary">(.*?)</p>\s*'
                                   r'<ol class="explainer-usage-steps" data-layout="([a-z-]+)">',
                                   card, re.S).groups(),
                "steps": re.findall(USAGE_STEP_RE, card, re.S),
                "scenario": re.search(r'<section class="explainer-scenario" aria-label="(.*?)">\s*'
                                      r'<div class="explainer-scenario-kicker">(.*?)</div>\s*'
                                      r"<h5>(.*?)</h5>\s*"
                                      r'<p class="explainer-scenario-summary">(.*?)</p>\s*'
                                      r'<ol class="explainer-scenario-steps">',
                                      card, re.S).groups(),
                "scenarioSteps": scenario_steps(card),
                "notes": card.count("explainer-note"),
            }
        static, hydrated = slots(self.static_detail), slots(self.dom)
        self.assertEqual(static, hydrated)
        self.assertEqual(len(static["callouts"]), len(TUTORIAL_ITEMS))
        self.assertEqual(len(static["hotspots"]), len(TUTORIAL_ITEMS))
        self.assertEqual(len(static["annotations"]), len(ANNOTATIONS))
        self.assertEqual(len(static["legend"]), len(LEGEND))
        self.assertEqual(len(static["steps"]), len(USAGE_STEPS))
        self.assertEqual(len(static["scenarioSteps"]), len(SCENARIO_STEPS))
        self.assertEqual(static["notes"], 0)

    def test_hydration_replaces_the_fallback_so_cards_cannot_double(self):
        # The browser renderer assigns innerHTML on the same container the static
        # markup was injected into, so the fallback is discarded, never appended.
        renderer = (ROOT / "magazine/magazine.js").read_text(encoding="utf-8")
        self.assertIn("articleRoot.innerHTML = ", renderer)
        self.assertNotIn("articleRoot.insertAdjacentHTML", renderer)
        self.assertNotIn("articleRoot.append", renderer)
        self.assertEqual(self.dom.count(CARD), 1)

    def test_other_explainer_layouts_are_untouched(self):
        # The default (code-zero) and image (lazy-bag) explainers must keep the
        # plain <dl> shape — the tutorial branch is opt-in via layout.
        for slug, theme in (("wangsan-to-yeosu-island-delivery-2026", ""),
                            ("shootingstar-club-tuning-2026", " explainer-lazybag")):
            rendered = json.loads(subprocess.run(
                [self.node, "-e", RENDER, str(ROOT), slug], check=True, capture_output=True, text=True
            ).stdout)["html"]
            aside = f'<aside class="article-explainer{theme}"'
            self.assertEqual(rendered.count(aside), 1, slug)
            card = rendered.partition(aside)[2].partition("</aside>")[0]
            self.assertIn("<dl>", card)
            self.assertNotIn("explainer-tutorial", card)
            self.assertNotIn("explainer-hotspot", card)
            self.assertNotIn("explainer-scenario", card)
        self.assertIn('<figure class="explainer-media">', rendered)


if __name__ == "__main__":
    unittest.main()
