// Request router for the Veronica booking API.
//
// The router depends only on injected interfaces (Store, CalendarAdapter, a
// clock and config), never on Deno/Supabase globals, so the entire request
// lifecycle — auth, CSRF, validation, privacy projection, conflict handling —
// is exercised end-to-end against an in-memory fake under `node --test`.
//
// Security posture:
//  * service-role DB access lives only in the Store implementation, never here.
//  * every response carries credentialed CORS headers (no wildcard).
//  * mutations require an allowlisted Origin + a session-bound CSRF token.
//  * errors are opaque codes; no secrets, phone numbers, or internals leak.

import type { CalendarAdapter } from "./calendar.ts";
import {
  bytesToBase64Url,
  constantTimeEqualBytes,
  encryptContact,
  randomBookingCode,
  randomToken,
  sha256,
  verifyPassword,
} from "./crypto.ts";
import {
  buildCookie,
  clearCookie,
  corsHeaders,
  CSRF_HEADER,
  errorResponse,
  isOriginAllowed,
  jsonResponse,
  parseCookies,
  SESSION_COOKIE,
  SESSION_MAX_AGE_SECONDS,
} from "./http.ts";
import {
  assertRedacted,
  buildOutboxPayload,
  newOutboxIdempotencyKey,
  type OutboxEventType,
} from "./outbox.ts";
import {
  projectBookingDetail,
  projectBookingSummary,
  projectMember,
  projectPublicBooking,
  type PublicBlockEntry,
} from "./privacy.ts";
import {
  ConflictError,
  type CreateBookingArgs,
  InvariantError,
  NotFoundError,
  type PassengerRow,
  type Store,
} from "./store.ts";
import {
  type CreateBookingInput,
  isUuid,
  validateAvailabilityQuery,
  validateCreateBooking,
  validateLogin,
  validatePatchBooking,
} from "./validation.ts";

export interface RouterConfig {
  loginId: string;
  passwordHash: string;
  encryptionKey: Uint8Array;
  sessionVersion: number;
  originAllowlist: string[];
  cookieSameSite: "Strict" | "Lax" | "None";
  cookiePath: string;
}

export interface Deps {
  store: Store;
  config: RouterConfig;
  calendar: CalendarAdapter;
  now: () => Date;
}

const LOGIN_WINDOW_MS = 15 * 60 * 1000;
const LOGIN_MAX_FAILURES = 10;

function getClientIp(req: Request): string {
  const xff = req.headers.get("x-forwarded-for");
  if (xff) return xff.split(",")[0].trim();
  return req.headers.get("cf-connecting-ip") ?? "unknown";
}

/** Strip the function-name prefix so routes match regardless of mount path. */
function routePath(url: URL): string {
  // Supabase serves functions under /functions/v1/<name>/... locally the path
  // may be /<name>/... . Reduce to the suffix after "veronica-booking".
  const marker = "/veronica-booking";
  const idx = url.pathname.indexOf(marker);
  let p = idx === -1 ? url.pathname : url.pathname.slice(idx + marker.length);
  if (p === "") p = "/";
  return p.replace(/\/+$/, "") || "/";
}

// A client-supplied idempotency key must be a bounded, opaque token. Requiring
// the client to supply it (rather than the server minting one) is what makes a
// retried create safe: the same key replays the original booking instead of
// duplicating it. Reject arbitrary/unbounded values before they reach the DB.
const IDEMPOTENCY_KEY_RE = /^[A-Za-z0-9_.:-]{8,200}$/;
function validIdempotencyKey(k: string | null): k is string {
  return typeof k === "string" && IDEMPOTENCY_KEY_RE.test(k);
}

// A booking id arrives percent-encoded in the path. Decode defensively: a
// malformed escape (e.g. "%ZZ") makes decodeURIComponent throw, and a non-UUID
// value would reach the DB as an invalid uuid cast. Both must be a clean 400,
// never an uncaught 500. Returns the canonical id or null when unusable.
function safeDecodeBookingId(raw: string): string | null {
  let decoded: string;
  try {
    decoded = decodeURIComponent(raw);
  } catch {
    return null;
  }
  return isUuid(decoded) ? decoded : null;
}

