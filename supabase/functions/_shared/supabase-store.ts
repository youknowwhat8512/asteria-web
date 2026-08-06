// Service-role Store implementation. This is the ONLY module that holds the
// service-role key or touches the database directly. It is never bundled to
// the browser. Atomic multi-table writes go through SECURITY-hardened RPCs
// (see migration 20260727090000) so booking + passengers + history + outbox +
// idempotency commit together or not at all.
//
// Runs only under Deno; not exercised by the Node unit tests.

import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { bytesToBase64 } from "./crypto.ts";
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
} from "./store.ts";

// PostgREST returns bytea as a hex string like "\x0a1b...". Decode to bytes.
function hexByteaToBytes(v: unknown): Uint8Array {
  if (v == null) return new Uint8Array();
  const s = String(v);
  const hex = s.startsWith("\\x") ? s.slice(2) : s;
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
  return out;
}

function b64(bytes: Uint8Array): string {
  return bytesToBase64(bytes);
}

// Map Postgres SQLSTATEs surfaced by RPC errors to typed domain errors.
function mapPgError(err: { code?: string; message?: string } | null): never {
  const code = err?.code ?? "";
  if (code === "23P01") throw new ConflictError("time_conflict"); // exclusion_violation
  if (code === "23514" || code === "23503") throw new InvariantError("invariant"); // check/fk
  if (code === "P0002") throw new NotFoundError("not_found"); // no_data_found
  throw new Error("db_error");
}

function passengersToJson(passengers: PassengerRow[]): unknown[] {
  return passengers.map((p) =>
    p.passenger_type === "member"
      ? { type: "member", member_id: p.member_id }
      : { type: "guest", guest_name: p.guest_name, guest_phone_b64: b64(p.guest_phone_encrypted!) }
  );
}

export class SupabaseStore implements Store {
  private db: SupabaseClient;
  constructor(url: string, serviceRoleKey: string) {
    this.db = createClient(url, serviceRoleKey, { auth: { persistSession: false } });
  }

  async createSession(
    tokenHash: Uint8Array,
    csrfHash: Uint8Array,
    sessionVersion: number,
    expiresAt: string,
  ): Promise<void> {
    const { error } = await this.db.rpc("session_create", {
      p_token_hash_b64: b64(tokenHash),
      p_csrf_hash_b64: b64(csrfHash),
      p_session_version: sessionVersion,
      p_expires_at: expiresAt,
    });
    if (error) mapPgError(error);
  }

  async getSessionByHash(tokenHash: Uint8Array): Promise<SessionRow | null> {
    const { data, error } = await this.db.rpc("session_get", { p_token_hash_b64: b64(tokenHash) });
    if (error) mapPgError(error);
    const row = Array.isArray(data) ? data[0] : data;
    if (!row) return null;
    return {
      id: row.id,
      session_version: row.session_version,
      csrf_token_hash: hexByteaToBytes(row.csrf_token_hash),
      expires_at: row.expires_at,
      revoked_at: row.revoked_at,
    };
  }

  async revokeSession(tokenHash: Uint8Array): Promise<void> {
    const { error } = await this.db.rpc("session_revoke", { p_token_hash_b64: b64(tokenHash) });
    if (error) mapPgError(error);
  }

  async touchSession(tokenHash: Uint8Array, at: string): Promise<void> {
    await this.db.rpc("session_touch", { p_token_hash_b64: b64(tokenHash), p_at: at });
  }

  async rotateCsrf(tokenHash: Uint8Array, newCsrfHash: Uint8Array): Promise<void> {
    const { error } = await this.db.rpc("session_rotate_csrf", {
      p_token_hash_b64: b64(tokenHash),
      p_new_csrf_hash_b64: b64(newCsrfHash),
    });
    if (error) mapPgError(error);
  }

  async countRecentLoginFailures(attemptKey: string, since: string): Promise<number> {
    const { data, error } = await this.db.rpc("login_failures_recent", {
      p_key: attemptKey,
      p_since: since,
    });
    if (error) mapPgError(error);
    return Number(data ?? 0);
  }

  async recordLoginAttempt(attemptKey: string, succeeded: boolean): Promise<void> {
    await this.db.rpc("login_attempt_record", { p_key: attemptKey, p_succeeded: succeeded });
  }

  async listActiveMembers(): Promise<MemberRow[]> {
    const { data, error } = await this.db
      .from("members").select("id, display_name, active").eq("active", true).order("display_name");
    if (error) mapPgError(error);
    return (data ?? []) as MemberRow[];
  }

  private nonPhoneCols =
    "id, booking_code, vessel, starts_at, ends_at, booker_name, booker_member_id, departure, destination, total_people, responsible_member_id, status, created_at, updated_at, cancelled_at";

