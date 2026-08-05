import { NextRequest, NextResponse } from "next/server";

/**
 * Assembles one diary payload from the FastAPI backend.
 *
 * The backend runs locally (docker compose up -d api); the deployed site has no
 * reachable API. So this route always answers 200 with an explicit `source`:
 *
 *   "live"    — real rows from Postgres
 *   "offline" — backend unreachable; the client falls back to sample data and
 *               says so in its banner
 *
 * The client must never present sample data as if it came from the database,
 * which is why the state is carried in the payload rather than inferred from a
 * failed request.
 */

const API_BASE = process.env.MINDBRIDGE_API_URL ?? "http://localhost:8000";
const TIMEOUT_MS = 2500;
const CARD_TIME_ZONE = process.env.MINDBRIDGE_TIMEZONE ?? "America/New_York";
const CARD_TIME_FORMATTER = new Intl.DateTimeFormat("en-US", {
  timeZone: CARD_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

export type DiaryTurn = {
  id: number;
  role: string;
  content: string;
  tool: string | null;
  token_count: number;
  created_at: string;
};

export type DiaryCard = {
  period: string;
  summary: string;
  developer_behavior_facts: string[];
  token_count: number;
  updated_at: string;
  /** Model prose, present only once M2 has run for that day. */
  narrative: string | null;
  open_threads: string[];
  /** "rule" for the computed card, otherwise "provider:model". */
  generated_by: string;
  model: string | null;
};

export type DiaryMemory = {
  id: number;
  content: string;
  category: string;
  created_at: string;
  valid_at: string | null;
  superseded_by: number | null;
  access_count: number;
  age_days: number;
  decay_multiplier: number;
};

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    signal: AbortSignal.timeout(TIMEOUT_MS),
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`${path} -> ${response.status}`);
  }
  return (await response.json()) as T;
}

/**
 * Cards are built by ingestion in CARD_TIME_ZONE, so the raw-turn window must
 * use the same timezone regardless of where the browser happens to be.
 */
function zonedMidnightUtc(date: string): number {
  const localMidnightAsUtc = Date.parse(`${date}T00:00:00Z`);
  let utcMs = localMidnightAsUtc;

  // Re-evaluate after applying the offset so DST transitions converge on the
  // offset that is actually in force at local midnight.
  for (let iteration = 0; iteration < 3; iteration += 1) {
    const parts = Object.fromEntries(
      CARD_TIME_FORMATTER.formatToParts(new Date(utcMs))
        .filter((part) => part.type !== "literal")
        .map((part) => [part.type, Number(part.value)]),
    );
    const wallClockAsUtc = Date.UTC(
      parts.year,
      parts.month - 1,
      parts.day,
      parts.hour,
      parts.minute,
      parts.second,
    );
    utcMs = localMidnightAsUtc - (wallClockAsUtc - utcMs);
  }
  return utcMs;
}

function dayRange(date: string): { start: string; end: string } {
  const nextDate = new Date(Date.parse(`${date}T00:00:00Z`) + 86_400_000)
    .toISOString()
    .slice(0, 10);
  return {
    start: new Date(zonedMidnightUtc(date)).toISOString(),
    end: new Date(zonedMidnightUtc(nextDate)).toISOString(),
  };
}

export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;
  const date = params.get("date");

  try {
    const [cards, memories] = await Promise.all([
      getJson<DiaryCard[]>("/summaries?limit=365"),
      getJson<DiaryMemory[]>("/memories?limit=40&include_superseded=true"),
    ]);

    const selected = date ?? cards[0]?.period ?? null;
    let turns: DiaryTurn[] = [];
    let turnsTotal = 0;
    if (selected) {
      const { start, end } = dayRange(selected);
      const window = await getJson<{
        turns: DiaryTurn[];
        total: number;
      }>(
        `/turns?start=${encodeURIComponent(start)}&end=${encodeURIComponent(
          end,
        )}&limit=12`,
      );
      turns = window.turns;
      turnsTotal = window.total;
    }

    return NextResponse.json({
      source: "live" as const,
      selected,
      cards,
      memories,
      turns,
      turnsTotal,
    });
  } catch (error) {
    console.warn(
      `[diary] backend unreachable at ${API_BASE}: ${
        error instanceof Error ? error.message : error
      }`,
    );
    return NextResponse.json({
      source: "offline" as const,
      apiBase: API_BASE,
      reason: error instanceof Error ? error.message : "unknown error",
    });
  }
}
