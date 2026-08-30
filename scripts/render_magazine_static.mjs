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
const HERO_LAYOUTS = ['natural-portrait'];
const heroLayoutClass = layout => HERO_LAYOUTS.includes(layout) ? ` hero-${layout}` : '';
const heroNaturalWidth = value => Math.min(4096, Math.max(0, Math.round(Number(value) || 0)));
const heroStyle = value => heroNaturalWidth(value) ? ` style="--hero-natural-width:${heroNaturalWidth(value)}px"` : '';

function cardMarkup(article) {
  const heading = esc(article.title);
  const subheading = esc(article.titleKo);
  return `<a class="pin-card" data-category="${esc(article.category)}" data-shape="${esc(article.shape || 'standard')}" href="${esc(article.url)}" aria-label="${subheading} 에피소드 열기">`
    + `<div class="pin-media"><img src="${esc(article.image)}" alt="${esc(article.imageAlt || article.titleKo)}" loading="lazy">`
    + `<span class="pin-category">${esc(article.category)}</span></div>`
    + `<div class="pin-body"><time datetime="${esc(article.publishedAt)}">${esc(article.publishedAt)}</time>`
    + `<h2>${heading}</h2><h3>${subheading}</h3><p>${esc(article.excerpt)}</p>`
    + `<span class="pin-arrow">Read Episode →</span></div></a>`;
}

function masonryMarkup(articles) {
  return articles.map(cardMarkup).join('\n');
}

// Crawler-facing twin of magazine.js `sectionExplainer`: same slots, same class
// hooks, every value escaped. Purely data-driven — a new explainer theme needs
// no change here, only a `theme` string in articles.js.
// Hotspots come from data, so the percentage that lands in a style attribute is
// clamped to a plain 0-100 number rather than escaped as text.
const hotspotPct = value => Math.min(100, Math.max(0, Number(value) || 0));
// Tones land in an attribute selector, so only the known accents are ever
// emitted — never a raw value from articles.js. Shared by the legend rows and
// by the rings drawn over the device image.
const ACCENT_TONES = ['yellow', 'red'];
const toneAttr = tone => ACCENT_TONES.includes(tone) ? ` data-tone="${tone}"` : '';
// Twin of magazine.js `imageAnnotations`: optional hollow rings pinned on the
// device image. The ring is placed like a hotspot and sized from a source-pixel
// diameter, so it keeps the same footprint on the photo at any display width.
// An unknown tone drops the ring outright rather than reflecting it.
const ringSize = (diameter, width) =>
  Math.min(40, Math.max(2, (Number(diameter) || 0) / (Number(width) || 1) * 100)).toFixed(2);
const annotationsMarkup = image => (image.annotations || [])
  .filter(entry => ACCENT_TONES.includes(entry.tone))
  .map(entry => `<span class="explainer-annotation" data-tone="${entry.tone}"`
    + ` style="left:${hotspotPct(entry.x)}%;top:${hotspotPct(entry.y)}%;`
    + `--ring-size:${ringSize(entry.diameter, image.width)}%"`
    + ` role="img" aria-label="${esc(entry.description || entry.label)}">`
    + `<b aria-hidden="true">${esc(entry.label)}</b></span>`).join('');

// Optional per-explainer "how to use" panel, twin of magazine.js
// `explainerUsage`: any explainer that declares `usage` gets it.
// usage.layout:"reading-map" swaps the step for a mapping row carrying the
// reading's own code, category and action. Its tones are their own allowlist:
// they tint a card and never reach the rings drawn on the photo.
const READING_MAP = 'reading-map';
const USAGE_TONES = ['yellow', 'red', 'cyan'];
const usageTone = tone => USAGE_TONES.includes(tone) ? ` data-tone="${tone}"` : '';
const usageStep = (step, layout) => layout === READING_MAP
  ? `<li class="explainer-usage-step"${usageTone(step.tone)}>`
    + `<b class="usage-marker">${esc(step.marker)}</b>`
    + `<b class="usage-code">${esc(step.code)}</b>`
    + `<span class="usage-metric">${esc(step.metric)}</span>`
    + `<b class="usage-action">${esc(step.action)}</b>`
    + `<span class="usage-body">${esc(step.body)}</span></li>`
  : `<li class="explainer-usage-step"><b>${esc(step.title)}</b>`
    + `<span>${esc(step.body)}</span></li>`;

