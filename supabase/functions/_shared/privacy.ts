// Privacy projections. These are ALLOWLIST constructors: each returns a brand
// new object containing only the permitted fields, so a future extra column on
// a row can never accidentally leak through a public surface.

import type { BookingRow, MemberRow, PassengerRow } from "./store.ts";
import { decryptContact } from "./crypto.ts";

// ---------------------------------------------------------------------------
// PUBLIC availability — logged-out visitors.
// Exposes ONLY the occupied time interval — nothing that identifies who booked
// it or where they are going. Never a booker name, destination/route, phones,
// passengers, member ids, internal ids, calendar ids, session or outbox data.
// A logged-out visitor learns only that a slot is taken.
// ---------------------------------------------------------------------------
export interface PublicAvailabilityEntry {
  startsAt: string;
  endsAt: string;
}

export function projectPublicBooking(row: BookingRow): PublicAvailabilityEntry {
  return {
    startsAt: row.starts_at,
    endsAt: row.ends_at,
  };
}

/** A maintenance / [예약불가] block sourced from Calendar — no internal ids. */
export interface PublicBlockEntry {
  startsAt: string;
  endsAt: string;
  label: string; // always "[예약불가]"
}

// ---------------------------------------------------------------------------
// AUTHENTICATED summary list — booking cards.
// Includes internal id (needed to open detail) but NOT phone/passenger data.
// ---------------------------------------------------------------------------
export interface BookingSummary {
  id: string;
  bookingCode: string;
  startsAt: string;
  endsAt: string;
  bookerName: string;
  /** Null only for legacy rows that could not be backfilled. */
  bookerMemberId: string | null;
  departure: string;
  destination: string;
  totalPeople: number;
  status: string;
}

export function projectBookingSummary(row: BookingRow): BookingSummary {
  return {
    id: row.id,
    bookingCode: row.booking_code,
    startsAt: row.starts_at,
    endsAt: row.ends_at,
    bookerName: row.booker_name,
    bookerMemberId: row.booker_member_id,
    departure: row.departure,
    destination: row.destination,
    totalPeople: row.total_people,
    status: row.status,
  };
}

// ---------------------------------------------------------------------------
// AUTHENTICATED full detail — decrypts phone numbers. Only reachable behind a
// valid session (enforced by the router before this is ever called).
// ---------------------------------------------------------------------------
export interface PassengerDetail {
  type: "member" | "guest";
  memberId: string | null;
  name: string | null;
  phone: string | null;
}

export interface BookingDetail extends BookingSummary {
  // The requester's active-member id (null for un-backfilled legacy rows) so the
  // edit form can preselect the requester dropdown.
  bookerMemberId: string | null;
  // Null for new-contract bookings (no requester phone); a decrypted legacy value
  // for old rows that still carry one.
  bookerPhone: string | null;
  responsibleMemberId: string;
  passengers: PassengerDetail[];
}

export async function projectBookingDetail(
  row: BookingRow,
  passengers: PassengerRow[],
  keyBytes: Uint8Array,
  memberNames: Map<string, string>,
): Promise<BookingDetail> {
  // New-contract bookings store no requester phone (empty bytea) → null. Only
  // legacy rows still carry a non-empty ciphertext to decrypt.
  const bookerPhone = row.booker_phone_encrypted.length > 0
    ? await decryptContact(row.booker_phone_encrypted, keyBytes)
    : null;
  const passengerDetails: PassengerDetail[] = [];
  for (const p of passengers) {
    if (p.passenger_type === "member") {
      passengerDetails.push({
        type: "member",
        memberId: p.member_id,
        name: p.member_id ? memberNames.get(p.member_id) ?? null : null,
        phone: null,
      });
    } else {
      passengerDetails.push({
        type: "guest",
        memberId: null,
        name: p.guest_name,
        phone: p.guest_phone_encrypted
          ? await decryptContact(p.guest_phone_encrypted, keyBytes)
          : null,
      });
    }
  }
  return {
    ...projectBookingSummary(row),
    bookerMemberId: row.booker_member_id,
    bookerPhone,
    responsibleMemberId: row.responsible_member_id,
    passengers: passengerDetails,
  };
}

// ---------------------------------------------------------------------------
// Members list for logged-in selection UIs — id + display name only.
// ---------------------------------------------------------------------------
export interface MemberOption {
  id: string;
  displayName: string;
}

export function projectMember(row: MemberRow): MemberOption {
  return { id: row.id, displayName: row.display_name };
}
