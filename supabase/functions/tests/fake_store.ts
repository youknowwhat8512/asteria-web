// In-memory Store implementation used to exercise the router end-to-end under
// `node --test`. It mimics the DB invariants the real Postgres schema enforces:
// overlap exclusion for active bookings, passenger-count == total_people, at
// least one member passenger, api-idempotency replay, and session lookups.
//
// It is TEST-ONLY and never imported by the Deno runtime.

import { bytesToBase64 } from "../_shared/crypto.ts";
import {
  type BookingRow,
  ConflictError,
  type CreateBookingArgs,
  InvariantError,
  type MemberRow,
  NotFoundError,
  type PassengerRow,
  type SessionRow,
  type Store,
  type UpdateBookingArgs,
} from "../_shared/store.ts";

let counter = 0;
function nextId(): string {
  counter += 1;
  return `00000000-0000-4000-8000-${String(counter).padStart(12, "0")}`;
}

function overlaps(aStart: string, aEnd: string, bStart: string, bEnd: string): boolean {
  // [) half-open overlap: boundary touch is NOT an overlap.
  return new Date(aStart) < new Date(bEnd) && new Date(bStart) < new Date(aEnd);
}

export interface FakeStoreOptions {
  members?: MemberRow[];
  now?: () => Date;
}

export class FakeStore implements Store {
  members: MemberRow[];
  bookings: BookingRow[] = [];
  passengers = new Map<string, PassengerRow[]>();
  sessions = new Map<string, SessionRow>();
  loginAttempts: { key: string; succeeded: boolean; at: number }[] = [];
  apiIdempotency = new Map<string, string>(); // key -> booking id
  outbox: { idempotency_key: string; payload: Record<string, unknown> }[] = [];
  history: { booking_id: string; action: string }[] = [];
  private now: () => Date;

  constructor(opts: FakeStoreOptions = {}) {
    // Deterministic default clock, aligned with the router test's fixed now().
    this.now = opts.now ?? (() => new Date("2026-07-27T00:00:00.000Z"));
    this.members = opts.members ?? [
      { id: "a57e51a0-0000-4000-8000-000000000001", display_name: "이동준", active: true },
      { id: "a57e51a0-0000-4000-8000-000000000002", display_name: "엄주범", active: true },
    ];
  }

  private key(hash: Uint8Array): string {
    return bytesToBase64(hash);
  }

  async createSession(
    tokenHash: Uint8Array,
    csrfHash: Uint8Array,
    sessionVersion: number,
    expiresAt: string,
  ): Promise<void> {
    this.sessions.set(this.key(tokenHash), {
      id: nextId(),
      session_version: sessionVersion,
      csrf_token_hash: csrfHash,
      expires_at: expiresAt,
      revoked_at: null,
    });
  }
  async getSessionByHash(tokenHash: Uint8Array): Promise<SessionRow | null> {
    return this.sessions.get(this.key(tokenHash)) ?? null;
  }
  async revokeSession(tokenHash: Uint8Array): Promise<void> {
    const s = this.sessions.get(this.key(tokenHash));
    if (s) s.revoked_at = this.now().toISOString();
  }
  async touchSession(): Promise<void> {}
  async rotateCsrf(tokenHash: Uint8Array, newCsrfHash: Uint8Array): Promise<void> {
    const s = this.sessions.get(this.key(tokenHash));
    // Mirror the SQL: only a live (non-revoked, unexpired) session rotates.
    if (!s || s.revoked_at || new Date(s.expires_at).getTime() <= this.now().getTime()) return;
    s.csrf_token_hash = newCsrfHash;
  }

  async countRecentLoginFailures(attemptKey: string, since: string): Promise<number> {
    const t = new Date(since).getTime();
    return this.loginAttempts.filter((a) => a.key === attemptKey && !a.succeeded && a.at >= t)
      .length;
  }
  async recordLoginAttempt(attemptKey: string, succeeded: boolean): Promise<void> {
    this.loginAttempts.push({ key: attemptKey, succeeded, at: this.now().getTime() });
  }

  async listActiveMembers(): Promise<MemberRow[]> {
    return this.members.filter((m) => m.active);
  }

  async listActiveBookingsInRange(from: string, to: string): Promise<BookingRow[]> {
    return this.bookings.filter(
      (b) => b.status !== "cancelled" && overlaps(b.starts_at, b.ends_at, from, to),
    );
  }

  async listBookings(): Promise<BookingRow[]> {
    return [...this.bookings];
  }
  async getBooking(
    id: string,
  ): Promise<{ booking: BookingRow; passengers: PassengerRow[] } | null> {
    const booking = this.bookings.find((b) => b.id === id);
    if (!booking) return null;
    return { booking, passengers: this.passengers.get(id) ?? [] };
  }

  private assertInvariants(total: number, passengers: PassengerRow[], status: string): void {
    if (passengers.length !== total) {
      throw new InvariantError("passenger count must equal total_people");
    }
    if (status !== "cancelled" && !passengers.some((p) => p.passenger_type === "member")) {
      throw new InvariantError("at least one member passenger required");
    }
    for (const p of passengers) {
      if (p.passenger_type === "member") {
        const m = this.members.find((mm) => mm.id === p.member_id);
        if (!m || !m.active) throw new InvariantError("member passenger must be active");
      }
    }
  }

