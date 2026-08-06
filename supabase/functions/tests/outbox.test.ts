import test from "node:test";
import assert from "node:assert/strict";
import {
  assertRedacted,
  buildOutboxPayload,
  FORBIDDEN_OUTBOX_KEYS,
  newOutboxIdempotencyKey,
} from "../_shared/outbox.ts";
import type { BookingRow } from "../_shared/store.ts";

const ROW: BookingRow = {
  id: "11111111-1111-4000-8000-000000000001",
  booking_code: "VER-ABCD2345",
  vessel: "veronica",
  starts_at: "2026-08-01T09:00:00.000Z",
  ends_at: "2026-08-01T12:00:00.000Z",
  booker_name: "이동준",
  booker_member_id: "a57e51a0-0000-4000-8000-000000000001",
  booker_phone_encrypted: new Uint8Array([1, 2, 3]),
  departure: "아라마리나",
  destination: "덕적도",
  total_people: 4,
  responsible_member_id: "a57e51a0-0000-4000-8000-000000000001",
  status: "confirmed",
  created_at: "2026-07-01T00:00:00.000Z",
  updated_at: "2026-07-01T00:00:00.000Z",
  cancelled_at: null,
};

test("outbox payload contains only operational fields", () => {
  const p = buildOutboxPayload("booking_created", ROW);
  assert.deepEqual(Object.keys(p).sort(), [
    "booker_name",
    "booking_code",
    "departure",
    "destination",
    "ends_at",
    "event_type",
    "starts_at",
    "total_people",
    "vessel",
  ]);
});

test("outbox payload never carries phone / passengers / codes / waiver", () => {
  const p = buildOutboxPayload("booking_cancelled", ROW);
  const serialized = JSON.stringify(p);
  for (const k of FORBIDDEN_OUTBOX_KEYS) {
    assert.ok(!(k in p), `outbox leaks ${k}`);
  }
  assert.ok(!serialized.includes("01012345678"));
  // encrypted phone bytes must not appear either
  assert.ok(!("booker_phone_encrypted" in p));
});

test("assertRedacted throws on any forbidden key at depth", () => {
  assert.doesNotThrow(() => assertRedacted(buildOutboxPayload("booking_updated", ROW)));
  assert.throws(() => assertRedacted({ nested: { passengers: [] } }));
  assert.throws(() => assertRedacted({ booker_phone: "01012345678" }));
});

test("idempotency keys are opaque, random, and unique", () => {
  const keys = new Set(Array.from({ length: 500 }, () => newOutboxIdempotencyKey()));
  assert.equal(keys.size, 500);
  for (const k of keys) assert.match(k, /^[A-Za-z0-9_-]+$/);
});
