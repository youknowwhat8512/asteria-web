// Notification outbox payload builder.
//
// The web API only ever writes a PRIVACY-SAFE outbox row; a separate Mac-side
// consumer performs the actual KakaoTalk delivery. This builder is an
// allowlist: it emits only operational fields and can NEVER include phone
// numbers, cancellation codes, guest lists, or waiver text. A redaction test
// asserts those forbidden keys are absent.

import type { BookingRow } from "./store.ts";
import { randomToken } from "./crypto.ts";

export type OutboxEventType = "booking_created" | "booking_updated" | "booking_cancelled";

export interface OutboxPayload {
  event_type: OutboxEventType;
  booking_code: string;
  vessel: string;
  starts_at: string;
  ends_at: string;
  departure: string;
  destination: string;
  total_people: number;
  // booker_name is public-tier data (already shown on the public calendar);
  // it is included so the operations room can identify the booking.
  booker_name: string;
}

/** Keys that must never appear in an outbox payload, at any nesting depth. */
export const FORBIDDEN_OUTBOX_KEYS = [
  "phone",
  "booker_phone",
  "guest_phone",
  "passengers",
  "passenger",
  "cancel_code",
  "cancellation_code",
  "waiver",
  "waiver_text",
  "booker_phone_encrypted",
  "guest_phone_encrypted",
];

export function buildOutboxPayload(eventType: OutboxEventType, row: BookingRow): OutboxPayload {
  return {
    event_type: eventType,
    booking_code: row.booking_code,
    vessel: row.vessel,
    starts_at: row.starts_at,
    ends_at: row.ends_at,
    departure: row.departure,
    destination: row.destination,
    total_people: row.total_people,
    booker_name: row.booker_name,
  };
}

/**
 * Opaque, server-generated idempotency key for a single notification event.
 * Random (not derived from sensitive data) and unique per occurrence so the
 * outbox unique constraint dedupes redeliveries without leaking anything.
 */
export function newOutboxIdempotencyKey(): string {
  return randomToken(24);
}

/** Defense-in-depth: reject any forbidden key (at any depth) before persisting.
 * Accepts `unknown` so callers can pass a built payload without an unsafe cast. */
export function assertRedacted(payload: unknown): void {
  const stack: unknown[] = [payload];
  while (stack.length) {
    const cur = stack.pop();
    if (cur && typeof cur === "object") {
      for (const [k, v] of Object.entries(cur as Record<string, unknown>)) {
        if (FORBIDDEN_OUTBOX_KEYS.includes(k)) {
          throw new Error("outbox payload contains a forbidden key");
        }
        if (v && typeof v === "object") stack.push(v);
      }
    }
  }
}
