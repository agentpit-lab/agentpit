import type { StatusMeta } from '../types'
import { PIE_PX, ringMarksWidth, type BarExtra, type Card, type CardBar, type Tone } from './desktop'
import type { CacheView, ContextView, Level, PlanView, View } from './view'
import { FIVE_BUDGET_MIN_MS, higher, planLevel, reserveLevel, roundedRate } from './view'

// The terminal and the pane draw in theme keys: the engine resolves each against the person's
// theme, light or dark, and a mod cannot read which one is active to pick a colour itself.
export const LABEL = 'inactive'
export const VALUE = 'text'
export const DIM = 'inactive'
export const RULE = 'inactive'
export const LEVEL: Record<Level, string> = { calm: 'inactive', watch: 'warning', act: 'error' }
// The terminal's counterparts of DESKTOP_COLD and DESKTOP_ON.
export const COLD = 'planMode'
export const ON = 'success'
// A gauge's track and its calm fill: the fill stands out from the track on either theme.
export const TRACK = 'subtle'
export const CALM_FILL = 'inactive'
// Marks a context reading whose auto-compact is on; nothing marks it off.
const AUTO_MARK = '↻'
// Follows AUTO_MARK while idle compaction is expected: a superscript z, one cell wide.
export const IDLE_MARK = 'ᶻ'
// Model and effort keys are picked by hue: they name a model or a level, never a state, so one may
// share its key with a state. The Desktop row keeps fixed hex, so its advisor name keeps these.
const FAMILY_COLORS = [
  { name: 'Opus', match: 'opus', theme: 'autoAccept', desktop: '#d787ff' },
  { name: 'Sonnet', match: 'sonnet', theme: 'suggestion', desktop: '#5fafff' },
  { name: 'Fable', match: 'fable', theme: 'bashBorder', desktop: '#ff87d7' },
  { name: 'Haiku', match: 'haiku', theme: 'success', desktop: '#87d7af' },
] as const
const MODEL_COLORS: [RegExp, string][] = FAMILY_COLORS.map(f => [new RegExp(f.match, 'i'), f.theme])
export const EFFORT_COLORS: Record<string, string> = {
  low: 'warning',
  medium: 'success',
  high: 'suggestion',
  xhigh: 'autoAccept',
  max: 'error',
}
const DESKTOP_MODEL_COLORS: [RegExp, string][] = FAMILY_COLORS.map(f => [new RegExp(f.match, 'i'), f.desktop])
// Every colour the terminal and the pane draw with, read-only, for tests.
export const TERMINAL_PALETTE = Object.freeze({
  value: VALUE,
  label: LABEL,
  dim: DIM,
  rule: RULE,
  track: TRACK,
  calmFill: CALM_FILL,
  level: Object.freeze({ ...LEVEL }),
  cold: COLD,
  on: ON,
  model: Object.freeze({ ...Object.fromEntries(MODEL_COLORS.map(([re, c]) => [re.source, c])), other: VALUE }),
  effort: Object.freeze({ ...EFFORT_COLORS }),
})
const FAMILIES: Record<string, string> = { opus: 'Opus', sonnet: 'Sonnet', haiku: 'Haiku', fable: 'Fable' }
const RANKS: [RegExp, number][] = [
  [/mythos-5|fable-5|\bfable\b/, 5],
  [/opus-(4-7|4-8|5)|\bopus\b/, 4],
  [/sonnet-5|opus-4-[56]|\bsonnet\b/, 3],
  [/sonnet-4-[56]/, 2],
  [/haiku-4-5|\bhaiku\b/, 1],
]

// A run of text, or a limit's wide bar drawn as cells on the terminal and as Svg elsewhere.
// wide: a limit's bar with its projection and even-pace tick, the pane's counterpart of the card bar.
export type Part = { text: string; color?: string; bold?: boolean } | { wide: CardBar; cells: number }
export type Row = Part[]

export const t = (text: string, color = VALUE, bold = false): Part => ({ text, color, bold })
export const dim = (text: string): Part => ({ text, color: DIM })
export const label = (text: string): Part => ({ text, color: LABEL })
const SEP = dim(' · ')
export const BAR = { text: '  │  ', color: RULE } as Part

export const displayModel = (id: string) => {
  const m = /^claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?(\[1m\])?$/i.exec(id.trim())
  if (!m || !m[1] || !m[2]) return FAMILIES[id.toLowerCase()] ?? id
  const family = FAMILIES[m[1].toLowerCase()] ?? m[1]
  return `${family} ${m[2]}${m[3] ? `.${m[3]}` : ''}`
}

// advisor_rank from the model catalog: the advisor only activates when its rank is at least the
// main model's; an unknown rank means active.
const rankOf = (model: string) => RANKS.find(([re]) => re.test(model.toLowerCase().replace(/\./g, '-')))?.[1]
export const modelColor = (name: string) => MODEL_COLORS.find(([re]) => re.test(name))?.[1] ?? VALUE
// Sand marks a model of no known family; the draw code italicizes it, so it reads as unknown, not off.
export const DESKTOP_OTHER = '#b8925a'
// The model and effort colours with their Desktop values, read-only, for the documentation facts.
// The fallback row has no match pattern: it applies when no family matches. It is drawn in italics on
// Desktop; an effort level has no Desktop colour.
export const COLOUR_FACTS = Object.freeze({
  models: Object.freeze([
    ...FAMILY_COLORS.map(f => Object.freeze({ ...f, italic: false })),
    Object.freeze({ name: 'Other', match: null, theme: VALUE, desktop: DESKTOP_OTHER, italic: true }),
  ]),
  efforts: Object.freeze(Object.entries(EFFORT_COLORS).map(([level, theme]) => Object.freeze({ level, theme }))),
})
export const isKnownFamily = (name: string) => DESKTOP_MODEL_COLORS.some(([re]) => re.test(name))
export const desktopModelColor = (name: string) => DESKTOP_MODEL_COLORS.find(([re]) => re.test(name))?.[1] ?? DESKTOP_OTHER

const kilo = (n: number) =>
  n >= 1_000_000 ? `${Number((n / 1_000_000).toFixed(1))}M` : n >= 1000 ? `${Math.round(n / 1000)}k` : String(n)
const mmss = (ms: number) => {
  const s = Math.max(0, Math.floor(ms / 1000))
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}
const span = (ms: number) => {
  const m = Math.floor(ms / 60000)
  const h = Math.floor(m / 60)
  const d = Math.floor(h / 24)
  if (d > 0) return `${d}d ${h % 24}h`
  if (h > 0) return `${h}h ${String(m % 60).padStart(2, '0')}m`
  return `${m}m`
}
const pct0 = (n: number) => `${Math.round(n)}%`
const pct1 = (n: number | null) => (n === null ? 'n/a' : `${n.toFixed(1)}%`)
const rate = (n: number | null) => (n === null ? 'n/a' : `${n.toFixed(1)}%/h`)

function clock(at: number, meta: StatusMeta | null, weekday = false) {
  const options: Intl.DateTimeFormatOptions = { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: meta?.timeZone }
  if (weekday) options.weekday = 'short'
  try {
    return new Date(at).toLocaleString(meta?.locale ?? undefined, options)
  } catch {
    return new Date(at).toLocaleString(undefined, options)
  }
}

export const cacheFraction = (v: View) => {
  const c = v.cache
  if (c.state === 'hot') return 100
  if (c.leftMs === null) return 0
  return (c.leftMs / (c.regime === '1h' ? 3600_000 : 300_000)) * 100
}

function advisorText(m: StatusMeta): string {
  if (!m.advisor) return 'advisor off'
  const name = FAMILIES[m.advisor.toLowerCase()] ?? displayModel(m.advisor)
  const main = rankOf(m.model)
  const rank = rankOf(m.advisor)
  const isActive = main === undefined || rank === undefined || main <= rank
  return `advisor ${name}${isActive ? '' : ' (inactive)'}`
}

// Until the first reply after a compaction no hit is counted, and the cached history is the old one.
function rebuiltNote(k: CacheView): string {
  return k.hitAvg === null
    ? 'compaction replaced the history; the next turn re-caches it'
    : 'the last turn wrote the history to the cache again'
}

// Where readings run as one list (the pane, a CLI detail line) Budget follows Pace, as it stands
// under Pace in the card's two columns.
export const listOrder = (rows: Card['rows']) => {
  const filled = rows.filter(([l]) => l)
  const budget = filled.find(([l]) => l === 'Budget')
  const at = filled.findIndex(([l]) => l === 'Pace')
  if (!budget || at < 0) return filled
  const rest = filled.filter(r => r !== budget)
  return [...rest.slice(0, at + 1), budget, ...rest.slice(at + 1)]
}
// 'week' holds the week plan (Part, 7d, the scoped weekly limits) apart from 5h: 5h │ Part · 7d.
export type Group = 'context' | 'cache' | 'limits' | 'week'
export type Gauge = {
  key: string
  group: Group
  label: string
  pct: number | null
  level: Level
  value: string | null
  extra?: string
  // Colours the value apart from the level scale: a cold cache.
  tone?: 'cold'
  // A cache with no countdown to draw: a flame while a reply runs, a snowflake once it expired.
  icon?: 'hot' | 'cold'
  // A state glyph after the gauge, in the `on` colour: auto-compact on.
  mark?: string
  // Idle compaction is expected: a second glyph after the mark.
  idle?: boolean
  opens?: string
  // A limit: desktop draws it as a bar with an even-pace tick at this share of the window gone,
  // null when the reset is unknown, and its value and time to reset always.
  pace?: number | null
  // What the tick is called in the alt text; omitted, it is the even pace.
  paceName?: string
  // The paler forecast layer of the bar: whole percent 0-100, and what the alt text calls it
  // ('at reset', or 'at part end' for the Part block).
  projected?: number
  projectedName?: string
  // The bar's fill level where it differs from the gauge's (the Part block, amber over its share).
  barLevel?: Level
  // What a bar's alt text says where the fill's percentage would mislead (an unplanned Part block).
  barAlt?: string
  reset?: string
  card: Card
}

