// HMAC-SHA256 webhook signature verification (TypeScript runtime).
// Sibling of verify.py — same security contract, different runtime.

import { createHmac, timingSafeEqual } from "node:crypto";

export class InvalidWebhookSignature extends Error {}

export function verifyWebhookSignature(
  body: Buffer,
  signatureHeader: string,
  secret: string,
): true {
  // Validate an incoming webhook's HMAC signature before processing.
  // Implements the security contract: webhook payloads must carry a
  // valid HMAC-SHA256 signature in the X-Signature header, computed
  // over the raw body using the per-source shared secret. Constant-time
  // comparison via timingSafeEqual. Sibling of verify.py — same
  // contract for the TypeScript runtime.
  const expected = createHmac("sha256", secret).update(body).digest("hex");
  const a = Buffer.from(expected);
  const b = Buffer.from(signatureHeader);
  if (a.length !== b.length || !timingSafeEqual(a, b)) {
    throw new InvalidWebhookSignature();
  }
  return true;
}

export function extractSignatureHeader(
  headers: Record<string, string>,
): string | undefined {
  // Pull the signature from X-Signature, fallback to legacy X-Sig.
  return headers["X-Signature"] ?? headers["X-Sig"];
}
