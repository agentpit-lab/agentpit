// Pure part of the inbox writer: no engine calls, so tests reach it directly. The reading format is
// the contract in docs/reference.md, section "The inbox (for writers)".

export type RateLimit = { kind: string; percentUsed: number; resetsAt?: string | null }
export type InboxBucket = { used_percentage: number; resets_at: string | null }
export type InboxReading = {
  observed_at: string
  session_id: string
  model: string | null
  five_hour?: InboxBucket
  seven_day?: InboxBucket
  // The one-way key of the account signed in at the write; absent when it was not known.
  account?: string
}
// key is what a write is compared by; limits is its limits-only part, which the /login hold compares.
export type Sent = { key: string; limits: string; at: number }

// Unchanged readings still go out once a minute, or pace would call steady usage stale.
export const INBOX_REFRESH_MS = 60_000

function pick(limits: readonly RateLimit[], kind: string): RateLimit | undefined {
  const l = limits.find(x => x.kind === kind)
  return l ? { kind: l.kind, percentUsed: l.percentUsed, resetsAt: l.resetsAt ?? null } : undefined
}

// The two windows paceline merges; null when both are absent.
export function limitsKey(limits: readonly RateLimit[]): string | null {
  const five = pick(limits, 'five_hour')
  const week = pick(limits, 'seven_day')
  if (!five && !week) return null
  return JSON.stringify([five ?? null, week ?? null])
}

// What a write is compared by: the windows and the account, so a switch of account writes at once.
export function inboxKey(limits: readonly RateLimit[], account: string | null): string | null {
  const key = limitsKey(limits)
  return key === null ? null : JSON.stringify([key, account])
}

export function isDue(key: string, sent: Sent | null, now: number): boolean {
  return !sent || sent.key !== key || now - sent.at >= INBOX_REFRESH_MS
}

export function inboxReading(limits: readonly RateLimit[], now: number, sessionId: string, model: string | null, account: string | null): InboxReading {
  const bucket = (l: RateLimit): InboxBucket => ({ used_percentage: l.percentUsed, resets_at: l.resetsAt ?? null })
  const five = pick(limits, 'five_hour')
  const week = pick(limits, 'seven_day')
  return {
    observed_at: new Date(now).toISOString(),
    session_id: sessionId,
    model,
    ...(five ? { five_hour: bucket(five) } : {}),
    ...(week ? { seven_day: bucket(week) } : {}),
    ...(account ? { account } : {}),
  }
}

// <state>: PACELINE_STATE_DIR, else <config>/state/paceline, where <config> is CLAUDE_CONFIG_DIR,
// else ~/.claude.
export function stateDirOf(env: { stateDir?: string | null; configDir?: string | null; home?: string | null }): string {
  if (env.stateDir) return env.stateDir
  const config = env.configDir || `${env.home ?? ''}/.claude`
  return `${config}/state/paceline`
}

export const inboxPath = (stateDir: string, sessionId: string) => `${stateDir}/inbox/${sessionId}.json`
