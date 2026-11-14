/* First-party page-view totals only. No browser/event identifiers or consent UI. */
(() => {
  'use strict';
  if (window.AsteriaAnalytics?.mode === 'pageviews_only') return;
  const ENDPOINT = '/api/v1/analytics/event';
  let sent = false;

  function currentPath() {
    if (location.pathname === '/magazine/article.html') {
      const slug = document.getElementById('articleRoot')?.dataset.articleSlug;
      return /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug || '') ? `/magazine/${slug}/` : null;
    }
    const path = location.pathname;
    return path === '/' || path === '/magazine/' || /^\/magazine\/[a-z0-9]+(?:-[a-z0-9]+)*\/$/.test(path) ? path : null;
  }
  function sendPageView() {
    if (sent || document.visibilityState !== 'visible') return;
    const path = currentPath();
    if (!path) return;
    sent = true;
    fetch(ENDPOINT, {
      method: 'POST', mode: 'same-origin', credentials: 'omit', referrerPolicy: 'no-referrer',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({kind: 'page_view', path}), keepalive: true
    }).catch(() => {});
  }
  function initialize() {
    // Remove only this feature's retired keys. Never read their values or
    // touch unrelated site storage. Unavailable storage does not block views.
    for (const key of ['asteria_analytics_browser_v1', 'asteria_analytics_consent_v1']) {
      try { localStorage.removeItem(key); } catch { /* storage unavailable */ }
    }
    document.querySelectorAll('.asteria-analytics-panel, .asteria-analytics-manager').forEach(node => node.remove());
    document.addEventListener('visibilitychange', sendPageView);
    sendPageView();
  }
  window.AsteriaAnalytics = Object.freeze({mode: 'pageviews_only'});
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize, {once: true});
  else initialize();
})();