function usageMarkup(usage) {
  if (!usage || !(usage.steps || []).length) return '';
  const layout = usage.layout === READING_MAP ? ` data-layout="${READING_MAP}"` : '';
  return `<section class="explainer-usage" aria-label="${esc(usage.title)}">`
    + `<div class="explainer-usage-kicker">${esc(usage.kicker)}</div>`
    + `<h5>${esc(usage.title)}</h5>`
    + `<p class="explainer-usage-summary">${esc(usage.summary)}</p>`
    + `<ol class="explainer-usage-steps"${layout}>`
    + usage.steps.map(step => usageStep(step, usage.layout)).join('')
    + `</ol></section>`;
}

// Optional sibling of `usage`, twin of magazine.js `explainerScenario`: the
// same readings walked once as a numbered decision, each step carrying its own
// chips (the values or actions it works on). Tones reuse the usage allowlist —
// a step is still titled and numbered, so an off-allowlist tone only costs the
// tint, never the meaning. Every slot is escaped.
const scenarioChips = chips => (chips || []).length
  ? `<span class="scenario-chips">${chips.map(chip => `<span class="scenario-chip">${esc(chip)}</span>`).join('')}</span>`
  : '';
const scenarioStep = step => `<li class="explainer-scenario-step"${usageTone(step.tone)}>`
  + `<b class="scenario-marker">${esc(step.marker)}</b>`
  + `<b class="scenario-title">${esc(step.title)}</b>`
  + scenarioChips(step.chips)
  + `<span class="scenario-body">${esc(step.body)}</span></li>`;

function scenarioMarkup(scenario) {
  if (!scenario || !(scenario.steps || []).length) return '';
  return `<section class="explainer-scenario" aria-label="${esc(scenario.title)}">`
    + `<div class="explainer-scenario-kicker">${esc(scenario.kicker)}</div>`
    + `<h5>${esc(scenario.title)}</h5>`
    + `<p class="explainer-scenario-summary">${esc(scenario.summary)}</p>`
    + `<ol class="explainer-scenario-steps">`
    + scenario.steps.map(scenarioStep).join('')
    + `</ol></section>`;
}

function tutorialMarkup(explainer) {
  const items = explainer.items || [];
  const image = explainer.image;
  const size = image && image.width && image.height
    ? ` width="${esc(image.width)}" height="${esc(image.height)}"` : '';
  const legend = explainer.legend || [];
  return `<div class="explainer-tutorial">`
    + (image ? `<figure class="explainer-device"><span class="explainer-device-frame">`
        + `<img src="${esc(image.src)}" alt="${esc(image.alt)}"${size} loading="lazy">`
        + items.map(item => `<span class="explainer-hotspot" data-marker="${esc(item.marker)}"`
            + ` style="left:${hotspotPct(item.hotspot && item.hotspot.x)}%;top:${hotspotPct(item.hotspot && item.hotspot.y)}%"`
            + ` aria-hidden="true">${esc(item.marker)}</span>`).join('')
        + annotationsMarkup(image)
        + `</span><figcaption>${esc(image.caption)}</figcaption></figure>` : '')
    + `<dl class="explainer-callouts">${items.map(item =>
        `<div class="explainer-callout" data-anchor="${esc(item.anchor || 'center')}" data-marker="${esc(item.marker)}">`
        + `<dt><b class="explainer-marker">${esc(item.marker)}</b>`
        + `<span class="explainer-label">${esc(item.label)}</span>`
        // Optional: the acronym spelled out under the label. A label that is
        // already written out omits it and the line disappears.
        + (item.fullName ? `<span class="explainer-fullname">${esc(item.fullName)}</span>` : '')
        + `<span class="explainer-reading">${esc(item.reading)}</span></dt>`
        + `<dd>${esc(item.value)}</dd></div>`).join('')}</dl>`
    + `</div>`
    + (legend.length ? `<ul class="explainer-legend">${legend.map(entry =>
        `<li class="explainer-legend-item"${toneAttr(entry.tone)}><b>${esc(entry.label)}</b>`
        + `<span>${esc(entry.value)}</span></li>`).join('')}</ul>` : '');
}

function explainerMarkup(explainer) {
  if (!explainer) return '';
  const image = explainer.image;
  const size = image && image.width && image.height
    ? ` width="${esc(image.width)}" height="${esc(image.height)}"` : '';
  const body = explainer.layout === 'device-tutorial' ? tutorialMarkup(explainer)
    : (image ? `<figure class="explainer-media"><img src="${esc(image.src)}" alt="${esc(image.alt)}"${size}`
        + ` loading="lazy"><figcaption>${esc(image.caption)}</figcaption></figure>` : '')
      + `<dl>${(explainer.items || []).map(item =>
          `<div><dt>${esc(item.label)}</dt><dd>${esc(item.value)}</dd></div>`).join('')}</dl>`;
  return `<aside class="article-explainer${explainer.theme ? ` explainer-${esc(explainer.theme)}` : ''}`
    + `${explainer.layout ? ` explainer-layout-${esc(explainer.layout)}` : ''}"`
    + ` aria-label="${esc(explainer.title)}">`
    + `<div class="explainer-kicker">${esc(explainer.kicker)}</div>`
    + `<h4>${esc(explainer.title)}</h4>`
    + `<p class="explainer-summary">${esc(explainer.summary)}</p>`
    + body
    + usageMarkup(explainer.usage)
    + scenarioMarkup(explainer.scenario)
    + (explainer.note ? `<p class="explainer-note">${esc(explainer.note)}</p>` : '')
    + `</aside>`;
}

