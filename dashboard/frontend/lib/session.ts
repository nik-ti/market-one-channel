// The dashboard's login. One password, one signed cookie, no user accounts.
//
// The cookie carries an expiry and a signature made with SESSION_SECRET, so it
// can be checked without storing anything server-side — which matters because
// Vercel functions keep nothing between requests.
//
// Web Crypto rather than node:crypto: middleware runs on the edge runtime,
// where node's crypto is not available.

const ENCODER = new TextEncoder();

export const COOKIE_NAME = "m1_session";
const MAX_AGE_SECONDS = 60 * 60 * 24 * 30;

function base64url(bytes: ArrayBuffer | Uint8Array): string {
  const view = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let binary = "";
  view.forEach((b) => (binary += String.fromCharCode(b)));
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function sign(payload: string, secret: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw", ENCODER.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]
  );
  return base64url(await crypto.subtle.sign("HMAC", key, ENCODER.encode(payload)));
}

/** A cookie value proving the password was entered, valid for 30 days. */
export async function issue(secret: string): Promise<{ value: string; maxAge: number }> {
  const expiresAt = Date.now() + MAX_AGE_SECONDS * 1000;
  const payload = String(expiresAt);
  return { value: `${payload}.${await sign(payload, secret)}`, maxAge: MAX_AGE_SECONDS };
}

/** True if this cookie was signed by us and has not expired. */
export async function verify(cookie: string | undefined, secret: string): Promise<boolean> {
  if (!cookie || !secret) return false;
  const [payload, signature] = cookie.split(".");
  if (!payload || !signature) return false;

  const expected = await sign(payload, secret);
  // Length-independent comparison, so a wrong signature gives nothing away.
  if (expected.length !== signature.length) return false;
  let same = 0;
  for (let i = 0; i < expected.length; i++) same |= expected.charCodeAt(i) ^ signature.charCodeAt(i);
  if (same !== 0) return false;

  const expiresAt = Number(payload);
  return Number.isFinite(expiresAt) && expiresAt > Date.now();
}
