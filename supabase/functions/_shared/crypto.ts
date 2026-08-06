// Cryptographic primitives for the Veronica charter booking API.
//
// This module uses ONLY Web Standard APIs (Web Crypto, TextEncoder, btoa/atob)
// so it runs unchanged under the Deno Edge runtime AND under `node --test`.
// No secret values live here — every key/hash is passed in by the caller,
// which sources it from Supabase Secrets at runtime.

const encoder = new TextEncoder();

// Web Crypto's BufferSource parameter resolves to an ArrayBuffer-backed view in
// current TypeScript libs: a plain `Uint8Array` defaults to
// `Uint8Array<ArrayBufferLike>` (which could be backed by a SharedArrayBuffer)
// and is NOT assignable to BufferSource. Copy any Uint8Array into a fresh
// ArrayBuffer-backed view before handing it to crypto.subtle. The copy is cheap
// (keys/IVs/digests are tens of bytes) and keeps every call site type-safe under
// both the Deno Edge runtime and node:test.
function bufferSource(bytes: Uint8Array): Uint8Array<ArrayBuffer> {
  const copy = new Uint8Array(bytes.length);
  copy.set(bytes);
  return copy;
}

// ---------------------------------------------------------------------------
// base64 helpers (work in both Deno and Node via global btoa/atob)
// ---------------------------------------------------------------------------
export function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}

export function base64ToBytes(b64: string): Uint8Array {
  const binary = atob(b64.trim());
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}

export function bytesToBase64Url(bytes: Uint8Array): string {
  return bytesToBase64(bytes).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// ---------------------------------------------------------------------------
// constant-time comparison — never short-circuits on the first differing byte
// ---------------------------------------------------------------------------
export function constantTimeEqualBytes(a: Uint8Array, b: Uint8Array): boolean {
  // Compare against the max length so the running time does not reveal which
  // input was shorter. A length mismatch always yields false.
  const len = Math.max(a.length, b.length);
  let diff = a.length ^ b.length;
  for (let i = 0; i < len; i++) {
    diff |= (a[i] ?? 0) ^ (b[i] ?? 0);
  }
  return diff === 0;
}

export function constantTimeEqualString(a: string, b: string): boolean {
  return constantTimeEqualBytes(encoder.encode(a), encoder.encode(b));
}

// ---------------------------------------------------------------------------
// SHA-256 — used to store only the HASH of opaque session tokens
// ---------------------------------------------------------------------------
export async function sha256(data: Uint8Array | string): Promise<Uint8Array> {
  const bytes = typeof data === "string" ? encoder.encode(data) : data;
  const digest = await crypto.subtle.digest("SHA-256", bufferSource(bytes));
  return new Uint8Array(digest);
}

// ---------------------------------------------------------------------------
// Random opaque values — booking codes, session tokens, idempotency keys, CSRF
// All generated server-side from a CSPRNG.
// ---------------------------------------------------------------------------
export function randomBytes(n: number): Uint8Array {
  const buf = new Uint8Array(n);
  crypto.getRandomValues(buf);
  return buf;
}

/** URL-safe opaque token (default 32 bytes → 43 char base64url). */
export function randomToken(bytes = 32): string {
  return bytesToBase64Url(randomBytes(bytes));
}

/** Human-shareable booking code like VER-7Q3K9F2M (crockford-ish, no ambiguous chars). */
export function randomBookingCode(): string {
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"; // no I,O,0,1
  const raw = randomBytes(8);
  let out = "";
  for (let i = 0; i < 8; i++) out += alphabet[raw[i] % alphabet.length];
  return `VER-${out}`;
}

// ---------------------------------------------------------------------------
// PBKDF2-SHA256 password verification.
// Compatible with the Django-style encoded form:
//   pbkdf2_sha256$<iterations>$<salt>$<base64-hash>
// where the salt is used as raw UTF-8 bytes and the derived key length matches
// the digest size (32 bytes), exactly like Django's PBKDF2PasswordHasher.
// The full encoded string is sourced from ASTERIA_PASSWORD_HASH (a Secret).
// ---------------------------------------------------------------------------
export interface ParsedPbkdf2 {
  algorithm: string;
  iterations: number;
  salt: string;
  hashB64: string;
}

export function parsePbkdf2(encoded: string): ParsedPbkdf2 {
  const parts = encoded.split("$");
  if (parts.length !== 4) {
    throw new Error("malformed password hash");
  }
  const [algorithm, iterationsStr, salt, hashB64] = parts;
  const iterations = Number.parseInt(iterationsStr, 10);
  if (algorithm !== "pbkdf2_sha256") {
    throw new Error("unsupported password hash algorithm");
  }
  if (!Number.isInteger(iterations) || iterations < 1) {
    throw new Error("invalid iteration count");
  }
  if (salt.length === 0 || hashB64.length === 0) {
    throw new Error("missing salt or hash");
  }
  return { algorithm, iterations, salt, hashB64 };
}

export async function pbkdf2Sha256(
  password: string,
  salt: string,
  iterations: number,
  dkLenBytes = 32,
): Promise<Uint8Array> {
  const keyMaterial = await crypto.subtle.importKey(
    "raw",
    encoder.encode(password),
    "PBKDF2",
    false,
    ["deriveBits"],
  );
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", salt: encoder.encode(salt), iterations, hash: "SHA-256" },
    keyMaterial,
    dkLenBytes * 8,
  );
  return new Uint8Array(bits);
}