const HOUR_MS = 3_600_000
const WEEK_MS = 7 * 24 * HOUR_MS
const paceOf = (leftMs: number | null, windowMs: number) =>
  leftMs === null ? null : Math.max(0, Math.min(100, (1 - leftMs / windowMs) * 100))

function desktopCache(v: View): string {
  const c = v.cache
  if (c.state === 'hot') return 'HOT'
  if (c.state === 'cold') return 'COLD'
  if (c.state === 'none' || c.leftMs === null) return 'n/a'
  return c.leftMs >= 300_000 ? `${Math.floor(c.leftMs / 60000)}m` : mmss(c.leftMs)
}

type Rows = [string, string, Tone?][]

const SOURCES: Record<string, string> = { env: 'env', settings: 'settings', clientdata: 'account', experiment: 'experiment', 'model-default': 'model default', 'unknown-model': 'default' }

// A card's bullets: the advice first when the reading needs a decision, then its explanation,
// two at most, so the card stays short.
const bulletsOf = (advice: string | null, explain: string[]) => [...(advice ? [advice] : []), ...explain]
// A card's bullets, and its advice apart: the popover shows only the advice, the pane every bullet.
const noted = (advice: string | null, explain: string[]) => ({ bullets: bulletsOf(advice, explain), ...(advice ? { advice } : {}) })

function idleText(c: ContextView): string {
  if (c.idle === 'on') return `on · from ${kilo(c.idleMin ?? 0)}`
  if (c.idle === 'below') return `on · below ${kilo(c.idleMin ?? 0)} (${kilo(c.used)})`
  return c.idle
}

function contextCard(v: View): Card {
  const c = v.context
  if (!c) return { title: 'Context', rows: [], bullets: ['No response in this window yet.'] }
  const hasTrigger = c.isAuto && c.limit < c.window
  const isNarrowed = c.compactWindow !== null && c.compactWindow < c.window
  // Who narrowed the window; the Window row carries its size.
  const via = isNarrowed ? ` (${SOURCES[c.compactSource ?? ''] ?? c.compactSource ?? 'settings'})` : ''
  const rows: Rows = [
    ['Auto-compact', c.isAuto ? `on${via}` : 'off', c.isAuto ? 'on' : undefined],
    ['Idle compact', idleText(c), c.idle === 'on' ? 'on' : undefined],
    // Used against what it counts toward: the compact point, or the full window.
    ['Used', `${kilo(c.used)} of ${kilo(c.limit)} (${c.pct.toFixed(1)}%)`, c.level],
    ...(hasTrigger
      ? ([
          ['Left', kilo(Math.max(0, c.limit - c.used))],
        ] as Rows)
      : []),
    ...(c.isAuto ? ([['Window', isNarrowed && c.compactWindow !== null ? `${kilo(c.compactWindow)} of ${kilo(c.window)}` : kilo(c.window)]] as Rows) : []),
    ...(c.turnsLeft !== null ? ([['Turns left', `~${c.turnsLeft}`, c.level]] as Rows) : []),
  ]
  const advice =
    c.level === 'calm'
      ? null
      : !c.isAuto
        ? 'Auto-compact is off: nothing compacts the context by itself.'
        : 'Auto-compact summarizes the history at the compaction point.'
  const explain = hasTrigger
    ? [isNarrowed ? 'Auto-compact uses a smaller window than the model; % counts toward it.' : 'Compacts at the window minus the buffer; % counts toward it.']
    : ['% counts the full window.']
  return {
    title: 'Context',
    level: c.level,
    rows,
    ...noted(advice, explain),
    ...(hasTrigger ? { desktopBullets: ["Desktop's own ring counts the full window, so it reads lower."] } : {}),
  }
}

// `withHits`: the row has no Hit reading, so its Cache card carries the hit rates.
// isRow: the row's card joins TTL to the countdown, "41m of 1h".
function cacheCard(v: View, withHits = false, isRow = false): Card {
  const k = v.cache
  const ratio = `${k.isRatioFloor ? '≥' : '~'}${k.ratio}×`
  const rewrite = k.rewrite !== null ? `${kilo(k.rewrite)} tokens` : 'the whole context'
  // Whole minutes while calm: the card is redrawn only with the row, which a calm cache leaves alone,
  // so seconds would stand still; once it warns the row redraws every second and so does this.
  const expires =
    k.state === 'hot'
      ? 'replying'
      : k.state === 'cold'
        ? 'expired'
        : k.state === 'none' || k.leftMs === null
          ? 'n/a'
          : k.level === 'calm'
            ? `${Math.floor(k.leftMs / 60_000)}m`
            : mmss(k.leftMs)
  const rows: Rows = [
    ...((isRow && k.state === 'warm'
      ? [['Countdown', `${expires} of ${k.regime ?? '5m?'}`, k.level]]
      : [
          ['Countdown', expires, k.state === 'warm' ? k.level : undefined],
          ['TTL', k.regime ?? '5m?'],
        ]) as Rows),
    // What the next turn writes again once the cache has expired, and how much dearer that turn is.
    ['If cold', k.rewrite !== null ? `${kilo(k.rewrite)} (${ratio})` : 'n/a'],
    ...(withHits
      ? ([
          ['Hit, last turn', pct1(k.hitLast)],
          ['Hit, session', pct1(k.hitAvg)],
        ] as Rows)
      : []),
  ]
  const advice =
    k.state === 'cold'
      ? `The next turn writes ${rewrite} again (${ratio} a warm turn).`
      : k.state === 'warm' && k.level !== 'calm' && k.leftMs !== null
        ? `A reply within ${mmss(k.leftMs)} keeps it warm.`
        : null
  const rebuilt = k.isRebuilt ? [`${rebuiltNote(k).replace(/^./, x => x.toUpperCase())}.`] : []
  const explain = [...rebuilt, 'Each reply restarts the TTL.', 'Once it expires, the next turn writes the context again.']
  return { title: 'Cache', level: k.state === 'cold' ? undefined : k.level, rows, ...noted(advice, explain) }
}

function hitCard(v: View): Card {
  const k = v.cache
  return {
    title: 'Cache hits',
    rows: [
      ['Last turn', pct1(k.hitLast)],
      ['Session', pct1(k.hitAvg)],
    ],
    bullets: ['Hit = cache reads / (reads + writes).', 'Session counts from the last compaction.'],
  }
}

// The pace a limit is spent at now against the pace that lasts to its reset. Pace needs a span of
// readings first: 10 minutes for 5h, an hour for the week, whose percentage moves in whole points.
// Pace and Budget stack in the left column, so the two rates compare down it; `beside` is what
// stands right of Pace (when the limit runs out), an empty cell otherwise.
// `noBudget` is what Budget reads when it is null.
function paceRows(ratePerHour: number | null, budgetPerHour: number | null, needs: string, alarm: Level, beside: Rows, noBudget = 'n/a (reset unknown)'): Rows {
  const isOver = ratePerHour !== null && budgetPerHour !== null && ratePerHour > budgetPerHour
  return [
    ['Pace', ratePerHour !== null ? rate(ratePerHour) : `n/a (needs ${needs})`, isOver ? alarm : undefined],
    ...(beside.length ? beside : [GAP]),
    ['Budget', budgetPerHour !== null ? rate(budgetPerHour) : noBudget],
  ]
}

// An empty cell: keeps Budget under Pace in the desktop card's two columns.
const GAP: [string, string] = ['', '']

// Said where the person looks: the limits file could not tell whose figures these are.
const accountRows = (isUnknown: boolean): Rows => (isUnknown ? [['Account', 'unknown', 'watch']] : [])

