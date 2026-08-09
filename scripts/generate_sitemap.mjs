#!/usr/bin/env node
// Generate or validate sitemap.xml from the public page set and articles.js.
import { readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import vm from 'node:vm';

function fail(message) {
  console.error(`generate_sitemap: ${message}`);
  process.exit(1);
}

const args = process.argv.slice(2);
const check = args.includes('--check');
const rootArg = args.find(arg => !arg.startsWith('--')) || '.';
const root = resolve(rootArg);
const articlesPath = join(root, 'magazine', 'articles.js');
const sitemapPath = join(root, 'sitemap.xml');
const ORIGIN = 'https://asteria.club';

function loadArticles() {
  const sandbox = { window: {} };
  vm.createContext(sandbox);
  try {
    new vm.Script(readFileSync(articlesPath, 'utf8'), { filename: articlesPath })
      .runInContext(sandbox, { timeout: 5000 });
  } catch (error) {
    fail(`could not evaluate ${articlesPath}: ${error.message}`);
  }
  const articles = sandbox.window.ASTERIA_ARTICLES;
  if (!Array.isArray(articles) || articles.length === 0) fail('articles.js must define a non-empty article array');
  return articles;
}

const esc = value => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const articles = loadArticles();
const seen = new Set();
for (const article of articles) {
  if (!article.slug || !article.url || !article.publishedAt) fail(`article missing slug, url, or publishedAt: ${article.titleKo || 'unknown'}`);
  if (article.url !== `/magazine/${article.slug}/`) fail(`article URL does not match slug: ${article.slug}`);
  if (seen.has(article.url)) fail(`duplicate article URL: ${article.url}`);
  seen.add(article.url);
  for (const [field, value] of [['publishedAt', article.publishedAt], ['modifiedAt', article.modifiedAt || article.publishedAt]]) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || Number.isNaN(Date.parse(`${value}T00:00:00Z`))) {
      fail(`${article.slug} has invalid ${field}: ${value}`);
    }
  }
}

const ordered = [...articles].sort((a, b) => String(b.publishedAt).localeCompare(String(a.publishedAt)));
const rows = [
  '  <url>',
  `    <loc>${ORIGIN}/</loc>`,
  '    <changefreq>weekly</changefreq>',
  '    <priority>1.0</priority>',
  '  </url>',
  '  <url>',
  `    <loc>${ORIGIN}/magazine/</loc>`,
  '    <changefreq>weekly</changefreq>',
  '    <priority>0.8</priority>',
  '  </url>',
];
for (const article of ordered) {
  rows.push(
    '  <url>',
    `    <loc>${esc(ORIGIN + article.url)}</loc>`,
    `    <lastmod>${article.modifiedAt || article.publishedAt}</lastmod>`,
    '    <changefreq>monthly</changefreq>',
    '    <priority>0.7</priority>',
    '  </url>',
  );
}
const output = `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${rows.join('\n')}\n</urlset>\n`;

if (check) {
  let current;
  try {
    current = readFileSync(sitemapPath, 'utf8');
  } catch (error) {
    fail(`could not read ${sitemapPath}: ${error.message}`);
  }
  if (current !== output) fail(`stale sitemap: run node scripts/generate_sitemap.mjs ${root}`);
  console.log(`generate_sitemap: --check OK (${ordered.length} article URLs)`);
} else {
  writeFileSync(sitemapPath, output);
  console.log(`generate_sitemap: wrote ${sitemapPath} (${ordered.length} article URLs)`);
}
