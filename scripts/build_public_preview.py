# task_id: t_082b0f7a
# 구현자: 덱[제품개발자·아스테리아자동화]
# 검증자: 레벨스 플러스[CEO·기업경영]
"""Build the production allowlist into a new isolated preview directory."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ("index.html", "favicon.ico", "robots.txt", "sitemap.xml")
DIRS = ("images", "magazine", "veronica")
PAGE_FILES = ("racing-crew/index.html",)
PAGE_DIRS = ("racing-crew/spinnaker-takedown",)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    destination = args.destination.expanduser().resolve()
    if destination.exists():
        raise SystemExit(f"preview destination already exists: {destination}")
    destination.mkdir(parents=True)
    for name in FILES:
        shutil.copy2(ROOT / name, destination / name)
    for name in DIRS:
        shutil.copytree(ROOT / name, destination / name)
    for name in PAGE_FILES:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    for name in PAGE_DIRS:
        shutil.copytree(ROOT / name, destination / name)
    node = shutil.which("node")
    if node is None:
        raise SystemExit("build_public_preview: node not found")
    subprocess.run([node, str(ROOT / "scripts/generate_sitemap.mjs"), str(destination)], check=True)
    subprocess.run([node, str(ROOT / "scripts/render_magazine_static.mjs"), str(destination)], check=True)
    subprocess.run([sys.executable, str(ROOT / "scripts/test_static_seo.py"), str(destination)], check=True)
    from prepare_site_analytics import prepare
    prepare(destination)
    print(f"preview_root={destination}")
    print(f"allowlisted_files={len(FILES) + len(PAGE_FILES)}")
    print(f"allowlisted_directories={len(DIRS) + len(PAGE_DIRS)}")


if __name__ == "__main__":
    main()
