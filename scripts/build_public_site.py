#!/usr/bin/env python3
"""Build the allow-listed static tree served by the Asteria production origin."""
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "scripts" / "render_magazine_static.mjs"
SITEMAP_GENERATOR = ROOT / "scripts" / "generate_sitemap.mjs"
SEO_TEST = ROOT / "scripts" / "test_static_seo.py"
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

# Inject crawler-facing static HTML into the copied magazine pages before the
# atomic swap. If Node is missing or the renderer fails, abort here: NEXT is
# left in place unswapped and the currently published DEST stays untouched.
node = shutil.which("node")
if node is None:
    sys.exit("build_public_site: 'node' not found on PATH; magazine static render aborted")
# Generate from the copied articles.js so sitemap membership and evidence-based
# lastmod values cannot drift from the public episode data.
subprocess.run([node, str(SITEMAP_GENERATOR), str(NEXT)], check=True)
subprocess.run([node, str(RENDERER), str(NEXT)], check=True)
# Validate the exact copied/rendered tree before the atomic swap. A metadata,
# canonical, JSON-LD, sitemap, or no-JS regression must never replace live.
subprocess.run([sys.executable, str(SEO_TEST), str(NEXT)], check=True)

if DEST.exists():
    DEST.rename(OLD)
NEXT.rename(DEST)
if OLD.exists():
    shutil.rmtree(OLD)

print(f"public_root={DEST}")
print("allowlisted_files=4")
print("allowlisted_directories=3")
