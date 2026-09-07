#!/usr/bin/env python3
"""Bump the magazine asset cache keys across every HTML entry point at once.

Nine HTML pages load magazine/articles.js, magazine/magazine.js and
magazine/magazine.css by `?v=` query. Editing them by hand is slow and easy to
get half-right, which ships a page that renders stale episode data. This walks
the repository, rewrites the requested asset's key everywhere it appears, and
reports the exact file count so a partial update is visible immediately.

Usage:
  python3 scripts/bump_magazine_cache.py articles=20260907-first-practice-r21
  python3 scripts/bump_magazine_cache.py articles=<key> magazine-js=<key> magazine-css=<key>
  python3 scripts/bump_magazine_cache.py --check articles=<key>

Exits non-zero when an asset is named that no page references, so a typo in the
asset name can never be mistaken for a completed bump.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = {
    "articles": "/magazine/articles.js",
    "magazine-js": "/magazine/magazine.js",
    "magazine-css": "/magazine/magazine.css",
}


def main(argv: list[str]) -> int:
    check = "--check" in argv
    pairs = [arg for arg in argv if not arg.startswith("--")]
    if not pairs:
        print(__doc__, file=sys.stderr)
        return 2

    updates: dict[str, str] = {}
    for pair in pairs:
        name, _, key = pair.partition("=")
        if name not in ASSETS or not key:
            print(f"bump_magazine_cache: expected <asset>=<key> with asset in {sorted(ASSETS)}, got {pair!r}", file=sys.stderr)
            return 2
        updates[ASSETS[name]] = key

    pages = sorted(path for path in ROOT.rglob("*.html") if ".git" not in path.parts)
    touched: dict[str, list[Path]] = {path: [] for path in updates}
    for page in pages:
        original = page.read_text(encoding="utf-8")
        text = original
        for asset, key in updates.items():
            pattern = re.compile(rf'({re.escape(asset)})\?v=[^"\']+')
            text, count = pattern.subn(rf'\g<1>?v={key}', text)
            if count:
                touched[asset].append(page)
        if text != original and not check:
            page.write_text(text, encoding="utf-8")

    status = 0
    for asset, key in updates.items():
        files = touched[asset]
        if not files:
            print(f"bump_magazine_cache: no page references {asset}", file=sys.stderr)
            status = 1
            continue
        verb = "would update" if check else "updated"
        print(f"{verb} {asset}?v={key} in {len(files)} pages")
        for page in files:
            print(f"  {page.relative_to(ROOT)}")
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
