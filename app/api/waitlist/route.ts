import { NextRequest, NextResponse } from "next/server";

/**
 * Developer-preview signups.
 *
 * There is no database here on purpose. Set WAITLIST_WEBHOOK_URL to anywhere
 * that accepts a JSON POST (a Formspree/Resend endpoint, a Slack incoming
 * webhook, a Google Apps Script) and the address gets forwarded there. With
 * nothing configured this route returns 503 so the form shows its
 * "email me directly" fallback instead of silently dropping the address.
 */

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

export async function POST(request: NextRequest) {
  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }

  const { email, role, locale } = (payload ?? {}) as Record<string, unknown>;
  if (typeof email !== "string" || !EMAIL.test(email.trim())) {
    return NextResponse.json({ error: "invalid email" }, { status: 400 });
  }

  const webhook = process.env.WAITLIST_WEBHOOK_URL;
  if (!webhook) {
    console.warn(
      "[waitlist] WAITLIST_WEBHOOK_URL is unset; signup was not stored",
    );
    return NextResponse.json({ error: "no destination configured" }, { status: 503 });
  }

  const forwarded = await fetch(webhook, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      source: "mindbridge-landing",
      email: email.trim(),
      role: typeof role === "string" ? role : "",
      locale: locale === "en" ? "en" : "zh",
      at: new Date().toISOString(),
    }),
  });

  if (!forwarded.ok) {
    console.error("[waitlist] webhook rejected the signup", forwarded.status);
    return NextResponse.json({ error: "upstream rejected" }, { status: 502 });
  }

  return NextResponse.json({ ok: true });
}
