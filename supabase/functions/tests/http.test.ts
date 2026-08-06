import test from "node:test";
import assert from "node:assert/strict";
import {
  buildCookie,
  clearCookie,
  corsHeaders,
  isOriginAllowed,
  parseCookies,
  parseOriginAllowlist,
  SESSION_COOKIE,
  SESSION_MAX_AGE_SECONDS,
} from "../_shared/http.ts";

const ALLOW = parseOriginAllowlist("https://asteria.club, https://www.asteria.club");

test("origin allowlist parsing + matching (exact, trailing slash tolerant)", () => {
  assert.deepEqual(ALLOW, ["https://asteria.club", "https://www.asteria.club"]);
  assert.equal(isOriginAllowed("https://asteria.club", ALLOW), true);
  assert.equal(isOriginAllowed("https://asteria.club/", ALLOW), true);
  assert.equal(isOriginAllowed("https://evil.example", ALLOW), false);
  assert.equal(isOriginAllowed(null, ALLOW), false);
});

test("credentialed CORS never uses wildcard and echoes only allowlisted origin", () => {
  const good = corsHeaders("https://asteria.club", ALLOW);
  assert.equal(good["Access-Control-Allow-Origin"], "https://asteria.club");
  assert.equal(good["Access-Control-Allow-Credentials"], "true");
  assert.equal(good["Vary"], "Origin");
  assert.match(good["Access-Control-Allow-Headers"], /Idempotency-Key/i);
  assert.match(good["Access-Control-Allow-Headers"], /x-csrf-token/i);
  assert.notEqual(good["Access-Control-Allow-Origin"], "*");

  const bad = corsHeaders("https://evil.example", ALLOW);
  assert.equal(bad["Access-Control-Allow-Origin"], undefined);
  assert.equal(bad["Access-Control-Allow-Credentials"], undefined);
});

test("session cookie carries HttpOnly, Secure, SameSite, Max-Age ~30d", () => {
  const c = buildCookie(SESSION_COOKIE, "opaque-token", {
    maxAge: SESSION_MAX_AGE_SECONDS,
    sameSite: "Lax",
    path: "/api/veronica",
  });
  assert.match(c, /HttpOnly/);
  assert.match(c, /Secure/);
  assert.match(c, /SameSite=Lax/);
  assert.match(c, new RegExp(`Max-Age=${SESSION_MAX_AGE_SECONDS}`));
  assert.equal(SESSION_MAX_AGE_SECONDS, 2592000);
  assert.match(c, /Path=\/api\/veronica/);
});

test("clearCookie expires the session cookie", () => {
  const c = clearCookie(SESSION_COOKIE);
  assert.match(c, /Max-Age=0/);
  assert.match(c, /HttpOnly/);
  assert.match(c, /Secure/);
});

test("parseCookies handles multiple pairs and stray spaces", () => {
  const c = parseCookies("veronica_session=abc; other=1;  x=y");
  assert.equal(c.veronica_session, "abc");
  assert.equal(c.other, "1");
  assert.equal(c.x, "y");
  assert.deepEqual(parseCookies(null), {});
});
