#!/usr/bin/env node
/**
 * source_export.mjs — Asteria Naver Blog Copilot: 에피소드 추출기 (단계 1)
 *
 * magazine/articles.js (window.ASTERIA_ARTICLES = [...]) 를 node:vm 으로
 * 안전하게 평가해 에피소드별 표준 JSON 을 내보냅니다.
 *
 * 사용법:
 *   node tools/naver-blog/source_export.mjs [--out <path>] [--check]
 *
 *   --out <path>  출력 파일 경로 (기본: .hermes/exports/episodes.json)
 *   --check       파일을 쓰지 않고 검증만 수행. 성공하면 exit 0.
 *
 * 실패 조건 (exit 1):
 *   - slug 또는 url 이 비어 있는 에피소드
 *   - 이미지 src 에 해당하는 실제 파일이 asteria-web/ 기준으로 존재하지 않음
 *     → 누락 목록을 모두 출력한 뒤 종료
 *
 * 외부 의존성 없음 (Node.js 22 내장 모듈만 사용).
 */

import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { createContext, runInContext } from 'node:vm';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

// ── 경로 상수 ──────────────────────────────────────────────────────────────
const __dir = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dir, '..', '..');
const ARTICLES_PATH = join(REPO_ROOT, 'magazine', 'articles.js');
const DEFAULT_OUT = join(REPO_ROOT, '.hermes', 'exports', 'episodes.json');

// ── CLI 인수 파싱 ──────────────────────────────────────────────────────────
const args = process.argv.slice(2);
let outPath = DEFAULT_OUT;
let checkOnly = false;

for (let i = 0; i < args.length; i++) {
  if (args[i] === '--out' && args[i + 1]) {
    outPath = resolve(args[++i]);
  } else if (args[i] === '--check') {
    checkOnly = true;
  }
}

// ── articles.js 평가 (node:vm, 가짜 window) ───────────────────────────────
const src = readFileSync(ARTICLES_PATH, 'utf8');
const ctx = { window: {} };
createContext(ctx);
runInContext(src, ctx);

/** @type {Array<object>} */
const rawArticles = ctx.window.ASTERIA_ARTICLES;
if (!Array.isArray(rawArticles) || rawArticles.length === 0) {
  console.error('ERROR: window.ASTERIA_ARTICLES 가 비어 있거나 배열이 아닙니다.');
  process.exit(1);
}

// ── 이미지 객체 정규화 헬퍼 ───────────────────────────────────────────────
/**
 * 이미지 값을 { src, width, height, alt, caption, layout, position } 형태로 정규화.
 * top-level hero image 는 문자열일 수도 있음.
 *
 * @param {string|object} imgVal
 * @param {string} position - 'hero' | 'section-image' | 'section-endImage' | 'section-midImage' | 'section-bodyMedia'| 'section-endGallery'
 * @returns {{ src: string, width?: number, height?: number, alt?: string, caption?: string, layout?: string, position: string }}
 */
function normalizeImage(imgVal, position) {
  if (typeof imgVal === 'string') {
    return { src: imgVal, position };
  }
  const { src, width, height, alt, caption, layout } = imgVal;
  return {
    src,
    ...(width != null && { width }),
    ...(height != null && { height }),
    ...(alt != null && { alt }),
    ...(caption != null && { caption }),
    ...(layout != null && { layout }),
    position,
  };
}

/**
 * 섹션에서 모든 이미지를 수집해 배열로 반환.
 * 순서: image → midImage → bodyMedia[] → endImage → endGallery[]
 *
 * @param {object} section
 * @returns {Array}
 */
function collectSectionImages(section) {
  const imgs = [];
  if (section.image) imgs.push(normalizeImage(section.image, 'section-image'));
  if (section.midImage) imgs.push(normalizeImage(section.midImage, 'section-midImage'));
  if (Array.isArray(section.bodyMedia)) {
    for (const m of section.bodyMedia) imgs.push(normalizeImage(m, 'section-bodyMedia'));
  }
  if (section.endImage) imgs.push(normalizeImage(section.endImage, 'section-endImage'));
  if (Array.isArray(section.endGallery)) {
    for (const g of section.endGallery) imgs.push(normalizeImage(g, 'section-endGallery'));
  }
  return imgs;
}

// ── 에피소드 변환 ─────────────────────────────────────────────────────────
/**
 * raw article 객체를 표준 에피소드 JSON 으로 변환.
 *
 * @param {object} raw
 * @returns {object}
 */