// isRow: the row's card puts the reset in the title and the budget beside the pace.
function fiveCard(v: View, isRow = false): Card {
  const f = v.five
  if (!f) return { title: '5h limit', rows: [], bullets: ['No reading yet.'] }
  const isShort = f.isFilling && f.fullAt !== null
  // Where the current pace leaves the window at its reset.
  const projected = f.ratePerHour !== null && f.resetInMs !== null ? f.pct + f.ratePerHour * (f.resetInMs / HOUR_MS) : null
  const forecast: Rows =
    isShort && f.fullAt !== null
      ? f.isPaceline
        ? [['Threshold', `${pct0(f.targetPct)} at ${clock(f.fullAt, v.meta)}`, 'act']]
        : [['Runs out', clock(f.fullAt, v.meta), 'act']]
      : [['At reset', projected !== null ? `~${pct0(projected)}` : 'n/a', projected !== null && projected >= 80 ? 'watch' : undefined]]
  const isOver = f.ratePerHour !== null && f.evenPerHour !== null && f.ratePerHour > f.evenPerHour
  const rows: Rows = isRow
    ? [
        ['Used', pct0(f.pct), f.level],
        [
          'Pace',
          f.ratePerHour === null
            ? 'n/a (needs 10 min)'
            : f.evenPerHour !== null
              ? `${f.ratePerHour.toFixed(1)} of ${rate(f.evenPerHour)}`
              : rate(f.ratePerHour),
          isShort ? 'act' : isOver ? 'watch' : undefined,
        ],
        ...forecast,
        ...accountRows(v.isAccountUnknown),
      ]
    : [
        ['Used', pct0(f.pct), f.level],
        ['Resets in', f.resetInMs !== null ? span(f.resetInMs) : 'n/a'],
        ...paceRows(f.ratePerHour, f.evenPerHour, '10 min', isShort ? 'act' : 'watch', forecast, f.resetInMs !== null && f.resetInMs < FIVE_BUDGET_MIN_MS ? 'n/a (under 15 min left)' : undefined),
        ...accountRows(v.isAccountUnknown),
      ]
  const tick = paceOf(f.resetInMs, 5 * HOUR_MS)
  // Whole percents: the card is part of the desktop key, and a finer tick would redraw the row every second.
  const bar = { used: Math.round(f.pct), ...(projected !== null ? { projected: Math.min(100, Math.round(projected)), projectedName: 'at reset' } : {}), tick: tick === null ? null : Math.round(tick), level: f.level }
  const advice =
    f.level === 'calm'
      ? null
      : isShort
        ? f.isPaceline
          ? `Reaches ${pct0(f.targetPct)} before the reset at this pace: hold subagents.`
          : 'Runs out before the reset at this pace: hold subagents.'
        : f.pct >= 70
          ? `${pct0(f.pct)} used: keep subagents for what needs them.`
          : 'Pace is above budget.'
  const explain = ['Pace: last 90 min. Budget: the pace that lasts to the reset.', 'Bar: used, paler to the reset at this pace; tick: even pace now.']
  const title = isRow && f.resetInMs !== null ? `5h limit · resets in ${span(f.resetInMs)}` : '5h limit'
  return { title, level: f.level, rows, ...noted(advice, explain), bar }
}

// The plan card's rows: the lead against the part's share, the reserve. The verdict is not a row here.
function planRows(p: PlanView, v: View): Rows {
  const level = planLevel(p)
  const rows: Rows = []
  if (p.lead !== null) {
    const hours = p.leadHours !== null ? ` (${span(Math.round(p.leadHours * 60) * 60_000)})` : ''
    rows.push([p.lead > 0 ? 'Overspent' : 'Spare', `${Math.round(Math.abs(p.lead))} pts${hours}`, p.isAbove ? level : undefined])
  }
  const r = p.reserve
  if (r.kind === 'unknown') rows.push(['Reserve', 'unknown: the stop is off'])
  else if (r.kind === 'held') {
    rows.push(['Reserve', `${pct0(r.pct)} ${r.isToReset ? 'to the reset' : `until ${clock(r.releaseAt, v.meta, true)}`}`, r.isStop ? 'act' : undefined])
  } else if (r.kind === 'released') rows.push(['Reserve', `${pct0(r.pct)} released ${clock(r.releaseAt, v.meta, true)}`])
  return rows
}

// The 7d bar: the tick is the share of the week gone, whole percent or null, and the paler layer the forecast
// at reset (the plain average since the reset held to the reset).
export function weekBarOf(v: View): { tick: number | null; extra: BarExtra } | null {
  const w = v.week
  if (!w) return null
  const leftMs = w.resetAt !== null ? Math.max(0, w.resetAt - v.now) : null
  const time = paceOf(leftMs, WEEK_MS)
  const extra: BarExtra = {}
  if (w.forecast) {
    extra.projected = Math.max(0, Math.min(100, Math.round(w.forecast.atReset)))
    extra.projectedName = 'at reset'
  }
  return { tick: time === null ? null : Math.round(time), extra }
}

// The week's use the forecast's rate implies now, where it differs from the
// account's whole percent; Used, the 7d value and the fill show it with one decimal. null otherwise.
export function weekEstimate(v: View): number | null {
  const w = v.week
  const f = w?.forecast
  if (!w || !f || w.isUsedUnderOne) return null
  return Math.abs(f.usedNow - w.pct) >= 0.05 ? f.usedNow : null
}

// The sign before a forecast value that rests on under 48 h of the week.
const approx = (f: { isApprox: boolean } | null) => (f?.isApprox ? '≈' : '')

// The Week card, week rows only; the plan's rows are on the Part card.
// Pace is the rate behind the bar's forecast: the week's average since the reset. The Part card keeps the part's own.
// isRow: the row's card (Desktop hover, CLI line) is short: the reset joins the title, Week elapsed is the tick.
function weekCard(v: View, isRow = false): Card {
  const w = v.week
  const model = weekBarOf(v)
  const even = w ? paceOf(w.resetAt !== null ? Math.max(0, w.resetAt - v.now) : null, WEEK_MS) : null
  const f = w?.forecast ?? null
  // The At reset row reads the forecast itself; only the bar's paler layer is held to 100.
  const projected = f ? Math.round(f.atReset) : undefined
  const rows: Rows = w
    ? [
        ['Used', w.isUsedUnderOne ? '<1%' : weekEstimate(v) !== null ? `~${pct1(weekEstimate(v)!)}` : pct0(w.pct), w.level],
        ...((isRow ? [] : [['Resets', w.resetAt !== null ? clock(w.resetAt, v.meta, true) : 'n/a']]) as Rows),
        // Before the forecast's first hour the rows that need it give way to one line: when it comes.
        ...((f
          ? [
              ['Pace', `${rate(f.rate)} (week average)`],
              ...((isRow ? [] : [['Week elapsed', even !== null ? pct0(even) : 'n/a']]) as Rows),
              // Past 100% the forecast's depth is of no use: the row says when the week runs out instead.
              f.runsOutAt !== null
                ? ['Runs out', `${approx(f)}${clock(f.runsOutAt, v.meta, true)}`, 'act']
                : ['At reset', projected !== undefined ? `${approx(f)}${pct0(projected)}` : 'n/a', projected !== undefined && projected >= 80 ? 'watch' : undefined],
            ]
          : [['Forecast', w.resetAt !== null ? `from ${clock(w.resetAt - WEEK_MS + HOUR_MS, v.meta, true)}` : 'n/a']]) as Rows),
        ...accountRows(v.isAccountUnknown),
      ]
    : []
  // Without a Part block the reserve's stop has no other card to say that launches ask.
  const reserve = w?.plan?.reserve
  const advice =
    w && partOf(v) === null && reserve?.kind === 'held' && reserve.isStop
      ? `Week at ${pct0(100 - reserve.pct)}+ before the reserve opens: launches ask before each agent.`
      : w && w.level !== 'calm'
        ? `${pct0(w.pct)} used: save it for work that needs it.`
        : null
  // The ≈ note shows only while the forecast carries the mark.
  const explain = [
    'Pace: the average rate since the reset. At reset: used plus that rate over the hours left. Runs out: when the week reaches 100% at it, before the reset.',
    ...(f?.isApprox ? ['≈: under 48 h of the week are gone, so the average is rough. No forecast in the first hour.'] : []),
    'Bar: used; tick: time elapsed in the week; paler: forecast at reset.',
  ]
  const bar = w && model ? { used: Math.round(w.pct), tick: earlyTick(even) === null ? null : model.tick, level: w.cardLevel, ...model.extra } : undefined
  const title = isRow && w?.resetAt != null ? `Week · resets ${clock(w.resetAt, v.meta, true)}` : 'Week'
  return { title, level: w?.cardLevel ?? 'calm', rows, ...noted(advice, explain), ...(bar ? { bar } : {}) }
}

