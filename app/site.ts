/**
 * Every deploy-specific value lives here so the page ships with placeholders
 * and needs no code edit to go live.
 *
 * FORM_ENDPOINT: set WAITLIST_WEBHOOK_URL in the environment to point
 * /api/waitlist at a real destination. With nothing configured the form falls
 * back to a pre-filled mailto: draft, so signups still reach a human.
 */
export const GITHUB_URL = "https://github.com/cassieliang6709/mindbridge";

export const CONTACT_EMAIL = "liangyue3666@gmail.com";

export const DEFAULT_LOCALE: Locale = "zh";

export type Locale = "zh" | "en";

/**
 * Counted on the author's own machine (2026-08-04) — the passive path has real
 * material to parse, not a hypothetical feed. Refresh with:
 *   find ~/.claude/projects -name '*.jsonl' | wc -l
 */
export const LOCAL_CORPUS = { projects: 23, transcripts: 155 };
