import type { StatusCache, StatusLimit, StatusMeasure, StatusMeta, StatusSample, StatusScoped } from '../types'

// calm: nothing to do; watch: worth a glance; act: changes what to do next.
export type Level = 'calm' | 'watch' | 'act'

export type ContextView = {
  pct: number
  used: number
  limit: number
  // The model's window, and the smaller one auto-compact measures against when one is set.
  window: number
  compactWindow: number | null
  compactSource: string | null
  isAuto: boolean
  turnsLeft: number | null
  level: Level
}

export type CacheView = {
  state: 'none' | 'hot' | 'warm' | 'cold'
  leftMs: number | null
  regime: '1h' | '5m' | null
  hitLast: number | null
  hitAvg: number | null
  isRebuilt: boolean
  // Tokens a cold turn would write again: the live context.
  rewrite: number | null
  level: Level
}

export type FiveView = {
  pct: number
  resetInMs: number | null
  ratePerHour: number | null
  evenPerHour: number | null
  fullAt: number | null
  isExact: boolean
  level: Level
}

export type WeekView = {
  pct: number
  resetAt: number | null
  isExact: boolean
  ratePerHour: number | null
  evenPerHour: number | null
  level: Level
}

export type View = {
  now: number
  context: ContextView | null
  cache: CacheView
  five: FiveView | null
  week: WeekView | null
  scoped: (StatusScoped & { level: Level })[]
  costUsd: number | null
  meta: StatusMeta | null
  elapsedMs: number | null
}

const HOUR = 3600_000

// A weekly window, all models or one model's, warns from 80% and acts from 95%.
const weekLevel = (pct: number): Level => (pct >= 95 ? 'act' : pct >= 80 ? 'watch' : 'calm')

export type ViewInput = {
  now: number
  measure: StatusMeasure | null
  cache: StatusCache
  meta: StatusMeta | null
  scoped: StatusScoped[]
  fileLimits: StatusLimit[]
  fiveSamples: StatusSample[]
  weekSamples: StatusSample[]
  ctxSamples: number[]
}

const TTL_MARGIN_MS = 5000

const windowKey = (resetsAt: string | null | undefined) => (resetsAt ? Math.round(Date.parse(resetsAt) / 60000) : null)

// The merge rule: a later window wins even when lower; within one window the higher reading
// wins, since a lower one is only ever an older observation.
// The model-scoped buckets exist only in the limits file, next to readings tagged `usage-endpoint`.
// They count as current only while the file's newest such reading is at most this old.
export const SCOPED_MAX_AGE_MS = 15 * 60_000

type FileBucket = { used_percentage?: unknown; resets_at?: unknown; observed_at?: unknown; source?: unknown }

const FUTURE_SLACK_MS = 5 * 60_000
const WINDOW_LENGTH_MS: Record<string, number> = { five_hour: 5 * HOUR, seven_day: 7 * 24 * HOUR }

// The file's five-hour and weekly buckets that can be real: a bucket observed more than 5 minutes
// ahead, or with a window ending later than its length allows, counts as absent.
export function fileLimits(file: unknown, now: number): StatusLimit[] {
  if (!file || typeof file !== 'object') return []
  const record = file as Record<string, unknown>
  const out: StatusLimit[] = []
  for (const kind of ['five_hour', 'seven_day']) {
    const b = record[kind] as FileBucket | undefined
    if (!b || typeof b.used_percentage !== 'number') continue
    const at = Date.parse(String(b.observed_at ?? record.observed_at))
    if (Number.isFinite(at) && at > now + FUTURE_SLACK_MS) continue
    const resetsAt = typeof b.resets_at === 'string' ? b.resets_at : undefined
    const reset = resetsAt ? Date.parse(resetsAt) : NaN
    if (Number.isFinite(reset) && reset > now + (WINDOW_LENGTH_MS[kind] ?? 0) + FUTURE_SLACK_MS) continue
    out.push({ kind, percentUsed: b.used_percentage, resetsAt })
  }
  return out
}

export function freshScoped(file: unknown, now: number): StatusScoped[] {
  if (!file || typeof file !== 'object') return []
  const record = file as Record<string, unknown>
  if (!Array.isArray(record.scoped)) return []
  const times = ['five_hour', 'seven_day']
    .map(name => record[name] as FileBucket | undefined)
    .filter(b => b && typeof b.used_percentage === 'number' && (b.source ?? record.source) === 'usage-endpoint')
    .map(b => Date.parse(String(b?.observed_at ?? record.observed_at)))
    .filter(t => Number.isFinite(t) && t <= now + 5 * 60_000)
  const newest = times.length ? Math.max(...times) : null
  if (newest === null || now - newest > SCOPED_MAX_AGE_MS) return []
  // The scoped buckets belong to the weekly window they were read in.
  const weekReset = Date.parse(String((record.seven_day as { resets_at?: unknown } | undefined)?.resets_at))
  if (Number.isFinite(weekReset) && weekReset <= now) return []
  return record.scoped
    .filter((b): b is { name: string; used_percentage: number; resets_at?: unknown } => typeof b?.name === 'string' && typeof b?.used_percentage === 'number')
    .map(b => ({ name: b.name, percentUsed: b.used_percentage, ...(typeof b.resets_at === 'string' ? { resetsAt: b.resets_at } : {}) }))
}

