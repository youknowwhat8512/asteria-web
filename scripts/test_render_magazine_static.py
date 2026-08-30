#!/usr/bin/env python3
"""Focused test for scripts/render_magazine_static.mjs.

Copies the real magazine tree into an isolated temp build root, runs the
renderer, and asserts crawler-facing static HTML was injected without
disturbing the browser hydration hooks. Run:

    python3 scripts/test_render_magazine_static.py
"""
from pathlib import Path
import json
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "scripts" / "render_magazine_static.mjs"


def load_slugs(articles_js: str):
    return re.findall(r'slug:\s*"([^"]+)"', articles_js)


# Ask the data itself how many explainers each episode declares, so this stays
# true as episodes come and go instead of pinning today's slugs.
EXPLAINER_CENSUS = r"""
const fs = require('fs'), vm = require('vm');
const sandbox = { window: {} };
vm.createContext(sandbox);
new vm.Script(fs.readFileSync(process.argv[1], 'utf8')).runInContext(sandbox);
process.stdout.write(JSON.stringify(Object.fromEntries(sandbox.window.ASTERIA_ARTICLES.map(a => [
  a.slug,
  (a.sections || []).filter(s => s.explainer).map(s => ({
    theme: s.explainer.theme || null,
    layout: s.explainer.layout || null,
    items: (s.explainer.items || []).length,
    hasImage: !!s.explainer.image,
    tones: (s.explainer.legend || []).map(entry => entry.tone || null),
    rings: (((s.explainer.image || {}).annotations) || []).map(entry => entry.tone || null),
    usageLayout: (s.explainer.usage || {}).layout || null,
    usageSteps: ((s.explainer.usage || {}).steps || []).length,
    usageTones: (((s.explainer.usage || {}).steps) || []).map(step => step.tone || null),
    scenarioSteps: ((s.explainer.scenario || {}).steps || []).length,
    scenarioTones: (((s.explainer.scenario || {}).steps) || []).map(step => step.tone || null),
    scenarioChips: (((s.explainer.scenario || {}).steps) || []).map(step => (step.chips || []).length),
  })),
]))));
"""

# Tones are one allowlist in both renderers, shared by the legend rows and the
# rings drawn on an explainer image; anything else must be dropped rather than
# reflected into the markup.
ACCENT_TONES = ("yellow", "red")
# The reading-map usage layout tints its cards from its own, wider allowlist —
# a mapping tone must never reach a ring, and vice versa.
READING_MAP = "reading-map"
USAGE_TONES = ("yellow", "red", "cyan")
# The optional scenario panel is a sibling of the usage panel and tints its
# steps from that same allowlist, on its own selectors — a scenario tone must
# never reach a ring either.
SCENARIO_STEP_RE = (r'<li class="explainer-scenario-step"(?: data-tone="([a-z]+)")?>'
                    r'<b class="scenario-marker">.*?</b><b class="scenario-title">.*?</b>'
                    r'(.*?)<span class="scenario-body">.*?</span></li>')


