import test from "node:test";
import assert from "node:assert/strict";
import { pbkdf2Sync, randomBytes as nodeRandomBytes } from "node:crypto";
import { type Deps, handleRequest, type RouterConfig } from "../_shared/router.ts";
import { parseEncryptionKey } from "../_shared/crypto.ts";
import { UnconfiguredCalendarAdapter } from "../_shared/calendar.ts";
import { FakeStore } from "./fake_store.ts";

const ORIGIN = "https://asteria.club";
const PASSWORD = "correct-horse-사랑-2026";
const M1 = "a57e51a0-0000-4000-8000-000000000001";
const M2 = "a57e51a0-0000-4000-8000-000000000002";
const UNKNOWN_MEMBER = "a57e51a0-0000-4000-8000-0000000000ff";

function djangoHash(password: string): string {
  const salt = "routerTestSalt";
  const dk = pbkdf2Sync(password, salt, 50000, 32, "sha256");
  return `pbkdf2_sha256$50000$${salt}$${dk.toString("base64")}`;
}

function makeDeps(store: FakeStore): Deps {
  const config: RouterConfig = {
    loginId: "clubasteria",
    passwordHash: djangoHash(PASSWORD),
    encryptionKey: parseEncryptionKey(nodeRandomBytes(32).toString("base64")),
    sessionVersion: 1,
    originAllowlist: [ORIGIN],
    cookieSameSite: "Lax",
    cookiePath: "/api/veronica",
  };
  return {
    store,
    config,
    calendar: new UnconfiguredCalendarAdapter(),
    now: () => new Date("2026-07-27T00:00:00.000Z"),
  };
}

const BASE = "https://ref.functions.supabase.co/veronica-booking";

let idemCounter = 0;
function nextIdem(): string {
  idemCounter += 1;
  return `test-idem-key-${idemCounter}`;
}

function req(
  path: string,
  init: RequestInit & { cookie?: string; csrf?: string; idem?: string } = {},
): Request {
  const headers = new Headers(init.headers);
  if (init.body) headers.set("content-type", "application/json");
  if (init.cookie) headers.set("cookie", init.cookie);
  if (init.csrf) headers.set("x-csrf-token", init.csrf);
  if (init.idem) headers.set("idempotency-key", init.idem);
  headers.set("origin", (init as { origin?: string }).origin ?? ORIGIN);
  return new Request(BASE + path, { ...init, headers });
}

function sessionCookie(res: Response): string {
  const setCookie = res.headers.get("set-cookie") ?? "";
  const m = setCookie.match(/veronica_session=([^;]*)/);
  return `veronica_session=${m ? m[1] : ""}`;
}

async function login(_store: FakeStore, deps: Deps): Promise<{ cookie: string; csrf: string }> {
  const res = await handleRequest(
    req("/login", {
      method: "POST",
      body: JSON.stringify({ id: "clubasteria", password: PASSWORD }),
    }),
    deps,
  );
  assert.equal(res.status, 200, "login should succeed");
  const body = await res.json();
  return { cookie: sessionCookie(res), csrf: body.csrfToken };
}

function bookingBody(over: Record<string, unknown> = {}) {
  return JSON.stringify({
    startsAt: "2026-08-01T09:00:00.000Z",
    endsAt: "2026-08-01T12:00:00.000Z",
    destination: "덕적도",
    totalPeople: 2,

    bookerMemberId: M1,
    passengers: [
      { type: "member", memberId: M1 },
      { type: "guest", name: "홍길동", phone: "01098765432" },
    ],
    waiverVersion: "waiver-v1",
    waiverAccepted: true,
    privacyConsentVersion: "privacy-v1",
    privacyConsentAccepted: true,
    ...JSON.parse(JSON.stringify(over)),
  });
}

test("login sets HttpOnly Secure SameSite cookie and returns a csrf token", async () => {
  const store = new FakeStore();
  const deps = makeDeps(store);
  const res = await handleRequest(
    req("/login", {
      method: "POST",
      body: JSON.stringify({ id: "clubasteria", password: PASSWORD }),
    }),
    deps,
  );
  assert.equal(res.status, 200);
  const setCookie = res.headers.get("set-cookie") ?? "";
  assert.match(setCookie, /veronica_session=/);
  assert.match(setCookie, /HttpOnly/);
  assert.match(setCookie, /Secure/);
  assert.match(setCookie, /SameSite=Lax/);
  assert.match(setCookie, /Path=\/api\/veronica/);
  const body = await res.json();
  assert.ok(body.csrfToken && typeof body.csrfToken === "string");
});

