#!/usr/bin/env node
// Inject crawler-facing semantic static HTML into a *copied* build root's
// magazine pages. The browser bundle (magazine.js) re-renders the same
// containers (#magazineMasonry, #articleRoot) on load, so this static markup
// is a progressive-enhancement / SEO fallback only — never the live UI.
//
// Usage:
//   node scripts/render_magazine_static.mjs <buildRoot> [--check]
//
//   <buildRoot>  Directory that already contains a full copy of the site
//                (magazine/articles.js + magazine/index.html + slug dirs).
//                Run against the copied build root, NOT the source tree.
//   --check      Dry run: validate that every article resolves to an
//                injectable page, but do not write any files.
//
// Exits non-zero on any failure so the caller can abort the deploy and keep
// the previous published tree intact.

import { readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import vm from 'node:vm';

function fail(message) {
  console.error(`render_magazine_static: ${message}`);
  process.exit(1);
}

const args = process.argv.slice(2);
const check = args.includes('--check');
const buildRoot = args.find(arg => !arg.startsWith('--'));
if (!buildRoot) fail('missing <buildRoot> argument');
const root = resolve(buildRoot);

// --- HTML escaping -----------------------------------------------------------
const ESCAPE = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, ch => ESCAPE[ch]);

// --- Load articles safely via a sandboxed vm context -------------------------
function loadArticles() {
  const source = readFileSync(join(root, 'magazine', 'articles.js'), 'utf8');
  const sandbox = { window: {} };
  vm.createContext(sandbox);
  try {
    new vm.Script(source, { filename: 'articles.js' }).runInContext(sandbox, { timeout: 5000 });
  } catch (error) {
    fail(`could not evaluate magazine/articles.js: ${error.message}`);
  }
  const articles = sandbox.window.ASTERIA_ARTICLES;
  if (!Array.isArray(articles) || articles.length === 0) {
    fail('magazine/articles.js did not define a non-empty window.ASTERIA_ARTICLES');
  }
  return articles;
}

// --- Replace the inner HTML of the element carrying a given id ---------------
// The target containers are always empty in source (<section ...></section>,
// <div ...></div>), so a lazy match to the first matching close tag is safe.
function injectInto(html, id, inner, file) {
  const re = new RegExp(`(<(\\w+)[^>]*\\bid="${id}"[^>]*>)([\\s\\S]*?)(</\\2>)`);
  const match = html.match(re);
  if (!match) fail(`element #${id} not found in ${file}`);
  return html.slice(0, match.index) + match[1] + '\n' + inner + '\n' + match[4]
    + html.slice(match.index + match[0].length);
}

// --- Static markup builders --------------------------------------------------
function cardMarkup(article) {
  const heading = esc(article.title);
  const subheading = esc(article.titleKo);
  return `<a class="pin-card" href="${esc(article.url)}" aria-label="${subheading} 에피소드 열기">`
    + `<div class="pin-media"><img src="${esc(article.image)}" alt="${esc(article.imageAlt || article.titleKo)}" loading="lazy">`
    + `<span class="pin-category">${esc(article.category)}</span></div>`
    + `<div class="pin-body"><time datetime="${esc(article.publishedAt)}">${esc(article.publishedAt)}</time>`
    + `<h2>${heading}</h2><h3>${subheading}</h3><p>${esc(article.excerpt)}</p>`
    + `<span class="pin-arrow">Read Episode →</span></div></a>`;
}

function masonryMarkup(articles) {
  return articles.map(cardMarkup).join('\n');
}

function articleMarkup(article) {
  const sections = (article.sections || []).map(section => {
    const body = (section.body || []).map(paragraph => `<p>${esc(paragraph)}</p>`).join('');
    return `<section class="article-section">${section.kicker ? `<div class="section-kicker">${esc(section.kicker)}</div>` : ''}`
      + `<h3>${esc(section.heading)}</h3>${body}</section>`;
  }).join('\n');
  return `<article>`
    + `<header class="article-head"><div class="article-meta"><span>${esc(article.category)}</span>`
    + `<time datetime="${esc(article.eventDate || article.publishedAt)}">${esc(article.eventDate || article.publishedAt)}</time></div>`
    + `<h1>${esc(article.title)}</h1><h2>${esc(article.titleKo)}</h2></header>`
    + `<div class="article-hero"><img src="${esc(article.image)}" alt="${esc(article.imageAlt || article.titleKo)}"></div>`
    + `<div class="article-content"><p class="article-lead">${esc(article.lead)}</p>`
    + `<div class="article-body">${sections}</div></div></article>`;
}

// --- Run ---------------------------------------------------------------------
const articles = loadArticles();
const ordered = [...articles].sort((a, b) => String(b.publishedAt).localeCompare(String(a.publishedAt)));

// Magazine index masonry.
const indexPath = join(root, 'magazine', 'index.html');
let indexHtml;
try {
  indexHtml = readFileSync(indexPath, 'utf8');
} catch (error) {
  fail(`could not read ${indexPath}: ${error.message}`);
}
const nextIndexHtml = injectInto(indexHtml, 'magazineMasonry', masonryMarkup(ordered), 'magazine/index.html');

// Per-article pages.
const pages = [];
for (const article of articles) {
  if (!article.slug) fail(`article missing slug: ${JSON.stringify(article.title || article.url)}`);
  const pagePath = join(root, 'magazine', article.slug, 'index.html');
  let pageHtml;
  try {
    pageHtml = readFileSync(pagePath, 'utf8');
  } catch (error) {
    fail(`could not read article page for slug "${article.slug}": ${error.message}`);
  }
  const nextPageHtml = injectInto(pageHtml, 'articleRoot', articleMarkup(article), `magazine/${article.slug}/index.html`);
  pages.push({ pagePath, nextPageHtml, slug: article.slug });
}

if (check) {
  console.log(`render_magazine_static: --check OK (${ordered.length} cards, ${pages.length} article pages)`);
  process.exit(0);
}

writeFileSync(indexPath, nextIndexHtml);
for (const page of pages) writeFileSync(page.pagePath, page.nextPageHtml);
console.log(`render_magazine_static: injected ${ordered.length} cards + ${pages.length} article pages under ${root}`);
