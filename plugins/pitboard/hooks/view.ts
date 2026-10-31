import type { StatusBurn, StatusCache, StatusLimit, StatusMeasure, StatusMeta, StatusSample, StatusScoped, StatusWeekPlan } from '../types'

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
  // Idle compaction as far as PitBoard can see: on (every visible condition holds), below (on, but the
  // context is under the threshold), off (the setting, the variable or auto-compact is off), n/a (the
  // cache is not known to live an hour). The engine's feature flag is out of sight.
  idle: 'on' | 'below' | 'off' | 'n/a'
  idleMin: number | null
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
  // How many times dearer a cold turn is than a warm one, and whether that is only a lower bound.
  ratio: number
  isRatioFloor: boolean
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
  // The percentage the projection aims at: 100, or the threshold of paceline's snapshot.
  targetPct: number
  // True when the rate, budget and time come from paceline's five-hour burn snapshot.
  isPaceline: boolean
}

export type WeekView = {
  pct: number
  resetAt: number | null
  isExact: boolean
  ratePerHour: number | null
  evenPerHour: number | null
  // The percentage rule (weekLevel): the Used row and today's weekly advice.
  level: Level
  // The level of the card and the band's week gauge: the highest of level and what the plan adds.
  cardLevel: Level
  // PaceLine's week plan measured at this moment, null when its file is missing or not usable.
  plan: PlanView | null
}

// The reserve of the plan file as the Week card reads it. none: no reserve (0%); unknown: the release
// cannot be told, so the stop is off; held: before the release, isStop when the week reached 100 - pct;
// released: from the release instant on. isToReset: no release candidate this week, kept to the reset.
export type ReserveView =
  | { kind: 'none' }
  | { kind: 'unknown' }
  | { kind: 'held'; pct: number; name: string | null; releaseAt: number; isToReset: boolean; isStop: boolean }
  | { kind: 'released'; pct: number; name: string | null; releaseAt: number }

// The part of PaceLine's week plan that holds now, measured against the week's usage. Percentages are
// points of the week, times are epoch milliseconds, spans are hours.
export type PlanView = {
  // The effective mode: inform_until is applied.
  mode: 'inform' | 'limit'
  start: number
  end: number
  // The part's fixed share, null for an unplanned part or week.
  share: number | null
  usedAtStart: number
  // Weekly usage since the part began, at least 0.
  spent: number
  // What the share allows by now, pro rata by time; null without a share.
  allowed: number | null
  // spent > allowed + PLAN_TOLERANCE_PCT, compared unrounded; false without a share.
  isAbove: boolean
  // spent - allowed (positive: overspent); null without a share.
  lead: number | null
  // |lead| in hours of the part's own rate; null without a share or with a share of 0.
  leadHours: number | null
  // A part with no share while later parts carry weight.
  isUnplannedPart: boolean
  // Nothing to spread: no share and no later weight. unplannedPct is what is left, or null otherwise.
  isUnplannedWeek: boolean
  unplannedPct: number | null
  // The part's rate in %/h (PaceLine's budget_per_hour_pct); null without a share.
  budget: number | null
  // The weekly rate within the part in %/h, null while the part is under an hour old or without samples.
  pace: number | null
  // Where the plan expects the week's usage now, whole percent in 0-100; null without a share.
  tick: number | null
  reserve: ReserveView
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
  // A five-hour or weekly figure came from limits readings whose account cannot be told to be this one's.
  isAccountUnknown: boolean
  // The same for the model-scoped weekly figures, which come from the limits file alone.
  isScopedAccountUnknown: boolean
}

const HOUR = 3600_000

// A weekly window, all models or one model's, warns from 80% and acts from 95%.
const weekLevel = (pct: number): Level => (pct >= 95 ? 'act' : pct >= 80 ? 'watch' : 'calm')

// paceline's week.TOLERANCE_PCT: a part is above its share only past this many points. The generated
// fixtures (tests/fixtures/week-plan.ts) pin it.
export const PLAN_TOLERANCE_PCT = 1
// paceline's SAME_WEEK_TOLERANCE_SEC: the file's week is this week when the resets differ by less.
const PLAN_WEEK_TOLERANCE_MS = 600_000

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
  // The limits in fileLimits come from a partition marked unknown (pickPartition).
  isFileAccountUnknown?: boolean
  // paceline's five-hour burn snapshot and the key of the signed-in account it must belong to.
  burn?: StatusBurn | null
  accountKey?: string | null
  // paceline's week plan file, read for accountKey; used only when planOf finds it usable.
  weekPlan?: StatusWeekPlan | null
}