def explainer_census(node: str, articles_path: Path):
    out = subprocess.run([node, "-e", EXPLAINER_CENSUS, str(articles_path)],
                         check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def main() -> int:
    node = shutil.which("node")
    if node is None:
        print("SKIP: node not found on PATH")
        return 0

    articles_js = (ROOT / "magazine" / "articles.js").read_text(encoding="utf-8")
    slugs = load_slugs(articles_js)
    assert slugs, "expected at least one article slug in source articles.js"

    # injectInto() lazily matches to the first closing tag, which is only safe
    # while the source containers are empty. Committing rendered HTML back into
    # the tree makes the next build nest a second copy of every card inside the
    # first — visible duplicates. Keep the source hooks empty.
    for page in [ROOT / "magazine" / "index.html"] + [ROOT / "magazine" / s / "index.html" for s in slugs]:
        html = page.read_text(encoding="utf-8")
        for hook in ("articleRoot", "magazineMasonry"):
            empty = re.search(rf'<(\w+)[^>]*\bid="{hook}"[^>]*>\s*</\1>', html)
            assert empty or f'id="{hook}"' not in html, \
                f"{page.name}: #{hook} must ship empty; rendered HTML belongs in the build root only"
        # Corollary: no rendered card fragment may be checked in either.
        for marker in ("explainer-usage", "explainer-legend-item", "explainer-hotspot", "<article>"):
            assert marker not in html, \
                f"{page.name}: rendered fragment {marker!r} committed into the source tree"

    with tempfile.TemporaryDirectory(prefix="asteria-render-test-") as tmp:
        build_root = Path(tmp)
        shutil.copytree(ROOT / "magazine", build_root / "magazine")

        # --check must succeed without mutating files.
        before = (build_root / "magazine" / "index.html").read_text(encoding="utf-8")
        subprocess.run([node, str(RENDERER), str(build_root), "--check"], check=True)
        after_check = (build_root / "magazine" / "index.html").read_text(encoding="utf-8")
        assert before == after_check, "--check must not modify files"

        # Real run.
        # Include a hostile script-closing sequence to prove JSON-LD remains
        # inside its script element and the rendered HTML is not corrupted.
        hostile = "crawler guard </script><script>alert(1)</script>"
        articles_path = build_root / "magazine" / "articles.js"
        hostile_source = articles_path.read_text(encoding="utf-8")
        first_excerpt = re.search(r'excerpt:\s*"([^"]+)"', hostile_source)
        assert first_excerpt, "expected an excerpt fixture in articles.js"
        hostile_source = (
            hostile_source[: first_excerpt.start(1)]
            + hostile
            + hostile_source[first_excerpt.end(1) :]
        )
        # Poison every slot of the first explainer too. Real copy rarely carries
        # markup in these fields, so without this the escaping of a card slot
        # would only be proven by accident.
        explainer_at = hostile_source.index("explainer: {")
        head, tail = hostile_source[:explainer_at], hostile_source[explainer_at:]
        # Slot poisoning is region-scoped: the optional usage panel reuses the
        # kicker/title/summary field names, so a plain count=1 sweep over the
        # whole tail would never reach it.
        image_at, items_at = tail.index("image: {"), tail.index("items: [")
        legend_at = tail.index("legend: [")
        usage_at, steps_at = tail.index("usage: {"), tail.index("steps: [")
        scenario_at = tail.index("scenario: {")
        scenario_steps_at = tail.index("steps: [", scenario_at)
        regions = [tail[:image_at], tail[image_at:items_at], tail[items_at:legend_at],
                   tail[legend_at:usage_at], tail[usage_at:steps_at], tail[steps_at:scenario_at],
                   tail[scenario_at:scenario_steps_at], tail[scenario_steps_at:]]
        poisoned_slots = 0
        for region_index, fields in enumerate((
            ("kicker", "title", "summary"),                    # explainer head
            ("label",),                                        # first ring label
            ("label", "value"),                                # first callout
            (),                                                # legend: tone only, below
            ("kicker", "title", "summary"),                    # usage panel
            ("marker", "code", "metric", "action", "body"),    # first mapping card
            ("kicker", "title", "summary"),                    # scenario panel
            ("marker", "title", "body"),                       # first scenario step
        )):
            for field in fields:
                regions[region_index], hits = re.subn(
                    rf'{field}: "[^"]*"', f'{field}: "{hostile}"', regions[region_index], count=1)
                assert hits == 1, f"expected an explainer {field} fixture to poison"
                poisoned_slots += 1
        # And off-allowlist tones that would break out of the attribute — one on
        # a ring (whole ring must vanish), one on a legend row and one on every
        # mapping card (the row/card survives, the attribute does not). Every
        # yellow goes, so any surviving yellow anywhere is a leak.
        regions[7], hits = re.subn(r'chips: \["[^"]*"', f'chips: ["{hostile}"', regions[7], count=1)
        assert hits == 1, "expected a scenario chip fixture to poison"
        poisoned_slots += 1
        for region_index, what, count in ((1, "a ring", 1), (3, "a legend", 1),
                                          (5, "a mapping card", 0), (7, "a scenario step", 0)):
            regions[region_index], hits = re.subn(
                r'tone: "yellow"', r'tone: "red\\" onload=alert(1) x=\\""',
                regions[region_index], count=count)
            assert hits >= 1, f"expected {what} tone fixture to poison"
        # A ring position that would escape the style attribute must be clamped.
        regions[1], hits = re.subn(r"x: [\d.]+, y: [\d.]+, diameter: \d+",
                                   'x: "9\\" onload=alert(1) x=\\"", y: -40, diameter: 99999',
                                   regions[1], count=1)
        assert hits == 1, "expected a ring coordinate fixture to poison"
        hostile_source = head + "".join(regions)
        # A second explainer gets a plain, layout-less usage panel, so the
        # default step renderer is proven to still work beside the reading map.
        sibling = hostile_source.index("explainer: {", explainer_at + 1)
        hostile_source = (hostile_source[:sibling] + 'explainer: {\n          usage: { '
                          'kicker: "SIDE", title: "곁들인 패널", summary: "기본 패널은 그대로입니다.", '
                          'steps: [{ title: "기본 단계", body: "제목과 본문만 있는 기존 단계입니다." }] },'
                          + hostile_source[sibling + len("explainer: {"):])
        articles_path.write_text(hostile_source, encoding="utf-8")
        subprocess.run([node, str(RENDERER), str(build_root)], check=True)

        index_html = (build_root / "magazine" / "index.html").read_text(encoding="utf-8")
        # Masonry container still present (hydration hook intact) and now non-empty.
        masonry = re.search(
            r'<section[^>]*id="magazineMasonry"[^>]*>([\s\S]*?)</section>', index_html
        )
        assert masonry, "magazineMasonry container missing after render"
        assert "<h2>" in masonry.group(1), "expected static card headings in masonry"
        assert 'id="magazineMasonry"' in index_html, "hydration hook id must be preserved"
        assert hostile not in index_html, "raw hostile JSON-LD/content sequence leaked into HTML"

        hostile_page = (build_root / "magazine" / slugs[0] / "index.html").read_text(encoding="utf-8")
        assert hostile not in hostile_page, "raw hostile sequence leaked into article HTML"
        assert "\\u003c/script>" in hostile_page, "article JSON-LD must escape script-closing sequences"

        # The poisoned card must reach the crawler HTML, and arrive inert.
        escaped_hostile = (hostile.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        poisoned = re.search(r'<aside class="article-explainer[\s\S]*?</aside>', hostile_page)
        assert poisoned, "poisoned explainer never reached the crawler HTML"
        card = poisoned.group(0)
        assert card.count(escaped_hostile) >= poisoned_slots, \
            f"every poisoned explainer slot must be present and escaped ({poisoned_slots} expected)"
        assert hostile not in card, "raw hostile sequence leaked into an explainer"
        assert "<script" not in card.lower(), "live script leaked into an explainer"
        # The usage panel is a slot like any other: kicker, heading, summary and
        # every slot of a mapping card must all arrive inert.
        usage = re.search(r'<section class="explainer-usage"[\s\S]*?</section>', card)
        assert usage, "poisoned usage panel never reached the crawler HTML"
        assert usage.group(0).count(escaped_hostile) >= 8, \
            "usage kicker/title/summary and card marker/code/metric/action/body must be escaped"
        assert hostile not in usage.group(0), "raw hostile sequence leaked into the usage panel"
        # The scenario panel is a slot like any other: its head and every slot
        # of a step — chips included — must arrive inert.
        scenario = re.search(r'<section class="explainer-scenario"[\s\S]*?</section>', card)
        assert scenario, "poisoned scenario panel never reached the crawler HTML"
        assert scenario.group(0).count(escaped_hostile) >= 7, \
            "scenario kicker/title/summary and step marker/title/chip/body must be escaped"
        assert hostile not in scenario.group(0), "raw hostile sequence leaked into the scenario panel"
        assert scenario.group(0).count('<li class="explainer-scenario-step"') == 4, \
            "a dropped tone must not drop the scenario step"
        assert 'data-tone="yellow"' not in card, "an off-allowlist tone must be dropped, not reflected"
        # A poisoned tone drops the attribute, never the card: the mapping panel
        # still carries one card per reading.
        assert usage.group(0).count('<li class="explainer-usage-step"') == 5, \
            "a dropped tone must not drop the card"
        # Untouched tones still render: one ring, one legend row, two mapping
        # cards and the scenario's first step keep red; the boat-speed mapping
        # card and the scenario's last step keep the panel-only cyan.
        assert card.count('data-tone="red"') == 5, "the untouched tones must still render"
        assert card.count('data-tone="cyan"') == 2, "the panel tone allowlist must keep cyan"
        assert "onload" not in card, "a tone must never escape into the attribute list"
        # An off-allowlist ring is dropped whole, not styled and not reflected.
        rings = re.findall(r'<span class="explainer-annotation"[\s\S]*?</span>', card)
        assert len(rings) == 1, f"one ring must survive the poisoned tone, got {len(rings)}"
        style = re.search(r'style="left:([\d.]+)%;top:([\d.]+)%;--ring-size:([\d.]+)%"', rings[0])
        assert style, f"ring style must stay a plain set of percentages: {rings[0]}"
        left, top, size = (float(value) for value in style.groups())
        assert 0 <= left <= 100 and 0 <= top <= 100 and 2 <= size <= 40, style.groups()

        # Census the data the pages were actually rendered from — the poisoned
        # copy — so slot-level expectations (tones especially) stay honest.
        census = explainer_census(node, articles_path)
        for slug in slugs:
            page = (build_root / "magazine" / slug / "index.html").read_text(encoding="utf-8")
            open_match = re.search(r'<div[^>]*id="articleRoot"[^>]*>', page)
            assert open_match, f"articleRoot container missing for {slug}"
            # Everything the renderer injected sits after the container open tag.
            inner = page[open_match.end():]
            assert "<article>" in inner, f"expected static <article> for {slug}"
            assert "<h1>" in inner, f"expected static <h1> for {slug}"
            assert 'class="article-lead"' in inner, f"expected static lead <p> for {slug}"
            assert f'data-article-slug="{slug}"' in page, "hydration slug hook must be preserved"
            # Article body strings must be escaped: no live <script> leaked in.
            body_region = inner[: inner.find("</article>")]
            assert "<script" not in body_region.lower(), f"unexpected script injected for {slug}"

            # Explainer parity: every card the data declares reaches the no-JS
            # body, with the same slots the browser renderer emits, all escaped.
            declared = census.get(slug, [])
            asides = re.findall(r'<aside class="article-explainer[^"]*"[\s\S]*?</aside>', body_region)
            assert len(asides) == len(declared), \
                f"{slug}: {len(declared)} explainer(s) declared, {len(asides)} rendered"
            for aside, spec in zip(asides, declared):
                expected_class = ("article-explainer"
                                  + (f" explainer-{spec['theme']}" if spec["theme"] else "")
                                  + (f" explainer-layout-{spec['layout']}" if spec["layout"] else ""))
                assert f'<aside class="{expected_class}"' in aside, f"{slug}: wrong theme/layout class"
                assert "aria-label=" in aside, f"{slug}: explainer aside must be labelled"
                # "<dl" not "<dl>": a layout may class its list (the device
                # tutorial does); the dt/dd count below is the real guard.
                for slot in ('<div class="explainer-kicker">', "<h4>", '<p class="explainer-summary">', "<dl"):
                    assert slot in aside, f"{slug}: explainer missing {slot}"
                assert aside.count("<dt>") == spec["items"] == aside.count("<dd>"), \
                    f"{slug}: expected {spec['items']} dt/dd pairs"
                # A layout picks its own figure hook; either way exactly one
                # figure appears when, and only when, the data declares an image.
                figure = ('<figure class="explainer-device">'
                          if spec["layout"] == "device-tutorial" else '<figure class="explainer-media">')
                assert aside.count(figure) == int(spec["hasImage"]), \
                    f"{slug}: explainer image slot mismatch"
                # Legend tones are optional and allowlisted: a row carries the
                # attribute when, and only when, the data names a known tone.
                found = [tone or None for tone in
                         re.findall(r'<li class="explainer-legend-item"(?: data-tone="([a-z]+)")?>', aside)]
                assert found == [t if t in ACCENT_TONES else None for t in spec["tones"]], \
                    f"{slug}: legend tone mismatch"
                # Image rings share that allowlist, but an unknown tone drops the
                # ring entirely — an explainer that declares none renders none.
                rings = re.findall(r'<span class="explainer-annotation" data-tone="([a-z]+)"', aside)
                assert rings == [t for t in spec["rings"] if t in ACCENT_TONES], \
                    f"{slug}: image ring mismatch"
                for ring in re.findall(r'<span class="explainer-annotation"[\s\S]*?</span>', aside):
                    assert re.search(r'style="left:[\d.]+%;top:[\d.]+%;--ring-size:[\d.]+%"', ring), \
                        f"{slug}: ring coordinates must be plain percentages"
                    assert 'role="img" aria-label="' in ring, f"{slug}: ring must be announced"
                # Optional usage panel: one semantic section with an ordered
                # list of exactly the steps the data declares, or nothing.
                assert aside.count('<section class="explainer-usage"') == int(spec["usageSteps"] > 0), \
                    f"{slug}: usage panel must appear only when steps are declared"
                assert aside.count('<li class="explainer-usage-step"') == spec["usageSteps"], \
                    f"{slug}: expected {spec['usageSteps']} usage step(s)"
                if spec["usageSteps"]:
                    for slot in ('<div class="explainer-usage-kicker">', "<h5>",
                                 '<p class="explainer-usage-summary">', '<ol class="explainer-usage-steps"'):
                        assert slot in aside, f"{slug}: usage panel missing {slot}"
                    assert re.search(r'<section class="explainer-usage" aria-label="[^"]+">', aside), \
                        f"{slug}: usage panel must be labelled"
                # The reading-map layout is opt-in per explainer: it swaps the
                # step for a mapping card and tints it from its own allowlist.
                # Any other usage keeps the plain title/body step.
                mapping = spec["usageLayout"] == READING_MAP
                assert aside.count(f'<ol class="explainer-usage-steps" data-layout="{READING_MAP}">') \
                    == int(mapping), f"{slug}: reading-map hook must follow the declared layout"
                cards = re.findall(r'<li class="explainer-usage-step"(?: data-tone="([a-z]+)")?>'
                                   r'<b class="usage-marker">.*?</b><b class="usage-code">.*?</b>'
                                   r'<span class="usage-metric">.*?</span>'
                                   r'<b class="usage-action">.*?</b>'
                                   r'<span class="usage-body">.*?</span></li>', aside, re.S)
                assert len(cards) == (spec["usageSteps"] if mapping else 0), \
                    f"{slug}: mapping cards must appear only under the reading-map layout"
                assert [tone or None for tone in cards] == \
                    [t if t in USAGE_TONES else None for t in (spec["usageTones"] if mapping else [])], \
                    f"{slug}: mapping tone mismatch"
                if not mapping:
                    assert aside.count('<li class="explainer-usage-step"><b>') == spec["usageSteps"], \
                        f"{slug}: a non-mapping usage must keep the plain title/body step"
                # Optional scenario panel: a sibling of the usage panel, always
                # after it, with exactly the steps and chips the data declares.
                assert aside.count('<section class="explainer-scenario"') == int(spec["scenarioSteps"] > 0), \
                    f"{slug}: scenario panel must appear only when steps are declared"
                assert aside.count('<li class="explainer-scenario-step"') == spec["scenarioSteps"], \
                    f"{slug}: expected {spec['scenarioSteps']} scenario step(s)"
                if spec["scenarioSteps"]:
                    for slot in ('<div class="explainer-scenario-kicker">', "<h5>",
                                 '<p class="explainer-scenario-summary">',
                                 '<ol class="explainer-scenario-steps">'):
                        assert slot in aside, f"{slug}: scenario panel missing {slot}"
                    assert re.search(r'<section class="explainer-scenario" aria-label="[^"]+">', aside), \
                        f"{slug}: scenario panel must be labelled"
                    assert aside.index('<section class="explainer-usage"') \
                        < aside.index('<section class="explainer-scenario"'), \
                        f"{slug}: the scenario must follow the usage panel"
                scenario_steps = re.findall(SCENARIO_STEP_RE, aside, re.S)
                assert [tone or None for tone, _ in scenario_steps] == \
                    [t if t in USAGE_TONES else None for t in spec["scenarioTones"]], \
                    f"{slug}: scenario tone mismatch"
                assert [len(re.findall(r'<span class="scenario-chip">', chips))
                        for _, chips in scenario_steps] == spec["scenarioChips"], \
                    f"{slug}: scenario chip mismatch"
                # Data strings may legitimately contain markup (the lazy-bag note
                # carries an <a>); in crawler HTML it must arrive inert.
                assert not re.search(r"<(a|script|img)\b", aside.replace('<img src="', "")), \
                    f"{slug}: unescaped markup leaked out of an explainer"

        # Escaping: any '&' in output cards must be part of an entity, never bare.
        assert not re.search(r'&(?!amp;|lt;|gt;|quot;|#39;|#\d+;|[a-zA-Z]+;)', masonry.group(1)), \
            "unescaped ampersand found in masonry markup"

    explainers = sum(len(v) for v in census.values())
    print(f"OK: rendered {len(slugs)} article pages + masonry, "
          f"{explainers} explainer card(s), escaping and hooks verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
