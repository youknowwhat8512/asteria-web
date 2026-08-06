// HTTP concerns: Origin allowlist, credentialed CORS, cookie flags, CSRF,
// and safe JSON responses. Pure functions over the Web `Request`/`Response`
// types, so they run under both Deno and `node --test`.

export const SESSION_COOKIE = "veronica_session";
export const CSRF_HEADER = "x-csrf-token";
export const SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600; // 30 days

/** Parse a comma/space separated allowlist string into an exact-match set. */
export function parseOriginAllowlist(raw: string | undefined | null): string[] {
  if (!raw) return [];
  return raw
    .split(/[\s,]+/)
    .map((o) => o.trim().replace(/\/$/, ""))
    .filter((o) => o.length > 0);
}

export function isOriginAllowed(origin: string | null, allowlist: string[]): boolean {
  if (!origin) return false;
  const normalized = origin.replace(/\/$/, "");
  return allowlist.includes(normalized);
}

/**
 * CORS headers for credentialed requests. We NEVER emit `*` when credentials
 * are in play; the ACAO header is echoed back only for an allowlisted origin.
 * A disallowed origin gets no ACAO header, so the browser blocks the response.
 */
export function corsHeaders(origin: string | null, allowlist: string[]): Record<string, string> {
  const headers: Record<string, string> = {
    Vary: "Origin",
    "Access-Control-Allow-Methods": "GET,POST,PATCH,OPTIONS",
    // Idempotency-Key is a custom header the create flow sends; it must be in the
    // credentialed preflight allowlist or the browser blocks the POST.
    "Access-Control-Allow-Headers": `Content-Type, ${CSRF_HEADER}, Idempotency-Key`,
    "Access-Control-Max-Age": "600",
  };
  if (isOriginAllowed(origin, allowlist)) {
    headers["Access-Control-Allow-Origin"] = origin!.replace(/\/$/, "");
    headers["Access-Control-Allow-Credentials"] = "true";
  }
  return headers;
}

// ---------------------------------------------------------------------------
// Cookies
// ---------------------------------------------------------------------------
export interface CookieOptions {
  maxAge?: number; // seconds; 0 clears
  sameSite?: "Strict" | "Lax" | "None";
  path?: string;
}

export function buildCookie(name: string, value: string, opts: CookieOptions = {}): string {
  // The API is reached through a SAME-ORIGIN reverse proxy (Cloudflare Pages
  // Function at /api/veronica), so the session cookie is first-party. We default
  // to SameSite=Lax (Strict is also acceptable) rather than None, which removes
  // any reliance on third-party cookies. SameSite is still configurable via env.
  // CSRF is defended by the Origin allowlist plus a session-bound double-submit
  // token; the cookie stays HttpOnly + Secure regardless.
  const sameSite = opts.sameSite ?? "Lax";
  const path = opts.path ?? "/";
  const parts = [
    `${name}=${value}`,
    `Path=${path}`,
    "HttpOnly",
    "Secure",
    `SameSite=${sameSite}`,
  ];
  if (opts.maxAge !== undefined) parts.push(`Max-Age=${opts.maxAge}`);
  return parts.join("; ");
}

export function clearCookie(
  name: string,
  path = "/",
  sameSite: "Strict" | "Lax" | "None" = "Lax",
): string {
  return [`${name}=`, `Path=${path}`, "HttpOnly", "Secure", `SameSite=${sameSite}`, "Max-Age=0"]
    .join(
      "; ",
    );
}

export function parseCookies(header: string | null): Record<string, string> {
  const out: Record<string, string> = {};
  if (!header) return out;
  for (const pair of header.split(";")) {
    const idx = pair.indexOf("=");
    if (idx === -1) continue;
    const k = pair.slice(0, idx).trim();
    const v = pair.slice(idx + 1).trim();
    if (k) out[k] = v;
  }
  return out;
}

// CSRF is enforced in the router: it requires an allowlisted Origin AND an
// X-CSRF-Token header whose SHA-256 matches the hash bound to the session at
// login. The custom header forces a CORS preflight that only allowlisted
// origins pass, and the token value is never readable cross-origin.

// ---------------------------------------------------------------------------
// JSON responses
// ---------------------------------------------------------------------------
export function jsonResponse(
  status: number,
  body: unknown,
  extraHeaders: Record<string, string> = {},
  cookies: string[] = [],
): Response {
  const headers = new Headers({
    "Content-Type": "application/json; charset=utf-8",
    ...extraHeaders,
  });
  for (const c of cookies) headers.append("Set-Cookie", c);
  return new Response(JSON.stringify(body), { status, headers });
}

/** Client-safe error — never leaks internal detail, secrets, or stack traces. */
export function errorResponse(
  status: number,
  code: string,
  extraHeaders: Record<string, string> = {},
): Response {
  return jsonResponse(status, { error: code }, extraHeaders);
}