// <state>: PACELINE_STATE_DIR, else <config>/state/paceline, where <config> is CLAUDE_CONFIG_DIR,
// else ~/.claude. paceline's own rule, so both plugins agree on its files.
export function stateDirOf(env: { stateDir?: string | null; configDir?: string | null; home?: string | null }): string {
  if (env.stateDir) return env.stateDir
  const config = env.configDir || `${env.home ?? ''}/.claude`
  return `${config}/state/paceline`
}

// Claude Code's transcript folder for a start directory: every character but a letter or digit
// turns into '-'. Past 200 characters it also cuts the name and adds a hash; the caller searches.
export const encodeProject = (root: string) => root.replace(/[^a-zA-Z0-9]/g, '-')

// The effort a session starts with, before any request reports it: the model's entry in
// `modelSettings` (exact id first, then the longest id it extends), else the top-level level.
export function settingsEffort(settings: Record<string, unknown>, model: string): string | null {
  const id = model.toLowerCase().replace(/[.\s]/g, '-')
  const perModel = settings.modelSettings
  if (perModel && typeof perModel === 'object') {
    const keys = Object.keys(perModel)
      // `k.endsWith` covers a display name ("Opus 5.5" -> "opus-5-5") against a full id.
      .filter(k => id === k || id.startsWith(`${k}-`) || k.endsWith(`-${id}`))
      .sort((a, b) => b.length - a.length)
    for (const k of keys) {
      const level = (perModel as Record<string, { effortLevel?: unknown } | undefined>)[k]?.effortLevel
      if (typeof level === 'string' && level) return level
    }
  }
  return typeof settings.effortLevel === 'string' && settings.effortLevel ? settings.effortLevel : null
}

const EFFORT_LEVELS = ['low', 'medium', 'high', 'xhigh', 'max']

// The level /effort leaves: the one it was given, or for the picker and `auto` the one it saved to
// settings.json. undefined when the settings cannot be read.
export function effortAfterCommand(args: string | undefined, settingsText: string, model: string): string | null | undefined {
  const asked = (args ?? '').trim().toLowerCase()
  if (EFFORT_LEVELS.includes(asked)) return asked
  try {
    return settingsEffort(JSON.parse(settingsText) as Record<string, unknown>, model)
  } catch {
    return undefined
  }
}

export function mergeLimit(payload: StatusLimit | undefined, file: StatusLimit | undefined): StatusLimit | undefined {
  if (!payload || !file) return payload ?? file
  const p = windowKey(payload.resetsAt)
  const f = windowKey(file.resetsAt)
  // On a tie the file wins: its writer may have read the same number from a more exact source.
  if (p === null || f === null || p === f) return file.percentUsed >= payload.percentUsed ? file : payload
  return f > p ? file : payload
}

function contextView(m: StatusMeasure | null, samples: number[]): ContextView | null {
  // No response in this window yet: the fill is unknown, not empty.
  if (!m || m.tokens === null) return null
  const limit = m.isAutoCompact && m.threshold ? m.threshold : m.window
  const used = m.tokens
  const pct = Math.min(100, (used / limit) * 100)
  // Growth per turn from the last turns; a drop is a compaction and starts the series over.
  const growth: number[] = []
  for (let i = samples.length - 1; i > 0 && growth.length < 5; i--) {
    const d = (samples[i] ?? 0) - (samples[i - 1] ?? 0)
    if (d < 0) break
    if (d > 0) growth.push(d)
  }
  const perTurn = growth.length >= 2 ? growth.reduce((a, b) => a + b, 0) / growth.length : null
  const turnsLeft = perTurn && m.isAutoCompact ? Math.max(0, Math.floor((limit - used) / perTurn)) : null
  const level: Level = pct >= 80 || (turnsLeft !== null && turnsLeft <= 3) ? 'act' : pct >= 60 ? 'watch' : 'calm'
  const compactWindow = m.compactWindow ?? null
  return { pct, used, limit, window: m.window, compactWindow, compactSource: m.compactSource ?? null, isAuto: m.isAutoCompact, turnsLeft, level }
}

function cacheView(c: StatusCache, now: number, tokens: number | null): CacheView {
  const hitAvg = c.reads + c.writes > 0 ? (c.reads / (c.reads + c.writes)) * 100 : null
  const base = { regime: c.regime, hitLast: c.hitLast, hitAvg, isRebuilt: c.isRebuilt, rewrite: tokens }
  if (c.isInFlight) return { ...base, state: 'hot', leftMs: null, level: 'calm' }
  if (c.anchorMs === null) return { ...base, state: 'none', leftMs: null, level: 'calm' }
  // With no write seen yet the documented 5-minute default is assumed.
  const span = c.regime === '1h' ? HOUR : 300_000
  const left = c.anchorMs + span - TTL_MARGIN_MS - now
  if (left <= 0) return { ...base, state: 'cold', leftMs: 0, level: 'watch' }
  const level: Level =
    left < Math.min(5 * 60_000, span / 5) ? 'act' : left < Math.min(15 * 60_000, span / 2) ? 'watch' : 'calm'
  return { ...base, state: 'warm', leftMs: left, level }
}

