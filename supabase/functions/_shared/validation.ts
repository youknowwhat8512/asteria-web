// Strict input validation for the Veronica booking API.
// Pure functions only — no I/O, no Deno globals — so they unit-test under Node.
//
// Each validator returns { ok, value, errors }. Callers must treat ok===false
// as a 400 and NEVER echo raw untrusted input back to the client verbatim.

export interface ValidationResult<T> {
  ok: boolean;
  value?: T;
  errors: string[];
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
// Korean mobile numbers: 10 or 11 digits starting 01x (01 + 8~9 more digits).
const PHONE_RE = /^01[0-9]{8,9}$/;

const MAX_TEXT = 200;
const MAX_PEOPLE = 8;
const MIN_PEOPLE = 1;

// KST is a fixed +09:00 offset (no DST), so day boundaries are deterministic.
const KST_OFFSET_MS = 9 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;

/** True for a canonical v1–v5 UUID. Used to reject malformed route ids early. */
export function isUuid(v: unknown): v is string {
  return typeof v === "string" && UUID_RE.test(v);
}

/** UTC instant of KST 00:00 for the operating day that contains `d`. */
function kstDayStartUtcMs(d: Date): number {
  const shifted = d.getTime() + KST_OFFSET_MS;
  return Math.floor(shifted / DAY_MS) * DAY_MS - KST_OFFSET_MS;
}

/**
 * A booking must fall within a single KST operating day. The UI models a
 * booking as one date + start/end hour where the end hour may be 24:00, so the
 * end is allowed to land exactly on the next KST midnight (24:00) but no later.
 * This closes the UI/API mismatch where the API otherwise accepted multi-day
 * ranges the form can never produce.
 */
function isWithinSingleKstDay(startsAt: Date, endsAt: Date): boolean {
  const dayStart = kstDayStartUtcMs(startsAt);
  const dayEndInclusive = dayStart + DAY_MS; // next KST midnight (24:00), allowed
  return startsAt.getTime() >= dayStart && endsAt.getTime() <= dayEndInclusive;
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function onlyKeys(obj: Record<string, unknown>, allowed: string[]): string[] {
  return Object.keys(obj).filter((k) => !allowed.includes(k));
}

/** Normalize a phone string to bare digits (strips spaces/dashes) then validate. */
export function normalizePhone(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const digits = raw.replace(/[^0-9]/g, "");
  return PHONE_RE.test(digits) ? digits : null;
}

function isWholeHourIso(v: unknown): Date | null {
  if (typeof v !== "string") return null;
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return null;
  // Must land exactly on a whole hour in UTC (mirrors the DB epoch % 3600 check).
  if (d.getUTCMinutes() !== 0 || d.getUTCSeconds() !== 0 || d.getUTCMilliseconds() !== 0) {
    return null;
  }
  return d;
}

// ---------------------------------------------------------------------------
// Login
// ---------------------------------------------------------------------------
export interface LoginInput {
  id: string;
  password: string;
}

export function validateLogin(body: unknown, expectedId: string): ValidationResult<LoginInput> {
  const errors: string[] = [];
  if (!isPlainObject(body)) return { ok: false, errors: ["body must be a JSON object"] };
  const extra = onlyKeys(body, ["id", "password"]);
  if (extra.length) errors.push("unexpected fields");
  const id = body.id;
  const password = body.password;
  if (typeof id !== "string" || id.length === 0 || id.length > MAX_TEXT) errors.push("invalid id");
  if (typeof password !== "string" || password.length === 0 || password.length > 1024) {
    errors.push("invalid password");
  }
  // The public login id is fixed; a wrong id is an auth failure, not a hint.
  if (typeof id === "string" && id !== expectedId) errors.push("invalid credentials");
  if (errors.length) return { ok: false, errors };
  return { ok: true, value: { id: id as string, password: password as string }, errors: [] };
}

// ---------------------------------------------------------------------------
// Availability query
// ---------------------------------------------------------------------------
export interface AvailabilityQuery {
  from: Date;
  to: Date;
}

export function validateAvailabilityQuery(
  fromRaw: string | null,
  toRaw: string | null,
): ValidationResult<AvailabilityQuery> {
  const errors: string[] = [];
  if (!fromRaw || !toRaw) return { ok: false, errors: ["from and to are required"] };
  const from = new Date(fromRaw);
  const to = new Date(toRaw);
  if (Number.isNaN(from.getTime())) errors.push("invalid from");
  if (Number.isNaN(to.getTime())) errors.push("invalid to");
  if (!errors.length && from.getTime() >= to.getTime()) errors.push("from must be before to");
  // Bound the window to keep the query cheap and prevent scraping the whole DB.
  if (!errors.length && to.getTime() - from.getTime() > 366 * 24 * 3600 * 1000) {
    errors.push("range too large");
  }
  if (errors.length) return { ok: false, errors };
  return { ok: true, value: { from, to }, errors: [] };
}

// ---------------------------------------------------------------------------
// Passenger list
// ---------------------------------------------------------------------------
export interface MemberPassenger {
  type: "member";
  memberId: string;
}
export interface GuestPassenger {
  type: "guest";
  name: string;
  phone: string; // normalized digits
}
export type Passenger = MemberPassenger | GuestPassenger;

function validatePassenger(raw: unknown, errors: string[], idx: number): Passenger | null {
  if (!isPlainObject(raw)) {
    errors.push(`passenger[${idx}] invalid`);
    return null;
  }
  if (raw.type === "member") {
    if (onlyKeys(raw, ["type", "memberId"]).length) {
      errors.push(`passenger[${idx}] unexpected fields`);
    }
    if (typeof raw.memberId !== "string" || !UUID_RE.test(raw.memberId)) {
      errors.push(`passenger[${idx}] invalid memberId`);
      return null;
    }
    return { type: "member", memberId: raw.memberId };
  }
  if (raw.type === "guest") {
    if (onlyKeys(raw, ["type", "name", "phone"]).length) {
      errors.push(`passenger[${idx}] unexpected fields`);
    }
    const name = typeof raw.name === "string" ? raw.name.trim() : "";
    const phone = normalizePhone(raw.phone);
    if (name.length === 0 || name.length > MAX_TEXT) errors.push(`passenger[${idx}] invalid name`);
    if (!phone) errors.push(`passenger[${idx}] invalid phone`);
    if (name.length === 0 || !phone) return null;
    return { type: "guest", name, phone };
  }
  errors.push(`passenger[${idx}] invalid type`);
  return null;
}

// ---------------------------------------------------------------------------
// Create booking
// ---------------------------------------------------------------------------
export interface CreateBookingInput {
  startsAt: Date;
  endsAt: Date;
  destination: string;
  totalPeople: number;
  // Internal derived value: the requester is always the responsible operator.
  // Clients do not send a separate responsibleMemberId field.
  responsibleMemberId: string;
  // The requester (예약자) is an active-member reference. The trusted display
  // name is derived server-side; the browser never supplies a name or phone.
  bookerMemberId: string;
  passengers: Passenger[];
  waiverVersion: string;
  waiverAccepted: true;
  privacyConsentVersion: string;
  privacyConsentAccepted: true;
}

const CREATE_KEYS = [
  "startsAt",
  "endsAt",
  "destination",
  "totalPeople",
  // Backward-compatible during browser cache rollout. If an older client sends
  // this field it is ignored; the derived value always comes from bookerMemberId.
  "responsibleMemberId",
  "bookerMemberId",
  "passengers",
  "waiverVersion",
  "waiverAccepted",
  "privacyConsentVersion",
  "privacyConsentAccepted",
];

export function validateCreateBooking(body: unknown): ValidationResult<CreateBookingInput> {
  const errors: string[] = [];
  if (!isPlainObject(body)) return { ok: false, errors: ["body must be a JSON object"] };
  if (onlyKeys(body, CREATE_KEYS).length) errors.push("unexpected fields");

  const startsAt = isWholeHourIso(body.startsAt);
  const endsAt = isWholeHourIso(body.endsAt);
  if (!startsAt) errors.push("startsAt must be a whole-hour ISO timestamp");
  if (!endsAt) errors.push("endsAt must be a whole-hour ISO timestamp");
  if (startsAt && endsAt && startsAt.getTime() >= endsAt.getTime()) {
    errors.push("startsAt must be before endsAt");
  }
  if (
    startsAt && endsAt && startsAt.getTime() < endsAt.getTime() &&
    !isWithinSingleKstDay(startsAt, endsAt)
  ) {
    errors.push("booking must fall within a single KST operating day (end at 24:00 allowed)");
  }

  const destination = typeof body.destination === "string" ? body.destination.trim() : "";
  if (destination.length === 0 || destination.length > MAX_TEXT) errors.push("invalid destination");

  const totalPeople = body.totalPeople;
  if (
    typeof totalPeople !== "number" || !Number.isInteger(totalPeople) ||
    totalPeople < MIN_PEOPLE || totalPeople > MAX_PEOPLE
  ) {
    errors.push("totalPeople must be an integer 1..8");
  }


  // Requester: an active-member id. Existence/active-ness is verified against
  // the member list by the router (and by DB FK + trigger); here we only reject
  // a malformed id early. No booker name or phone is part of the contract.
  if (typeof body.bookerMemberId !== "string" || !UUID_RE.test(body.bookerMemberId)) {
    errors.push("invalid bookerMemberId");
  }

  const passengers: Passenger[] = [];
  if (
    !Array.isArray(body.passengers) || body.passengers.length === 0 ||
    body.passengers.length > MAX_PEOPLE
  ) {
    errors.push("passengers must be a non-empty array of up to 8");
  } else {
    body.passengers.forEach((p, i) => {
      const parsed = validatePassenger(p, errors, i);
      if (parsed) passengers.push(parsed);
    });
  }

  const waiverVersion = typeof body.waiverVersion === "string" ? body.waiverVersion.trim() : "";
  if (waiverVersion.length === 0 || waiverVersion.length > MAX_TEXT) {
    errors.push("invalid waiverVersion");
  }
  if (body.waiverAccepted !== true) errors.push("waiver must be accepted");

  const privacyConsentVersion = typeof body.privacyConsentVersion === "string"
    ? body.privacyConsentVersion.trim()
    : "";
  if (privacyConsentVersion.length === 0 || privacyConsentVersion.length > MAX_TEXT) {
    errors.push("invalid privacyConsentVersion");
  }
  if (body.privacyConsentAccepted !== true) errors.push("privacy consent must be accepted");

  // Cross-field invariants (mirrored server-side by DB constraints/triggers):
  if (passengers.length && typeof totalPeople === "number" && passengers.length !== totalPeople) {
    errors.push("passenger count must equal totalPeople");
  }
  if (passengers.length && !passengers.some((p) => p.type === "member")) {
    errors.push("at least one member passenger is required");
  }

  if (errors.length) return { ok: false, errors };
  return {
    ok: true,
    errors: [],
    value: {
      startsAt: startsAt!,
      endsAt: endsAt!,
      destination,
      totalPeople: totalPeople as number,
      responsibleMemberId: body.bookerMemberId as string,
      bookerMemberId: body.bookerMemberId as string,
      passengers,
      waiverVersion,
      waiverAccepted: true,
      privacyConsentVersion,
      privacyConsentAccepted: true,
    },
  };
}

// ---------------------------------------------------------------------------
// Patch booking — a full replacement of the mutable booking fields.
// Uses the same shape as create (simpler + safer than partial merges).
//
// INTENDED BEHAVIOUR — waiver / privacy consent on update:
// The payload is validated with the SAME shape as create, so waiverAccepted and
// privacyConsentAccepted must still be true and their versions present. This
// keeps a single, uniform booking payload on the client. However the update_booking
// RPC deliberately does NOT write these consent fields: the ORIGINAL acceptance
// (version + timestamp captured at creation) is preserved untouched. The resent
// consent flags are therefore confirmatory only — an edit never rewrites, clears,
// or bumps the stored consent version. See supabase-store.updateBooking (which
// omits the consent params) and migration 20260727090000 (update_booking).
// ---------------------------------------------------------------------------
export function validatePatchBooking(body: unknown): ValidationResult<CreateBookingInput> {
  return validateCreateBooking(body);
}