// The Part block. The bar runs 0..share of the part in points of the week:
// the fill is spent in the part, the tick the share of the part's time gone. Past the share (the plan's
// tolerance), or with any spend in a part of share 0, the fill is amber. null without a usable plan; a share
// of 0 (a planned off part) keeps the block. A part with no share (an unplanned week or part) keeps it only
// once at least UNPLANNED_SPENT_MIN points of the week were spent in it: then the bar is an empty track with
// no tick (nothing to measure against; a full grey bar read as "all spent"), and the block gives no advice.
export const UNPLANNED_SPENT_MIN = 1
export type PartModel = {
  glyph: '☼' | '☾'
  // Fill and tick as a share of the bar, 0-100; no tick for a part with no share.
  fill: number
  tick: number | null
  isOver: boolean
  spent: number
  // The plan's isSpentUnderOne: shown as "<1".
  isSpentUnderOne: boolean
  // null: the part is not planned.
  share: number | null
  leftMs: number
  // The block's level: the plan's, with the reserve's stop.
  level: Level
  // The fill's level: amber when over, else the block's.
  barLevel: Level
  // The bar's paler forecast: spent at the part's end at its average pace so far, as a share of the bar
  // (past 100 when the share runs out first), and in points; null in the part's first PART_FORECAST_MIN_MS.
  projected: number | null
  atEnd: number | null
  // The part's average rate behind atEnd, points an hour; null with it.
  rate: number | null
  // The spent the rate implies now, when it differs from the rounded figure; else null.
  estimate: number | null
}
// No tick in a period's first percent: at the bar's edge it reads as its frame, and the
// empty bar already says the period has just begun.
export const earlyTick = (tick: number | null) => (tick !== null && tick < 1 ? null : tick)
// The forecast needs this much of the part gone; earlier the average pace is a few minutes of noise.
export const PART_FORECAST_MIN_MS = 15 * 60_000
export function partOf(v: View): PartModel | null {
  const p = v.week?.plan
  if (!p) return null
  const level = higher(planLevel(p), reserveLevel(p))
  if (p.share === null) {
    if (p.spent < UNPLANNED_SPENT_MIN) return null
    return {
      glyph: p.kind === 'off' ? '☾' : '☼',
      fill: 0,
      tick: null,
      isOver: false,
      spent: p.spent,
    isSpentUnderOne: p.isSpentUnderOne,
      share: null,
      leftMs: Math.max(0, p.end - v.now),
      level,
      barLevel: 'calm',
      projected: null,
      atEnd: null,
      rate: null,
      estimate: null,
    }
  }
  const isOver = p.share === 0 ? p.spent > 0 : p.isAbove
  const tick = earlyTick(Math.max(0, Math.min(100, ((v.now - p.start) / (p.end - p.start)) * 100)))
  const gone = v.now - p.start
  // The part's average rate in points an hour, read through the week's last whole-percent step (roundedRate).
  const step = v.week?.step ?? null
  const ownRate = roundedRate(p.spent, p.start, v.now, step ? { t: step.t, used: step.used - p.usedAtStart } : null)
  // The part cannot have spent more than the week's estimate past the part's start: the two rates start
  // at different instants, so the part's alone can run ahead of the week's.
  const weekEst = weekEstimate(v)
  const partRate = weekEst !== null && gone > 0 ? Math.min(ownRate, Math.max(0, weekEst - p.usedAtStart) / (gone / HOUR_MS)) : ownRate
  // The spent that rate implies now, where it differs from the rounded figure: the fill, the value and the
  // card's Spent and Spare / Overspent show it. The plan's verdict (isOver) keeps the account's figure.
  const est = gone >= PART_FORECAST_MIN_MS ? partRate * (gone / HOUR_MS) : null
  const estimate = est !== null && !p.isSpentUnderOne && Math.abs(est - p.spent) >= 0.05 ? est : null
  const shown = estimate ?? p.spent
  const fill = p.share === 0 ? (shown > 0 ? 100 : 0) : Math.min(100, (shown / p.share) * 100)
  const atEnd = gone >= PART_FORECAST_MIN_MS && p.end > p.start ? partRate * ((p.end - p.start) / HOUR_MS) : null
  const projected = atEnd === null ? null : p.share === 0 ? (atEnd > 0 ? 100 : 0) : (atEnd / p.share) * 100
  return {
    glyph: p.kind === 'off' ? '☾' : '☼',
    fill,
    tick,
    isOver,
    spent: p.spent,
    isSpentUnderOne: p.isSpentUnderOne,
    share: p.share,
    leftMs: Math.max(0, p.end - v.now),
    level,
    barLevel: isOver ? 'watch' : level,
    projected,
    atEnd,
    rate: gone >= PART_FORECAST_MIN_MS ? partRate : null,
    estimate,
  }
}

function partGauge(v: View, isRow = false): Gauge | null {
  const m = partOf(v)
  if (!m) return null
  return {
    key: 'part',
    group: 'week',
    label: m.glyph,
    pct: m.fill,
    level: m.level,
    value: `${m.isSpentUnderOne ? '<1' : m.estimate !== null ? pts(m.estimate) : Math.round(m.spent)}/${m.share === null ? '-' : Math.round(m.share)}`,
    pace: m.tick,
    paceName: 'part time',
    barLevel: m.barLevel,
    ...(m.share === null ? { barAlt: `${Math.round(m.spent)} pts spent, no share` } : {}),
    ...(m.projected !== null ? { projected: Math.min(100, Math.round(m.projected)), projectedName: 'at part end' } : {}),
    reset: span(m.leftMs),
    card: partCard(v, m, isRow),
  }
}

export const pts = (n: number) => String(Number(n.toFixed(1)))

// isRow: the row's card (Desktop hover, CLI line) is short: Hours join the title, Share joins Spent, Budget
// joins Pace, Mode and Reserve show only while they act. The pane keeps every row.
function partCard(v: View, m: PartModel, isRow = false): Card {
  const p = v.week!.plan!
  if (m.share === null) {
    // Not planned: what was spent, no share to measure it against, no advice.
    return {
      title: 'Part',
      level: m.level,
      rows: [
        ['Hours', `${p.kind === 'off' ? 'off' : 'work'} ${clock(p.start, v.meta)}-${clock(p.end, v.meta)}`],
        ['Spent', `${pts(p.spent)} pts`],
        ['Share', 'none (not planned)'],
      ],
      ...noted(null, ['Bar: empty, as this part has no share to measure against; no tick.']),
      bar: { used: 0, tick: null, level: 'calm' },
    }
  }
  const planned = planRows(p, v)
  const find = (label: string) => planned.filter(([l]) => l === label)
  let rowsOverride: { title: string; rows: Rows } | null = null
  // The part's average pace since its start: the same rate the bar's paler forecast and At part end use.
  const pace = m.rate
  const isOver = pace !== null && p.budget !== null && pace > p.budget
  // The spent the rate implies now, where it differs from the rounded figure; Spent and
  // Spare / Overspent show it with "~". The plan's own verdict keeps the account's figure.
  const est = m.estimate
  const isEst = est !== null
  const estLead = isEst && p.allowed !== null ? est! - p.allowed : null
  const leadRows: Rows =
    estLead === null
      ? // Under one point the spent is counted as 0, so the lead is off by up to a point: "~".
        ([...find('Spare'), ...find('Overspent')].map(r => (p.isSpentUnderOne ? [r[0], `~${r[1]}`, ...r.slice(2)] : r)) as Rows)
      : [[
          estLead > 0 ? 'Overspent' : 'Spare',
          `~${pts(Math.abs(estLead))} pts${p.share ? ` (${span(Math.round((Math.abs(estLead) / (p.share / ((p.end - p.start) / HOUR_MS))) * 60) * 60_000)})` : ''}`,
          p.isAbove ? planLevel(p) : undefined,
        ]]
  const stop = p.reserve.kind === 'held' && p.reserve.isStop ? p.reserve.pct : null
  const hours = `${p.kind === 'off' ? 'off' : 'work'} ${clock(p.start, v.meta)}-${clock(p.end, v.meta)}`
  if (isRow) {
    const of = ` of ${pts(p.share!)} pts`
    const budget = p.budget !== null ? ` of ${rate(p.budget)}` : '%/h'
    const num = (n: number) => n.toFixed(1)
    const compact: Rows = [
      ['Spent', `${p.isSpentUnderOne ? '<1' : isEst ? `~${pts(est!)}` : pts(p.spent)}${of}`],
      ...leadRows,
      ...((p.isSpentUnderOne && v.now > p.start
        ? [['Pace', `<${num(1 / ((v.now - p.start) / HOUR_MS))}${budget} (part average)`]]
        : pace !== null && m.atEnd !== null
          ? [
              ['Pace', `${num(pace)}${budget} (part average)`, isOver ? 'watch' : undefined],
              ['At part end', `~${pts(m.atEnd)}${of}`, m.projected !== null && m.projected > 100 ? 'watch' : undefined],
            ]
          : [['Forecast', `from ${clock(p.start + PART_FORECAST_MIN_MS, v.meta)}`]]) as Rows),
      ...((p.mode === 'limit' ? [['Mode', p.mode]] : []) as Rows),
      ...(stop !== null ? find('Reserve') : []),
    ]
    rowsOverride = { title: `Part · ${hours}`, rows: compact }
  }
  const rows: Rows = [
    ['Hours', hours],
    ['Share', `${pts(p.share!)} pts`],
    ['Spent', p.isSpentUnderOne ? '<1 pts' : isEst ? `~${pts(est!)} pts` : `${pts(p.spent)} pts`],
    ...leadRows,
    ['Budget', p.budget !== null ? rate(p.budget) : 'n/a (unplanned)'],
    // Under one point the account's figure has not moved, so its rate reads 0; the
    // pace is shown as its bound (under 1 point over the time gone) and the part's end is not projected.
    ...((p.isSpentUnderOne && v.now > p.start
      ? [
          ['Pace', `<${rate(1 / ((v.now - p.start) / HOUR_MS))} (part average)`],
          ['At part end', 'n/a (under 1 pt so far)'],
        ]
      : pace !== null && m.atEnd !== null
      ? [
          ['Pace', `${rate(pace)} (part average)`, isOver ? 'watch' : undefined],
          ['At part end', `~${pts(m.atEnd)} of ${pts(p.share!)} pts`, m.projected !== null && m.projected > 100 ? 'watch' : undefined],
        ]
      : [['Forecast', `from ${clock(p.start + PART_FORECAST_MIN_MS, v.meta)}`]]) as Rows),
    ['Mode', p.mode],
    ...find('Reserve'),
  ]
  const advice =
    stop !== null
      ? `Week at ${pct0(100 - stop)}+ before the reserve opens: launches ask before each agent.`
      : p.isAbove && p.mode === 'limit'
        ? "Above the plan's share: PaceLine asks before each agent launch while its data is fresh."
        : null
  const bar = { used: Math.round(m.fill), ...(m.projected !== null ? { projected: Math.min(100, Math.round(m.projected)), projectedName: 'at part end' } : {}), tick: m.tick === null ? null : Math.round(m.tick), level: m.barLevel }
  const explain = [
    'Pace: the average since the part began. At part end: what the part spends by its end at that pace.',
    "Bar: spent against the share, paler to the part's end at that pace; tick: time elapsed in the part. Paler past the tick means faster than the share allows; paler to the end means the share runs out.",
  ]
  return { title: rowsOverride?.title ?? 'Part', level: m.level, rows: rowsOverride?.rows ?? rows, ...noted(advice, explain), bar }
}

