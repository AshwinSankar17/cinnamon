/**
 * Device identity and protocol signatures (protocol/API.md v0.2).
 *
 * ENCODING (byte-for-byte; must match Brain):
 *   - Every "‖" in API.md is plain byte concatenation, no separators, no length prefixes.
 *   - Literal tags ("cb-pair-v1", "cb-chal-v1", "cb-tok-v1", "cb-dec-v1", "cb-ep-v1") are UTF-8 bytes.
 *   - brain_pub and device_pub are the RAW 32-byte Ed25519 public keys (base64url-decoded on the wire).
 *   - nonce_device, the challenge `nonce`, and the pairing `secret` are base64url-DECODED to raw bytes
 *     before being concatenated / used as the HMAC key.
 *   - All other strings (endpoint, host, approval id, decision, device_id, ts) are UTF-8 bytes of the
 *     exact string used on the wire. `endpoint` is the string exactly as it appeared in the pairing
 *     payload (no normalisation, no trailing-slash fix-up). `host` is the Host header value the app
 *     sent: lower-cased authority of the endpoint URL with the default port (:443 / :80) removed.
 *   - Signatures, keys, nonces and MACs are sent as base64url WITHOUT padding.
 *   - Unambiguity relies on fixed-length fields: keys and nonce_device are 32 bytes; the only
 *     variable-length field in the pairing transcript is `endpoint`.
 *
 * The device key is an Ed25519 seed (32 random bytes from expo-crypto) kept in expo-secure-store;
 * the keypair is derived from it on load. It never leaves the device.
 */
import * as Crypto from 'expo-crypto';
import { getSecret, setSecret } from './secure';
import nacl from 'tweetnacl';
import { sha256 } from '@noble/hashes/sha2.js';
import { hmac } from '@noble/hashes/hmac.js';
import { b64urlDecode, b64urlEncode, concatBytes, hex, utf8 } from './b64';

// tweetnacl's default PRNG looks for window.crypto / node crypto; route it to expo-crypto instead.
nacl.setPRNG((x: Uint8Array, n: number) => {
  const r = Crypto.getRandomBytes(n);
  for (let i = 0; i < n; i++) x[i] = r[i];
});

const SEED_KEY = 'cb.device_seed.v1';
export type DeviceKeys = { publicKey: Uint8Array; secretKey: Uint8Array };

let cached: DeviceKeys | null = null;

/** Returns the device Ed25519 keypair, generating and persisting it on first call. */
export async function getDeviceKeys(): Promise<DeviceKeys> {
  if (cached) return cached;
  let seedB64 = await getSecret(SEED_KEY);
  if (!seedB64) {
    seedB64 = b64urlEncode(Crypto.getRandomBytes(32));
    await setSecret(SEED_KEY, seedB64);
  }
  const kp = nacl.sign.keyPair.fromSeed(b64urlDecode(seedB64));
  cached = { publicKey: kp.publicKey, secretKey: kp.secretKey };
  return cached;
}

export async function devicePubB64(): Promise<string> {
  return b64urlEncode((await getDeviceKeys()).publicKey);
}

/** Detached Ed25519 signature over `message`, returned as raw bytes. */
export async function sign(message: Uint8Array): Promise<Uint8Array> {
  const { secretKey } = await getDeviceKeys();
  return nacl.sign.detached(message, secretKey);
}

export function verify(message: Uint8Array, sigB64: string, pubKey: Uint8Array): boolean {
  try {
    const sig = b64urlDecode(sigB64);
    if (sig.length !== 64 || pubKey.length !== 32) return false;
    return nacl.sign.detached.verify(message, sig, pubKey);
  } catch {
    return false;
  }
}

export function randomBytes(n: number): Uint8Array {
  return Crypto.getRandomBytes(n);
}

export function uuid(): string {
  return Crypto.randomUUID();
}

// ---------------------------------------------------------------------------------------------
// Pairing

export type PairingPayload = {
  v: 1;
  brain_pub: string; // b64url, 32 bytes
  endpoint: string;
  code: string;
  secret: string; // b64url, 32 bytes
  exp: number; // unix seconds
  alt?: string[]; // failover relay URLs (not part of the pairing transcript)
};

/** transcript = sha256("cb-pair-v1" ‖ brain_pub ‖ device_pub ‖ endpoint ‖ nonce_device) */
export function pairTranscript(
  brainPub: Uint8Array,
  devicePub: Uint8Array,
  endpoint: string,
  nonceDevice: Uint8Array,
): Uint8Array {
  return sha256(concatBytes(utf8('cb-pair-v1'), brainPub, devicePub, utf8(endpoint), nonceDevice));
}

export type PairCompleteBody = {
  code: string;
  device_pub: string;
  device_name: string;
  nonce_device: string;
  sig: string;
  mac: string;
};

