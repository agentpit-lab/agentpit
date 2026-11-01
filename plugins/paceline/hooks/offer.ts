// Pure part of the off-hours offer: no engine calls, so tests reach it directly. The offer itself, its
// one-per-part record and its text belong to `paceline plan offer` (scripts/pace.py plan_offer); this file
// only decides whether a prompt is worth the call, from the two small files that command reads and writes.

import { instant } from './watch'

// The one-per-part record of an account: pace.py offer_path.
export const offerPath = (stateDir: string, key: string) => `${stateDir}/plan-offer-${key}.json`

const rec = (value: unknown): Record<string, unknown> | null =>
  typeof value === 'object' && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : null

const asRecord = (text: string | null): Record<string, unknown> | null => {
  try {
    return text === null ? null : rec(JSON.parse(text))
  } catch {
    return null
  }
}

// True when the week plan file's part holds now, is an off part, and has no share or no weight (pace.py
// offer_unplanned), and the record does not already name that part (pace.py offer_part: day, kind and week).
// A true answer only means "ask the CLI": the CLI decides, and it is the one that records. A file that does
// not parse as the plan's version 1 gives false, so a missing or odd file never costs a process.
export function offerWorthAsking(planText: string | null, offerText: string | null, now: number): boolean {
  const plan = asRecord(planText)
  const part = plan ? rec(plan.part) : null
  if (!plan || !part || plan.version !== 1 || part.kind !== 'off') return false
  const starts = instant(part.start)
  const ends = instant(part.end)
  const weekEnds = instant(plan.week_resets_at)
  if (starts === null || ends === null || weekEnds === null || !(starts <= now && now < ends && weekEnds > now)) return false
  const unplanned = part.share_pct === null || part.share_pct === undefined || (typeof part.weight === 'number' && part.weight <= 0)
  if (!unplanned) return false
  const seen = asRecord(offerText)
  const same = seen !== null && seen.day === part.day && seen.kind === part.kind && seen.week_reset === plan.week_resets_at
  return !same
}

// The part the plan file names, as one key for "the offer was tried for this part in this process" (day, kind,
// week); null when the file does not name one.
export function offerTriedKey(key: string, planText: string | null): string | null {
  const plan = asRecord(planText)
  const part = plan ? rec(plan.part) : null
  if (!plan || !part || typeof part.day !== 'string' || typeof part.kind !== 'string') return null
  return `${key}|${part.day}|${part.kind}|${String(plan.week_resets_at)}`
}