// isRow: the row's card puts the reset in the title.
function scopedCard(v: View, b: View['scoped'][number], isRow = false): Card {
  const own = b.resetsAt ? Date.parse(b.resetsAt) : NaN
  const resetAt = Number.isFinite(own) ? own : (v.week?.resetAt ?? null)
  return {
    title: isRow && resetAt !== null ? `${b.name} · resets ${clock(resetAt, v.meta, true)}` : b.name,
    level: b.level,
    rows: [
      ['Used', pct0(b.percentUsed), b.level],
      ...((isRow && resetAt !== null ? [] : [['Resets', resetAt !== null ? clock(resetAt, v.meta, true) : 'n/a']]) as Rows),
      ...accountRows(v.isScopedAccountUnknown),
    ],
    ...noted(b.level !== 'calm' ? `${pct0(b.percentUsed)} used: switch model for work that does not need ${b.name}.` : null, [
      `Weekly limit for ${b.name} models alone; resets with the week.`,
    ]),
  }
}

function advisorCard(m: StatusMeta): Card {
  const name = m.advisor ? (FAMILIES[m.advisor.toLowerCase()] ?? displayModel(m.advisor)) : null
  const state = !m.advisor ? 'off' : advisorText(m).endsWith(' (inactive)') ? 'inactive' : 'active'
  return {
    title: 'Advisor',
    rows: [
      ['Model', name ?? 'off'],
      ['Main', displayModel(m.model)],
      ['State', state, state === 'inactive' ? 'watch' : undefined],
    ],
    bullets: ['Set with /advisor in settings.json, shared by every session.', 'Inactive when its model ranks below the main one.'],
  }
}

// What both rows leave out, and so out of their cards, detail lines and widths: Hit at
// every level, and a scoped weekly limit while its level is calm; past calm it returns to its place
// in the row. Only the pane keeps what is out. desktopKey reads the row's readings, so a limit
// crossing calm and watch adds or drops its key and the row redraws.
const isPaneOnly = (g: Gauge) =>
  (g.key === 'hit' || (g.key.startsWith('scoped-') && g.level === 'calm'))

// The readings both rows draw, with each one's detail card; `scope: 'pane'` gives every reading the
// pane unfolds.
export function readings(v: View, scope: 'row' | 'pane' = 'row'): { gauges: Gauge[]; info: Card; advisor: Card | null; advisorOn: string | null } {
  const m = v.meta
  const c = v.context
  const f = v.five
  const k = v.cache
  let gauges: Gauge[] = [
    {
      key: 'context',
      group: 'context',
      label: 'Ctx',
      pct: c?.pct ?? 0,
      level: c?.level ?? 'calm',
      value: c ? pct0(c.pct) : null,
      mark: c?.isAuto ? AUTO_MARK : undefined,
      idle: c?.idle === 'on' ? true : undefined,
      card: contextCard(v),
    },
    {
      key: 'cache',
      group: 'cache',
      label: 'Cache',
      // Fills as the TTL runs out, like every other gauge fills as its budget is used.
      pct: k.state === 'warm' ? 100 - cacheFraction(v) : 0,
      level: k.level,
      value: desktopCache(v),
      tone: k.state === 'cold' ? 'cold' : undefined,
      icon: k.state === 'hot' || k.state === 'cold' ? k.state : undefined,
      card: cacheCard(v, scope === 'row', scope === 'row'),
    },
    {
      key: 'hit',
      group: 'cache',
      label: 'Hit',
      pct: null,
      level: 'calm',
      value: null,
      extra: k.hitLast !== null ? pct0(k.hitLast) : undefined,
      card: hitCard(v),
    },
    {
      key: 'five',
      group: 'limits',
      label: '5h',
      pct: f?.pct ?? 0,
      level: f?.level ?? 'calm',
      value: f ? pct0(f.pct) : null,
      pace: paceOf(f?.resetInMs ?? null, 5 * HOUR_MS),
      // The row draws the paler forecast at reset its card already draws, as 7d and Part do.
      ...(f && f.ratePerHour !== null && f.resetInMs !== null
        ? { projected: Math.min(100, Math.round(f.pct + f.ratePerHour * (f.resetInMs / HOUR_MS))), projectedName: 'at reset' }
        : {}),
      reset: f?.resetInMs != null ? span(f.resetInMs) : undefined,
      card: fiveCard(v, scope === 'row'),
    },
    ...(v.week
      ? [
          {
            key: 'week',
            group: 'week' as Group,
            label: '7d',
            pct: weekEstimate(v) ?? v.week.pct,
            level: v.week.cardLevel,
            value: v.week.isUsedUnderOne ? '<1%' : weekEstimate(v) !== null ? pct1(weekEstimate(v)!) : pct0(v.week.pct),
            pace: earlyTick(paceOf(v.week.resetAt !== null ? Math.max(0, v.week.resetAt - v.now) : null, 7 * 24 * HOUR_MS)),
            ...weekBarOf(v)?.extra,
            reset: v.week.resetAt !== null ? span(Math.max(0, v.week.resetAt - v.now)) : undefined,
            card: weekCard(v, scope === 'row'),
          },
          ...(partGauge(v, scope === 'row') ? [partGauge(v, scope === 'row')!] : []),
        ]
      : []),
    ...v.scoped.map(b => ({
      key: `scoped-${b.name}`,
      group: 'week' as Group,
      label: b.name,
      pct: b.percentUsed,
      level: b.level,
      value: pct0(b.percentUsed),
      card: scopedCard(v, b, scope === 'row'),
    })),
  ]
  // The part leads its week, the finer reading first: 5h │ Part · 7d.
  const part = gauges.find(g => g.key === 'part')
  if (part) gauges = gauges.filter(g => g !== part).flatMap(g => (g.key === 'week' ? [part, g] : [g]))
  if (scope === 'row') gauges = gauges.filter(g => !isPaneOnly(g))
  const advisorOn = m?.advisor ? advisorText(m) : null
  const isInactive = advisorOn?.endsWith(' (inactive)') ?? false
  const info: Card = m
    ? {
        title: displayModel(m.model),
        rows: [
          ...(scope === 'pane' && m.account ? ([['Account', m.account]] as Rows) : []),
          ['Effort', m.effort ?? 'n/a'],
          ['Advisor', advisorOn ? advisorOn.replace(/^advisor /, '') : 'off', isInactive ? 'watch' : undefined],
          ['Claude Code', m.version],
          ['Started in', `…${m.leaf}`],
          ...(m.branch ? ([['Branch', m.branch]] as Rows) : []),
          ['Running', v.elapsedMs !== null ? span(v.elapsedMs) : 'n/a'],
          ['Turns', String(m.turns)],
          // What /cost totals at API prices; a subscription is not charged per token.
          ['API cost', v.costUsd !== null ? `$${v.costUsd.toFixed(2)}` : 'n/a'],
        ],
        bullets: [
          ...(isInactive ? ['Advisor inactive: its model ranks below the main one.'] : []),
          'API cost: the session at API prices; a subscription is not charged per token.',
          'Full detail: /pitboard',
        ],
      }
    : { title: 'Session', rows: [], bullets: ['Full detail: /pitboard'] }
  return { gauges, info, advisor: m ? advisorCard(m) : null, advisorOn }
}

export const GROUPS: Group[] = ['context', 'cache', 'limits', 'week']

export const cardFor = (gauges: Gauge[], info: Card, key: string | null) => {
  if (key === 'info') return info
  const g = gauges.find(x => x.key === key)
  if (!g) return undefined
  return g.opens ? (gauges.find(x => x.key === g.opens)?.card ?? g.card) : g.card
}

// The advisor as both rows show it: its name, and whether its rank leaves it inactive.
export function advisorView(advisorOn: string | null) {
  const isInactive = advisorOn?.endsWith(' (inactive)') ?? false
  const name = advisorOn ? advisorOn.replace(/^advisor /, '').replace(/ \(inactive\)$/, '') : null
  return { name, isInactive }
}