test("login rejects wrong password and enforces rate limit", async () => {
  const store = new FakeStore();
  const deps = makeDeps(store);
  for (let i = 0; i < 10; i++) {
    const res = await handleRequest(
      req("/login", {
        method: "POST",
        body: JSON.stringify({ id: "clubasteria", password: "nope" }),
      }),
      deps,
    );
    assert.equal(res.status, 401);
  }
  const limited = await handleRequest(
    req("/login", {
      method: "POST",
      body: JSON.stringify({ id: "clubasteria", password: PASSWORD }),
    }),
    deps,
  );
  assert.equal(limited.status, 429, "should be rate limited after 10 failures");
});

test("authenticated routes require a valid session", async () => {
  const store = new FakeStore();
  const deps = makeDeps(store);
  assert.equal((await handleRequest(req("/members", { method: "GET" }), deps)).status, 401);
  assert.equal((await handleRequest(req("/bookings", { method: "GET" }), deps)).status, 401);
});

test("full lifecycle: create → list → detail → patch → cancel", async () => {
  const store = new FakeStore();
  const deps = makeDeps(store);
  const { cookie, csrf } = await login(store, deps);

  // members visible after login
  const membersRes = await handleRequest(req("/members", { method: "GET", cookie }), deps);
  assert.equal(membersRes.status, 200);
  assert.ok((await membersRes.json()).members.length >= 2);

  // create
  const createRes = await handleRequest(
    req("/bookings", { method: "POST", cookie, csrf, idem: nextIdem(), body: bookingBody() }),
    deps,
  );
  assert.equal(createRes.status, 201, await createRes.clone().text());
  const created = await createRes.json();
  assert.match(created.bookingCode, /^VER-/);
  const id = created.id;

  // list
  const listRes = await handleRequest(req("/bookings", { method: "GET", cookie }), deps);
  const list = await listRes.json();
  assert.equal(list.bookings.length, 1);
  assert.ok(!JSON.stringify(list).includes("01012345678"), "list must not leak phone");

  // detail: requester name is derived server-side from the member id; no phone
  const detailRes = await handleRequest(req(`/bookings/${id}`, { method: "GET", cookie }), deps);
  const detail = await detailRes.json();
  assert.equal(detail.booking.bookerMemberId, M1);
  assert.equal(detail.booking.bookerName, "이동준"); // M1 display name, not client-supplied
  assert.equal(detail.booking.bookerPhone, null); // new bookings carry no requester phone
  assert.equal(detail.booking.passengers[1].phone, "01098765432");

  // patch destination
  const patchRes = await handleRequest(
    req(`/bookings/${id}`, {
      method: "PATCH",
      cookie,
      csrf,
      body: bookingBody({ destination: "영흥도" }),
    }),
    deps,
  );
  assert.equal(patchRes.status, 200);
  const afterPatch = await handleRequest(req(`/bookings/${id}`, { method: "GET", cookie }), deps);
  assert.equal((await afterPatch.json()).booking.destination, "영흥도");

  // cancel
  const cancelRes = await handleRequest(
    req(`/bookings/${id}/cancel`, { method: "POST", cookie, csrf }),
    deps,
  );
  assert.equal(cancelRes.status, 200);
  assert.equal(store.bookings[0].status, "cancelled");

  // history + outbox recorded
  assert.ok(store.history.some((h) => h.action === "created"));
  assert.ok(store.history.some((h) => h.action === "cancelled"));
  assert.equal(store.outbox.length, 3);
});

test("mutations without CSRF token or cross-origin are rejected", async () => {
  const store = new FakeStore();
  const deps = makeDeps(store);
  const { cookie, csrf } = await login(store, deps);

  // missing csrf
  const noCsrf = await handleRequest(
    req("/bookings", { method: "POST", cookie, body: bookingBody() }),
    deps,
  );
  assert.equal(noCsrf.status, 403);

  // wrong origin (even with correct csrf value)
  const crossOrigin = await handleRequest(
    req(
      "/bookings",
      {
        method: "POST",
        cookie,
        csrf,
        body: bookingBody(),
        origin: "https://evil.example",
      } as never,
    ),
    deps,
  );
  assert.equal(crossOrigin.status, 403);
});

