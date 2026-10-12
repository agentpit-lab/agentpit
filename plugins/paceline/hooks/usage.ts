// Pure part of usage-watch: no engine calls, so tests reach it directly. The reading follows the
// exact-reading contract in docs/reference.md, section "The inbox (for writers)".

export const USAGE_URL = 'https://api.anthropic.com/api/oauth/usage'
export const USAGE_TICK_MS = 10_000
export const USAGE_INTERVAL_MS = 125_000
export const USAGE_BACKOFF_MS = 10 * 60_000
const MAX_BODY = 1024 * 1024
const MAX_NAME = 64

export type Gate = { last_attempt: number | null; last_success: number | null; last_error: string | null }
type Bucket = { used_percentage: number; resets_at: string | null }
type Scoped = { name: string; used_percentage: number; resets_at?: string }
export type ExactReading = {
  observed_at: string
  source: 'usage-endpoint'
  session_id: null
  model: null
  five_hour?: Bucket
  seven_day?: Bucket
  scoped?: Scoped[]
}
export type Outcome = { reading: ExactReading; at: number } | { error: string }

export const usageSwitchPath = (stateDir: string) => `${stateDir}/usage-watch.json`
export const usageGatePath = (stateDir: string) => `${stateDir}/usage-watch.state.json`
export const usageInboxPath = (stateDir: string) => `${stateDir}/inbox/usage-watch.json`

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const isNumber = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

function parse(text: string | null): unknown {
  try {
    return text === null ? null : JSON.parse(text)
  } catch {
    return null
  }
}

export function switchOn(text: string | null): boolean {
  const s = parse(text)
  return isObject(s) && s.on === true
}

export function readGate(text: string | null): Gate {
  const s = parse(text)
  const num = (v: unknown) => (isNumber(v) ? v : null)
  if (!isObject(s)) return { last_attempt: null, last_success: null, last_error: null }
  return { last_attempt: num(s.last_attempt), last_success: num(s.last_success), last_error: typeof s.last_error === 'string' ? s.last_error : null }
}

// The inbox file's own time stands in for a lost gate file, so deleting it cannot cause a burst.
export function inboxObservedAt(text: string | null): number | null {
  const s = parse(text)
  const at = isObject(s) && typeof s.observed_at === 'string' ? Date.parse(s.observed_at) : NaN
  return Number.isNaN(at) ? null : at
}

// A time in the future is ignored: a clock set back must not close the gate for good. An attempt
// with no recorded result waits one interval, as a success does; only a non-429 error waits longer.
export function gateOpen(gate: Gate, now: number, inboxAt: number | null): boolean {
  const past = (t: number | null) => (t !== null && t <= now ? t : null)
  const success = Math.max(past(gate.last_success) ?? -Infinity, past(inboxAt) ?? -Infinity)
  if (now < success + USAGE_INTERVAL_MS) return false
  const attempt = past(gate.last_attempt)
  if (attempt === null) return true
  const backoff = gate.last_error !== null && !gate.last_error.startsWith('HTTP 429')
  return now >= attempt + (backoff ? USAGE_BACKOFF_MS : USAGE_INTERVAL_MS)
}

// The endpoint sends microseconds and an offset; the inbox keeps the millisecond Z form.
function iso(v: unknown): string | null {
  if (typeof v !== 'string') return null
  const at = Date.parse(v.replace(/(\.\d{3})\d+/, '$1'))
  return Number.isNaN(at) ? null : new Date(at).toISOString()
}

function bucket(v: unknown): Bucket | undefined {
  if (!isObject(v) || !isNumber(v.utilization)) return undefined
  return { used_percentage: v.utilization, resets_at: iso(v.resets_at) }
}

// pitboard draws the name, so it is cut short and stripped of control characters.
function scopedOf(limits: unknown): Scoped[] {
  if (!Array.isArray(limits)) return []
  const out: Scoped[] = []
  for (const l of limits) {
    if (!isObject(l) || l.kind !== 'weekly_scoped') continue
    const percent = isNumber(l.percent) ? l.percent : isNumber(l.utilization) ? l.utilization : null
    const model = isObject(l.scope) && isObject(l.scope.model) ? l.scope.model.display_name : null
    const name = typeof model === 'string' ? model.replace(/[\u0000-\u001f\u007f-\u009f]/g, '').slice(0, MAX_NAME) : ''
    if (percent === null || !name) continue
    const resets = iso(l.resets_at)
    out.push({ name, used_percentage: percent, ...(resets ? { resets_at: resets } : {}) })
  }
  return out
}

export function readingFrom(status: number, text: string, at: number): Outcome {
  if (status !== 200) return { error: `HTTP ${status}` }
  const body = text.length > MAX_BODY ? null : parse(text)
  if (!isObject(body)) return { error: 'unexpected response' }
  const five = bucket(body.five_hour)
  const week = bucket(body.seven_day)
  if (!five && !week) return { error: 'unexpected response' }
  const scoped = week ? scopedOf(body.limits) : []
  return {
    at,
    reading: {
      observed_at: new Date(at).toISOString(),
      source: 'usage-endpoint',
      session_id: null,
      model: null,
      ...(five ? { five_hour: five } : {}),
      ...(week ? { seven_day: week } : {}),
      ...(scoped.length ? { scoped } : {}),
    },
  }
}