// Everything the desktop row shows, as plain data: desktop redraws a Button on every render and
// drops its hover highlight, so the row is redrawn only when this changes.
// A calm cache draws no number, so its countdown moves only the hidden card: keyed by the whole
// minute, the 5-minute window does not redraw every second. A pie moves in 2% steps, about a pixel.
// `cards`: the view the hidden cards are keyed on, taken at the start of the minute. Their rates and
// times move every few seconds, and a redraw while a card is up keeps desktop from seeing the
// pointer leave, so cards stuck open. While the pane is open the row draws no cards, so they key nothing.
export function desktopKey(v: View, open: string | null, cards?: View, isPaneOpen = false): string {
  const c = v.cache
  const coarse: View =
    c.state === 'warm' && c.level === 'calm' && c.leftMs !== null
      ? { ...v, cache: { ...c, leftMs: Math.ceil(c.leftMs / 60_000) * 60_000 } }
      : v
  const { gauges } = readings(v)
  const shown = readings(coarse)
  const hidden = cards ? readings(cards) : shown
  return JSON.stringify([
    gauges.map(g => [g.key, g.pct === null ? null : Math.round(g.pct / 2), g.pace == null ? g.pace : Math.round(g.pace), g.projected == null ? null : Math.round(g.projected / 2), g.projectedName ?? null, g.barLevel ?? null]),
    shown.gauges.map(g => [g.key, g.label, g.level, g.value, g.extra, g.tone, g.reset, g.mark, g.idle]),
    shown.advisorOn,
    v.meta !== null,
    open,
    isPaneOpen,
    ...(isPaneOpen ? [] : [hidden.gauges.map(g => cardFor(hidden.gauges, hidden.info, g.key) ?? g.card), hidden.info, hidden.advisor]),
  ])
}
// Desktop lays the row out in cells of about 8 px; its proportional text runs about 0.9 cell a
// character. Measured on Desktop at 95 columns, where the row with a cache countdown wraps and
// the row without it fits.
const PX_PER_CELL = 8
const CELLS_PER_CHAR = 0.9
const BAR_PX = 40
const RULE_PX = 9

// How the desktop row draws the five-hour and weekly limits: bars with the time to reset, bars
// alone, or pies as the rest of the row.
export type LimitStyle = 'bars' | 'bare' | 'pies'

// The parts of a desktop reading, as drawn by drawDesktop.
function desktopItems(g: Gauge, style: LimitStyle): (number | string)[] {
  if (style !== 'pies' && g.pace !== undefined)
    return [g.label, BAR_PX, ...(g.value ? [g.value] : []), ...(style === 'bars' && g.reset ? [g.reset] : [])]
  const isLoud = g.level !== 'calm'
  return [
    g.label,
    ...(g.mark && !g.icon ? [ringMarksWidth(g.pct !== null, true, !!g.idle)] : g.pct !== null ? [PIE_PX] : []),
    ...(isLoud && g.value && !g.icon ? [g.value] : []),
    ...(g.extra ? [g.extra] : []),
    ...(isLoud && g.reset ? [g.reset] : []),
  ]
}

// Desktop's font is proportional, and the row's values are mostly digits, %, m and
// spaces, which differ most from an average letter. Widths in cells, measured on owner screenshots
// 2026-10-31 (a 40 px bar as the ruler); anything else keeps CELLS_PER_CHAR.
const CHAR_CELLS: Record<string, number> = { '%': 1.35, m: 1.45, ' ': 0.5, '☼': 1.7, '☾': 1.7, 'ⓘ': 1.7, '/': 0.6 }
export const textCells = (text: string) =>
  [...text].reduce((sum, ch) => sum + (CHAR_CELLS[ch] ?? (ch >= '0' && ch <= '9' ? 0.97 : CELLS_PER_CHAR)), 0)

// The desktop row's width in cells: each reading's parts one cell apart inside one cell of padding
// a side, the rules between groups, the advisor group, and ⓘ with its margin.
export function desktopCells(v: View, style: LimitStyle, withInfo = true): number {
  const { gauges, advisorOn } = readings(v)
  const width = (items: (number | string)[]) =>
    items.reduce<number>((sum, x) => sum + (typeof x === 'number' ? x / PX_PER_CELL : textCells(x)), 0) + items.length - 1 + 2
  const groups = GROUPS.map(id => gauges.filter(g => g.group === id && !g.opens)).filter(gs => gs.length)
  // Readings within a group are parted by a dot, about one cell.
  let cells = groups.reduce(
    (sum, gs) =>
      sum + gs.reduce((s, g) => s + width([g, ...gauges.filter(x => x.opens === g.key)].flatMap(x => desktopItems(x, style))), 0) + (gs.length - 1) * CELLS_PER_CHAR,
    0,
  )
  let parts = groups.length
  if (v.meta?.advisor) {
    const adv = advisorView(advisorOn)
    cells += width(['Advisor', adv.name ?? 'off', ...(adv.isInactive ? ['(inactive)'] : [])])
    parts += 1
  }
  // A rule parts ⓘ from the readings, as it parts the groups.
  // The band's own padding, about a cell a side, measured on owner screenshots.
  return cells + (withInfo ? parts : parts - 1) * (RULE_PX / PX_PER_CELL) + (withInfo ? width(['ⓘ']) : 0) + 2
}

// The fullest row that stays on one line: ⓘ goes first, then the time to reset, both of which
// a card still shows; the shorter styles keep ⓘ.
export function rowFit(v: View, columns: number): { style: LimitStyle; info: boolean } {
  // The row measures its text per character (CHAR_CELLS) and the band's padding, and keeps
  // 1.5 cells spare: the row wrapped ⓘ within half a cell of the estimate.
  const fits = (style: LimitStyle, withInfo = true) => desktopCells(v, style, withInfo) <= columns - 1.5
  if (fits('bars')) return { style: 'bars', info: true }
  if (fits('bars', false)) return { style: 'bars', info: false }
  return { style: fits('bare') ? 'bare' : 'pies', info: true }
}

export const limitStyle = (v: View, columns: number): LimitStyle => rowFit(v, columns).style

// The pane: a verdict, what happens next, and the cards folded into groups. Everything below is
// plain data; draw.tsx lays it out.

// Minutes and seconds without a leading zero, as the mockups show them (4:12).
const mss = (ms: number) => mmss(ms).replace(/^0/, '')

export type PaneValue = {
  text: string
  // A level or `on`, or `cold` for a cold cache; `color` is a theme key that overrides it (model, effort).
  tone?: Tone | 'cold'
  color?: string
}

export type PaneGroup = 'limits' | 'conversation' | 'session'
export const PANE_GROUPS: { id: PaneGroup; title: string }[] = [
  { id: 'limits', title: 'Limits' },
  { id: 'conversation', title: 'This conversation' },
  { id: 'session', title: 'Session' },
]

export type PaneCard = {
  key: string
  group: PaneGroup
  label: string
  labelColor?: string
  // A state glyph after the label: Part's sun, Context's auto-compact arrow.
  mark?: string
  markTone?: Tone
  level: Level
  // The folded line's values, in the order shown, parted by " · " where they wrap.
  values: PaneValue[]
  bar?: CardBar
  // The unfolded card: every row, the advice, and the explanations behind "How it is computed".
  rows: Card['rows']
  advice?: string
  explain: string[]
}

const NOT_EXPLANATION = 'Full detail: /pitboard'

// A clock time, with the weekday once it is more than 18 hours away.
const when = (at: number, v: View) => clock(at, v.meta, at - v.now > 18 * HOUR_MS)

// The part's lead over what its share allows by now, as the Part card words it: from the estimate when there is one.
const leadOf = (pm: PartModel, p: PlanView) => (pm.estimate !== null && p.allowed !== null ? pm.estimate - p.allowed : p.lead)

// A pane bar takes the colour of the value beside it, its forecast the paler shade of the same.
const withLevel = (bar: CardBar | undefined, level: Level): CardBar | undefined => (bar ? { ...bar, level } : undefined)