// Samples are kept only where the percentage changed, so a reading holds from its time until
// the next one. The burn runs from the value held at the look-back's start (or the window's first
// sample, if later) to the current value now; a flat stretch counts as time at zero burn.
function burnRate(pct: number, resetsAt: string | undefined, samples: StatusSample[], now: number, lookbackMs: number, minSpanMs: number) {
  const key = windowKey(resetsAt)
  const inWindow = samples.filter(s => windowKey(s.resetsAt) === key)
  const from = now - lookbackMs
  const held = inWindow.filter(s => s.t <= from).pop()
  const base = held ? { t: from, pct: held.pct } : inWindow[0]
  if (!base || now - base.t < minSpanMs) return null
  return Math.max(0, ((pct - base.pct) / (now - base.t)) * HOUR)
}

function fiveView(limit: StatusLimit | undefined, isExact: boolean, samples: StatusSample[], now: number): FiveView | null {
  if (!limit) return null
  const resetAt = limit.resetsAt ? Date.parse(limit.resetsAt) : null
  const resetInMs = resetAt === null ? null : Math.max(0, resetAt - now)
  const ratePerHour = burnRate(limit.percentUsed, limit.resetsAt, samples, now, 90 * 60_000, 10 * 60_000)
  const evenPerHour = resetInMs && resetInMs > 0 ? (100 - limit.percentUsed) / (resetInMs / HOUR) : null
  const fullAt = ratePerHour && ratePerHour > 0 ? now + ((100 - limit.percentUsed) / ratePerHour) * HOUR : null
  const isBeforeReset = fullAt !== null && resetAt !== null && fullAt < resetAt
  const level: Level =
    limit.percentUsed >= 90 || isBeforeReset
      ? 'act'
      : limit.percentUsed >= 70 || (ratePerHour !== null && evenPerHour !== null && ratePerHour > evenPerHour)
        ? 'watch'
        : 'calm'
  return { pct: limit.percentUsed, resetInMs, ratePerHour, evenPerHour, fullAt, isExact, level }
}

// The weekly percentage moves in whole points, so its burn needs a longer look-back than the
// five-hour one: the last 6 hours of this window, over at least one hour.
function weekPace(pct: number, resetsAt: string | undefined, samples: StatusSample[], now: number) {
  const resetAt = resetsAt ? Date.parse(resetsAt) : null
  const ratePerHour = burnRate(pct, resetsAt, samples, now, 6 * HOUR, HOUR)
  const evenPerHour = resetAt !== null && resetAt > now ? (100 - pct) / ((resetAt - now) / HOUR) : null
  return { ratePerHour, evenPerHour }
}

const WINDOW_MS: Record<string, number> = { seven_day: 7 * 24 * HOUR }

// A window whose reset has passed holds no usage: the next one starts at 0%. A weekly window
// follows its predecessor, so its reset rolls forward; a five-hour one opens at the first request
// after the reset, so its reset is unknown until a reading from it arrives.
export function rollLimit(limit: StatusLimit | undefined, now: number): StatusLimit | undefined {
  if (!limit?.resetsAt) return limit
  let reset = Date.parse(limit.resetsAt)
  if (!Number.isFinite(reset) || reset > now) return limit
  const period = WINDOW_MS[limit.kind]
  if (!period) return { kind: limit.kind, percentUsed: 0, resetsAt: undefined }
  while (reset <= now) reset += period
  return { kind: limit.kind, percentUsed: 0, resetsAt: new Date(reset).toISOString() }
}

export function buildView(i: ViewInput): View {
  const pick = (kind: string) => {
    const payload = rollLimit(i.measure?.limits.find(l => l.kind === kind), i.now)
    const read = i.fileLimits.find(l => l.kind === kind)
    const file = rollLimit(read, i.now)
    const merged = mergeLimit(payload, file)
    return { merged, isExact: merged !== undefined && merged === file && file === read }
  }
  const five = pick('five_hour')
  const week = pick('seven_day')
  const weekPct = week.merged?.percentUsed
  return {
    now: i.now,
    context: contextView(i.measure, i.ctxSamples),
    cache: cacheView(i.cache, i.now, i.measure?.tokens ?? null),
    five: fiveView(five.merged, five.isExact, i.fiveSamples, i.now),
    week:
      weekPct === undefined
        ? null
        : {
            pct: weekPct,
            resetAt: week.merged?.resetsAt ? Date.parse(week.merged.resetsAt) : null,
            isExact: week.isExact,
            ...weekPace(weekPct, week.merged?.resetsAt, i.weekSamples, i.now),
            level: weekLevel(weekPct),
          },
    scoped: i.scoped.map(b => ({ ...b, level: weekLevel(b.percentUsed) })),
    costUsd: i.measure?.costUsd ?? null,
    meta: i.meta,
    elapsedMs: i.meta ? i.now - i.meta.startedAt : null,
  }
}
