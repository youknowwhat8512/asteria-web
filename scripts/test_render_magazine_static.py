#!/usr/bin/env python3
"""Focused test for scripts/render_magazine_static.mjs.

Copies the real magazine tree into an isolated temp build root, runs the
renderer, and asserts crawler-facing static HTML was injected without
disturbing the browser hydration hooks. Run:

    python3 scripts/test_render_magazine_static.py
"""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "scripts" / "render_magazine_static.mjs"


def load_slugs(articles_js: str):
    return re.findall(r'slug:\s*"([^"]+)"', articles_js)


def main() -> int:
    node = shutil.which("node")
    if node is None:
        print("SKIP: node not found on PATH")
        return 0

    articles_js = (ROOT / "magazine" / "articles.js").read_text(encoding="utf-8")
    slugs = load_slugs(articles_js)
    assert slugs, "expected at least one article slug in source articles.js"

    with tempfile.TemporaryDirectory(prefix="asteria-render-test-") as tmp:
        build_root = Path(tmp)
        shutil.copytree(ROOT / "magazine", build_root / "magazine")

        # --check must succeed without mutating files.
        before = (build_root / "magazine" / "index.html").read_text(encoding="utf-8")
        subprocess.run([node, str(RENDERER), str(build_root), "--check"], check=True)
        after_check = (build_root / "magazine" / "index.html").read_text(encoding="utf-8")
        assert before == after_check, "--check must not modify files"

        # Real run.
        subprocess.run([node, str(RENDERER), str(build_root)], check=True)

        index_html = (build_root / "magazine" / "index.html").read_text(encoding="utf-8")
        # Masonry container still present (hydration hook intact) and now non-empty.
        masonry = re.search(
            r'<section[^>]*id="magazineMasonry"[^>]*>([\s\S]*?)</section>', index_html
        )
        assert masonry, "magazineMasonry container missing after render"
        assert "<h2>" in masonry.group(1), "expected static card headings in masonry"
        assert 'id="magazineMasonry"' in index_html, "hydration hook id must be preserved"

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

        # Escaping: any '&' in output cards must be part of an entity, never bare.
        assert not re.search(r'&(?!amp;|lt;|gt;|quot;|#39;|#\d+;|[a-zA-Z]+;)', masonry.group(1)), \
            "unescaped ampersand found in masonry markup"

    print(f"OK: rendered {len(slugs)} article pages + masonry, escaping and hooks verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