test("overlapping bookings conflict with 409; boundary-touch allowed", async () => {
  const store = new FakeStore();
  const deps = makeDeps(store);
  const { cookie, csrf } = await login(store, deps);

  const first = await handleRequest(
    req("/bookings", { method: "POST", cookie, csrf, idem: nextIdem(), body: bookingBody() }),
    deps,
  );
  assert.equal(first.status, 201);

  // overlapping 10:00-13:00 → conflict
  const overlap = await handleRequest(
    req("/bookings", {
      method: "POST",
      cookie,
      csrf,
      idem: nextIdem(),
      body: bookingBody({
        startsAt: "2026-08-01T10:00:00.000Z",
        endsAt: "2026-08-01T13:00:00.000Z",
      }),
    }),
    deps,
  );
  assert.equal(overlap.status, 409);

  // boundary touch 12:00-14:00 → allowed
  const touch = await handleRequest(
    req("/bookings", {
      method: "POST",
      cookie,
      csrf,
      idem: nextIdem(),
      body: bookingBody({
        startsAt: "2026-08-01T12:00:00.000Z",
        endsAt: "2026-08-01T14:00:00.000Z",
      }),
    }),
    deps,
  );
  assert.equal(touch.status, 201, await touch.clone().text());
});

test("idempotency-key replays the same booking instead of duplicating", async () => {
  const store = new FakeStore();
  const deps = makeDeps(store);
  const { cookie, csrf } = await login(store, deps);
  const headers = { "idempotency-key": "fixed-key-123" };

  const a = await handleRequest(
    req("/bookings", { method: "POST", cookie, csrf, headers, body: bookingBody() }),
    deps,
  );
  assert.equal(a.status, 201);
  const b = await handleRequest(
    req("/bookings", { method: "POST", cookie, csrf, headers, body: bookingBody() }),
    deps,
  );
  assert.equal(b.status, 200);
  assert.equal((await b.json()).replayed, true);
  assert.equal(store.bookings.length, 1);
});

test("public availability leaks no phone/passenger/internal id; calendar unconfigured", async () => {
  const store = new FakeStore();
  const deps = makeDeps(store);
  const { cookie, csrf } = await login(store, deps);
  await handleRequest(
    req("/bookings", { method: "POST", cookie, csrf, idem: nextIdem(), body: bookingBody() }),
    deps,
  );

  // note: no cookie → public request
  const res = await handleRequest(
    req("/availability?from=2026-08-01T00:00:00.000Z&to=2026-08-02T00:00:00.000Z", {
      method: "GET",
    }),
    deps,
  );
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.calendarAvailable, false);
  assert.equal(body.bookings.length, 1);
  assert.deepEqual(Object.keys(body.bookings[0]).sort(), ["endsAt", "startsAt"]);
  const serialized = JSON.stringify(body);
  assert.ok(!serialized.includes("예약자"));
  assert.ok(!serialized.includes("덕적도"));
  assert.ok(!serialized.includes("01012345678"));
  assert.ok(!serialized.includes(store.bookings[0].id));
  assert.ok(!serialized.includes(M1));
});

test("session resume rotates CSRF and keeps the 30-day cookie useful", async () => {
  const store = new FakeStore();
  const deps = makeDeps(store);
  const { cookie, csrf: oldCsrf } = await login(store, deps);
  const resumed = await handleRequest(req("/session", { method: "GET", cookie }), deps);
  assert.equal(resumed.status, 200);
  const { csrfToken: newCsrf } = await resumed.json();
  assert.ok(newCsrf && newCsrf !== oldCsrf);
  const stale = await handleRequest(
    req("/logout", { method: "POST", cookie, csrf: oldCsrf }),
    deps,
  );
  assert.equal(stale.status, 403);
  const current = await handleRequest(
    req("/logout", { method: "POST", cookie, csrf: newCsrf }),
    deps,
  );
  assert.equal(current.status, 200);
});

test("malformed booking route ids return 400", async () => {
  const store = new FakeStore();
  const deps = makeDeps(store);
  const { cookie } = await login(store, deps);
  assert.equal(
    (await handleRequest(req("/bookings/not-a-uuid", { method: "GET", cookie }), deps)).status,
    400,
  );
  assert.equal(
    (await handleRequest(req("/bookings/%ZZ", { method: "GET", cookie }), deps)).status,
    400,
  );
});

test("cancelled bookings cannot be edited or cancelled twice", async () => {
  const store = new FakeStore();
  const deps = makeDeps(store);
  const { cookie, csrf } = await login(store, deps);
  const created = await handleRequest(
    req("/bookings", { method: "POST", cookie, csrf, idem: nextIdem(), body: bookingBody() }),
    deps,
  );
  const id = (await created.json()).id;
  assert.equal(
    (await handleRequest(req(`/bookings/${id}/cancel`, { method: "POST", cookie, csrf }), deps))
      .status,
    200,
  );
  const historyCount = store.history.length;
  const outboxCount = store.outbox.length;
  assert.equal(
    (await handleRequest(
      req(`/bookings/${id}`, { method: "PATCH", cookie, csrf, body: bookingBody() }),
      deps,
    )).status,
    422,
  );
  assert.equal(
    (await handleRequest(req(`/bookings/${id}/cancel`, { method: "POST", cookie, csrf }), deps))
      .status,
    404,
  );
  assert.equal(store.history.length, historyCount);
  assert.equal(store.outbox.length, outboxCount);
});

