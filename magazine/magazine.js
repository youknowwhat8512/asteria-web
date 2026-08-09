(() => {
  const articles = [...(window.ASTERIA_ARTICLES || [])].sort((a, b) => b.publishedAt.localeCompare(a.publishedAt));
  const articleUrl = article => article.url || `/magazine/article.html?slug=${encodeURIComponent(article.slug)}`;
  const dateLabel = value => new Intl.DateTimeFormat('ko-KR', {year:'numeric',month:'long',day:'numeric'}).format(new Date(`${value}T00:00:00`));

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
    const sectionExplainer = explainer => explainer ? `<aside class="article-explainer${explainer.theme ? ` explainer-${explainer.theme}` : ''}" aria-label="${explainer.title}">
      <div class="explainer-kicker">${explainer.kicker}</div>
      <h4>${explainer.title}</h4>
      <p class="explainer-summary">${explainer.summary}</p>
      ${explainer.image ? `<figure class="explainer-media"><img src="${explainer.image.src}" alt="${explainer.image.alt}"${explainer.image.width && explainer.image.height ? ` width="${explainer.image.width}" height="${explainer.image.height}"` : ''} loading="lazy"><figcaption>${explainer.image.caption}</figcaption></figure>` : ''}
      <dl>${explainer.items.map(item => `<div><dt>${item.label}</dt><dd>${item.value}</dd></div>`).join('')}</dl>
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
    const sectionMarkup = section => {
      const [firstParagraph, secondParagraph, ...remainingParagraphs] = section.body;
      return `<section class="article-section">
        ${section.kicker ? `<div class="section-kicker">${section.kicker}</div>` : ''}
        <h3>${section.heading}</h3>
        ${sectionVisual(section.image)}
        ${firstParagraph ? `<p>${firstParagraph}</p>` : ''}
        ${section.intro ? `<p>${section.intro}</p>` : ''}
        ${sectionExplainer(section.explainer)}
        ${sectionGallery(section.gallery, section.galleryLayout)}
        ${sectionTip(section.tip)}
        ${sectionVisual(section.midImage)}
        ${secondParagraph ? `<p>${secondParagraph}</p>` : ''}
        ${sectionVisual(section.endImage)}
        ${remainingParagraphs.map(paragraph => `<p>${paragraph}</p>`).join('')}
        ${sectionGallery(section.endGallery, section.endGalleryLayout || section.galleryLayout)}
      </section>`;
    };
    const storyDate = article.eventDate || article.publishedAt;
    articleRoot.innerHTML = `<article>
      <header class="article-head"><a class="article-back" href="/magazine/">← Magazine 전체보기</a><div class="article-meta"><span>${article.category}</span><time datetime="${storyDate}">${dateLabel(storyDate)}</time></div><h1>${article.title}</h1><h2>${article.titleKo}</h2></header>
      <div class="article-hero"><img src="${article.image}" alt="${article.imageAlt || article.titleKo}">${article.heroNote ? `<span>${article.heroNote}</span>` : ''}</div>
      ${facts ? `<div class="article-facts">${facts}</div>` : ''}
      <section class="article-share" aria-label="에피소드 공유 및 클럽 가입"><div><span>Share This Episode</span><strong>${article.titleKo}</strong></div><div class="article-share-actions"><button class="share-primary" type="button" data-share>바로 공유하기 <b aria-hidden="true">↗</b></button><button type="button" data-apply>클럽 가입 신청</button></div><p class="share-status" role="status" aria-live="polite"></p></section>
      ${raceFlow ? `<section class="article-race-flow"><div class="eyebrow">Race in Three Acts</div><div class="race-flow-grid">${raceFlow}</div></section>` : ''}
      ${article.pullQuote ? `<blockquote class="article-pullquote">${article.pullQuote}</blockquote>` : ''}
      <div class="article-content"><p class="article-lead">${article.lead}</p><div class="article-body">
        ${results ? `<section class="article-results"><div class="eyebrow">Final Standing</div><ol>${results}</ol></section>` : ''}
        ${article.sections.map(sectionMarkup).join('')}
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

    document.body.insertAdjacentHTML('beforeend', `
      <div class="membership-modal" id="membershipModal" aria-hidden="true">
        <div class="membership-modal-card" role="dialog" aria-modal="true" aria-labelledby="membershipModalTitle">
          <div class="membership-modal-head">
            <h3 id="membershipModalTitle">아스테리아 가입 신청</h3>
            <button class="membership-modal-close" type="button" data-membership-close aria-label="가입 신청서 닫기">×</button>
          </div>
          <iframe class="membership-form-embed" title="아스테리아 가입 신청서" data-src="https://docs.google.com/forms/d/e/1FAIpQLSfQgBt8oAAZyvqMs5Ng6H7hI2Br4NpJzzyIpQeuWZ-VSm_h9Q/viewform?embedded=true" loading="lazy" referrerpolicy="strict-origin-when-cross-origin"></iframe>
          <div class="membership-modal-foot"><a href="https://forms.gle/FyeS2XVyxLnpxY3r8" target="_blank" rel="noopener noreferrer">새 창에서 작성 ↗</a></div>
        </div>
      </div>`);
    const applyButton = articleRoot.querySelector('[data-apply]');
    const membershipModal = document.getElementById('membershipModal');
    const membershipForm = membershipModal.querySelector('.membership-form-embed');
    const membershipClose = membershipModal.querySelector('[data-membership-close]');
    const openMembershipModal = () => {
      if (!membershipForm.src) membershipForm.src = membershipForm.dataset.src;
      membershipModal.classList.add('open');
      membershipModal.setAttribute('aria-hidden', 'false');
      document.body.classList.add('membership-modal-open');
      membershipClose.focus();
    };
    const closeMembershipModal = () => {
      membershipModal.classList.remove('open');
      membershipModal.setAttribute('aria-hidden', 'true');
      document.body.classList.remove('membership-modal-open');
      applyButton.focus();
    };
    applyButton.addEventListener('click', openMembershipModal);
    membershipClose.addEventListener('click', closeMembershipModal);
    membershipModal.addEventListener('click', event => { if(event.target === membershipModal) closeMembershipModal(); });
    addEventListener('keydown', event => { if(event.key === 'Escape' && membershipModal.classList.contains('open')) closeMembershipModal(); });

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
