// Runtime configuration for the Deno Edge runtime. This is the ONLY module
// that reads secrets from the environment (Supabase Secrets). Secret VALUES
// never appear in source, logs, or client responses — only their presence is
// checked here at cold start.

import { parseEncryptionKey } from "./crypto.ts";
import { parseOriginAllowlist } from "./http.ts";
import type { RouterConfig } from "./router.ts";

// deno-lint-ignore no-explicit-any
declare const Deno: any;

function required(name: string): string {
  const v = Deno.env.get(name);
  if (!v || v.length === 0) {
    // Message names the missing variable only — never its value.
    throw new Error(`missing required secret: ${name}`);
  }
  return v;
}

export interface RuntimeConfig extends RouterConfig {
  supabaseUrl: string;
  serviceRoleKey: string;
  calendar: {
    calendarId?: string;
    oauthClientId?: string;
    oauthClientSecret?: string;
    oauthRefreshToken?: string;
  };
}

export function loadConfig(): RuntimeConfig {
  // The browser reaches the API through a same-origin proxy (/api/veronica), so
  // the session cookie is first-party. Default to Lax (Strict also fine); never
  // depend on SameSite=None third-party cookies.
  const sameSiteRaw = Deno.env.get("ASTERIA_COOKIE_SAMESITE") ?? "Lax";
  const sameSite = (["Strict", "Lax", "None"].includes(sameSiteRaw) ? sameSiteRaw : "Lax") as
    | "Strict"
    | "Lax"
    | "None";
  return {
    loginId: Deno.env.get("ASTERIA_LOGIN_ID") ?? "clubasteria",
    passwordHash: required("ASTERIA_PASSWORD_HASH"),
    encryptionKey: parseEncryptionKey(required("ASTERIA_CONTACT_ENCRYPTION_KEY")),
    sessionVersion: Number.parseInt(Deno.env.get("ASTERIA_SESSION_VERSION") ?? "1", 10) || 1,
    originAllowlist: parseOriginAllowlist(required("ASTERIA_ORIGIN_ALLOWLIST")),
    cookieSameSite: sameSite,
    // Same-origin proxy path. The cookie is scoped to /api/veronica so the
    // browser only replays it on booking-API calls, never the wider site.
    cookiePath: Deno.env.get("ASTERIA_COOKIE_PATH") ?? "/api/veronica",
    // Supabase injects SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY into functions.
    supabaseUrl: required("SUPABASE_URL"),
    serviceRoleKey: required("SUPABASE_SERVICE_ROLE_KEY"),
    calendar: {
      calendarId: Deno.env.get("VERONICA_CALENDAR_ID") ?? undefined,
      oauthClientId: Deno.env.get("VERONICA_CALENDAR_OAUTH_CLIENT_ID") ?? undefined,
      oauthClientSecret: Deno.env.get("VERONICA_CALENDAR_OAUTH_CLIENT_SECRET") ?? undefined,
      oauthRefreshToken: Deno.env.get("VERONICA_CALENDAR_OAUTH_REFRESH_TOKEN") ?? undefined,
    },
  };
}
