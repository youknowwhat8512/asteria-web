import test from "node:test";
import assert from "node:assert/strict";
import {
  normalizePhone,
  validateAvailabilityQuery,
  validateCreateBooking,
  validateLogin,
} from "../_shared/validation.ts";

const M1 = "a57e51a0-0000-4000-8000-000000000001";
const M2 = "a57e51a0-0000-4000-8000-000000000002";

function baseBooking(over: Record<string, unknown> = {}) {
  return {
    startsAt: "2026-08-01T09:00:00.000Z",
    endsAt: "2026-08-01T12:00:00.000Z",
    destination: "덕적도",
    totalPeople: 2,

    // New contract: the requester (예약자) is chosen from the active-member
    // dropdown. No browser-supplied booker name or phone is accepted.
    bookerMemberId: M1,
    passengers: [
      { type: "member", memberId: M1 },
      { type: "guest", name: "홍길동", phone: "01098765432" },
    ],
    waiverVersion: "waiver-v1",
    waiverAccepted: true,
    privacyConsentVersion: "privacy-v1",
    privacyConsentAccepted: true,
    ...over,
  };
}

test("validateLogin requires the fixed public id and a password", () => {
  assert.equal(validateLogin({ id: "clubasteria", password: "x" }, "clubasteria").ok, true);
  assert.equal(validateLogin({ id: "someoneelse", password: "x" }, "clubasteria").ok, false);
  assert.equal(validateLogin({ id: "clubasteria" }, "clubasteria").ok, false);
  assert.equal(
    validateLogin({ id: "clubasteria", password: "x", extra: 1 }, "clubasteria").ok,
    false,
  );
  assert.equal(validateLogin("nope", "clubasteria").ok, false);
});

test("normalizePhone strips separators and enforces KR mobile", () => {
  assert.equal(normalizePhone("010-1234-5678"), "01012345678");
  assert.equal(normalizePhone("010 9999 8888"), "01099998888");
  assert.equal(normalizePhone("02-123-4567"), null);
  assert.equal(normalizePhone(12345), null);
});

test("availability query validation", () => {
  assert.equal(validateAvailabilityQuery("2026-08-01", "2026-08-02").ok, true);
  assert.equal(validateAvailabilityQuery(null, "2026-08-02").ok, false);
  assert.equal(validateAvailabilityQuery("2026-08-02", "2026-08-01").ok, false);
  assert.equal(validateAvailabilityQuery("2020-01-01", "2026-01-01").ok, false); // too large
});

test("valid create booking passes", () => {
  const r = validateCreateBooking(baseBooking());
  assert.equal(r.ok, true, r.errors.join(","));
  assert.equal(r.value!.bookerMemberId, M1);
  assert.equal(r.value!.responsibleMemberId, M1);
});

test("requester is a member id; browser name/phone are rejected", () => {
  // Requester must be an active-member id, not a typed name/phone.
  assert.equal(validateCreateBooking(baseBooking({ bookerMemberId: undefined })).ok, false);
  assert.equal(validateCreateBooking(baseBooking({ bookerMemberId: "not-a-uuid" })).ok, false);
  // Legacy fields are no longer part of the contract → unexpected fields.
  assert.equal(validateCreateBooking(baseBooking({ bookerName: "이동준" })).ok, false);
  assert.equal(validateCreateBooking(baseBooking({ bookerPhone: "01012345678" })).ok, false);
});

test("rejects non-whole-hour times and reversed range", () => {
  assert.equal(
    validateCreateBooking(baseBooking({ startsAt: "2026-08-01T09:30:00.000Z" })).ok,
    false,
  );
  assert.equal(
    validateCreateBooking(
      baseBooking({ startsAt: "2026-08-01T12:00:00.000Z", endsAt: "2026-08-01T09:00:00.000Z" }),
    ).ok,
    false,
  );
});

test("rejects multi-day KST ranges but allows end at 24:00", () => {
  assert.equal(
    validateCreateBooking(baseBooking({ endsAt: "2026-08-02T01:00:00.000Z" })).ok,
    false,
  );
  assert.equal(
    validateCreateBooking(baseBooking({ endsAt: "2026-08-01T15:00:00.000Z" })).ok,
    true,
  );
});

test("rejects totalPeople out of range and mismatched passenger count", () => {
  assert.equal(validateCreateBooking(baseBooking({ totalPeople: 9 })).ok, false);
  assert.equal(validateCreateBooking(baseBooking({ totalPeople: 0 })).ok, false);
  assert.equal(validateCreateBooking(baseBooking({ totalPeople: 3 })).ok, false); // 2 passengers
});

test("requires at least one member passenger", () => {
  const r = validateCreateBooking(baseBooking({
    totalPeople: 1,
    passengers: [{ type: "guest", name: "손님", phone: "01011112222" }],
  }));
  assert.equal(r.ok, false);
  assert.ok(r.errors.some((e) => e.includes("member passenger")));
});

test("requires accepted waiver + privacy consent", () => {
  assert.equal(validateCreateBooking(baseBooking({ waiverAccepted: false })).ok, false);
  assert.equal(validateCreateBooking(baseBooking({ privacyConsentAccepted: false })).ok, false);
});

test("rejects unknown fields and ignores the legacy responsible-operator input", () => {
  assert.equal(validateCreateBooking(baseBooking({ hacked: true })).ok, false);
  const legacy = validateCreateBooking(baseBooking({ responsibleMemberId: M2 }));
  assert.equal(legacy.ok, true);
  assert.equal(legacy.value!.responsibleMemberId, M1);
});

test("guest passenger requires name + phone; member requires memberId", () => {
  assert.equal(
    validateCreateBooking(baseBooking({
      passengers: [{ type: "member", memberId: M1 }, {
        type: "guest",
        name: "",
        phone: "01098765432",
      }],
    })).ok,
    false,
  );
  assert.equal(
    validateCreateBooking(baseBooking({
      passengers: [{ type: "member", memberId: "x" }, {
        type: "guest",
        name: "홍",
        phone: "01098765432",
      }],
    })).ok,
    false,
  );
  // two members, count 2 → valid
  assert.equal(
    validateCreateBooking(baseBooking({
      passengers: [{ type: "member", memberId: M1 }, { type: "member", memberId: M2 }],
    })).ok,
    true,
  );
});
