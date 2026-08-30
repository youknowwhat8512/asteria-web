(() => {
  const articles = [...(window.ASTERIA_ARTICLES || [])].sort((a, b) => b.publishedAt.localeCompare(a.publishedAt));
  const articleUrl = article => article.url || `/magazine/article.html?slug=${encodeURIComponent(article.slug)}`;
  const dateLabel = value => new Intl.DateTimeFormat('ko-KR', {year:'numeric',month:'long',day:'numeric'}).format(new Date(`${value}T00:00:00`));
  const HERO_LAYOUTS = ['natural-portrait'];
  const heroLayoutClass = layout => HERO_LAYOUTS.includes(layout) ? ` hero-${layout}` : '';
  const heroNaturalWidth = value => Math.min(4096, Math.max(0, Math.round(Number(value) || 0)));
  const heroStyle = value => heroNaturalWidth(value) ? ` style="--hero-natural-width:${heroNaturalWidth(value)}px"` : '';

  function cardMarkup(article) {
    return `<a class="pin-card" data-category="${article.category}" data-shape="${article.shape}" href="${articleUrl(article)}" aria-label="${article.titleKo} 에피소드 열기">
      <div class="pin-media"><img src="${article.image}" alt="${article.imageAlt || article.titleKo}" loading="lazy"><span class="pin-category">${article.category}</span></div>
      <div class="pin-body"><time datetime="${article.publishedAt}">${dateLabel(article.publishedAt)}</time><h2>${article.title}</h2><h3>${article.titleKo}</h3><p>${article.excerpt}</p><span class="pin-arrow">Read Episode →</span></div>
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
      count.textContent = `${visible.length} ${visible.length === 1 ? 'Episode' : 'Episodes'}`;
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
    const slug = articleRoot.dataset.articleSlug || new URLSearchParams(location.search).get('slug');
    const article = articles.find(item => item.slug === slug);
    if (!article) {
      document.title = '에피소드를 찾을 수 없습니다 | Asteria Magazine';
      articleRoot.innerHTML = `<div class="article-not-found"><h1>Episode<br>Not Found.</h1><p>요청한 매거진 에피소드를 찾을 수 없습니다.</p><a href="/magazine/">Magazine 전체보기</a></div>`;
      document.getElementById('relatedSection').hidden = true;
      return;
    }

    document.title = `${article.titleKo} | Asteria Magazine`;
    const description = document.querySelector('meta[name="description"]');
    if (description) description.content = article.excerpt;
    const canonicalUrl = new URL(articleUrl(article), location.origin).href;
    const ogImageUrl = new URL(article.ogImage || article.image, location.origin).href;
    const canonical = document.querySelector('link[rel="canonical"]') || document.head.appendChild(Object.assign(document.createElement('link'), { rel: 'canonical' }));
    canonical.href = canonicalUrl;
    const setMeta = (selector, attribute, value, content) => {
      let meta = document.head.querySelector(selector);
      if (!meta) {
        meta = document.createElement('meta');
        meta.setAttribute(attribute, value);
        document.head.appendChild(meta);
      }
      meta.content = content;
    };
    setMeta('meta[property="og:title"]', 'property', 'og:title', article.titleKo);
    setMeta('meta[property="og:description"]', 'property', 'og:description', article.excerpt);
    setMeta('meta[property="og:url"]', 'property', 'og:url', canonicalUrl);
    setMeta('meta[property="og:image"]', 'property', 'og:image', ogImageUrl);
    setMeta('meta[name="twitter:title"]', 'name', 'twitter:title', article.titleKo);
    setMeta('meta[name="twitter:description"]', 'name', 'twitter:description', article.excerpt);
    setMeta('meta[name="twitter:image"]', 'name', 'twitter:image', ogImageUrl);
    const facts = (article.facts || []).map(item => `<div class="article-fact"><small>${item.label}</small><strong>${item.value}</strong></div>`).join('');
    const results = (article.results || []).map(item => `<li><span>${item.rank}</span><div><div class="result-name"><strong>${item.skipper}</strong>${(item.badges || []).map(badge => `<em>${badge}</em>`).join('')}</div><small>${item.detail}</small></div></li>`).join('');
    const raceFlow = (article.raceFlow || []).map(item => `<article class="race-flow-card"><img src="${item.image}" alt="${item.alt}" loading="lazy"><div><span>${item.step} / ${item.label}</span><h3>${item.title}</h3><p>${item.text}</p></div></article>`).join('');
    const gallery = (article.gallery || []).map(item => `<figure class="gallery-${item.layout || 'standard'}"><img src="${item.src}" alt="${item.alt}" loading="lazy"${item.position ? ` style="object-position:${item.position}"` : ''}><figcaption>${item.caption}</figcaption></figure>`).join('');
    const naturalWidthStyle = image => image.width ? ` style="--image-natural-width:${image.rotate ? image.height : image.width}px"` : '';
    const sectionVisual = image => image ? `<figure class="article-section-visual${image.layout ? ` visual-${image.layout}` : ''}"${naturalWidthStyle(image)}>${image.rotate ? `<span class="article-rotated-media rotate-${image.rotate}">` : ''}<img src="${image.src}" alt="${image.alt}"${image.width && image.height ? ` width="${image.width}" height="${image.height}"` : ''} loading="lazy"${image.width && image.height || image.position ? ` style="${image.width && image.height ? `height:auto;aspect-ratio:${image.width}/${image.height};` : ''}${image.position ? `object-position:${image.position};` : ''}"` : ''}>${image.rotate ? '</span>' : ''}<figcaption>${image.caption}</figcaption></figure>` : '';
    const sectionGallery = (items, layout) => items?.length ? `<div class="article-section-gallery${layout ? ` gallery-${layout}` : ''}">${items.map(item => `<figure class="section-gallery-${item.layout || 'standard'}"${naturalWidthStyle(item)}><img src="${item.src}" alt="${item.alt}"${item.width && item.height ? ` width="${item.width}" height="${item.height}"` : ''} loading="lazy"${item.position ? ` style="object-position:${item.position}"` : ''}><figcaption>${item.caption}</figcaption></figure>`).join('')}</div>` : '';
    // Hotspots are placed from data, so a bad number must never escape into the
    // style attribute — clamp to a plain 0-100 percentage.
    const hotspotPct = value => Math.min(100, Math.max(0, Number(value) || 0));
    const imageSize = image => image.width && image.height ? ` width="${image.width}" height="${image.height}"` : '';
    // layout:"device-tutorial" — a device screenshot in the middle, numbered
    // hotspots on it, and one callout per reading placed by its anchor.
    // A reading may add `fullName` to spell its acronym out under the label;
    // a label that is already written out simply omits it.
    // Tones come from data and land in an attribute selector, so only the known
    // accents are ever emitted — never a raw value from articles.js. Shared by
    // the legend rows and by the rings drawn over the device image.
    const ACCENT_TONES = ['yellow', 'red'];
    const toneAttr = tone => ACCENT_TONES.includes(tone) ? ` data-tone="${tone}"` : '';
    // Optional hollow rings pinned on the device image, for pointing at
    // something the screen already draws: the ring is placed like a hotspot and
    // sized from a source-pixel diameter, so it keeps the same footprint on the
    // photo at any display width. An unknown tone drops the ring outright.
    const ringSize = (diameter, width) =>
      Math.min(40, Math.max(2, (Number(diameter) || 0) / (Number(width) || 1) * 100)).toFixed(2);
    const imageAnnotations = image => (image.annotations || [])
      .filter(entry => ACCENT_TONES.includes(entry.tone))
      .map(entry => `<span class="explainer-annotation" data-tone="${entry.tone}" style="left:${hotspotPct(entry.x)}%;top:${hotspotPct(entry.y)}%;--ring-size:${ringSize(entry.diameter, image.width)}%" role="img" aria-label="${entry.description || entry.label}"><b aria-hidden="true">${entry.label}</b></span>`).join('');
    // Optional per-explainer "how to use" panel: any explainer that declares
    // `usage` gets it, none that don't. Nothing here knows which story it is.
    // usage.layout:"reading-map" swaps the step for a mapping row that carries
    // the reading's own code, category and action instead of a bare title, so a
    // reader matches a screen acronym to what to do without re-deriving it.
    // Its tones are their own allowlist: they tint a card, they never reach the
    // rings, so a new mapping tone cannot change what the photo claims.
    const READING_MAP = 'reading-map';
    const USAGE_TONES = ['yellow', 'red', 'cyan'];
    const usageTone = tone => USAGE_TONES.includes(tone) ? ` data-tone="${tone}"` : '';
    const usageStep = (step, layout) => layout === READING_MAP
      ? `<li class="explainer-usage-step"${usageTone(step.tone)}><b class="usage-marker">${step.marker}</b><b class="usage-code">${step.code}</b><span class="usage-metric">${step.metric}</span><b class="usage-action">${step.action}</b><span class="usage-body">${step.body}</span></li>`
      : `<li class="explainer-usage-step"><b>${step.title}</b><span>${step.body}</span></li>`;
    const explainerUsage = usage => usage?.steps?.length ? `<section class="explainer-usage" aria-label="${usage.title}">
      <div class="explainer-usage-kicker">${usage.kicker}</div>
      <h5>${usage.title}</h5>
      <p class="explainer-usage-summary">${usage.summary}</p>
      <ol class="explainer-usage-steps"${usage.layout === READING_MAP ? ` data-layout="${READING_MAP}"` : ''}>${usage.steps.map(step => usageStep(step, usage.layout)).join('')}</ol>
    </section>` : '';
    // Optional sibling of `usage`: the same readings walked once as a numbered
    // decision, each step carrying its own chips (the values or actions it
    // works on) instead of a single reading. Any explainer can declare it, none
    // has to. Tones reuse the usage allowlist — a step is still titled and
    // numbered, so an off-allowlist tone only costs the tint, never the meaning.
    const scenarioChips = chips => chips?.length
      ? `<span class="scenario-chips">${chips.map(chip => `<span class="scenario-chip">${chip}</span>`).join('')}</span>` : '';
    const scenarioStep = step => `<li class="explainer-scenario-step"${usageTone(step.tone)}><b class="scenario-marker">${step.marker}</b><b class="scenario-title">${step.title}</b>${scenarioChips(step.chips)}<span class="scenario-body">${step.body}</span></li>`;
    const explainerScenario = scenario => scenario?.steps?.length ? `<section class="explainer-scenario" aria-label="${scenario.title}">
      <div class="explainer-scenario-kicker">${scenario.kicker}</div>
      <h5>${scenario.title}</h5>
      <p class="explainer-scenario-summary">${scenario.summary}</p>
      <ol class="explainer-scenario-steps">${scenario.steps.map(scenarioStep).join('')}</ol>
    </section>` : '';
    const explainerTutorial = explainer => {
      const items = explainer.items || [];
      const image = explainer.image;
      return `<div class="explainer-tutorial">
        ${image ? `<figure class="explainer-device"><span class="explainer-device-frame"><img src="${image.src}" alt="${image.alt}"${imageSize(image)} loading="lazy">${items.map(item => `<span class="explainer-hotspot" data-marker="${item.marker}" style="left:${hotspotPct(item.hotspot?.x)}%;top:${hotspotPct(item.hotspot?.y)}%" aria-hidden="true">${item.marker}</span>`).join('')}${imageAnnotations(image)}</span><figcaption>${image.caption}</figcaption></figure>` : ''}
        <dl class="explainer-callouts">${items.map(item => `<div class="explainer-callout" data-anchor="${item.anchor || 'center'}" data-marker="${item.marker}"><dt><b class="explainer-marker">${item.marker}</b><span class="explainer-label">${item.label}</span>${item.fullName ? `<span class="explainer-fullname">${item.fullName}</span>` : ''}<span class="explainer-reading">${item.reading}</span></dt><dd>${item.value}</dd></div>`).join('')}</dl>
      </div>
      ${explainer.legend?.length ? `<ul class="explainer-legend">${explainer.legend.map(entry => `<li class="explainer-legend-item"${toneAttr(entry.tone)}><b>${entry.label}</b><span>${entry.value}</span></li>`).join('')}</ul>` : ''}`;
    };
    const explainerBody = explainer => explainer.layout === 'device-tutorial' ? explainerTutorial(explainer)
      : `${explainer.image ? `<figure class="explainer-media"><img src="${explainer.image.src}" alt="${explainer.image.alt}"${imageSize(explainer.image)} loading="lazy"><figcaption>${explainer.image.caption}</figcaption></figure>` : ''}
      <dl>${explainer.items.map(item => `<div><dt>${item.label}</dt><dd>${item.value}</dd></div>`).join('')}</dl>`;
    const sectionExplainer = explainer => explainer ? `<aside class="article-explainer${explainer.theme ? ` explainer-${explainer.theme}` : ''}${explainer.layout ? ` explainer-layout-${explainer.layout}` : ''}" aria-label="${explainer.title}">
      <div class="explainer-kicker">${explainer.kicker}</div>
      <h4>${explainer.title}</h4>
      <p class="explainer-summary">${explainer.summary}</p>
      ${explainerBody(explainer)}
      ${explainerUsage(explainer.usage)}
      ${explainerScenario(explainer.scenario)}
      ${explainer.note ? `<p class="explainer-note">${explainer.note}</p>` : ''}
    </aside>` : '';
    const sectionTip = tip => tip ? `<aside class="article-tip" aria-label="${tip.title}">
      <div class="article-tip-mark" aria-hidden="true">TIP</div>
      <div class="article-tip-copy">
        <div class="article-tip-kicker">${tip.kicker}</div>
        <h4>${tip.title}</h4>
        <p>${tip.summary}</p>
        <dl>${tip.items.map((item, index) => `<div><dt>${String(index + 1).padStart(2, '0')} · ${item.label}</dt><dd>${item.value}</dd></div>`).join('')}</dl>
      </div>
    </aside>` : '';
    // Opt-in placement: a section may declare `explainerPlacement: 'article-end'`
    // to have its explainer rendered once under the whole body instead of inline.
    const atArticleEnd = section => section.explainerPlacement === 'article-end';
    const bodyMediaAfter = (section, paragraphNumber) => (section.bodyMedia || [])
      .filter(image => Number(image.afterParagraph) === paragraphNumber)
      .map(sectionVisual).join('');
    const sectionParagraph = (section, paragraph, paragraphNumber) => paragraph
      ? `<p>${paragraph}</p>${bodyMediaAfter(section, paragraphNumber)}` : '';
    const sectionMarkup = section => {
      const [firstParagraph, secondParagraph, ...remainingParagraphs] = section.body;
      return `<section class="article-section">
        ${section.kicker ? `<div class="section-kicker">${section.kicker}</div>` : ''}
        <h3>${section.heading}</h3>
        ${sectionVisual(section.image)}
        ${sectionParagraph(section, firstParagraph, 1)}
        ${section.intro ? `<p>${section.intro}</p>` : ''}
        ${atArticleEnd(section) ? '' : sectionExplainer(section.explainer)}
        ${sectionGallery(section.gallery, section.galleryLayout)}
        ${sectionTip(section.tip)}
        ${sectionVisual(section.midImage)}
        ${sectionParagraph(section, secondParagraph, 2)}
        ${sectionVisual(section.endImage)}
        ${remainingParagraphs.map((paragraph, index) => sectionParagraph(section, paragraph, index + 3)).join('')}
        ${sectionGallery(section.endGallery, section.endGalleryLayout || section.galleryLayout)}
      </section>`;
    };
    const storyDate = article.eventDate || article.publishedAt;
    articleRoot.innerHTML = `<article>
      <header class="article-head"><a class="article-back" href="/magazine/">← Magazine 전체보기</a><div class="article-meta"><span>${article.category}</span><time datetime="${storyDate}">${dateLabel(storyDate)}</time></div><h1>${article.title}</h1><h2>${article.titleKo}</h2></header>
      <div class="article-hero${heroLayoutClass(article.heroLayout)}"${heroStyle(article.heroNaturalWidth)}><img src="${article.image}" alt="${article.imageAlt || article.titleKo}">${article.heroNote ? `<span>${article.heroNote}</span>` : ''}</div>
      ${facts ? `<div class="article-facts">${facts}</div>` : ''}
      <section class="article-share" aria-label="에피소드 공유 및 가입 안내"><div><span>Share This Episode</span><strong>${article.titleKo}</strong></div><div class="article-share-actions"><button class="share-primary" type="button" data-share>바로 공유하기 <b aria-hidden="true">↗</b></button><a class="share-guide" href="/#club-guide">가입 안내 확인</a></div><p class="share-status" role="status" aria-live="polite"></p></section>
      ${raceFlow ? `<section class="article-race-flow"><div class="eyebrow">Race in Three Acts</div><div class="race-flow-grid">${raceFlow}</div></section>` : ''}
      ${article.pullQuote ? `<blockquote class="article-pullquote">${article.pullQuote}</blockquote>` : ''}
      <div class="article-content"><p class="article-lead">${article.lead}</p><div class="article-body">
        ${results ? `<section class="article-results"><div class="eyebrow">Final Standing</div><ol>${results}</ol></section>` : ''}
        ${article.sections.map(sectionMarkup).join('')}${article.sections.filter(atArticleEnd).map(section => sectionExplainer(section.explainer)).join('')}
      </div></div>
      ${gallery ? `<section class="article-gallery"><div class="eyebrow">Final Frames</div><div>${gallery}</div></section>` : ''}
    </article>`;

    const shareStatus = articleRoot.querySelector('.share-status');
    const copyShareUrl = async () => {
      let copied = false;
      try {
        if (!navigator.clipboard?.writeText) throw new Error('Clipboard API unavailable');
        await Promise.race([
          navigator.clipboard.writeText(canonicalUrl),
          new Promise((_, reject) => setTimeout(() => reject(new Error('Clipboard timeout')), 800))
        ]);
        copied = true;
      } catch {
        const input = Object.assign(document.createElement('textarea'), { value: canonicalUrl });
        input.setAttribute('readonly', '');
        input.style.position = 'fixed';
        input.style.opacity = '0';
        document.body.appendChild(input);
        input.select();
        copied = document.execCommand('copy');
        input.remove();
      }
      shareStatus.textContent = copied ? '링크를 복사했습니다.' : '주소창의 링크를 복사해 주세요.';
    };
    articleRoot.querySelector('[data-share]').addEventListener('click', async () => {
      if (!navigator.share) return copyShareUrl();
      try {
        await navigator.share({ title: article.titleKo, text: article.excerpt, url: canonicalUrl });
        shareStatus.textContent = '공유했습니다.';
      } catch (error) {
        if (error.name !== 'AbortError') await copyShareUrl();
      }
    });

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
    relatedGrid.innerHTML = related.map(item => `<a class="related-card" href="${articleUrl(item)}"><img src="${item.image}" alt="${item.imageAlt || item.titleKo}"><div><small>${item.category}</small><h3>${item.titleKo}</h3></div></a>`).join('');
  }
})();
