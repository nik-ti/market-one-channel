// Forwards the dashboard's requests to the API on the VPS.
//
// Two things happen here that cannot happen in the browser. The token is added
// server-side, so it never appears in the page source — anything named
// NEXT_PUBLIC_ would. And the request leaves Vercel's server over plain HTTP,
// which a browser would refuse to do from an HTTPS page.

import { NextRequest, NextResponse } from "next/server";

const BACKEND = process.env.BACKEND_ORIGIN ?? "";
const TOKEN = process.env.DASHBOARD_TOKEN ?? "";

export const dynamic = "force-dynamic";

async function forward(request: NextRequest, path: string[]) {
  if (!BACKEND || !TOKEN) {
    return NextResponse.json(
      { error: "dashboard is not configured: BACKEND_ORIGIN or DASHBOARD_TOKEN is missing" },
      { status: 500 },
    );
  }

  const target = `${BACKEND}/${path.join("/")}${request.nextUrl.search}`;
  try {
    const response = await fetch(target, {
      method: request.method,
      headers: {
        Authorization: `Bearer ${TOKEN}`,
        ...(request.method === "POST" ? { "Content-Type": "application/json" } : {}),
      },
      body: request.method === "POST" ? await request.text() : undefined,
      cache: "no-store",
    });
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: { "Content-Type": response.headers.get("Content-Type") ?? "application/json" },
    });
  } catch (error) {
    return NextResponse.json(
      { error: `cannot reach the API: ${error instanceof Error ? error.message : "unknown"}` },
      { status: 502 },
    );
  }
}

export async function GET(request: NextRequest, context: { params: { path: string[] } }) {
  return forward(request, context.params.path);
}

export async function POST(request: NextRequest, context: { params: { path: string[] } }) {
  return forward(request, context.params.path);
}
