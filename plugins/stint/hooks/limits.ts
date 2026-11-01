// Pure part of the limits writer: no engine calls, so tests reach it directly. Stint reads the file this
// writes (scripts/stint_queue.py, read_limits) when PaceLine is not there to answer.

export type RateLimit = { kind: string; percentUsed: number; resetsAt?: string | null }
export type LimitsBucket = { used_percentage: number; resets_at: string | null }
export type LimitsReading = {
  observed_at: string
  session_id: string
  five_hour?: LimitsBucket
  seven_day?: LimitsBucket
}
export type Sent = { key: string; at: number }

// Unchanged figures still go out once a minute, or Stint would read steady usage as stale.
export const LIMITS_REFRESH_MS = 60_000

function pick(limits: readonly RateLimit[], kind: string): RateLimit | undefined {
  const l = limits.find(x => x.kind === kind)
  return l ? { kind: l.kind, percentUsed: l.percentUsed, resetsAt: l.resetsAt ?? null } : undefined
}

// The two windows Stint reads; null when both are absent.
export function limitsKey(limits: readonly RateLimit[]): string | null {
  const five = pick(limits, 'five_hour')
  const week = pick(limits, 'seven_day')
  if (!five && !week) return null
  return JSON.stringify([five ?? null, week ?? null])
}

export function isDue(key: string, sent: Sent | null, now: number): boolean {
  return !sent || sent.key !== key || now - sent.at >= LIMITS_REFRESH_MS
}

export function limitsReading(limits: readonly RateLimit[], now: number, sessionId: string): LimitsReading {
  const bucket = (l: RateLimit): LimitsBucket => ({ used_percentage: l.percentUsed, resets_at: l.resetsAt ?? null })
  const five = pick(limits, 'five_hour')
  const week = pick(limits, 'seven_day')
  return {
    observed_at: new Date(now).toISOString(),
    session_id: sessionId,
    ...(five ? { five_hour: bucket(five) } : {}),
    ...(week ? { seven_day: bucket(week) } : {}),
  }
}

// <state>: STINT_STATE_DIR, else <config>/state/stint, where <config> is CLAUDE_CONFIG_DIR, else ~/.claude.
export function stateDirOf(env: { stateDir?: string | null; configDir?: string | null; home?: string | null }): string {
  if (env.stateDir) return env.stateDir
  const config = env.configDir || `${env.home ?? ''}/.claude`
  return `${config}/state/stint`
}

export const limitsPath = (stateDir: string) => `${stateDir}/limits.json`
