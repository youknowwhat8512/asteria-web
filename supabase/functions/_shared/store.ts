// Data types and the storage interface used by the router. The router depends
// only on this interface, never on Supabase directly, so it can be exercised
// end-to-end against an in-memory fake under `node --test`. The real
// implementation (supabase-store.ts) uses the service-role client and is the
// ONLY place the service key is ever touched — it never reaches the browser.

export type BookingStatus = "creating" | "confirmed" | "calendar_failed" | "cancelled";

export interface MemberRow {
  id: string;
  display_name: string;
  active: boolean;
}

export interface PassengerRow {
  passenger_type: "member" | "guest";
  member_id: string | null;
  guest_name: string | null;
  guest_phone_encrypted: Uint8Array | null;
}

export interface BookingRow {
  id: string;
  booking_code: string;
  vessel: string;
  starts_at: string; // ISO
  ends_at: string; // ISO
  // Trusted requester display name, derived server-side from booker_member_id.
  booker_name: string;
  // Requester's active-member reference. Nullable only for legacy rows that
  // predate this column and could not be safely backfilled; new rows always set it.
  booker_member_id: string | null;
  // Encrypted legacy requester phone. Empty for bookings created under the new
  // contract (which collects no requester phone); non-empty only for old rows.
  booker_phone_encrypted: Uint8Array;
  departure: string;
  destination: string;
  total_people: number;
  responsible_member_id: string;
  status: BookingStatus;
  created_at: string;
  updated_at: string;
  cancelled_at: string | null;
}

export interface SessionRow {
  id: string;
  session_version: number;
  csrf_token_hash: Uint8Array;
  expires_at: string; // ISO
  revoked_at: string | null;
}

export interface CreateBookingArgs {
  booking_code: string;
  starts_at: string;
  ends_at: string;
  // The requester is identified only by an active-member id. The trusted
  // display name is derived from this id server-side (in the RPC / fake store),
  // never accepted from the caller, and no requester phone is stored.
  booker_member_id: string;
  destination: string;
  total_people: number;
  responsible_member_id: string;
  waiver_version: string;
  waiver_accepted_at: string;
  privacy_consent_version: string;
  privacy_consent_accepted_at: string;
  passengers: PassengerRow[];
  session_hash: Uint8Array;
  api_idempotency_key: string;
  outbox_idempotency_key: string;
  outbox_payload: Record<string, unknown>;
}

export interface UpdateBookingArgs extends CreateBookingArgs {
  id: string;
}

/** Raised when a booking overlaps an existing active booking (DB 23P01). */
export class ConflictError extends Error {}
/** Raised when a domain invariant/foreign-key check fails (DB 23514/23503). */
export class InvariantError extends Error {}
/** Raised when a booking id is not found. */
export class NotFoundError extends Error {}

export interface Store {
  // Auth / sessions
  createSession(
    tokenHash: Uint8Array,
    csrfHash: Uint8Array,
    sessionVersion: number,
    expiresAt: string,
  ): Promise<void>;
  getSessionByHash(tokenHash: Uint8Array): Promise<SessionRow | null>;
  revokeSession(tokenHash: Uint8Array): Promise<void>;
  touchSession(tokenHash: Uint8Array, at: string): Promise<void>;
  /** Atomically replace the session-bound CSRF hash (session resume). */
  rotateCsrf(tokenHash: Uint8Array, newCsrfHash: Uint8Array): Promise<void>;

  // Login rate limiting
  countRecentLoginFailures(attemptKey: string, since: string): Promise<number>;
  recordLoginAttempt(attemptKey: string, succeeded: boolean): Promise<void>;

  // Members
  listActiveMembers(): Promise<MemberRow[]>;

  // Availability (redacted at the router — this returns full rows to project)
  listActiveBookingsInRange(from: string, to: string): Promise<BookingRow[]>;

  // Bookings
  listBookings(): Promise<BookingRow[]>;
  getBooking(id: string): Promise<{ booking: BookingRow; passengers: PassengerRow[] } | null>;
  createBooking(
    args: CreateBookingArgs,
  ): Promise<{ id: string; booking_code: string; replayed: boolean }>;
  updateBooking(args: UpdateBookingArgs): Promise<void>;
  cancelBooking(
    id: string,
    sessionHash: Uint8Array,
    outboxIdempotencyKey: string,
    outboxPayload: Record<string, unknown>,
  ): Promise<void>;
}