/** Builds the POST /v1/pair/complete body and returns the transcript so brain_sig can be checked. */
export async function buildPairComplete(
  payload: PairingPayload,
  deviceName: string,
): Promise<{ body: PairCompleteBody; transcript: Uint8Array; brainPub: Uint8Array }> {
  const brainPub = b64urlDecode(payload.brain_pub);
  if (brainPub.length !== 32) throw new Error('brain_pub is not a 32-byte Ed25519 key');
  const secret = b64urlDecode(payload.secret);
  if (secret.length !== 32) throw new Error('pairing secret is not 32 bytes');
  const { publicKey } = await getDeviceKeys();
  const nonceDevice = randomBytes(32);
  const transcript = pairTranscript(brainPub, publicKey, payload.endpoint, nonceDevice);
  const sig = await sign(transcript);
  const mac = hmac(sha256, secret, transcript);
  return {
    transcript,
    brainPub,
    body: {
      code: payload.code,
      device_pub: b64urlEncode(publicKey),
      device_name: deviceName,
      nonce_device: b64urlEncode(nonceDevice),
      sig: b64urlEncode(sig),
      mac: b64urlEncode(mac),
    },
  };
}

export function verifyPairBrainSig(transcript: Uint8Array, brainSigB64: string, brainPub: Uint8Array): boolean {
  return verify(transcript, brainSigB64, brainPub);
}

// ---------------------------------------------------------------------------------------------
// Sessions

/** Host header value for an endpoint URL: lower-cased authority, default port stripped. */
export function hostOf(endpoint: string): string {
  const m = /^(https?):\/\/([^/?#]+)/i.exec(endpoint.trim());
  if (!m) throw new Error(`not an http(s) endpoint: ${endpoint}`);
  const scheme = m[1].toLowerCase();
  let authority = m[2].toLowerCase();
  authority = authority.replace(/^[^@]*@/, ''); // drop userinfo if any
  if (scheme === 'https' && authority.endsWith(':443')) authority = authority.slice(0, -4);
  if (scheme === 'http' && authority.endsWith(':80')) authority = authority.slice(0, -3);
  return authority;
}

/** Brain's challenge signature covers "cb-chal-v1" ‖ nonce ‖ host. */
export function verifyChallenge(nonceB64: string, host: string, brainSigB64: string, brainPub: Uint8Array): boolean {
  const msg = concatBytes(utf8('cb-chal-v1'), b64urlDecode(nonceB64), utf8(host));
  return verify(msg, brainSigB64, brainPub);
}

/** Device signature for /v1/session/token: "cb-tok-v1" ‖ nonce. */
export async function signToken(nonceB64: string): Promise<string> {
  return b64urlEncode(await sign(concatBytes(utf8('cb-tok-v1'), b64urlDecode(nonceB64))));
}

/** Device signature for an approval decision: "cb-dec-v1" ‖ id ‖ decision. The note is NOT covered. */
export async function signDecision(approvalId: string, decision: 'allow' | 'deny'): Promise<string> {
  return b64urlEncode(await sign(concatBytes(utf8('cb-dec-v1'), utf8(approvalId), utf8(decision))));
}

/** Push kind:"transport": endpoint_sig = Brain sig over "cb-ep-v1" ‖ endpoint ‖ ts ‖ device_id.
 *  `ts` is taken as the UTF-8 decimal string carried in the push data. */
export function verifyEndpointSig(
  endpoint: string,
  ts: string,
  deviceId: string,
  sigB64: string,
  brainPub: Uint8Array,
): boolean {
  const msg = concatBytes(utf8('cb-ep-v1'), utf8(endpoint), utf8(ts), utf8(deviceId));
  return verify(msg, sigB64, brainPub);
}

/** Short human fingerprint of a public key: first and last two bytes of sha256, "9f:2c:…:e1:07". */
export function fingerprint(pub: Uint8Array): string {
  const h = hex(sha256(pub));
  const pairs = h.match(/../g) ?? [];
  return `${pairs.slice(0, 2).join(':')}:…:${pairs.slice(-2).join(':')}`;
}

export function fullFingerprint(pub: Uint8Array): string {
  return (hex(sha256(pub)).match(/../g) ?? []).slice(0, 16).join(':');
}

export function parsePairingPayload(raw: string): PairingPayload {
  let obj: unknown;
  try {
    obj = JSON.parse(raw.trim());
  } catch {
    throw new Error('Not a pairing payload (expected the JSON printed by `brain pair`).');
  }
  const p = obj as Partial<PairingPayload>;
  if (p.v !== 1) throw new Error(`Unsupported pairing payload version: ${String(p.v)}`);
  for (const k of ['brain_pub', 'endpoint', 'code', 'secret'] as const) {
    if (typeof p[k] !== 'string' || !p[k]) throw new Error(`Pairing payload is missing ${k}.`);
  }
  if (typeof p.exp !== 'number') throw new Error('Pairing payload is missing exp.');
  if (p.alt !== undefined && !(Array.isArray(p.alt) && p.alt.every((x) => typeof x === 'string'))) {
    throw new Error('Pairing payload has a malformed alt list.');
  }
  return p as PairingPayload;
}
