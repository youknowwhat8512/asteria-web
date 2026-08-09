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
const ORIGIN = 'https://asteria.club';

// --- HTML escaping -----------------------------------------------------------
const ESCAPE = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, ch => ESCAPE[ch]);
const regexEsc = value => String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const absolute = value => new URL(value, ORIGIN).href;

function replaceTitle(html, title) {
  if (!/<title>[\s\S]*?<\/title>/i.test(html)) fail('page is missing <title>');
  return html.replace(/<title>[\s\S]*?<\/title>/i, `<title>${esc(title)}</title>`);
}

function upsertMeta(html, attribute, key, content) {
  const pattern = new RegExp(`<meta\\s+[^>]*\\b${attribute}=["']${regexEsc(key)}["'][^>]*>`, 'i');
  const tag = `<meta ${attribute}="${esc(key)}" content="${esc(content)}">`;
  return pattern.test(html) ? html.replace(pattern, tag) : html.replace('</head>', `  ${tag}\n</head>`);
}

function upsertCanonical(html, canonical) {
  const pattern = /<link\s+[^>]*\brel=["']canonical["'][^>]*>/i;
  const tag = `<link rel="canonical" href="${esc(canonical)}">`;
  return pattern.test(html) ? html.replace(pattern, tag) : html.replace('</head>', `  ${tag}\n</head>`);
}

function replaceStructuredData(html, value, file) {
  const pattern = /<script\s+type=["']application\/ld\+json["'][^>]*>[\s\S]*?<\/script>/i;
  if (!pattern.test(html)) fail(`JSON-LD script not found in ${file}`);
  // JSON-LD lives inside an HTML <script> element. Escape '<' so article data
  // can never synthesize a closing </script> tag or corrupt the document.
  const serialized = JSON.stringify(value, null, 2).replace(/</g, '\\u003c');
  const tag = `<script type="application/ld+json">\n${serialized}\n  </script>`;
  return html.replace(pattern, tag);
}

function articleDate(value) {
  return `${value}T00:00:00+09:00`;
}

function collectionStructuredData(articles) {
  const items = articles.map((article, index) => ({
    '@type': 'ListItem',
    position: index + 1,
    url: absolute(article.url),
    name: article.titleKo,
  }));
  return {
    '@context': 'https://schema.org',
    '@graph': [
      {
        '@type': 'CollectionPage',
        '@id': `${ORIGIN}/magazine/#collection`,
        url: `${ORIGIN}/magazine/`,
        name: '아스테리아 매거진',
        description: '아스테리아의 세일링 훈련, 레이싱 도전, 크루 성장 이야기를 모은 매거진입니다.',
        inLanguage: 'ko-KR',
        isPartOf: { '@id': `${ORIGIN}/#website` },
        publisher: { '@id': `${ORIGIN}/#organization` },
        breadcrumb: { '@id': `${ORIGIN}/magazine/#breadcrumb` },
        mainEntity: { '@id': `${ORIGIN}/magazine/#episodes` },
      },
      {
        '@type': 'ItemList',
        '@id': `${ORIGIN}/magazine/#episodes`,
        name: '아스테리아 매거진 에피소드',
        numberOfItems: items.length,
        itemListOrder: 'https://schema.org/ItemListOrderDescending',
        itemListElement: items,
      },
      {
        '@type': 'BreadcrumbList',
        '@id': `${ORIGIN}/magazine/#breadcrumb`,
        itemListElement: [
          { '@type': 'ListItem', position: 1, name: '아스테리아 요트 클럽', item: `${ORIGIN}/` },
          { '@type': 'ListItem', position: 2, name: '매거진', item: `${ORIGIN}/magazine/` },
        ],
      },
    ],
  };
}

function articleStructuredData(article) {
  const canonical = absolute(article.url);
  const published = articleDate(article.publishedAt);
  const modified = articleDate(article.modifiedAt || article.publishedAt);
  return {
    '@context': 'https://schema.org',
    '@graph': [
      {
        '@type': 'BlogPosting',
        '@id': `${canonical}#article`,
        headline: article.titleKo,
        description: article.excerpt,
        image: [absolute(article.ogImage || article.image)],
        datePublished: published,
        dateModified: modified,
        inLanguage: 'ko-KR',
        articleSection: article.category,
        mainEntityOfPage: { '@type': 'WebPage', '@id': canonical },
        isPartOf: { '@id': `${ORIGIN}/magazine/#collection` },
        author: { '@id': `${ORIGIN}/#organization` },
        publisher: { '@id': `${ORIGIN}/#organization` },
        breadcrumb: { '@id': `${canonical}#breadcrumb` },
      },
      {
        '@type': 'BreadcrumbList',
        '@id': `${canonical}#breadcrumb`,
        itemListElement: [
          { '@type': 'ListItem', position: 1, name: '아스테리아 요트 클럽', item: `${ORIGIN}/` },
          { '@type': 'ListItem', position: 2, name: '매거진', item: `${ORIGIN}/magazine/` },
          { '@type': 'ListItem', position: 3, name: article.titleKo, item: canonical },
        ],
      },
    ],
  };
}

function applyIndexSeo(html, articles) {
  const title = '아스테리아 매거진 | 세일링 훈련과 레이싱 이야기';
  const description = '아스테리아의 세일링 훈련, 레이싱 도전, 크루 성장 이야기를 모은 매거진입니다.';
  const image = `${ORIGIN}/images/og-asteria.jpg`;
  let next = replaceTitle(html, title);
  next = upsertCanonical(next, `${ORIGIN}/magazine/`);
  for (const [attribute, key, content] of [
    ['name', 'description', description], ['name', 'robots', 'index,follow,max-image-preview:large'],
    ['property', 'og:type', 'website'], ['property', 'og:locale', 'ko_KR'],
    ['property', 'og:site_name', 'Asteria Magazine'], ['property', 'og:title', title],
    ['property', 'og:description', description], ['property', 'og:url', `${ORIGIN}/magazine/`],
    ['property', 'og:image', image], ['property', 'og:image:alt', '아스테리아 요트 클럽 세일링 매거진'],
    ['name', 'twitter:card', 'summary_large_image'], ['name', 'twitter:title', title],
    ['name', 'twitter:description', description], ['name', 'twitter:image', image],
    ['name', 'twitter:image:alt', '아스테리아 요트 클럽 세일링 매거진'],
  ]) next = upsertMeta(next, attribute, key, content);
  next = replaceStructuredData(next, collectionStructuredData(articles), 'magazine/index.html');
  return next.replace(/(<[^>]+id="magazineCount"[^>]*>)[\s\S]*?(<\/[^>]+>)/, `$1${articles.length} Episodes$2`);
}

function applyArticleSeo(html, article) {
  const canonical = absolute(article.url);
  const image = absolute(article.ogImage || article.image);
  const title = `${article.titleKo} | Asteria Magazine`;
  const published = articleDate(article.publishedAt);
  const modified = articleDate(article.modifiedAt || article.publishedAt);
  let next = replaceTitle(html, title);
  next = upsertCanonical(next, canonical);
  for (const [attribute, key, content] of [
    ['name', 'description', article.excerpt], ['name', 'robots', 'index,follow,max-image-preview:large'],
    ['property', 'og:type', 'article'], ['property', 'og:locale', 'ko_KR'],
    ['property', 'og:site_name', 'Asteria Magazine'], ['property', 'og:title', article.titleKo],
    ['property', 'og:description', article.excerpt], ['property', 'og:url', canonical],
    ['property', 'og:image', image], ['property', 'og:image:secure_url', image],
    ['property', 'og:image:alt', article.imageAlt || article.titleKo],
    ['property', 'article:published_time', published], ['property', 'article:modified_time', modified],
    ['property', 'article:section', article.category], ['name', 'twitter:card', 'summary_large_image'],
    ['name', 'twitter:title', article.titleKo], ['name', 'twitter:description', article.excerpt],
    ['name', 'twitter:image', image], ['name', 'twitter:image:alt', article.imageAlt || article.titleKo],
  ]) next = upsertMeta(next, attribute, key, content);
  return replaceStructuredData(next, articleStructuredData(article), `magazine/${article.slug}/index.html`);
}

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
const nextIndexHtml = injectInto(
  applyIndexSeo(indexHtml, ordered),
  'magazineMasonry',
  masonryMarkup(ordered),
  'magazine/index.html',
);

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
  const seoHtml = applyArticleSeo(pageHtml, article);
  const nextPageHtml = injectInto(seoHtml, 'articleRoot', articleMarkup(article), `magazine/${article.slug}/index.html`);
  pages.push({ pagePath, nextPageHtml, slug: article.slug });
}

if (check) {
  console.log(`render_magazine_static: --check OK (${ordered.length} cards, ${pages.length} article pages)`);
  process.exit(0);
}

writeFileSync(indexPath, nextIndexHtml);
for (const page of pages) writeFileSync(page.pagePath, page.nextPageHtml);
console.log(`render_magazine_static: injected ${ordered.length} cards + ${pages.length} article pages under ${root}`);