export function paneCards(v: View, isRemote = false): PaneCard[] {
  const { gauges, info, advisor } = readings(v, 'pane')
  const m = v.meta
  const out: PaneCard[] = []
  const g = (key: string) => gauges.find(x => x.key === key)
  const explainOf = (card: Card, extra: string[] = []) => [
    ...card.bullets.filter(b => b !== card.advice && b !== NOT_EXPLANATION),
    ...extra,
    ...(isRemote ? (card.desktopBullets ?? []) : []),
  ]
  const f = v.five
  const five = g('five')
  if (five) {
    const resetAt = f && f.resetInMs !== null ? v.now + f.resetInMs : null
    out.push({
      key: 'five',
      group: 'limits',
      label: '5h',
      level: five.level,
      values: f ? [{ text: pct0(f.pct), tone: five.level }, { text: resetAt !== null ? `resets ${when(resetAt, v)}` : 'reset unknown' }] : [{ text: 'no reading yet' }],
      bar: withLevel(five.card.bar, five.level),
      rows: five.card.rows,
      advice: five.card.advice,
      explain: explainOf(five.card),
    })
  }
  const part = g('part')
  const pm = partOf(v)
  if (part && pm) {
    const p = v.week!.plan!
    const spent = pm.isSpentUnderOne ? '<1' : pts(pm.estimate ?? pm.spent)
    const level = higher(pm.level, pm.isOver ? 'watch' : 'calm')
    out.push({
      key: 'part',
      group: 'limits',
      label: 'Part',
      mark: pm.glyph,
      markTone: level,
      level,
      values: [
        { text: pm.share === null ? `${spent} pts spent` : `${spent} of ${pts(pm.share)} pts`, tone: level },
        { text: pm.share === null ? 'no share' : `ends ${when(p.end, v)}` },
      ],
      bar: withLevel(part.card.bar, level),
      rows: part.card.rows,
      advice: part.card.advice,
      explain: explainOf(part.card),
    })
  }
  const week = g('week')
  const w = v.week
  if (week && w) {
    const fc = w.forecast
    const forecast: PaneValue | null = fc
      ? fc.runsOutAt !== null
        ? { text: `runs out ${when(fc.runsOutAt, v)}`, tone: 'act' }
        : { text: `${fc.isApprox ? '≈' : '~'}${pct0(fc.atReset)} at ${w.resetAt !== null ? when(w.resetAt, v) : 'the reset'}`, tone: fc.atReset >= 80 ? 'watch' : undefined }
      : w.resetAt !== null
        ? { text: `resets ${when(w.resetAt, v)}` }
        : null
    out.push({
      key: 'week',
      group: 'limits',
      label: 'Week',
      level: w.level,
      values: [{ text: week.value ?? '', tone: w.level }, ...(forecast ? [forecast] : [])],
      bar: withLevel(week.card.bar, w.level),
      rows: week.card.rows,
      advice: week.card.advice,
      explain: explainOf(week.card),
    })
  }
  for (const sg of gauges.filter(x => x.key.startsWith('scoped-'))) {
    const b = v.scoped.find(x => `scoped-${x.name}` === sg.key)
    const own = b?.resetsAt ? Date.parse(b.resetsAt) : NaN
    const resetAt = Number.isFinite(own) ? own : (w?.resetAt ?? null)
    out.push({
      key: sg.key,
      group: 'limits',
      label: sg.label,
      level: sg.level,
      values: [{ text: sg.value ?? '', tone: sg.level }, ...(resetAt !== null ? [{ text: `resets ${when(resetAt, v)}` }] : [])],
      bar: withLevel({ used: Math.round(sg.pct ?? 0), tick: null, level: sg.level }, sg.level),
      rows: sg.card.rows,
      advice: sg.card.advice,
      explain: explainOf(sg.card),
    })
  }
  const c = v.context
  const ctx = g('context')
  if (ctx) {
    out.push({
      key: 'context',
      group: 'conversation',
      label: 'Context',
      ...(c?.isAuto ? { mark: AUTO_MARK, markTone: 'on' as Tone } : {}),
      level: ctx.level,
      values: c
        ? [
            { text: pct0(c.pct), tone: c.level },
            { text: c.isAuto ? `${kilo(Math.max(0, c.limit - c.used))} to auto-compact` : 'auto-compact off', tone: c.isAuto ? undefined : 'watch' },
          ]
        : [{ text: 'no response yet' }],
      ...(c ? { bar: { used: Math.round(c.pct), tick: null, level: c.level } } : {}),
      rows: ctx.card.rows,
      advice: ctx.card.advice,
      explain: explainOf(ctx.card),
    })
  }
  if (advisor && m?.advisor) {
    const view = advisorView(advisorText(m))
    out.push({
      key: 'advisor',
      group: 'conversation',
      label: 'Advisor',
      level: 'calm',
      values: [
        { text: view.name ?? 'off', color: view.name ? modelColor(view.name) : undefined },
        { text: view.isInactive ? 'inactive' : 'active', tone: view.isInactive ? 'watch' : undefined },
      ],
      rows: advisor.rows,
      explain: explainOf(advisor),
    })
  }
  const cacheG = g('cache')
  if (cacheG) {
    const k = v.cache
    // Rounded down: "100%" claims no miss at all, so 99.6% reads 99%.
    const hitPct = (n: number) => `${Math.floor(n)}%`
    const hits = [k.hitLast !== null ? `${hitPct(k.hitLast)} now` : null, k.hitAvg !== null ? `${hitPct(k.hitAvg)} session` : null].filter(x => x !== null)
    const state: PaneValue =
      k.state === 'hot'
        ? { text: 'replying' }
        : k.state === 'cold'
          ? { text: 'cold', tone: 'cold' }
          : k.state === 'warm' && k.leftMs !== null
            ? k.level === 'calm'
              ? { text: `warm ${Math.floor(k.leftMs / 60_000)} min left` }
              : { text: `cools in ${mss(k.leftMs)}`, tone: k.level }
            : { text: 'n/a' }
    const hit = hitCard(v)
    out.push({
      key: 'cache',
      group: 'conversation',
      label: 'Cache',
      level: k.state === 'cold' ? 'calm' : k.level,
      values: [state, ...(hits.length ? [{ text: `hits ${hits.join(', ')}` }] : [])],
      rows: [...cacheG.card.rows, ['Hit, last turn', hit.rows[0]?.[1] ?? 'n/a'], ['Hit, session', hit.rows[1]?.[1] ?? 'n/a']],
      advice: cacheG.card.advice,
      explain: explainOf(cacheG.card, hit.bullets),
    })
  }
  if (m) {
    const model = displayModel(m.model)
    out.push({
      key: 'info',
      group: 'session',
      label: model,
      labelColor: modelColor(model),
      level: 'calm',
      values: [
        { text: m.effort ?? 'n/a', color: m.effort ? (EFFORT_COLORS[m.effort] ?? DIM) : undefined },
        ...(m.branch ? [{ text: m.branch }] : []),
        ...(v.elapsedMs !== null ? [{ text: span(v.elapsedMs) }] : []),
        ...(v.costUsd !== null ? [{ text: `$${v.costUsd.toFixed(2)} at API prices` }] : []),
      ],
      rows: info.rows,
      explain: explainOf(info),
    })
  }
  // A gauge added to `readings` and not listed above still shows: its label, value, rows and bar. The
  // group follows the gauge's kind (context and cache gauges are the conversation's, the rest limits).
  // `hit` is part of the Cache card.
  const handled = new Set(['five', 'part', 'week', 'context', 'cache', 'hit'])
  for (const x of gauges) {
    if (handled.has(x.key) || x.key.startsWith('scoped-')) continue
    out.push({
      key: x.key,
      group: x.group === 'context' || x.group === 'cache' ? 'conversation' : 'limits',
      label: x.label,
      level: x.level,
      values: [{ text: x.value ?? 'n/a', tone: x.level }],
      bar: withLevel(x.card.bar, x.level),
      rows: x.card.rows,
      advice: x.card.advice,
      explain: explainOf(x.card),
    })
  }
  return out
}

export type Verdict = { level: Level; sentence: string; advice: string[]; forecast: { text: string; level: Level } | null }

