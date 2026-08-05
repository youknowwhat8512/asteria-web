(() => {
  const params = new URLSearchParams(window.location.search);
  if (params.get("variant") !== "reporter") return;

  const copy = window.ASTERIA_REPORTER_COPY;
  const article = (window.ASTERIA_ARTICLES || []).find(item => item.slug === "wangsan-to-yeosu-island-delivery-2026");
  if (!article || !copy || !Array.isArray(copy.sections) || copy.sections.length !== article.sections.length) {
    console.error("Reporter variant could not be applied: copy structure mismatch.");
    return;
  }

  article.excerpt = copy.excerpt;
  article.lead = copy.lead;
  article.sections.forEach((section, index) => {
    const replacement = copy.sections[index];
    section.heading = replacement.heading;
    section.body = [...replacement.body];
  });

  document.documentElement.dataset.articleVariant = "reporter";
})();
