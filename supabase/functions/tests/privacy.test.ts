import test from "node:test";
import assert from "node:assert/strict";
import { encryptContact, parseEncryptionKey } from "../_shared/crypto.ts";
import {
  projectBookingDetail,
  projectBookingSummary,
  projectMember,
  projectPublicBooking,
} from "../_shared/privacy.ts";
import type { BookingRow, PassengerRow } from "../_shared/store.ts";

const KEY = parseEncryptionKey("0".repeat(64));

async function makeRow(): Promise<BookingRow> {
  return {
    id: "11111111-1111-4000-8000-000000000001",
    booking_code: "VER-ABCD2345",
    vessel: "veronica",
    starts_at: "2026-08-01T09:00:00.000Z",
    ends_at: "2026-08-01T12:00:00.000Z",
    booker_name: "이동준",
    booker_member_id: "a57e51a0-0000-4000-8000-000000000001",
    booker_phone_encrypted: await encryptContact("01012345678", KEY),
    departure: "아라마리나",
    destination: "덕적도",
    total_people: 2,
    responsible_member_id: "a57e51a0-0000-4000-8000-000000000001",
    status: "confirmed",
    created_at: "2026-07-01T00:00:00.000Z",
    updated_at: "2026-07-01T00:00:00.000Z",
    cancelled_at: null,
  };
}

const FORBIDDEN_PUBLIC = [
  "phone",
  "booker_phone",
  "bookerPhone",
  "passengers",
  "member_id",
  "responsibleMemberId",
  "booker_phone_encrypted",
  "calendar_event_id",
  "id",
  "responsible_member_id",
  // the requester's member id is authenticated-only; it must never surface
  // on the anonymous availability projection.
  "booker_member_id",
  "bookerMemberId",
];

test("public projection exposes occupied times only", async () => {
  const row = await makeRow();
  const pub = projectPublicBooking(row);
  assert.deepEqual(Object.keys(pub).sort(), ["endsAt", "startsAt"]);
  const serialized = JSON.stringify(pub);
  assert.ok(!serialized.includes(row.booker_name), "public leaks booker name");
  assert.ok(!serialized.includes(row.destination), "public leaks destination");
  for (const bad of FORBIDDEN_PUBLIC) {
    assert.ok(!Object.prototype.hasOwnProperty.call(pub, bad), `public leaks ${bad}`);
  }
  assert.ok(!serialized.includes("01012345678"), "public leaks phone digits");
  assert.ok(!serialized.includes(row.id), "public leaks internal id");
  assert.ok(!serialized.includes(row.responsible_member_id), "public leaks member id");
});

test("summary includes id + status but never phone/passengers", async () => {
  const s = projectBookingSummary(await makeRow());
  assert.equal(s.bookingCode, "VER-ABCD2345");
  assert.ok(!("bookerPhone" in s));
  assert.ok(!("passengers" in s));
  assert.ok(!JSON.stringify(s).includes("01012345678"));
});

test("authenticated detail decrypts phones and lists passengers", async () => {
  const row = await makeRow();
  const passengers: PassengerRow[] = [
    {
      passenger_type: "member",
      member_id: "a57e51a0-0000-4000-8000-000000000001",
      guest_name: null,
      guest_phone_encrypted: null,
    },
    {
      passenger_type: "guest",
      member_id: null,
      guest_name: "홍길동",
      guest_phone_encrypted: await encryptContact("01099998888", KEY),
    },
  ];
  const names = new Map([["a57e51a0-0000-4000-8000-000000000001", "이동준"]]);
  const detail = await projectBookingDetail(row, passengers, KEY, names);
  assert.equal(detail.bookerMemberId, "a57e51a0-0000-4000-8000-000000000001");
  assert.equal(detail.bookerName, "이동준");
  assert.equal(detail.bookerPhone, "01012345678"); // legacy row keeps a readable phone
  assert.equal(detail.passengers[0].type, "member");
  assert.equal(detail.passengers[0].name, "이동준");
  assert.equal(detail.passengers[0].phone, null);
  assert.equal(detail.passengers[1].type, "guest");
  assert.equal(detail.passengers[1].phone, "01099998888");
});

test("new-contract booking (no requester phone) projects bookerPhone as null", async () => {
  const row = await makeRow();
  // A booking created under the new contract stores no requester phone.
  row.booker_phone_encrypted = new Uint8Array();
  const names = new Map([["a57e51a0-0000-4000-8000-000000000001", "이동준"]]);
  const detail = await projectBookingDetail(row, [], KEY, names);
  assert.equal(detail.bookerPhone, null);
  assert.equal(detail.bookerMemberId, "a57e51a0-0000-4000-8000-000000000001");
  assert.equal(detail.bookerName, "이동준");
});

test("member option projection is id + displayName only", () => {
  const m = projectMember({ id: "x", display_name: "이동준", active: true });
  assert.deepEqual(Object.keys(m).sort(), ["displayName", "id"]);
  assert.ok(!("active" in m));
});