  private assertNoOverlap(startsAt: string, endsAt: string, excludeId?: string): void {
    for (const b of this.bookings) {
      if (b.id === excludeId) continue;
      if (b.status === "cancelled") continue;
      if (overlaps(b.starts_at, b.ends_at, startsAt, endsAt)) {
        throw new ConflictError("time_conflict");
      }
    }
  }

  async createBooking(
    args: CreateBookingArgs,
  ): Promise<{ id: string; booking_code: string; replayed: boolean }> {
    // idempotency replay
    const existing = this.apiIdempotency.get(args.api_idempotency_key);
    if (existing) {
      const b = this.bookings.find((x) => x.id === existing)!;
      return { id: b.id, booking_code: b.booking_code, replayed: true };
    }
    const responsible = this.members.find((m) => m.id === args.responsible_member_id);
    if (!responsible || !responsible.active) {
      throw new InvariantError("responsible member must be active");
    }
    // Mirror the RPC: the requester must be an active member and the trusted
    // display name is derived from that row — never trusted from the caller.
    const booker = this.members.find((m) => m.id === args.booker_member_id);
    if (!booker || !booker.active) {
      throw new InvariantError("booker member must be active");
    }
    this.assertInvariants(args.total_people, args.passengers, "confirmed");
    this.assertNoOverlap(args.starts_at, args.ends_at);

    const id = nextId();
    const nowIso = this.now().toISOString();
    this.bookings.push({
      id,
      booking_code: args.booking_code,
      vessel: "veronica",
      starts_at: args.starts_at,
      ends_at: args.ends_at,
      booker_name: booker.display_name,
      booker_member_id: args.booker_member_id,
      // New-contract bookings store no requester phone.
      booker_phone_encrypted: new Uint8Array(),
      departure: "아라마리나",
      destination: args.destination,
      total_people: args.total_people,
      responsible_member_id: args.responsible_member_id,
      status: "confirmed",
      created_at: nowIso,
      updated_at: nowIso,
      cancelled_at: null,
    });
    this.passengers.set(id, args.passengers);
    this.apiIdempotency.set(args.api_idempotency_key, id);
    this.outbox.push({
      idempotency_key: args.outbox_idempotency_key,
      payload: args.outbox_payload,
    });
    this.history.push({ booking_id: id, action: "created" });
    return { id, booking_code: args.booking_code, replayed: false };
  }

  async updateBooking(args: UpdateBookingArgs): Promise<void> {
    const booking = this.bookings.find((b) => b.id === args.id);
    if (!booking) throw new NotFoundError("not found");
    // Mirror update_booking: the row is locked (FOR UPDATE) and a cancelled
    // booking can never be updated — reject atomically.
    if (booking.status === "cancelled") {
      throw new InvariantError("cannot update a cancelled booking");
    }
    const responsible = this.members.find((m) => m.id === args.responsible_member_id);
    if (!responsible || !responsible.active) {
      throw new InvariantError("responsible member must be active");
    }
    const booker = this.members.find((m) => m.id === args.booker_member_id);
    if (!booker || !booker.active) {
      throw new InvariantError("booker member must be active");
    }
    this.assertInvariants(args.total_people, args.passengers, "confirmed");
    this.assertNoOverlap(args.starts_at, args.ends_at, args.id);
    booking.starts_at = args.starts_at;
    booking.ends_at = args.ends_at;
    // Requester name is re-derived from the (active) member row; the legacy
    // encrypted phone is intentionally left untouched (preserved verbatim).
    booking.booker_name = booker.display_name;
    booking.booker_member_id = args.booker_member_id;
    booking.destination = args.destination;
    booking.total_people = args.total_people;
    booking.responsible_member_id = args.responsible_member_id;
    booking.updated_at = this.now().toISOString();
    this.passengers.set(args.id, args.passengers);
    this.outbox.push({
      idempotency_key: args.outbox_idempotency_key,
      payload: args.outbox_payload,
    });
    this.history.push({ booking_id: args.id, action: "updated" });
  }

  async cancelBooking(
    id: string,
    _sessionHash: Uint8Array,
    outboxKey: string,
    payload: Record<string, unknown>,
  ): Promise<void> {
    const booking = this.bookings.find((b) => b.id === id);
    // Mirror cancel_booking: the guarded SELECT ... FOR UPDATE only matches an
    // active row, so a missing OR already-cancelled booking transitions nothing
    // and emits NO duplicate history/outbox — concurrent cancels are safe.
    if (!booking || booking.status === "cancelled") {
      throw new NotFoundError("not found or already cancelled");
    }
    booking.status = "cancelled";
    booking.cancelled_at = this.now().toISOString();
    this.outbox.push({ idempotency_key: outboxKey, payload });
    this.history.push({ booking_id: id, action: "cancelled" });
  }
}