function transformEpisode(raw) {
  const {
    slug,
    url,
    category,
    title,
    titleKo,
    publishedAt,
    eventDate,
    excerpt,
    lead,
    pullQuote,
    facts = [],
    sections = [],
  } = raw;

  // ── sections 정규화: body[] 와 이미지 보존 ────────────────────────────
  const normalizedSections = sections.map((sec) => {
    // body 는 그대로 보존 (string[] 또는 undefined)
    const body = Array.isArray(sec.body) ? sec.body : [];

    // 섹션 이미지 수집
    const images = collectSectionImages(sec);

    // 그 외 섹션 메타 필드 보존 (kicker, heading, explainer, tip 등)
    const {
      kicker,
      heading,
      explainer,
      tip,
      // 이미 처리한 이미지 키는 스프레드에서 제외
      image: _img,
      endImage: _end,
      midImage: _mid,
      bodyMedia: _bm,
      endGallery: _eg,
      body: _body,
      galleryLayout,
      ...rest
    } = sec;

    return {
      ...(kicker != null && { kicker }),
      ...(heading != null && { heading }),
      ...(explainer != null && { explainer }),
      ...(tip != null && { tip }),
      ...(galleryLayout != null && { galleryLayout }),
      ...rest,
      body,
      images,
    };
  });

  // ── 전체 이미지 평탄화 ─────────────────────────────────────────────────
  const allImages = [];
  // top-level hero image
  if (raw.image) {
    allImages.push({
      ...normalizeImage(raw.image, 'hero'),
      ...(raw.imageAlt && { alt: raw.imageAlt }),
      ...(raw.heroLayout && { layout: raw.heroLayout }),
    });
  }
  // 섹션별 이미지
  for (const sec of normalizedSections) {
    allImages.push(...sec.images);
  }

  // ── sourceHash (정규화된 에피소드 기준 sha256) ─────────────────────────
  // 정규화 대상: slug, url, title, titleKo, publishedAt, eventDate,
  //             excerpt, lead, pullQuote, facts, sections (body + images)
  const hashable = JSON.stringify({
    slug, url, category, title, titleKo, publishedAt, eventDate,
    excerpt, lead, pullQuote, facts,
    sections: normalizedSections,
  });
  const sourceHash = createHash('sha256').update(hashable).digest('hex');

  return {
    slug,
    url,
    category,
    title,
    titleKo,
    publishedAt,
    eventDate,
    excerpt,
    lead,
    pullQuote,
    facts,
    sections: normalizedSections,
    images: allImages,
    sourceHash,
  };
}

// ── 변환 실행 ─────────────────────────────────────────────────────────────
const episodes = rawArticles.map(transformEpisode);

// ── 검증 1: slug / url 비어 있음 ─────────────────────────────────────────
const missingSlugOrUrl = episodes.filter((ep) => !ep.slug || !ep.url);
if (missingSlugOrUrl.length > 0) {
  console.error('ERROR: slug 또는 url 이 비어 있는 에피소드:');
  for (const ep of missingSlugOrUrl) {
    console.error(`  slug="${ep.slug}" url="${ep.url}"`);
  }
  process.exit(1);
}

// ── 검증 2: 이미지 파일 존재 여부 ────────────────────────────────────────
const missingFiles = [];
for (const ep of episodes) {
  for (const img of ep.images) {
    if (!img.src) continue;
    // src 는 /images/... 형태의 절대 URL 경로
    const filePath = join(REPO_ROOT, img.src.startsWith('/') ? img.src.slice(1) : img.src);
    if (!existsSync(filePath)) {
      missingFiles.push({ slug: ep.slug, src: img.src, position: img.position });
    }
  }
}

if (missingFiles.length > 0) {
  console.error(`ERROR: 이미지 파일이 존재하지 않습니다 (${missingFiles.length}개):`);
  for (const m of missingFiles) {
    console.error(`  [${m.slug}] ${m.position}: ${m.src}`);
  }
  process.exit(1);
}

// ── 출력 ─────────────────────────────────────────────────────────────────
const output = {
  generatedAt: new Date().toISOString(),
  count: episodes.length,
  episodes,
};

if (checkOnly) {
  // 검증만 — 파일 쓰지 않음
  console.log(`OK: ${episodes.length}편, 이미지 ${episodes.reduce((s, e) => s + e.images.length, 0)}개. 모든 파일 존재 확인.`);
  episodes.forEach((ep) => {
    console.log(`  ${ep.slug}: images=${ep.images.length}`);
  });
  process.exit(0);
}

// 출력 디렉토리 생성
const outDir = dirname(outPath);
mkdirSync(outDir, { recursive: true });

writeFileSync(outPath, JSON.stringify(output, null, 2), 'utf8');
console.log(`OK: ${episodes.length}편 → ${outPath}`);
episodes.forEach((ep) => {
  console.log(`  ${ep.slug}: images=${ep.images.length}, hash=${ep.sourceHash.slice(0, 12)}…`);
});