test("requester is also the responsible operator; legacy client override is ignored", async () => {
  const store = new FakeStore();
  const deps = makeDeps(store);
  const { cookie, csrf } = await login(store, deps);
  const createRes = await handleRequest(
    req("/bookings", {
      method: "POST",
      cookie,
      csrf,
      idem: nextIdem(),
      body: bookingBody({ bookerMemberId: M2, responsibleMemberId: M1 }),
    }),
    deps,
  );
  assert.equal(createRes.status, 201, await createRes.clone().text());
  const id = (await createRes.json()).id;
  const detail = await (await handleRequest(
    req(`/bookings/${id}`, { method: "GET", cookie }),
    deps,
  )).json();
  assert.equal(detail.booking.bookerMemberId, M2);
  assert.equal(detail.booking.bookerName, "엄주범"); // trusted, from the member row
  assert.equal(detail.booking.responsibleMemberId, M2); // always follows requester
});

test("patch can re-assign the requester; the derived name updates server-side", async () => {
  const store = new FakeStore();
  const deps = makeDeps(store);
  const { cookie, csrf } = await login(store, deps);
  const created = await handleRequest(
    req("/bookings", { method: "POST", cookie, csrf, idem: nextIdem(), body: bookingBody() }),
    deps,
  );
  const id = (await created.json()).id;

  // Edit re-assigns the requester from M1 (이동준) to M2 (엄주범).
  const patchRes = await handleRequest(
    req(`/bookings/${id}`, {
      method: "PATCH",
      cookie,
      csrf,
      body: bookingBody({ bookerMemberId: M2 }),
    }),
    deps,
  );
  assert.equal(patchRes.status, 200, await patchRes.clone().text());
  const detail = await (await handleRequest(
    req(`/bookings/${id}`, { method: "GET", cookie }),
    deps,
  )).json();
  assert.equal(detail.booking.bookerMemberId, M2);
  assert.equal(detail.booking.bookerName, "엄주범"); // re-derived from the member row
  assert.equal(detail.booking.bookerPhone, null); // still no requester phone
  assert.equal(detail.booking.responsibleMemberId, M2); // re-assigned with requester
});

test("patch with an unknown/inactive requester is rejected and does not mutate", async () => {
  const store = new FakeStore();
  const deps = makeDeps(store);
  const { cookie, csrf } = await login(store, deps);
  const created = await handleRequest(
    req("/bookings", { method: "POST", cookie, csrf, idem: nextIdem(), body: bookingBody() }),
    deps,
  );
  const id = (await created.json()).id;
  const before = store.bookings[0].booker_member_id;
  const res = await handleRequest(
    req(`/bookings/${id}`, {
      method: "PATCH",
      cookie,
      csrf,
      body: bookingBody({ bookerMemberId: UNKNOWN_MEMBER }),
    }),
    deps,
  );
  assert.equal(res.status, 422, await res.clone().text());
  assert.equal(store.bookings[0].booker_member_id, before, "requester must be unchanged");
});

test("an unknown/inactive requester member id is rejected", async () => {
  const store = new FakeStore();
  const deps = makeDeps(store);
  const { cookie, csrf } = await login(store, deps);
  const res = await handleRequest(
    req("/bookings", {
      method: "POST",
      cookie,
      csrf,
      idem: nextIdem(),
      body: bookingBody({ bookerMemberId: UNKNOWN_MEMBER }),
    }),
    deps,
  );
  assert.equal(res.status, 422, await res.clone().text());
  assert.equal(store.bookings.length, 0);
});

test("logout revokes the session", async () => {
  const store = new FakeStore();
  const deps = makeDeps(store);
  const { cookie, csrf } = await login(store, deps);
  const out = await handleRequest(req("/logout", { method: "POST", cookie, csrf }), deps);
  assert.equal(out.status, 200);
  const after = await handleRequest(req("/members", { method: "GET", cookie }), deps);
  assert.equal(after.status, 401);
});

test("stale session_version invalidates the session", async () => {
  const store = new FakeStore();
  const deps = makeDeps(store);
  const { cookie } = await login(store, deps);
  deps.config.sessionVersion = 2; // simulate password rotation / mass revoke
  const after = await handleRequest(req("/members", { method: "GET", cookie }), deps);
  assert.equal(after.status, 401);
});
