#!/usr/bin/env python3
"""Build the allow-listed static tree served by the Asteria production origin."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
DEST = Path.home() / ".local/share/asteria-web-public"
NEXT = DEST.with_name(DEST.name + ".next")
OLD = DEST.with_name(DEST.name + ".old")
FILES = ("index.html", "favicon.ico", "robots.txt", "sitemap.xml")
DIRS = ("images", "magazine", "veronica")

for path in (NEXT, OLD):
    if path.exists():
        shutil.rmtree(path)
NEXT.mkdir(parents=True)
for name in FILES:
    shutil.copy2(ROOT / name, NEXT / name)
for name in DIRS:
    shutil.copytree(ROOT / name, NEXT / name)

if DEST.exists():
    DEST.rename(OLD)
NEXT.rename(DEST)
if OLD.exists():
    shutil.rmtree(OLD)

print(f"public_root={DEST}")
print("allowlisted_files=4")
print("allowlisted_directories=3")