// Twin of magazine.js `atArticleEnd`: a section may declare
// `explainerPlacement: 'article-end'` to move its explainer under the whole body.
const atArticleEnd = section => section.explainerPlacement === 'article-end';

const staticNaturalWidthStyle = image => image.width
  ? ` style="--image-natural-width:${esc(image.rotate ? image.height : image.width)}px"` : '';
const staticSectionVisual = image => {
  if (!image) return '';
  const size = image.width && image.height
    ? ` width="${esc(image.width)}" height="${esc(image.height)}"` : '';
  const imageStyle = image.width && image.height || image.position
    ? ` style="${image.width && image.height ? `height:auto;aspect-ratio:${esc(image.width)}/${esc(image.height)};` : ''}`
      + `${image.position ? `object-position:${esc(image.position)};` : ''}"` : '';
  return `<figure class="article-section-visual${image.layout ? ` visual-${esc(image.layout)}` : ''}"${staticNaturalWidthStyle(image)}>`
    + `<img src="${esc(image.src)}" alt="${esc(image.alt)}"${size} loading="lazy"${imageStyle}>`
    + `<figcaption>${esc(image.caption)}</figcaption></figure>`;
};
const staticBodyMediaAfter = (section, paragraphNumber) => (section.bodyMedia || [])
  .filter(image => Number(image.afterParagraph) === paragraphNumber)
  .map(staticSectionVisual).join('');
const staticSectionParagraph = (section, paragraph, paragraphNumber) => paragraph
  ? `<p>${esc(paragraph)}</p>${staticBodyMediaAfter(section, paragraphNumber)}` : '';

function articleMarkup(article) {
  const sections = (article.sections || []).map(section => {
    const [firstParagraph, ...remainingParagraphs] = section.body || [];
    const tip = section.tip ? `<aside class="article-tip" aria-label="${esc(section.tip.title)}">`
      + `<div class="article-tip-mark" aria-hidden="true">TIP</div><div class="article-tip-copy">`
      + `<div class="article-tip-kicker">${esc(section.tip.kicker)}</div><h4>${esc(section.tip.title)}</h4>`
      + `<p>${esc(section.tip.summary)}</p><dl>${(section.tip.items || []).map((item, index) => ``
        + `<div><dt>${String(index + 1).padStart(2, '0')} · ${esc(item.label)}</dt><dd>${esc(item.value)}</dd></div>`).join('')}</dl>`
      + `</div></aside>` : '';
    const body = `${staticSectionParagraph(section, firstParagraph, 1)}`
      + `${section.intro ? `<p>${esc(section.intro)}</p>` : ''}`
      + `${atArticleEnd(section) ? '' : explainerMarkup(section.explainer)}${tip}`
      + remainingParagraphs.map((paragraph, index) => staticSectionParagraph(section, paragraph, index + 2)).join('');
    return `<section class="article-section">${section.kicker ? `<div class="section-kicker">${esc(section.kicker)}</div>` : ''}`
      + `<h3>${esc(section.heading)}</h3>${body}</section>`;
  }).join('\n');
  const endExplainers = (article.sections || []).filter(atArticleEnd)
    .map(section => explainerMarkup(section.explainer)).join('');
  return `<article>`
    + `<header class="article-head"><div class="article-meta"><span>${esc(article.category)}</span>`
    + `<time datetime="${esc(article.eventDate || article.publishedAt)}">${esc(article.eventDate || article.publishedAt)}</time></div>`
    + `<h1>${esc(article.title)}</h1><h2>${esc(article.titleKo)}</h2></header>`
    + `<div class="article-hero${heroLayoutClass(article.heroLayout)}"${heroStyle(article.heroNaturalWidth)}><img src="${esc(article.image)}" alt="${esc(article.imageAlt || article.titleKo)}"></div>`
    + `<div class="article-content"><p class="article-lead">${esc(article.lead)}</p>`
    + `<div class="article-body">${sections}${endExplainers}</div></div></article>`;
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
