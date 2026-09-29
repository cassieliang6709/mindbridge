import { NextRequest, NextResponse } from "next/server";

const API_BASE = process.env.MINDBRIDGE_API_URL ?? "http://localhost:8000";
const TIMEOUT_MS = 120_000;

type RouteContext = {
  params: Promise<{ path?: string[] }>;
};

async function proxy(request: NextRequest, context: RouteContext) {
  const { path = [] } = await context.params;
  const suffix = path.length ? `/${path.map(encodeURIComponent).join("/")}` : "";
  const target = `${API_BASE}/retrieval-evals${suffix}`;

  try {
    const response = await fetch(target, {
      method: request.method,
      headers: { "content-type": "application/json" },
      body: request.method === "GET" ? undefined : await request.text(),
      signal: AbortSignal.timeout(TIMEOUT_MS),
      cache: "no-store",
    });
    const body = await response.text();
    return new NextResponse(body, {
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