// The leading reading: the highest level, and among equals the limits first (5h, Part, the week, a
// model-scoped limit), then the cache, then context.
export function paneVerdict(v: View): Verdict {
  type Lead = { level: Level; rank: number; sentence: string; advice: string[] }
  const leads: Lead[] = []
  const f = v.five
  const pause = (ms: number | null) => (ms !== null ? ` Or pause until ${clock(v.now + ms, v.meta)}.` : '')
  if (f && f.level !== 'calm') {
    const spending = f.ratePerHour !== null && f.evenPerHour !== null ? `Spending ${rate(f.ratePerHour)}; ${rate(f.evenPerHour)} would last to the reset.` : null
    if (f.isFilling && f.fullAt !== null && f.resetInMs !== null)
      leads.push({ level: 'act', rank: 0, sentence: 'Hold subagents: the 5h window runs short.', advice: [`${spending ?? `${pct0(f.pct)} used.`}${pause(f.resetInMs)}`] })
    else if (f.pct >= 70)
      leads.push({ level: f.level, rank: 0, sentence: `The 5h window is ${pct0(f.pct)} used.`, advice: [f.resetInMs !== null ? `It resets in ${span(f.resetInMs)}.` : 'Its reset is unknown.'] })
    else leads.push({ level: f.level, rank: 0, sentence: 'The 5h pace is above its budget.', advice: [spending ?? `${pct0(f.pct)} used.`] })
  }
  const pm = partOf(v)
  const plan = v.week?.plan ?? null
  if (pm && plan && pm.share !== null) {
    const level = higher(pm.level, pm.isOver ? 'watch' : 'calm')
    const r = plan.reserve
    const lead = leadOf(pm, plan)
    if (level !== 'calm') {
      if (plan.isAbove || pm.isOver)
        leads.push({
          level,
          rank: 1,
          sentence: 'This part is over its share.',
          advice: [`${pts(pm.estimate ?? pm.spent)} of ${pts(pm.share)} points spent${lead !== null && lead > 0 ? `, ${pts(lead)} over` : ''}.${plan.mode === 'limit' ? ' Agent launches ask first.' : ''}`],
        })
      else if (r.kind === 'held' && r.isStop)
        leads.push({ level, rank: 1, sentence: 'The reserve is held: agent launches ask first.', advice: [`The week is past ${pct0(100 - r.pct)}; the reserve opens ${clock(r.releaseAt, v.meta, true)}.`] })
    }
  }
  const w = v.week
  if (w && w.level !== 'calm')
    leads.push({ level: w.level, rank: 2, sentence: `The week is ${pct0(w.pct)} used.`, advice: [`Save it for work that needs it${w.resetAt !== null ? `; it resets ${clock(w.resetAt, v.meta, true)}` : ''}.`] })
  for (const b of v.scoped)
    if (b.level !== 'calm') leads.push({ level: b.level, rank: 3, sentence: `${b.name} is ${pct0(b.percentUsed)} used this week.`, advice: [`Switch model for work that does not need ${b.name}.`] })
  const k = v.cache
  if (k.level !== 'calm') {
    const rewrite = k.rewrite !== null ? `${kilo(k.rewrite)} tokens` : 'the whole context'
    const ratio = `${k.isRatioFloor ? '≥' : '~'}${k.ratio}×`
    if (k.state === 'cold') leads.push({ level: k.level, rank: 4, sentence: 'The cache is cold.', advice: [`The next turn writes ${rewrite} again (${ratio} a warm one).`] })
    else if (k.state === 'warm' && k.leftMs !== null)
      leads.push({
        level: k.level,
        rank: 4,
        sentence: k.level === 'act' ? `The cache cools in ${mss(k.leftMs)}.` : `The cache has ${mss(k.leftMs)} left.`,
        advice: [`Reply now to keep it; a cold turn writes ${rewrite} again (${ratio} a warm one).`],
      })
  }
  const c = v.context
  if (c && c.level !== 'calm')
    leads.push({
      level: c.level,
      rank: 5,
      sentence: `Context is at ${pct0(c.pct)}${c.isAuto ? '' : ' of the full window'}.`,
      advice: [!c.isAuto ? 'Auto-compact is off: nothing compacts it by itself.' : c.turnsLeft !== null ? `About ${c.turnsLeft} ${c.turnsLeft === 1 ? 'turn' : 'turns'} to auto-compact.` : 'Auto-compact is near.'],
    })
  const fc = w?.forecast ?? null
  const forecast =
    !w || !fc
      ? null
      : fc.runsOutAt !== null
        ? { level: 'act' as Level, text: `The week runs out at ${clock(fc.runsOutAt, v.meta, true)} at its average pace.` }
        : {
            level: (fc.atReset >= 80 ? 'watch' : 'calm') as Level,
            text: `The week heads to ${fc.isApprox ? '≈' : '~'}${pct0(fc.atReset)}${w.resetAt !== null ? ` by ${clock(w.resetAt, v.meta, true)}` : ''} at its average pace.${fc.isApprox && w.resetAt !== null ? ` Rough until ${clock(w.resetAt - WEEK_MS + 48 * HOUR_MS, v.meta, true)}.` : ''}`,
          }
  const lead = leads.sort((a, b) => (a.level === b.level ? a.rank - b.rank : a.level === 'act' ? -1 : 1))[0]
  if (lead) return { level: lead.level, sentence: lead.sentence, advice: lead.advice, forecast }
  const advice: string[] = []
  if (pm && plan && pm.share !== null) {
    const lead = leadOf(pm, plan)
    const spare = lead !== null && lead < 0 ? `, ${Math.round(-lead)} to spare` : ''
    // A part with no share (off hours) has no plan to be on: say when the next share starts.
    if (pm.share === 0) advice.push(`No share planned for this part; it ends at ${clock(plan.end, v.meta)}.`)
    else advice.push(`This part is on plan: ${pm.isSpentUnderOne ? '<1' : pts(pm.estimate ?? pm.spent)} of ${pts(pm.share)} points spent${spare}.`)
  } else {
    // The week's figure is the Week card's own value, so the advice and the folded line agree.
    const weekValue = w ? readings(v, 'pane').gauges.find(x => x.key === 'week')?.value : undefined
    const figures = [f ? `5h at ${pct0(f.pct)}` : null, weekValue ? `week at ${weekValue}` : null].filter(x => x !== null)
    if (figures.length) advice.push(`${figures.join(', ')}.`.replace(/^./, x => x.toUpperCase()))
  }
  return { level: 'calm', sentence: f || w ? 'Limits hold to their resets.' : 'Nothing needs you.', advice, forecast }
}

// One thing that happens next: a clock time, or a count (`~9 turns`) that has none.
export type PaneEvent = { at: number | null; when: string; what: string; why: string; level: Level }

// The events of the current 5h window in time order, every amber or red one wherever it falls, and at most
// three grey ones; the count-based one follows the timed ones.
export function paneNext(v: View): PaneEvent[] {
  const events: PaneEvent[] = []
  const f = v.five
  const windowEnd = f && f.resetInMs !== null ? v.now + f.resetInMs : v.now + 5 * HOUR_MS
  const add = (at: number, what: string, why: string, level: Level) => events.push({ at, when: at <= v.now ? 'now' : when(at, v), what, why, level })
  const k = v.cache
  const rewrite = k.rewrite !== null ? kilo(k.rewrite) : 'the whole context'
  const ratio = `${k.isRatioFloor ? '≥' : '~'}${k.ratio}×`
  if (k.state === 'warm' && k.leftMs !== null)
    add(
      v.now + k.leftMs,
      k.level === 'calm' ? 'Cache cools' : `Cache cools in ${mss(k.leftMs)}`,
      k.level === 'calm' ? `unless a reply comes first; a cold turn writes ${rewrite} again (${ratio})` : `a reply now keeps it; a cold turn writes ${rewrite} again (${ratio})`,
      k.level,
    )
  else if (k.state === 'cold' && k.level !== 'calm') add(v.now, 'Cache is cold', `the next turn writes ${rewrite} again (${ratio})`, k.level)
  if (f) {
    const resetAt = f.resetInMs !== null ? v.now + f.resetInMs : null
    if (f.isFilling && f.fullAt !== null && resetAt !== null)
      add(f.fullAt, f.isPaceline ? `5h reaches ${pct0(f.targetPct)}` : '5h hits 100%', `at the current pace, ${span(resetAt - f.fullAt)} before the reset`, f.level)
    if (resetAt !== null) {
      const projected = f.ratePerHour !== null ? Math.min(100, Math.round(f.pct + f.ratePerHour * (f.resetInMs! / HOUR_MS))) : null
      // An earlier entry that says the window fills before the reset leaves nothing to add here.
      const isFilling = f.isFilling && f.fullAt !== null
      add(resetAt, '5h window resets', !isFilling ? `at ~${projected ?? Math.round(f.pct)}% used` : '', 'calm')
    }
  }
  const pm = partOf(v)
  const plan = v.week?.plan
  if (pm && plan && pm.share !== null) {
    const level = higher(pm.level, pm.isOver ? 'watch' : 'calm')
    const lead = leadOf(pm, plan)
    const over = lead !== null && lead > 0
    // Under one point the pane says "<1", as the card does.
    const atEnd = pm.atEnd === null ? '' : pm.atEnd === 0 ? '0' : pm.atEnd < 1 ? '<1' : `~${pts(pm.atEnd)}`
    add(
      plan.end,
      'This part ends',
      pm.atEnd === null || (pm.share === 0 && level === 'calm')
        ? ''
        : level === 'calm'
          ? `at ${atEnd} of its ${pts(pm.share)} points`
          : `heading for ${atEnd} of ${pts(pm.share)} points${over ? `; already ${pts(lead!)} over` : ''}${plan.mode === 'limit' ? ', so agent launches ask first' : ''}`,
      level,
    )
  }
  const w = v.week
  if (w && w.level !== 'calm' && w.resetAt !== null) add(w.resetAt, 'Week resets', `at ${pct0(w.pct)} used`, w.level)
  for (const b of v.scoped) {
    if (b.level === 'calm') continue
    const own = b.resetsAt ? Date.parse(b.resetsAt) : NaN
    const at = Number.isFinite(own) ? own : (w?.resetAt ?? null)
    if (at !== null) add(at, `${b.name} limit resets`, `${pct0(b.percentUsed)} used; switch model for work that does not need ${b.name}`, b.level)
  }
  const inWindow = events.filter(e => e.level !== 'calm' || (e.at !== null && e.at <= windowEnd))
  const grey = new Set(inWindow.filter(e => e.level === 'calm').sort((a, b) => (a.at ?? 0) - (b.at ?? 0)).slice(0, 3))
  const kept = inWindow.filter(e => e.level !== 'calm' || grey.has(e)).sort((a, b) => (a.at ?? 0) - (b.at ?? 0))
  const c = v.context
  if (c && c.level !== 'calm' && c.isAuto)
    kept.push({ at: null, when: c.turnsLeft !== null ? `~${c.turnsLeft} ${c.turnsLeft === 1 ? 'turn' : 'turns'}` : 'soon', what: 'Auto-compact', why: `context at ${pct0(c.pct)}`, level: c.level })
  return kept
}
