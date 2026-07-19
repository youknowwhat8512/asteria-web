# Asteria Sailing Yacht Club Website

아스테리아 세일링 요트 스포츠 클럽의 운영 웹사이트입니다.

## Local preview

```bash
python3 -m http.server 8788
```

Open <http://127.0.0.1:8788/>.

## Structure

- `index.html` — production landing page
- `images/` — production image assets
- `magazine/articles.js` — magazine article data source
- `magazine/index.html` — Pinterest-style magazine archive
- `magazine/article.html` — shared article detail view (`?slug=`)
- `magazine/magazine.css`, `magazine/magazine.js` — shared magazine UI
- `favicon.ico` — browser icon
- `robots.txt`, `sitemap.xml` — search-engine metadata

## Source of decisions

기획, 브랜드 결정, 카피, 작업 기록은 별도 저장소 `22_1_memory/wiki/projects/asteria-yacht-club/`에서 관리합니다.