const TTL_MARGIN_MS = 5000

// The snapshot is rewritten at every merge, at least once a minute while a session is active.
export const BURN_MAX_AGE_MS = 3 * 60_000

const isNumber = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const isTextOrNull = (value: unknown): value is string | null => value === null || typeof value === 'string'
const isNumberOrNull = (value: unknown): value is number | null => value === null || isNumber(value)

// paceline's five-hour-burn-<key>.json, or null when the text is not a snapshot with every field in its place.
export function parseBurn(text: string): StatusBurn | null {
  let raw: unknown
  try {
    raw = JSON.parse(text)
  } catch {
    return null
  }
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const r = raw as Record<string, unknown>
  const ok =
    typeof r.computed_at === 'string' &&
    typeof r.account === 'string' &&
    isNumber(r.used_pct) &&
    typeof r.resets_at === 'string' &&
    isNumber(r.threshold_pct) &&
    isNumber(r.budget_per_hour_pct) &&
    isNumberOrNull(r.rate_per_hour_pct) &&
    isTextOrNull(r.threshold_at) &&
    typeof r.limited === 'boolean'
  return ok ? (raw as StatusBurn) : null
}

// An instant as paceline writes it: ISO 8601 with a zone designator (Z or +HH:MM), never a local time.
const INSTANT = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(\.\d+)?(Z|[+-](\d{2}):(\d{2}))$/
// Date.parse rolls 2026-02-30 over to March and reads T24:00:00, which paceline refuses: the date's own
// parts must survive a round trip, and the hour, minute, second and offset must be in range.
const isInstant = (value: unknown): value is string => {
  const m = typeof value === 'string' ? INSTANT.exec(value) : null
  if (!m) return false
  const [year = 0, month = 0, day = 0, hour = 0, minute = 0, second = 0] = m.slice(1, 7).map(Number)
  if (hour > 23 || minute > 59 || second > 59 || Number(m[9] ?? 0) > 23 || Number(m[10] ?? 0) > 59) return false
  const date = new Date(Date.UTC(2000, month - 1, day))
  date.setUTCFullYear(year)
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return false
  return Number.isFinite(Date.parse(value as string))
}
const isInstantOrNull = (value: unknown): value is string | null => value === null || isInstant(value)

// paceline's week-plan-<key>.json, or null when the text is not a version-1 plan with every field in its place.
export function parseWeekPlan(text: string): StatusWeekPlan | null {
  let raw: unknown
  try {
    raw = JSON.parse(text)
  } catch {
    return null
  }
  if (!isObject(raw) || raw.version !== 1) return null
  const { reserve, part, later } = raw
  if (
    !isInstant(raw.computed_at) ||
    typeof raw.account !== 'string' ||
    !isInstant(raw.week_resets_at) ||
    (raw.mode !== 'inform' && raw.mode !== 'limit') ||
    !isInstantOrNull(raw.inform_until) ||
    !isNumber(raw.five_hour_to_week_pct) ||
    !isObject(reserve) ||
    !isNumber(reserve.pct) ||
    !isInstantOrNull(reserve.release_at) ||
    !(reserve.name === undefined || isTextOrNull(reserve.name)) ||
    !(reserve.error === undefined || isTextOrNull(reserve.error)) ||
    !isObject(part) ||
    !isInstant(part.start) ||
    !isInstant(part.end) ||
    !(Date.parse(part.start) < Date.parse(part.end)) ||
    !isNumberOrNull(part.share_pct) ||
    !isNumber(part.used_at_start) ||
    typeof part.unplanned !== 'boolean' ||
    typeof part.estimated !== 'boolean' ||
    !Array.isArray(later) ||
    !later.every(l => isObject(l) && isNumber(l.weight))
  ) {
    return null
  }
  return raw as unknown as StatusWeekPlan
}

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

const ACCOUNT_KEY = /^[0-9a-f]{16}$/
const UNKNOWN_ACCOUNT = 'unknown'

const isObject = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value)

// The part of the limits file that belongs to the account with this key (null when it could not be
// resolved), the same rule as paceline's pick_partition. isUnknown: the figures cannot be told to be
// this account's, because the key is unresolved or they come from untagged readings. Another
// account's partition is never returned; a known account with no partition and no untagged one gets
// partition null.
export function pickPartition(file: unknown, key: string | null): { partition: unknown; isUnknown: boolean } {
  if (!isObject(file)) return { partition: null, isUnknown: key === null }
  if (key === null) return { partition: file, isUnknown: true }
  const accounts = file.accounts
  if (!isObject(accounts)) return { partition: file, isUnknown: true }
  const held = (name: string) => (name === UNKNOWN_ACCOUNT || ACCOUNT_KEY.test(name)) && isObject(accounts[name]) ? accounts[name] : undefined
  const own = held(key)
  if (own !== undefined) return { partition: own, isUnknown: false }
  const untagged = held(UNKNOWN_ACCOUNT)
  if (untagged !== undefined) return { partition: untagged, isUnknown: true }
  return { partition: null, isUnknown: false }
}

