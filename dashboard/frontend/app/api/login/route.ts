// Checks the password and hands back the session cookie.

import { NextRequest, NextResponse } from "next/server";

import { COOKIE_NAME, issue } from "@/lib/session";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  const password = process.env.DASHBOARD_PASSWORD ?? "";
  const secret = process.env.SESSION_SECRET ?? "";
  if (!password || !secret) {
    return NextResponse.json({ error: "login is not configured" }, { status: 500 });
  }

  const { password: supplied } = (await request.json()) as { password?: string };
  if (!supplied || supplied !== password) {
    // Deliberately slow: a password worth guessing should not be guessable
    // thousands of times a second.
    await new Promise((resolve) => setTimeout(resolve, 600));
    return NextResponse.json({ error: "wrong password" }, { status: 401 });
  }

  const { value, maxAge } = await issue(secret);
  const response = NextResponse.json({ ok: true });
  response.cookies.set(COOKIE_NAME, value, {
    httpOnly: true,     // JavaScript on the page cannot read it
    secure: true,       // only sent over HTTPS
    sameSite: "lax",    // not sent from another site's requests
    path: "/",
    maxAge,
  });
  return response;
}
