import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..", "..", ".."); // repo root
const html = readFileSync(join(repo, "veronica", "index.html"), "utf8");
const appjs = readFileSync(join(repo, "veronica", "app.js"), "utf8");

// Demo credentials / fake values that lived in the prototype must NOT appear.
const DEMO_TOKENS = [
  "SAIL2026",
  "VER-260721",
  "WAVE-4829",
  "demo-account",
  "샘플 비밀번호",
  "샘플 계정",
];

test("staging UI contains no prototype demo credentials", () => {
  for (const tok of DEMO_TOKENS) {
    assert.ok(!html.includes(tok), `index.html leaks demo token ${tok}`);
    assert.ok(!appjs.includes(tok), `app.js leaks demo token ${tok}`);
  }
});

test("no password value is hard-coded in the UI", () => {
  // The login id is public and may be prefilled; a password value must not be.
  assert.ok(/id="loginPw"[^>]*type="password"/.test(html));
  assert.ok(!/id="loginPw"[^>]*value="/.test(html), "password input must not have a value");
});

test("session token is never placed in web storage or the URL", () => {
  assert.ok(!/localStorage/.test(appjs), "must not use localStorage");
  assert.ok(!/sessionStorage/.test(appjs), "must not use sessionStorage");
  // csrf token stays in a module variable, never persisted
  assert.ok(!/location\.(hash|search)\s*=/.test(appjs));
});

test("no innerHTML/outerHTML/document.write with values (XSS-safe DOM building)", () => {
  assert.ok(!/innerHTML/.test(appjs), "app.js must not use innerHTML");
  assert.ok(!/outerHTML/.test(appjs), "app.js must not use outerHTML");
  assert.ok(!/document\.write/.test(appjs), "app.js must not use document.write");
});

test("API base is a non-secret same-origin proxy path", () => {
  assert.match(html, /<meta name="veronica-api-base" content="\/api\/veronica">/);
  assert.match(appjs, /meta\[name="veronica-api-base"\]/);
  assert.ok(!html.includes("supabase.co"), "browser HTML must not use cross-site Supabase API");
});

test("create retries reuse one form-scoped idempotency key and consent resets", () => {
  assert.match(appjs, /createIdempotencyKey = prefill \? null : crypto\.randomUUID\(\)/);
  assert.match(appjs, /idempotencyKey: createIdempotencyKey/);
  assert.match(appjs, /waiverAgree"\)\.checked = false/);
  assert.match(appjs, /privacyAgree"\)\.checked = false/);
});

test("requests use credentialed cookies + CSRF header, not bearer tokens", () => {
  assert.match(appjs, /credentials:\s*"include"/);
  assert.match(appjs, /X-CSRF-Token/);
  assert.ok(!/Authorization/.test(appjs), "no Authorization/bearer header in browser client");
});

test("requester is chosen from the active-member dropdown, never typed", () => {
  // The requester select exists…
  assert.match(html, /<select id="bookerMember">/);
  // …and the old free-text requester name / phone inputs are gone.
  assert.ok(!/id="bookerName"/.test(html), "requester name input must be removed");
  assert.ok(!/id="bookerPhone"/.test(html), "requester phone input must be removed");
  assert.ok(!/\$\("bookerName"\)/.test(appjs), "app.js must not read a typed requester name input");
  assert.ok(!/\$\("bookerPhone"\)/.test(appjs), "app.js must not read a requester phone input");
});

test("responsible operator is not a separate browser input", () => {
  assert.ok(!/id="responsibleMember"/.test(html), "responsible select must be removed");
  assert.ok(!/\$\("responsibleMember"\)/.test(appjs), "browser must not read a responsible select");
  assert.ok(!/responsibleMemberId,/.test(appjs), "payload must not send responsibleMemberId");
});

test("create/update payload carries bookerMemberId and no requester phone/name", () => {
  assert.match(appjs, /bookerMemberId/);
  assert.ok(!/bookerName:/.test(appjs), "payload must not send a requester name");
  assert.ok(!/bookerPhone:/.test(appjs), "payload must not send a requester phone");
});

test("reservation list offers an accessible month calendar view alongside the list", () => {
  // list/calendar toggle + calendar container exist in the markup
  assert.match(html, /id="listViewBtn"/);
  assert.match(html, /id="calendarViewBtn"/);
  assert.match(html, /id="bookingCalendar"/);
  assert.match(html, /aria-pressed=/); // toggle communicates state to AT
  // calendar is a real, keyboard/AT-navigable month grid built without any CDN
  assert.match(appjs, /role:\s*"grid"/);
  assert.match(appjs, /\["일", "월", "화", "수", "목", "금", "토"\]/);
  // previous / next month navigation with labelled controls
  assert.match(appjs, /calPrevBtn/);
  assert.match(appjs, /calNextBtn/);
  assert.match(appjs, /이전 달/);
  assert.match(appjs, /다음 달/);
  // no external stylesheet/script dependency is introduced
  assert.ok(!/cdn|unpkg|jsdelivr|googleapis\.com\/.*\.js/i.test(html), "no external CDN deps");
});

test("public availability start date defaults the end date (one-way only)", () => {
  // Changing the start date copies it into the end date at that moment…
  assert.match(appjs, /availFrom"\)\.addEventListener\("change"/);
  assert.match(appjs, /availTo"\)\.value\s*=\s*\$\("availFrom"\)\.value/);
  // …and the end date never drives the start date: no listener on #availTo,
  // so there is no reverse synchronization.
  assert.ok(
    !/availTo"\)\.addEventListener/.test(appjs),
    "end date must not synchronize back to the start date",
  );
});

test("booking start hour defaults end hour to start + 3h, capped at 24 (one-way)", () => {
  assert.match(appjs, /startHour"\)\.addEventListener\("change"/);
  // end = min(start + 3, 24) — capped so it never exceeds 24:00
  assert.match(
    appjs,
    /endHour"\)\.value\s*=\s*String\(Math\.min\(Number\(\$\("startHour"\)\.value\) \+ 3, 24\)\)/,
  );
  // strictly one-way: the end hour never drives the start hour
  assert.ok(
    !/endHour"\)\.addEventListener/.test(appjs),
    "end hour must not synchronize back to the start hour",
  );
});

test("solo booking: requester is the mandatory first member passenger", () => {
  // Requester is prepended as a member passenger; the visible list is companions.
  assert.match(
    appjs,
    /const passengers = \[\{ type: "member", memberId: bookerMemberId \}, \.\.\.companions\]/,
  );
  // total = 1 (requester) + companions, so totalPeople follows passengers.length
  assert.match(appjs, /totalPeople: passengers\.length/);
  // A companion row cannot re-use the requester member id (no duplicate booking).
  assert.match(
    appjs,
    /companions\.some\(\(p\) => p\.type === "member" && p\.memberId === bookerMemberId\)/,
  );
  // Empty companion list is valid: create no longer force-adds a member row.
  assert.ok(
    !/else \{\s*addPaxRow\("member"\)/.test(appjs),
    "create form must start with an empty companion list",
  );
});

test("editing an existing booking hides the requester from the companion list", () => {
  // The first passenger matching the requester member id is dropped once so it
  // is neither shown nor re-added as a companion.
  assert.match(appjs, /requesterDropped/);
  assert.match(appjs, /p\.type === "member" && p\.memberId === prefill\.bookerMemberId/);
});

test("requester is excluded from member companion choices and stale duplicates are cleared", () => {
  assert.match(appjs, /function fillMemberSelect\(sel, selectedId, excludedId = ""\)/);
  assert.match(appjs, /if \(m\.id === excludedId\) continue/);
  assert.match(
    appjs,
    /fillMemberSelect\(sel, preset\.memberId, \$\("bookerMember"\)\.value\)/,
  );
  assert.match(appjs, /function refreshMemberPassengerSelects\(\)/);
  assert.match(appjs, /selectedId === excludedId \? "" : selectedId/);
  // Changing requester recalculates every existing member-companion dropdown.
  assert.match(
    appjs,
    /bookerMember"\)\.addEventListener\("change", \(\) => \{[\s\S]*refreshMemberPassengerSelects\(\)/,
  );
});

test("companion copy excludes the requester and counts them explicitly", () => {
  assert.match(html, /동승자 명단 \(예약자 제외\)/);
  // count copy makes the requester's inclusion in the total explicit
  assert.match(appjs, /예약자 포함 총 \$\{n \+ 1\}명/);
});

test("all single-line input boxes use one exact control size", () => {
  // Native input/select controls differ across browsers unless both dimensions
  // are explicit. Keep every single-line form control aligned at one height.
  assert.match(html, /input,select\{[^}]*width:100%[^}]*height:46px[^}]*min-height:46px/);
  assert.match(html, /input,select\{[^}]*padding:0 13px[^}]*line-height:1\.2/);
});

test("mobile booking calendar renders a single-column agenda view", () => {
  // Desktop keeps the 7-column month grid.
  assert.match(html, /grid-template-columns:repeat\(7,1fr\)/);
  // Mobile (<=600px) agenda: hide weekday headers + unbooked days, booked days
  // become full-width cells.
  assert.match(html, /@media\(max-width:600px\)/);
  assert.match(html, /\.cal-th\{display:none\}/);
  assert.match(html, /\.cal-cell\.has-bookings/);
  assert.match(html, /\.cal-datelabel/);
  // Booked cells are flagged and carry a full Korean month/day/weekday label.
  assert.match(appjs, /has-bookings/);
  assert.match(appjs, /\$\{calMonth \+ 1\}월 \$\{day\}일 \(\$\{WEEKDAYS\[dow\]\}\)/);
  // Pills show readable time range + requester + route.
  assert.match(appjs, /kstTime\(b\.startsAt\)\}–\$\{kstTime\(b\.endsAt\)\}/);
  assert.match(appjs, /b\.departure\} → \$\{b\.destination\}/);
  // Cancelled styling stays distinct; no external CDN is introduced.
  assert.match(appjs, /cal-pill\$\{b\.status === "cancelled" \? " cancelled" : ""\}/);
  assert.ok(!/cdn|unpkg|jsdelivr|googleapis\.com\/.*\.js/i.test(html), "no external CDN deps");
});

test("production route remains noindex and is linked from the production card", () => {
  assert.match(html, /noindex/);
  const indexHtml = readFileSync(join(repo, "index.html"), "utf8");
  assert.match(indexHtml, /href="\/veronica\/"/);
});