// The history lines that belong to the account with this key, the same rule as paceline's
// history_account_filter. Lines without a valid account, and lines an older merge wrote with an
// accounts map, are untagged: they count only when every tagged line is this account's, which is the
// case of a person who never switched. An unresolved key keeps every line.
export function historyForAccount<T extends { account?: unknown; accounts?: unknown }>(records: T[], key: string | null): T[] {
  if (key === null) return records
  const tagged = records.map(r =>
    'accounts' in r ? UNKNOWN_ACCOUNT : typeof r.account === 'string' && ACCOUNT_KEY.test(r.account) ? r.account : UNKNOWN_ACCOUNT,
  )
  const isMixed = tagged.some(a => a !== key && a !== UNKNOWN_ACCOUNT)
  return records.filter((_, i) => tagged[i] === key || (tagged[i] === UNKNOWN_ACCOUNT && !isMixed))
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

const IDLE_DEFAULT = { isOn: true, minTokens: 200_000 }

function contextView(m: StatusMeasure | null, samples: number[], regime: '1h' | '5m' | null): ContextView | null {
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
  const idleSetting = m.idleCompact ?? IDLE_DEFAULT
  // Only a one-hour cache is compacted while idle; a TTL not seen yet is not known to be one hour.
  const idle: ContextView['idle'] = !idleSetting.isOn || !m.isAutoCompact ? 'off' : regime !== '1h' ? 'n/a' : used >= idleSetting.minTokens ? 'on' : 'below'
  const idleMin = idle === 'on' || idle === 'below' ? idleSetting.minTokens : null
  return { pct, used, limit, window: m.window, compactWindow, compactSource: m.compactSource ?? null, isAuto: m.isAutoCompact, turnsLeft, level, idle, idleMin }
}

// A cold turn writes the context again at 2x (one-hour TTL) or 1.25x (five-minute TTL) the base input
// price, where a warm turn reads it at the cache-read price: 0.05x on Opus 5.5 and Sonnet 5.5, 0.025x
// on Fable 5.1 and Mythos 5.1, 0.1x elsewhere. Prices from the Pricing section of
// https://platform.claude.com/docs/en/about-claude/pricing#prompt-caching. A model not
// listed here may read cheaper than 0.1x, so its ratio is a lower bound.
export function coldRatio(model: string | null, regime: '1h' | '5m' | null): { ratio: number; isFloor: boolean } {
  const id = (model ?? '').toLowerCase().replace(/[.\s]/g, '-')
  const family = (name: string) => new RegExp(`(^|-)${name}(?![0-9])`).test(id)
  const read = family('opus-5-5') || family('sonnet-5-5') ? 0.05 : family('fable-5-1') || family('mythos-5-1') ? 0.025 : null
  const write = regime === '1h' ? 2 : 1.25
  return { ratio: Math.round((write / (read ?? 0.1)) * 10) / 10, isFloor: read === null }
}

function cacheView(c: StatusCache, now: number, tokens: number | null, model: string | null): CacheView {
  const hitAvg = c.reads + c.writes > 0 ? (c.reads / (c.reads + c.writes)) * 100 : null
  const { ratio, isFloor } = coldRatio(model, c.regime)
  const base = { regime: c.regime, hitLast: c.hitLast, hitAvg, isRebuilt: c.isRebuilt, rewrite: tokens, ratio, isRatioFloor: isFloor }
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

function fiveView(limit: StatusLimit | undefined, isExact: boolean, samples: StatusSample[], now: number, burn: StatusBurn | null, accountKey: string | null): FiveView | null {
  if (!limit) return null
  const resetAt = limit.resetsAt ? Date.parse(limit.resetsAt) : null
  const resetInMs = resetAt === null ? null : Math.max(0, resetAt - now)
  const key = windowKey(limit.resetsAt)
  const age = burn ? now - Date.parse(burn.computed_at) : NaN
  // The snapshot stands in only for the figure it was computed from: this account, this window, this
  // percentage, and not older than the merge cadence allows. At or past its threshold it projects
  // nothing, so the 100% projection from the samples takes over.
  if (
    burn &&
    limit.percentUsed < burn.threshold_pct &&
    accountKey !== null &&
    burn.account === accountKey &&
    key !== null &&
    windowKey(burn.resets_at) === key &&
    burn.used_pct === limit.percentUsed &&
    age >= 0 &&
    age <= BURN_MAX_AGE_MS
  ) {
    const thresholdAt = burn.threshold_at ? Date.parse(burn.threshold_at) : NaN
    const level: Level = limit.percentUsed >= 90 || burn.limited ? 'act' : limit.percentUsed >= 70 ? 'watch' : 'calm'
    return {
      pct: limit.percentUsed,
      resetInMs,
      ratePerHour: burn.rate_per_hour_pct,
      evenPerHour: burn.budget_per_hour_pct,
      fullAt: Number.isFinite(thresholdAt) ? thresholdAt : null,
      isExact,
      level,
      targetPct: burn.threshold_pct,
      isPaceline: true,
    }
  }
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
  return { pct: limit.percentUsed, resetInMs, ratePerHour, evenPerHour, fullAt, isExact, level, targetPct: 100, isPaceline: false }
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

export type PlanInput = {
  now: number
  // The key of the signed-in account, null when it is not known.
  accountKey: string | null
  // The weekly figure comes from limits readings whose account cannot be told to be this one's.
  isWeekAccountUnknown: boolean
  // The week card's figure and its reset, as buildView resolved them.
  pct: number
  resetsAt: string | undefined
  // The week's own samples, empty where the figure is of an unknown account.
  samples: StatusSample[]
}

// The week plan measured now, by paceline's rule (spent, allowed and the 1-point tolerance of its plan
// figures) on the file's part. null unless the file is usable, by PaceLine's own rule for a file that holds now
// (limits_merge.plan_holds, which the shared cases pin): it is this account's and this week's, was
// not computed in the future, and its part holds now. A part that has ended means the writer has not run
// since the boundary; it is never extended or re-spread here.
export function planOf(file: StatusWeekPlan | null | undefined, i: PlanInput): PlanView | null {
  if (!file) return null
  const { now } = i
  const resetAt = i.resetsAt ? Date.parse(i.resetsAt) : NaN
  const start = Date.parse(file.part.start)
  const end = Date.parse(file.part.end)
  const weekEnd = Date.parse(file.week_resets_at)
  // PaceLine's rule (limits_merge.plan_holds): the file's week is the reading's unless the reading's reset is known,
  // after now and 600 s or more from the file's.
  const isOffSchedule = Number.isFinite(resetAt) && resetAt > now && !(Math.abs(weekEnd - resetAt) < PLAN_WEEK_TOLERANCE_MS)
  if (
    i.accountKey === null ||
    file.account !== i.accountKey ||
    i.isWeekAccountUnknown ||
    isOffSchedule ||
    !(weekEnd > now) ||
    !(Date.parse(file.computed_at) <= now + FUTURE_SLACK_MS) ||
    !(start <= now && now < end)
  ) {
    return null
  }
  const informUntil = file.inform_until ? Date.parse(file.inform_until) : NaN
  const mode = file.mode === 'limit' && !(Number.isFinite(informUntil) && now < informUntil) ? 'limit' : 'inform'
  const usedAtStart = file.part.used_at_start
  const spent = Math.max(0, i.pct - usedAtStart)
  const share = file.part.share_pct
  const partHours = (end - start) / HOUR
  const allowed = share === null ? null : share * Math.min(1, Math.max(0, (now - start) / (end - start)))
  const lead = allowed === null ? null : spent - allowed
  const hasLaterWeight = file.later.reduce((sum, l) => sum + l.weight, 0) > 0
  const isUnplannedWeek = share === null && !hasLaterWeight
  const reserve = reserveOf(file, i.pct, now)
  const kept = reserve.kind === 'held' ? reserve.pct : 0
  return {
    mode,
    start,
    end,
    share,
    usedAtStart,
    spent,
    allowed,
    isAbove: allowed !== null && spent > allowed + PLAN_TOLERANCE_PCT,
    lead,
    leadHours: lead === null || !share ? null : Math.abs(lead) / (share / partHours),
    isUnplannedPart: share === null && hasLaterWeight,
    isUnplannedWeek,
    unplannedPct: isUnplannedWeek ? Math.max(0, 100 - i.pct - kept) : null,
    budget:
      share === null ? null : Math.min(Math.max(0, share - spent) / Math.max((end - now) / HOUR, 1e-9), file.five_hour_to_week_pct / 5),
    // The look-back is the part's own age, up to 6 hours; in the part's first hour there is no rate.
    pace: now - start < HOUR ? null : burnRate(i.pct, i.resetsAt, i.samples, now, Math.min(6 * HOUR, now - start), HOUR),
    tick: allowed === null ? null : Math.round(Math.min(100, Math.max(0, usedAtStart + allowed))),
    reserve,
  }
}

const LEVEL_RANK: Record<Level, number> = { calm: 0, watch: 1, act: 2 }
const higher = (a: Level, b: Level): Level => (LEVEL_RANK[b] > LEVEL_RANK[a] ? b : a)

// The Plan row's level: above the share is watch in inform mode and act in limit mode; within, an
// unplanned part and an unplanned week have none.
export const planLevel = (p: PlanView): Level => (p.isAbove ? (p.mode === 'limit' ? 'act' : 'watch') : 'calm')
// The Reserve row's level: act once the week is at 100 - reserve before the release.
export const reserveLevel = (p: PlanView): Level => (p.reserve.kind === 'held' && p.reserve.isStop ? 'act' : 'calm')

// The week's plan and the level of its card and gauge: the highest of the percentage rule, the Plan
// row and the Reserve row. Without a plan the card's level is the percentage rule's.
const withPlan = (level: Level, plan: PlanView | null): { cardLevel: Level; plan: PlanView | null } => ({
  cardLevel: plan ? higher(higher(level, planLevel(plan)), reserveLevel(plan)) : level,
  plan,
})

function reserveOf(file: StatusWeekPlan, used: number, now: number): ReserveView {
  const r = file.reserve
  if (r.error) return { kind: 'unknown' }
  if (r.pct <= 0) return { kind: 'none' }
  if (r.release_at === null) return { kind: 'unknown' }
  const releaseAt = Date.parse(r.release_at)
  const name = r.name ?? null
  if (now >= releaseAt) return { kind: 'released', pct: r.pct, name, releaseAt }
  // No release candidate this week: PaceLine puts the release at the reset.
  const isToReset = Math.abs(Date.parse(file.week_resets_at) - releaseAt) < 60_000
  return { kind: 'held', pct: r.pct, name, releaseAt, isToReset, isStop: used >= 100 - r.pct }
}

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
    // On an equal figure in the same window the payload is the session's own account's reading, so the
    // file's copy of it is no reason to mark the figure.
    const isTie = !!payload && !!file && payload.percentUsed === file.percentUsed && windowKey(payload.resetsAt) === windowKey(file.resetsAt)
    return { merged, isExact: merged !== undefined && merged === file && file === read, isFromFile: merged !== undefined && merged === file && !isTie }
  }
  const five = pick('five_hour')
  const week = pick('seven_day')
  const weekPct = week.merged?.percentUsed
  // Samples are kept only for the account's own readings, so a figure of a file whose account is
  // unknown has none to compute a burn from.
  const isUnknown = !!i.isFileAccountUnknown
  const weekSamples = week.isFromFile && isUnknown ? [] : i.weekSamples
  return {
    now: i.now,
    context: contextView(i.measure, i.ctxSamples, i.cache.regime),
    cache: cacheView(i.cache, i.now, i.measure?.tokens ?? null, i.meta?.model ?? null),
    five: fiveView(five.merged, five.isExact, five.isFromFile && isUnknown ? [] : i.fiveSamples, i.now, i.burn ?? null, i.accountKey ?? null),
    week:
      weekPct === undefined
        ? null
        : {
            pct: weekPct,
            resetAt: week.merged?.resetsAt ? Date.parse(week.merged.resetsAt) : null,
            isExact: week.isExact,
            ...weekPace(weekPct, week.merged?.resetsAt, weekSamples, i.now),
            level: weekLevel(weekPct),
            ...withPlan(weekLevel(weekPct), planOf(i.weekPlan, {
              now: i.now,
              accountKey: i.accountKey ?? null,
              isWeekAccountUnknown: week.isFromFile && isUnknown,
              pct: weekPct,
              resetsAt: week.merged?.resetsAt,
              samples: weekSamples,
            })),
          },
    scoped: i.scoped.map(b => ({ ...b, level: weekLevel(b.percentUsed) })),
    costUsd: i.measure?.costUsd ?? null,
    meta: i.meta,
    elapsedMs: i.meta ? i.now - i.meta.startedAt : null,
    isAccountUnknown: isUnknown && (five.isFromFile || week.isFromFile),
    isScopedAccountUnknown: isUnknown && i.scoped.length > 0,
  }
}