/** Constant-time verification of a candidate password against an encoded hash. */
export async function verifyPassword(password: string, encoded: string): Promise<boolean> {
  let parsed: ParsedPbkdf2;
  try {
    parsed = parsePbkdf2(encoded);
  } catch {
    return false;
  }
  let expected: Uint8Array;
  try {
    expected = base64ToBytes(parsed.hashB64);
  } catch {
    return false;
  }
  const derived = await pbkdf2Sha256(password, parsed.salt, parsed.iterations, expected.length);
  return constantTimeEqualBytes(derived, expected);
}

// ---------------------------------------------------------------------------
// AES-256-GCM authenticated encryption for phone numbers at rest.
// Key comes from ASTERIA_CONTACT_ENCRYPTION_KEY (32 bytes, base64 or hex).
// Wire format (bytea stored in DB):  0x01 | iv(12) | ciphertext+tag
// ---------------------------------------------------------------------------
const CONTACT_VERSION = 0x01;
const IV_LEN = 12;

export function parseEncryptionKey(raw: string): Uint8Array {
  const trimmed = raw.trim();
  let bytes: Uint8Array;
  if (/^[0-9a-fA-F]{64}$/.test(trimmed)) {
    bytes = new Uint8Array(32);
    for (let i = 0; i < 32; i++) bytes[i] = Number.parseInt(trimmed.substr(i * 2, 2), 16);
  } else {
    bytes = base64ToBytes(trimmed);
  }
  if (bytes.length !== 32) {
    throw new Error("ASTERIA_CONTACT_ENCRYPTION_KEY must decode to 32 bytes");
  }
  return bytes;
}

function importAesKey(keyBytes: Uint8Array): Promise<CryptoKey> {
  return crypto.subtle.importKey("raw", bufferSource(keyBytes), "AES-GCM", false, [
    "encrypt",
    "decrypt",
  ]);
}

export async function encryptContact(plaintext: string, keyBytes: Uint8Array): Promise<Uint8Array> {
  const key = await importAesKey(keyBytes);
  const iv = randomBytes(IV_LEN);
  const ct = new Uint8Array(
    await crypto.subtle.encrypt(
      { name: "AES-GCM", iv: bufferSource(iv) },
      key,
      bufferSource(encoder.encode(plaintext)),
    ),
  );
  const out = new Uint8Array(1 + IV_LEN + ct.length);
  out[0] = CONTACT_VERSION;
  out.set(iv, 1);
  out.set(ct, 1 + IV_LEN);
  return out;
}

export async function decryptContact(blob: Uint8Array, keyBytes: Uint8Array): Promise<string> {
  if (blob.length < 1 + IV_LEN + 16 || blob[0] !== CONTACT_VERSION) {
    throw new Error("invalid contact ciphertext");
  }
  const key = await importAesKey(keyBytes);
  const iv = blob.slice(1, 1 + IV_LEN);
  const ct = blob.slice(1 + IV_LEN);
  const pt = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: bufferSource(iv) },
    key,
    bufferSource(ct),
  );
  return new TextDecoder().decode(pt);
}
