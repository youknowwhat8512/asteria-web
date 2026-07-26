// Cloudflare Pages Function: first-party proxy for the Veronica Supabase Edge
// Function. The public, non-secret upstream URL has a production default and
// can be overridden with VERONICA_UPSTREAM_BASE for preview deployments.
const DEFAULT_UPSTREAM_BASE =
  "https://wbvbvfqrdhpsjmcwzouv.supabase.co/functions/v1/veronica-booking";

export async function onRequest(context) {
  const base = String(context.env.VERONICA_UPSTREAM_BASE || DEFAULT_UPSTREAM_BASE).replace(/\/$/, "");
  if (!base.startsWith("https://")) {
    return Response.json({ error: "proxy_not_configured" }, { status: 503 });
  }

  const parts = context.params.path;
  const pathParts = Array.isArray(parts) ? parts : String(parts || "").split("/").filter(Boolean);
  const suffix = pathParts.map(encodeURIComponent).join("/");
  const incoming = new URL(context.request.url);
  const upstream = new URL(`${base}/${suffix}`);
  upstream.search = incoming.search;

  const headers = new Headers(context.request.headers);
  headers.delete("host");
  const clientIp = context.request.headers.get("cf-connecting-ip");
  if (clientIp) headers.set("x-forwarded-for", clientIp);
  headers.delete("cf-connecting-ip");
  headers.delete("cf-ray");

  const init = {
    method: context.request.method,
    headers,
    redirect: "manual",
  };
  if (!/^(GET|HEAD)$/i.test(context.request.method)) init.body = context.request.body;

  const response = await fetch(upstream, init);
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: response.headers,
  });
}

// Cloudflare Worker entrypoint for sites whose static origin is not Pages.
// The route is scoped to /api/veronica* at the zone edge.
export default {
  fetch(request, env) {
    const pathname = new URL(request.url).pathname;
    const rawPath = pathname.replace(/^\/api\/veronica\/?/, "");
    return onRequest({
      request,
      env,
      params: { path: rawPath ? rawPath.split("/") : [] },
    });
  },
};
