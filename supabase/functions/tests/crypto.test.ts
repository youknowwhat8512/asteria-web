import test from "node:test";
import assert from "node:assert/strict";
import { pbkdf2Sync, randomBytes as nodeRandomBytes } from "node:crypto";
import {
  base64ToBytes,
  bytesToBase64,
  constantTimeEqualBytes,
  decryptContact,
  encryptContact,
  parseEncryptionKey,
  parsePbkdf2,
  randomBookingCode,
  randomToken,
  sha256,
  verifyPassword,
} from "../_shared/crypto.ts";

// Build a Django-format pbkdf2_sha256 hash using an INDEPENDENT implementation
// (node:crypto) to prove our verifier is compatible with the documented format
// pbkdf2_sha256$iterations$salt$hash.
function makeDjangoHash(password: string, iterations: number, salt: string): string {
  const dk = pbkdf2Sync(password, salt, iterations, 32, "sha256");
  return `pbkdf2_sha256$${iterations}$${salt}$${dk.toString("base64")}`;
}

test("verifyPassword accepts the correct password (node-generated hash)", async () => {
  const encoded = makeDjangoHash("s3cret-사랑", 120000, "abcSALT123");
  assert.equal(await verifyPassword("s3cret-사랑", encoded), true);
});

test("verifyPassword rejects the wrong password (constant-time)", async () => {
  const encoded = makeDjangoHash("correct horse", 100000, "saltyMcSalt");
  assert.equal(await verifyPassword("wrong horse", encoded), false);
  assert.equal(await verifyPassword("", encoded), false);
});

test("verifyPassword rejects malformed / unsupported hashes without throwing", async () => {
  assert.equal(await verifyPassword("x", "not-a-hash"), false);
  assert.equal(await verifyPassword("x", "bcrypt$12$salt$hash"), false);
  assert.equal(await verifyPassword("x", "pbkdf2_sha256$0$salt$aGk="), false);
});

test("parsePbkdf2 extracts fields", () => {
  const p = parsePbkdf2("pbkdf2_sha256$260000$saltX$aGVsbG8=");
  assert.equal(p.algorithm, "pbkdf2_sha256");
  assert.equal(p.iterations, 260000);
  assert.equal(p.salt, "saltX");
});

test("constantTimeEqualBytes true/false and length-mismatch", () => {
  assert.equal(constantTimeEqualBytes(new Uint8Array([1, 2, 3]), new Uint8Array([1, 2, 3])), true);
  assert.equal(constantTimeEqualBytes(new Uint8Array([1, 2, 3]), new Uint8Array([1, 2, 4])), false);
  assert.equal(constantTimeEqualBytes(new Uint8Array([1, 2]), new Uint8Array([1, 2, 3])), false);
});

test("sha256 matches base64 round-trip and is 32 bytes", async () => {
  const h = await sha256("hello");
  assert.equal(h.length, 32);
  assert.deepEqual(base64ToBytes(bytesToBase64(h)), h);
});

test("AES-GCM contact encryption round-trips and is authenticated", async () => {
  const key = parseEncryptionKey(nodeRandomBytes(32).toString("base64"));
  const blob = await encryptContact("01012345678", key);
  assert.equal(blob[0], 0x01, "version byte");
  assert.ok(blob.length > 1 + 12 + 16);
  assert.equal(await decryptContact(blob, key), "01012345678");

  // ciphertext contains no plaintext bytes
  const asText = new TextDecoder().decode(blob);
  assert.ok(!asText.includes("01012345678"));

  // tamper → auth failure
  blob[blob.length - 1] ^= 0xff;
  await assert.rejects(() => decryptContact(blob, key));
});

test("encryption uses a fresh IV each call (no deterministic ciphertext)", async () => {
  const key = parseEncryptionKey(nodeRandomBytes(32).toString("hex"));
  const a = await encryptContact("01099998888", key);
  const b = await encryptContact("01099998888", key);
  assert.notDeepEqual(a, b);
});

test("parseEncryptionKey accepts base64 and hex, rejects wrong length", () => {
  assert.equal(parseEncryptionKey(nodeRandomBytes(32).toString("hex")).length, 32);
  assert.equal(parseEncryptionKey(nodeRandomBytes(32).toString("base64")).length, 32);
  assert.throws(() => parseEncryptionKey(nodeRandomBytes(16).toString("base64")));
});

test("random tokens and booking codes are opaque and unique", () => {
  const codes = new Set(Array.from({ length: 200 }, () => randomBookingCode()));
  assert.ok(codes.size > 190, "booking codes should be near-unique");
  for (const c of codes) assert.match(c, /^VER-[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{8}$/);
  const t = randomToken(32);
  assert.match(t, /^[A-Za-z0-9_-]+$/);
  assert.notEqual(t, randomToken(32));
});
