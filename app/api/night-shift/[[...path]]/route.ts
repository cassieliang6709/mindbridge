import { NextRequest, NextResponse } from "next/server";

const API_BASE = process.env.MINDBRIDGE_API_URL ?? "http://localhost:8000";
const TIMEOUT_MS = 15_000;

type RouteContext = { params: Promise<{ path?: string[] }> };

async function proxy(request: NextRequest, context: RouteContext) {
  const { path = [] } = await context.params;
  const suffix = path.length ? `/${path.map(encodeURIComponent).join("/")}` : "";
  try {
    const response = await fetch(`${API_BASE}/night-shift${suffix}`, {
      method: request.method,
      headers: { "content-type": "application/json" },
      body: request.method === "GET" ? undefined : await request.text(),
      signal: AbortSignal.timeout(TIMEOUT_MS),
      cache: "no-store",
    });
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: { "content-type": "application/json" },
    });
  } catch (error) {
    return NextResponse.json(
      {
        detail: `MindBridge API unavailable at ${API_BASE}: ${
          error instanceof Error ? error.message : "unknown error"
        }`,
      },
      { status: 503 },
    );
  }
}

export const GET = proxy;
export const POST = proxy;
