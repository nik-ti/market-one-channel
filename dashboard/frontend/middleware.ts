// Nothing is served without the session cookie, except the login page itself.
//
// This is the whole reason the dashboard is not public: it lives on a custom
// domain, and Vercel's own protection covers every URL except those.

import { NextRequest, NextResponse } from "next/server";

import { COOKIE_NAME, verify } from "./lib/session";

const OPEN = ["/login", "/api/login"];

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  if (OPEN.some((path) => pathname.startsWith(path))) return NextResponse.next();

  if (await verify(request.cookies.get(COOKIE_NAME)?.value, process.env.SESSION_SECRET ?? "")) {
    return NextResponse.next();
  }

  // A page gets the login screen; the proxy gets a status its caller can read.
  if (pathname.startsWith("/api/")) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  const login = request.nextUrl.clone();
  login.pathname = "/login";
  return NextResponse.redirect(login);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
