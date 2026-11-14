"""Add first-party analytics only to a caller-provided, already-built tree."""
import argparse
import hashlib
import html
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def prepare(destination: Path):
    destination = destination.resolve(strict=True)
    source = ROOT / 'site-analytics.js'
    version = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
    shutil.copy2(source, destination / source.name)
    pages = [destination / 'index.html', *sorted((destination / 'magazine').rglob('index.html'))]
    alias = destination / 'magazine/article.html'
    catalog = {}
    for page in pages:
        if not page.is_file():
            continue
        relative = page.relative_to(destination).as_posix()
        path = '/' if relative == 'index.html' else '/' + relative.removesuffix('index.html')
        text = page.read_text()
        match = re.search(r'<title>(.*?)</title>', text, re.S)
        title = html.unescape(match[1]).split(' | ')[0].strip() if match else path
        catalog[path] = title
    for page in [*pages, alias]:
        if not page.is_file():
            continue
        text = page.read_text()
        text = re.sub(r'\s*<script\b[^>]*src=[\"\']/site-analytics\.js[^\"\']*[\"\'][^>]*></script>', '', text)
        script = f'\n<script defer src="/site-analytics.js?v={version}"></script>\n'
        if '</body>' not in text:
            raise ValueError(f'missing body end: {page}')
        page.write_text(text.replace('</body>', script + '</body>'))
    (destination / 'analytics-catalog.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n')
    print(f'analytics_pages={len(catalog)} episodes={len(catalog)-2} client_version={version}')
    return catalog


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('destination', type=Path)
    prepare(parser.parse_args().destination)