  async listActiveBookingsInRange(from: string, to: string): Promise<BookingRow[]> {
    // Never selects phone columns for the public availability surface.
    const { data, error } = await this.db
      .from("bookings").select(this.nonPhoneCols)
      .neq("status", "cancelled")
      .lt("starts_at", to).gt("ends_at", from);
    if (error) mapPgError(error);
    const rows = (data ?? []) as unknown as Record<string, unknown>[];
    return rows.map((r) => ({
      ...r,
      booker_phone_encrypted: new Uint8Array(),
    })) as BookingRow[];
  }

  async listBookings(): Promise<BookingRow[]> {
    const { data, error } = await this.db
      .from("bookings").select(this.nonPhoneCols).order("starts_at", { ascending: false });
    if (error) mapPgError(error);
    const rows = (data ?? []) as unknown as Record<string, unknown>[];
    return rows.map((r) => ({
      ...r,
      booker_phone_encrypted: new Uint8Array(),
    })) as BookingRow[];
  }

  async getBooking(
    id: string,
  ): Promise<{ booking: BookingRow; passengers: PassengerRow[] } | null> {
    const detailColumns: string = `${this.nonPhoneCols}, booker_phone_encrypted`;
    const { data: b, error: be } = await this.db
      .from("bookings").select(detailColumns).eq("id", id)
      .maybeSingle();
    if (be) mapPgError(be);
    if (!b) return null;
    const rawBooking = b as unknown as Record<string, unknown>;
    const booking: BookingRow = {
      ...rawBooking,
      booker_phone_encrypted: hexByteaToBytes(rawBooking.booker_phone_encrypted),
    } as BookingRow;
    const { data: ps, error: pe } = await this.db
      .from("booking_passengers")
      .select("passenger_type, member_id, guest_name, guest_phone_encrypted").eq("booking_id", id);
    if (pe) mapPgError(pe);
    const passengerRows = (ps ?? []) as unknown as Record<string, unknown>[];
    const passengers: PassengerRow[] = passengerRows.map((p) => ({
      passenger_type: p.passenger_type as "member" | "guest",
      member_id: p.member_id as string | null,
      guest_name: p.guest_name as string | null,
      guest_phone_encrypted: p.guest_phone_encrypted
        ? hexByteaToBytes(p.guest_phone_encrypted)
        : null,
    }));
    return { booking, passengers };
  }

  async createBooking(
    args: CreateBookingArgs,
  ): Promise<{ id: string; booking_code: string; replayed: boolean }> {
    const { data, error } = await this.db.rpc("create_booking", {
      p_booking_code: args.booking_code,
      p_starts_at: args.starts_at,
      p_ends_at: args.ends_at,
      // Only the requester's active-member id is sent; the RPC derives the
      // trusted display name server-side and stores no requester phone.
      p_booker_member_id: args.booker_member_id,
      p_destination: args.destination,
      p_total_people: args.total_people,
      p_responsible_member_id: args.responsible_member_id,
      p_waiver_version: args.waiver_version,
      p_waiver_accepted_at: args.waiver_accepted_at,
      p_privacy_consent_version: args.privacy_consent_version,
      p_privacy_consent_accepted_at: args.privacy_consent_accepted_at,
      p_passengers: passengersToJson(args.passengers),
      p_session_hash_b64: b64(args.session_hash),
      p_api_idempotency_key: args.api_idempotency_key,
      p_outbox_idempotency_key: args.outbox_idempotency_key,
      p_outbox_payload: args.outbox_payload,
    });
    if (error) mapPgError(error);
    const row = Array.isArray(data) ? data[0] : data;
    return { id: row.id, booking_code: row.booking_code, replayed: !!row.replayed };
  }

  async updateBooking(args: UpdateBookingArgs): Promise<void> {
    const { error } = await this.db.rpc("update_booking", {
      p_id: args.id,
      p_starts_at: args.starts_at,
      p_ends_at: args.ends_at,
      p_booker_member_id: args.booker_member_id,
      p_destination: args.destination,
      p_total_people: args.total_people,
      p_responsible_member_id: args.responsible_member_id,
      p_passengers: passengersToJson(args.passengers),
      p_session_hash_b64: b64(args.session_hash),
      p_outbox_idempotency_key: args.outbox_idempotency_key,
      p_outbox_payload: args.outbox_payload,
    });
    if (error) mapPgError(error);
  }

  async cancelBooking(
    id: string,
    sessionHash: Uint8Array,
    outboxIdempotencyKey: string,
    outboxPayload: Record<string, unknown>,
  ): Promise<void> {
    const { error } = await this.db.rpc("cancel_booking", {
      p_id: id,
      p_session_hash_b64: b64(sessionHash),
      p_outbox_idempotency_key: outboxIdempotencyKey,
      p_outbox_payload: outboxPayload,
    });
    if (error) mapPgError(error);
  }
}