async function readJson(req: Request): Promise<unknown | undefined> {
  const ct = req.headers.get("content-type") ?? "";
  if (!ct.includes("application/json")) return undefined;
  const text = await req.text();
  if (text.length > 64 * 1024) return undefined; // hard cap
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

interface AuthedSession {
  sessionHash: Uint8Array;
  csrfHash: Uint8Array;
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------
export async function handleRequest(req: Request, deps: Deps): Promise<Response> {
  const url = new URL(req.url);
  const origin = req.headers.get("origin");
  const cors = corsHeaders(origin, deps.config.originAllowlist);
  const path = routePath(url);
  const method = req.method.toUpperCase();

  if (method === "OPTIONS") {
    return new Response(null, { status: 204, headers: cors });
  }

  try {
    // ---- public routes ---------------------------------------------------
    if (path === "/availability" && method === "GET") {
      return await handleAvailability(url, deps, cors);
    }
    if (path === "/login" && method === "POST") {
      return await handleLogin(req, deps, cors);
    }

    // ---- authenticated routes -------------------------------------------
    const session = await authenticate(req, deps);
    if (!session) return errorResponse(401, "unauthorized", cors);

    if (path === "/session" && method === "GET") {
      return await handleSessionResume(req, session, deps, cors);
    }
    if (path === "/logout" && method === "POST") {
      return await handleLogout(req, session, deps, cors);
    }
    if (path === "/members" && method === "GET") {
      return await handleMembers(deps, cors);
    }
    if (path === "/bookings" && method === "GET") {
      return await handleListBookings(deps, cors);
    }
    if (path === "/bookings" && method === "POST") {
      return await handleCreateBooking(req, session, deps, cors);
    }

    const detailMatch = path.match(/^\/bookings\/([^/]+)$/);
    if (detailMatch) {
      const id = safeDecodeBookingId(detailMatch[1]);
      if (!id) return errorResponse(400, "invalid_id", cors);
      if (method === "GET") return await handleBookingDetail(id, deps, cors);
      if (method === "PATCH") return await handlePatchBooking(id, req, session, deps, cors);
    }
    const cancelMatch = path.match(/^\/bookings\/([^/]+)\/cancel$/);
    if (cancelMatch && method === "POST") {
      const id = safeDecodeBookingId(cancelMatch[1]);
      if (!id) return errorResponse(400, "invalid_id", cors);
      return await handleCancelBooking(id, req, session, deps, cors);
    }

    return errorResponse(404, "not_found", cors);
  } catch (err) {
    if (err instanceof ConflictError) return errorResponse(409, "time_conflict", cors);
    if (err instanceof InvariantError) return errorResponse(422, "invariant_violation", cors);
    if (err instanceof NotFoundError) return errorResponse(404, "not_found", cors);
    // Never surface raw error text — could contain internal detail.
    return errorResponse(500, "internal_error", cors);
  }
}

// ---------------------------------------------------------------------------
// Auth helpers
// ---------------------------------------------------------------------------
async function authenticate(req: Request, deps: Deps): Promise<AuthedSession | null> {
  const cookies = parseCookies(req.headers.get("cookie"));
  const token = cookies[SESSION_COOKIE];
  if (!token) return null;
  const hash = await sha256(token);
  const row = await deps.store.getSessionByHash(hash);
  if (!row) return null;
  if (row.revoked_at) return null;
  if (new Date(row.expires_at).getTime() <= deps.now().getTime()) return null;
  if (row.session_version !== deps.config.sessionVersion) return null;
  return { sessionHash: hash, csrfHash: row.csrf_token_hash };
}

/** Origin allowlist + session-bound CSRF token check for mutations. */
async function csrfOk(req: Request, session: AuthedSession, deps: Deps): Promise<boolean> {
  if (!isOriginAllowed(req.headers.get("origin"), deps.config.originAllowlist)) return false;
  const headerToken = req.headers.get(CSRF_HEADER);
  if (!headerToken) return false;
  const headerHash = await sha256(headerToken);
  return constantTimeEqualBytes(headerHash, session.csrfHash);
}

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------
async function handleAvailability(
  url: URL,
  deps: Deps,
  cors: Record<string, string>,
): Promise<Response> {
  const parsed = validateAvailabilityQuery(
    url.searchParams.get("from"),
    url.searchParams.get("to"),
  );
  if (!parsed.ok) return errorResponse(400, "invalid_query", cors);
  const { from, to } = parsed.value!;
  const rows = await deps.store.listActiveBookingsInRange(from.toISOString(), to.toISOString());
  const bookings = rows.map(projectPublicBooking);

  // Calendar blocks only when the integration is available; fail safe otherwise.
  let blocks: PublicBlockEntry[] = [];
  let calendarAvailable = false;
  const cal = await deps.calendar.listBlocks(from.toISOString(), to.toISOString());
  calendarAvailable = cal.available;
  if (cal.available) {
    blocks = cal.blocks.map((b) => ({ startsAt: b.startsAt, endsAt: b.endsAt, label: b.label }));
  }
  return jsonResponse(200, { bookings, blocks, calendarAvailable }, cors);
}

async function handleLogin(
  req: Request,
  deps: Deps,
  cors: Record<string, string>,
): Promise<Response> {
  // Minting a credentialed session is a mutation: reject a non-allowlisted (or
  // missing) Origin BEFORE touching credentials, rate-limit state, or the DB, so
  // a foreign origin can never provoke a Set-Cookie or a recorded attempt.
  if (!isOriginAllowed(req.headers.get("origin"), deps.config.originAllowlist)) {
    return errorResponse(403, "forbidden_origin", cors);
  }
  // A missing/invalid JSON content type is a malformed request, not a failed
  // login: return 400 with NO side effects (no attempt recorded, no session).
  const body = await readJson(req);
  if (body === undefined) return errorResponse(400, "invalid_content_type", cors);
  const parsed = validateLogin(body, deps.config.loginId);
  // Rate-limit key ties failures to the client IP (opaque, hashed on store).
  const attemptKey = bytesToBase64Url(await sha256(`${getClientIp(req)}::${deps.config.loginId}`));
  const since = new Date(deps.now().getTime() - LOGIN_WINDOW_MS).toISOString();
  const failures = await deps.store.countRecentLoginFailures(attemptKey, since);
  if (failures >= LOGIN_MAX_FAILURES) {
    return errorResponse(429, "too_many_attempts", cors);
  }
  if (!parsed.ok) {
    await deps.store.recordLoginAttempt(attemptKey, false);
    return errorResponse(401, "invalid_credentials", cors);
  }
  const valid = await verifyPassword(parsed.value!.password, deps.config.passwordHash);
  await deps.store.recordLoginAttempt(attemptKey, valid);
  if (!valid) return errorResponse(401, "invalid_credentials", cors);

  // Mint an opaque session token + CSRF token; store only their hashes.
  const token = randomToken(32);
  const csrfToken = randomToken(32);
  const tokenHash = await sha256(token);
  const csrfHash = await sha256(csrfToken);
  const expiresAt = new Date(deps.now().getTime() + SESSION_MAX_AGE_SECONDS * 1000).toISOString();
  await deps.store.createSession(tokenHash, csrfHash, deps.config.sessionVersion, expiresAt);

  const cookie = buildCookie(SESSION_COOKIE, token, {
    maxAge: SESSION_MAX_AGE_SECONDS,
    sameSite: deps.config.cookieSameSite,
    path: deps.config.cookiePath,
  });
  return jsonResponse(200, { ok: true, csrfToken }, cors, [cookie]);
}

async function handleLogout(
  req: Request,
  session: AuthedSession,
  deps: Deps,
  cors: Record<string, string>,
): Promise<Response> {
  // Logout is a state-changing mutation (it revokes the session) so it carries
  // the same allowlisted-Origin + session-bound CSRF requirement as every other
  // mutation. Without this a cross-site page could forcibly log the club out.
  if (!(await csrfOk(req, session, deps))) return errorResponse(403, "csrf_failed", cors);
  await deps.store.revokeSession(session.sessionHash);
  const cleared = clearCookie(SESSION_COOKIE, deps.config.cookiePath);
  return jsonResponse(200, { ok: true }, cors, [cleared]);
}

// Session resume: a returning visitor still holds the 30-day HttpOnly session
// cookie but the in-memory CSRF token was lost on reload. This authenticated GET
// re-issues a CSRF token so the client can perform mutations again WITHOUT ever
// persisting the session or CSRF token in localStorage/sessionStorage/URL.
//
// Issuing a token is sensitive, so we keep an Origin check: a cross-site page's
// fetch always carries an Origin header, and if present it must be allowlisted.
// A genuine same-origin GET omits Origin, which is permitted; either way the
// browser only exposes the response body to allowlisted (same-origin) callers.
// The CSRF hash is rotated atomically so a stale token cannot be replayed.
async function handleSessionResume(
  req: Request,
  session: AuthedSession,
  deps: Deps,
  cors: Record<string, string>,
): Promise<Response> {
  const origin = req.headers.get("origin");
  if (origin !== null && !isOriginAllowed(origin, deps.config.originAllowlist)) {
    return errorResponse(403, "forbidden_origin", cors);
  }
  const csrfToken = randomToken(32);
  const csrfHash = await sha256(csrfToken);
  await deps.store.rotateCsrf(session.sessionHash, csrfHash);
  return jsonResponse(200, { ok: true, csrfToken }, cors);
}

async function handleMembers(deps: Deps, cors: Record<string, string>): Promise<Response> {
  const members = (await deps.store.listActiveMembers()).map(projectMember);
  return jsonResponse(200, { members }, cors);
}

async function handleListBookings(deps: Deps, cors: Record<string, string>): Promise<Response> {
  const bookings = (await deps.store.listBookings()).map(projectBookingSummary);
  return jsonResponse(200, { bookings }, cors);
}

async function handleBookingDetail(
  id: string,
  deps: Deps,
  cors: Record<string, string>,
): Promise<Response> {
  const found = await deps.store.getBooking(id);
  if (!found) return errorResponse(404, "not_found", cors);
  const members = await deps.store.listActiveMembers();
  const memberNames = new Map(members.map((m) => [m.id, m.display_name]));
  const detail = await projectBookingDetail(
    found.booking,
    found.passengers,
    deps.config.encryptionKey,
    memberNames,
  );
  return jsonResponse(200, { booking: detail }, cors);
}

/**
 * Resolve the requester's TRUSTED display name from the active-member list.
 * The browser only sends a member id; the name shown and stored is derived here
 * server-side, so a client can never spoof a booker name. Returns null when the
 * id is not an active member (rejected by the caller as an invariant violation).
 */
async function resolveBookerName(memberId: string, deps: Deps): Promise<string | null> {
  const members = await deps.store.listActiveMembers();
  return members.find((m) => m.id === memberId)?.display_name ?? null;
}

/** Build the encrypted DB args shared by create + patch. */
async function toCreateArgs(
  input: CreateBookingInput,
  bookerName: string,
  session: AuthedSession,
  deps: Deps,
  eventType: OutboxEventType,
  bookingCode: string,
): Promise<CreateBookingArgs> {
  const acceptedAt = deps.now().toISOString();
  const passengers: PassengerRow[] = [];
  for (const p of input.passengers) {
    if (p.type === "member") {
      passengers.push({
        passenger_type: "member",
        member_id: p.memberId,
        guest_name: null,
        guest_phone_encrypted: null,
      });
    } else {
      passengers.push({
        passenger_type: "guest",
        member_id: null,
        guest_name: p.name,
        guest_phone_encrypted: await encryptContact(p.phone, deps.config.encryptionKey),
      });
    }
  }
  const outboxPayload = buildOutboxPayload(eventType, {
    id: "",
    booking_code: bookingCode,
    vessel: "veronica",
    starts_at: input.startsAt.toISOString(),
    ends_at: input.endsAt.toISOString(),
    booker_name: bookerName,
    booker_member_id: input.bookerMemberId,
    booker_phone_encrypted: new Uint8Array(),
    departure: "아라마리나",
    destination: input.destination,
    total_people: input.totalPeople,
    responsible_member_id: input.responsibleMemberId,
    status: "confirmed",
    created_at: acceptedAt,
    updated_at: acceptedAt,
    cancelled_at: null,
  }) as unknown as Record<string, unknown>;
  assertRedacted(outboxPayload);

  return {
    booking_code: bookingCode,
    starts_at: input.startsAt.toISOString(),
    ends_at: input.endsAt.toISOString(),
    booker_member_id: input.bookerMemberId,
    destination: input.destination,
    total_people: input.totalPeople,
    responsible_member_id: input.responsibleMemberId,
    waiver_version: input.waiverVersion,
    waiver_accepted_at: acceptedAt,
    privacy_consent_version: input.privacyConsentVersion,
    privacy_consent_accepted_at: acceptedAt,
    passengers,
    session_hash: session.sessionHash,
    api_idempotency_key: "", // set by caller
    outbox_idempotency_key: newOutboxIdempotencyKey(),
    outbox_payload: outboxPayload,
  };
}

async function handleCreateBooking(
  req: Request,
  session: AuthedSession,
  deps: Deps,
  cors: Record<string, string>,
): Promise<Response> {
  if (!(await csrfOk(req, session, deps))) return errorResponse(403, "csrf_failed", cors);
  const idempotencyKey = req.headers.get("idempotency-key");
  if (!validIdempotencyKey(idempotencyKey)) {
    return errorResponse(400, "invalid_idempotency_key", cors);
  }
  const body = await readJson(req);
  const parsed = validateCreateBooking(body);
  if (!parsed.ok) return errorResponse(400, "invalid_body", cors);

  // Resolve the TRUSTED requester name from the active-member list. A non-active
  // or unknown id yields null → 422 before any write (the DB enforces the same).
  const bookerName = await resolveBookerName(parsed.value!.bookerMemberId, deps);
  if (!bookerName) return errorResponse(422, "invariant_violation", cors);

  const bookingCode = randomBookingCode();
  const args = await toCreateArgs(
    parsed.value!,
    bookerName,
    session,
    deps,
    "booking_created",
    bookingCode,
  );
  args.api_idempotency_key = idempotencyKey;
  const result = await deps.store.createBooking(args);
  return jsonResponse(result.replayed ? 200 : 201, {
    id: result.id,
    bookingCode: result.booking_code,
    replayed: result.replayed,
  }, cors);
}

async function handlePatchBooking(
  id: string,
  req: Request,
  session: AuthedSession,
  deps: Deps,
  cors: Record<string, string>,
): Promise<Response> {
  if (!(await csrfOk(req, session, deps))) return errorResponse(403, "csrf_failed", cors);
  const existing = await deps.store.getBooking(id);
  if (!existing) return errorResponse(404, "not_found", cors);
  const body = await readJson(req);
  const parsed = validatePatchBooking(body);
  if (!parsed.ok) return errorResponse(400, "invalid_body", cors);

  // Requester resolves to a trusted name from the active-member list (422 if not
  // active), exactly as on create — an edit can re-assign the requester.
  const bookerName = await resolveBookerName(parsed.value!.bookerMemberId, deps);
  if (!bookerName) return errorResponse(422, "invariant_violation", cors);

  // toCreateArgs also computes waiver_/privacy_consent_* fields, but updateBooking
  // intentionally does not persist them — the original consent captured at creation
  // is preserved. See validatePatchBooking and update_booking (migration 100000).
  const args = await toCreateArgs(
    parsed.value!,
    bookerName,
    session,
    deps,
    "booking_updated",
    existing.booking.booking_code,
  );
  await deps.store.updateBooking({ ...args, id });
  return jsonResponse(200, { ok: true, id }, cors);
}

async function handleCancelBooking(
  id: string,
  req: Request,
  session: AuthedSession,
  deps: Deps,
  cors: Record<string, string>,
): Promise<Response> {
  if (!(await csrfOk(req, session, deps))) return errorResponse(403, "csrf_failed", cors);
  const existing = await deps.store.getBooking(id);
  if (!existing) return errorResponse(404, "not_found", cors);
  const outboxPayload = buildOutboxPayload(
    "booking_cancelled",
    existing.booking,
  ) as unknown as Record<string, unknown>;
  assertRedacted(outboxPayload);
  await deps.store.cancelBooking(id, session.sessionHash, newOutboxIdempotencyKey(), outboxPayload);
  return jsonResponse(200, { ok: true, id }, cors);
}
