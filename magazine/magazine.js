(() => {
  const articles = [...(window.ASTERIA_ARTICLES || [])].sort((a, b) => b.publishedAt.localeCompare(a.publishedAt));
  const articleUrl = slug => `/magazine/article.html?slug=${encodeURIComponent(slug)}`;
  const dateLabel = value => new Intl.DateTimeFormat('ko-KR', {year:'numeric',month:'long',day:'numeric'}).format(new Date(`${value}T00:00:00`));

  function cardMarkup(article) {
    return `<a class="pin-card" data-category="${article.category}" data-shape="${article.shape}" href="${articleUrl(article.slug)}" aria-label="${article.titleKo} 아티클 열기">
      <div class="pin-media"><img src="${article.image}" alt="${article.imageAlt || article.titleKo}" loading="lazy"><span class="pin-category">${article.category}</span></div>
      <div class="pin-body"><time datetime="${article.publishedAt}">${dateLabel(article.publishedAt)}</time><h2>${article.title}</h2><h3>${article.titleKo}</h3><p>${article.excerpt}</p><span class="pin-arrow">Read Story →</span></div>
    </a>`;
  }

  const masonry = document.getElementById('magazineMasonry');
  if (masonry) {
    const filters = document.getElementById('magazineFilters');
    const count = document.getElementById('magazineCount');
    const empty = document.getElementById('magazineEmpty');
    const categories = ['All', ...new Set(articles.map(article => article.category))];
    let active = 'All';

    const render = () => {
      const visible = active === 'All' ? articles : articles.filter(article => article.category === active);
      masonry.innerHTML = visible.map(cardMarkup).join('');
      masonry.classList.toggle('single-story', visible.length === 1);
      count.textContent = `${visible.length} ${visible.length === 1 ? 'Story' : 'Stories'}`;
      empty.classList.toggle('show', visible.length === 0);
    };

    filters.innerHTML = categories.map(category => `<button class="filter-btn${category === active ? ' active' : ''}" type="button" data-filter="${category}">${category}</button>`).join('');
    filters.addEventListener('click', event => {
      const button = event.target.closest('[data-filter]');
      if (!button) return;
      active = button.dataset.filter;
      filters.querySelectorAll('.filter-btn').forEach(item => item.classList.toggle('active', item === button));
      render();
    });
    render();
  }

  const articleRoot = document.getElementById('articleRoot');
  if (articleRoot) {
    const slug = new URLSearchParams(location.search).get('slug');
    const article = articles.find(item => item.slug === slug);
    if (!article) {
      document.title = '아티클을 찾을 수 없습니다 | Asteria Magazine';
      articleRoot.innerHTML = `<div class="article-not-found"><h1>Story<br>Not Found.</h1><p>요청한 매거진 아티클을 찾을 수 없습니다.</p><a href="/magazine/">Magazine 전체보기</a></div>`;
      document.getElementById('relatedSection').hidden = true;
      return;
    }

    document.title = `${article.titleKo} | Asteria Magazine`;
    const description = document.querySelector('meta[name="description"]');
    if (description) description.content = article.excerpt;
    const facts = (article.facts || []).map(item => `<div class="article-fact"><small>${item.label}</small><strong>${item.value}</strong></div>`).join('');
    const results = (article.results || []).map(item => `<li><span>${item.rank}</span><div><strong>${item.skipper}</strong><small>${item.detail}</small></div></li>`).join('');
    const raceFlow = (article.raceFlow || []).map(item => `<article class="race-flow-card"><img src="${item.image}" alt="${item.alt}" loading="lazy"><div><span>${item.step} / ${item.label}</span><h3>${item.title}</h3><p>${item.text}</p></div></article>`).join('');
    const gallery = (article.gallery || []).map(item => `<figure class="gallery-${item.layout || 'standard'}"><img src="${item.src}" alt="${item.alt}" loading="lazy"><figcaption>${item.caption}</figcaption></figure>`).join('');
    const sectionMarkup = section => `<section class="article-section">
      ${section.kicker ? `<div class="section-kicker">${section.kicker}</div>` : ''}
      <h3>${section.heading}</h3>
      ${section.image ? `<figure class="article-section-visual"><img src="${section.image.src}" alt="${section.image.alt}" loading="lazy"><figcaption>${section.image.caption}</figcaption></figure>` : ''}
      ${section.body.map(paragraph => `<p>${paragraph}</p>`).join('')}
    </section>`;
    const storyDate = article.eventDate || article.publishedAt;
    articleRoot.innerHTML = `<article>
      <header class="article-head"><a class="article-back" href="/magazine/">← Magazine 전체보기</a><div class="article-meta"><span>${article.category}</span><time datetime="${storyDate}">${dateLabel(storyDate)}</time></div><h1>${article.title}</h1><h2>${article.titleKo}</h2></header>
      <div class="article-hero"><img src="${article.image}" alt="${article.imageAlt || article.titleKo}">${article.heroNote ? `<span>${article.heroNote}</span>` : ''}</div>
      ${facts ? `<div class="article-facts">${facts}</div>` : ''}
      ${raceFlow ? `<section class="article-race-flow"><div class="eyebrow">Race in Three Acts</div><div class="race-flow-grid">${raceFlow}</div></section>` : ''}
      ${article.pullQuote ? `<blockquote class="article-pullquote">${article.pullQuote}</blockquote>` : ''}
      <div class="article-content"><p class="article-lead">${article.lead}</p><div class="article-body">
        ${results ? `<section class="article-results"><div class="eyebrow">Final Standing</div><ol>${results}</ol></section>` : ''}
        ${article.sections.map(sectionMarkup).join('')}
      </div></div>
      ${gallery ? `<section class="article-gallery"><div class="eyebrow">Final Frames</div><div>${gallery}</div></section>` : ''}
    </article>`;

    const motionItems = articleRoot.querySelectorAll('.race-flow-card, .article-section, .article-gallery figure');
    if (!matchMedia('(prefers-reduced-motion: reduce)').matches && 'IntersectionObserver' in window) {
      articleRoot.classList.add('article-motion-ready');
      const observer = new IntersectionObserver(entries => {
        entries.forEach(entry => {
          if (!entry.isIntersecting) return;
          entry.target.classList.add('is-active');
          observer.unobserve(entry.target);
        });
      }, { threshold: .12, rootMargin: '0px 0px -8%' });
      motionItems.forEach(item => observer.observe(item));
    }

    const related = articles.filter(item => item.slug !== article.slug).slice(0, 3);
    const relatedGrid = document.getElementById('relatedGrid');
    const relatedSection = document.getElementById('relatedSection');
    relatedSection.hidden = related.length === 0;
    relatedGrid.innerHTML = related.map(item => `<a class="related-card" href="${articleUrl(item.slug)}"><img src="${item.image}" alt="${item.imageAlt || item.titleKo}"><div><small>${item.category}</small><h3>${item.titleKo}</h3></div></a>`).join('');
  }
})();
