import type { StatusMeta } from '../types'
import { PIE_PX, ringMarksWidth, type Card, type CardBar, type Tone } from './desktop'
import type { CacheView, ContextView, Level, View } from './view'

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

// What needs a decision now, most urgent first, with its advice; the detail cards quote it.

// Until the first reply after a compaction no hit is counted, and the cached history is the old one.
function rebuiltNote(k: CacheView): string {
  return k.hitAvg === null
    ? 'compaction replaced the history; the next turn re-caches it'
    : 'the last turn wrote the history to the cache again'
}

export function alerts(v: View): { level: Level; head: string; advice: string }[] {
  const out: { level: Level; head: string; advice: string }[] = []
  const c = v.context
  if (c && c.level !== 'calm') {
    out.push({
      level: c.level,
      head: c.isAuto ? `Context ${pct0(c.pct)}` : `Context ${pct0(c.pct)} of the full window`,
      advice: !c.isAuto
        ? 'auto-compact is off'
        : c.turnsLeft !== null
          ? `~${c.turnsLeft} turns to auto-compact`
          : 'auto-compact is near',
    })
  }
  const k = v.cache
  if (k.level !== 'calm') {
    const rewrite = k.rewrite !== null ? `${kilo(k.rewrite)} tokens` : 'the whole context'
    const ratio = `${k.isRatioFloor ? '≥' : '~'}${k.ratio}×`
    if (k.state === 'cold') out.push({ level: k.level, head: 'Cache is cold', advice: `the next turn writes ${rewrite} again (${ratio} a warm one)` })
    else if (k.state === 'warm' && k.leftMs !== null)
      out.push({
        level: k.level,
        head: k.level === 'act' ? `Cache cools in ${mmss(k.leftMs)}` : `Cache ${mmss(k.leftMs)} left`,
        advice: k.isRebuilt
          ? rebuiltNote(k)
          : `a reply now keeps it; a cold turn writes ${rewrite} again (${ratio} a warm one)`,
      })
  }
  const f = v.five
  if (f && f.level !== 'calm') {
    const reset = f.resetInMs !== null ? `resets in ${span(f.resetInMs)}` : 'reset unknown'
    if (f.fullAt !== null && f.resetInMs !== null && f.fullAt < v.now + f.resetInMs)
      out.push({
        level: 'act',
        head: `5h limit hits 100% at ${clock(f.fullAt, v.meta)}`,
        advice: `${reset} · pace ${rate(f.ratePerHour)} vs budget ${rate(f.evenPerHour)} · hold subagents`,
      })
    else if (f.pct >= 70) out.push({ level: f.level, head: `5h limit ${pct0(f.pct)}`, advice: reset })
    else out.push({ level: f.level, head: `5h pace ${rate(f.ratePerHour)}`, advice: `above the budget ${rate(f.evenPerHour)} · ${pct0(f.pct)} used, ${reset}` })
  }
  return out.sort((a, b) => (a.level === b.level ? 0 : a.level === 'act' ? -1 : 1))
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
type Group = 'context' | 'cache' | 'limits'
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

function cacheCard(v: View): Card {
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
    ['Countdown', expires, k.state === 'warm' ? k.level : undefined],
    ['TTL', k.regime ?? '5m?'],
    // What the next turn writes again once the cache has expired, and how much dearer that turn is.
    ['If cold', k.rewrite !== null ? `${kilo(k.rewrite)} (${ratio})` : 'n/a'],
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
function paceRows(ratePerHour: number | null, budgetPerHour: number | null, needs: string, alarm: Level, beside: Rows): Rows {
  const isOver = ratePerHour !== null && budgetPerHour !== null && ratePerHour > budgetPerHour
  return [
    ['Pace', ratePerHour !== null ? rate(ratePerHour) : `n/a (needs ${needs})`, isOver ? alarm : undefined],
    ...(beside.length ? beside : [GAP]),
    ['Budget', budgetPerHour !== null ? rate(budgetPerHour) : 'n/a (reset unknown)'],
  ]
}

// An empty cell: keeps Budget under Pace in the desktop card's two columns.
const GAP: [string, string] = ['', '']

// Said where the person looks: the limits file could not tell whose figures these are.
const accountRows = (isUnknown: boolean): Rows => (isUnknown ? [['Account', 'unknown', 'watch']] : [])

function fiveCard(v: View): Card {
  const f = v.five
  if (!f) return { title: '5h limit', rows: [], bullets: ['No reading yet.'] }
  const isShort = f.fullAt !== null && f.resetInMs !== null && f.fullAt < v.now + f.resetInMs
  // Where the current pace leaves the window at its reset.
  const projected = f.ratePerHour !== null && f.resetInMs !== null ? f.pct + f.ratePerHour * (f.resetInMs / HOUR_MS) : null
  const forecast: Rows =
    isShort && f.fullAt !== null
      ? [['Runs out', clock(f.fullAt, v.meta), 'act']]
      : [['At reset', projected !== null ? `~${pct0(projected)}` : 'n/a', projected !== null && projected >= 80 ? 'watch' : undefined]]
  const rows: Rows = [
    ['Used', pct0(f.pct), f.level],
    ['Resets in', f.resetInMs !== null ? span(f.resetInMs) : 'n/a'],
    ...paceRows(f.ratePerHour, f.evenPerHour, '10 min', isShort ? 'act' : 'watch', forecast),
    ...accountRows(v.isAccountUnknown),
  ]
  const tick = paceOf(f.resetInMs, 5 * HOUR_MS)
  // Whole percents: the card is part of the desktop key, and a finer tick would redraw the row every second.
  const bar = { used: Math.round(f.pct), ...(projected !== null ? { projected: Math.round(projected) } : {}), tick: tick === null ? null : Math.round(tick), level: f.level }
  const advice =
    f.level === 'calm'
      ? null
      : isShort
        ? 'Runs out before the reset at this pace: hold subagents.'
        : f.pct >= 70
          ? `${pct0(f.pct)} used: keep subagents for what needs them.`
          : 'Pace is above budget.'
  const explain = ['Pace: last 90 min. Budget: the pace that lasts to the reset.', 'Bar: used, paler to the reset at this pace; tick: even pace now.']
  return { title: '5h limit', level: f.level, rows, ...noted(advice, explain), bar }
}

function weekCard(v: View): Card {
  const w = v.week
  // What even pace would have spent by now, and how far the week is from it, in points and in hours
  // of budget (a week spends 100% over 168 h): Spare below it, uncoloured; Overspent past it, yellow,
  // and red past a day.
  const even = w ? paceOf(w.resetAt !== null ? Math.max(0, w.resetAt - v.now) : null, WEEK_MS) : null
  const lead = w && even !== null ? w.pct - even : null
  const leadText =
    lead === null ? 'n/a' : `${Math.round(Math.abs(lead))} pts (${span(Math.round(((Math.abs(lead) / 100) * WEEK_MS) / 60_000) * 60_000)})`
  const rows: Rows = w
    ? [
        ['Used', pct0(w.pct), w.level],
        ['Resets', w.resetAt !== null ? clock(w.resetAt, v.meta, true) : 'n/a'],
        ...paceRows(w.ratePerHour, w.evenPerHour, '1 h', 'watch', [['Week elapsed', even !== null ? pct0(even) : 'n/a']]),
        [lead !== null && lead > 0 ? 'Overspent' : 'Spare', leadText, lead === null || lead <= 0 ? undefined : lead > 100 / 7 ? 'act' : 'watch'],
        ...accountRows(v.isAccountUnknown),
      ]
    : []
  const advice = w && w.level !== 'calm' ? `${pct0(w.pct)} used: save it for work that needs it.` : null
  const explain = ['Spare / Overspent: below or past even pace, in points and hours.', 'Pace: last 6 h. Budget: the pace that lasts to the reset.', 'Bar: used; tick: even pace now.']
  const bar = w ? { used: Math.round(w.pct), tick: even === null ? null : Math.round(even), level: w.level } : undefined
  return { title: 'Week', level: w?.level ?? 'calm', rows, ...noted(advice, explain), ...(bar ? { bar } : {}) }
}

function scopedCard(v: View, b: View['scoped'][number]): Card {
  const own = b.resetsAt ? Date.parse(b.resetsAt) : NaN
  const resetAt = Number.isFinite(own) ? own : (v.week?.resetAt ?? null)
  return {
    title: b.name,
    level: b.level,
    rows: [
      ['Used', pct0(b.percentUsed), b.level],
      ['Resets', resetAt !== null ? clock(resetAt, v.meta, true) : 'n/a'],
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

// The readings both rows draw, with each one's detail card.
export function readings(v: View): { gauges: Gauge[]; info: Card; advisor: Card | null; advisorOn: string | null } {
  const m = v.meta
  const c = v.context
  const f = v.five
  const k = v.cache
  const gauges: Gauge[] = [
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
      card: cacheCard(v),
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
      reset: f?.resetInMs != null ? span(f.resetInMs) : undefined,
      card: fiveCard(v),
    },
    ...(v.week
      ? [
          {
            key: 'week',
            group: 'limits' as Group,
            label: '7d',
            pct: v.week.pct,
            level: v.week.level,
            value: pct0(v.week.pct),
            pace: paceOf(v.week.resetAt !== null ? Math.max(0, v.week.resetAt - v.now) : null, 7 * 24 * HOUR_MS),
            reset: v.week.resetAt !== null ? span(Math.max(0, v.week.resetAt - v.now)) : undefined,
            card: weekCard(v),
          },
        ]
      : []),
    ...v.scoped.map(b => ({
      key: `scoped-${b.name}`,
      group: 'limits' as Group,
      label: b.name,
      pct: b.percentUsed,
      level: b.level,
      value: pct0(b.percentUsed),
      card: scopedCard(v, b),
    })),
  ]
  const advisorOn = m?.advisor ? advisorText(m) : null
  const isInactive = advisorOn?.endsWith(' (inactive)') ?? false
  const info: Card = m
    ? {
        title: displayModel(m.model),
        rows: [
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

export const GROUPS: Group[] = ['context', 'cache', 'limits']

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
    gauges.map(g => [g.key, g.pct === null ? null : Math.round(g.pct / 2), g.pace == null ? g.pace : Math.round(g.pace)]),
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

// The desktop row's width in cells: each reading's parts one cell apart inside one cell of padding
// a side, the rules between groups, the advisor group, and ⓘ with its margin.
export function desktopCells(v: View, style: LimitStyle): number {
  const { gauges, advisorOn } = readings(v)
  const width = (items: (number | string)[]) =>
    items.reduce<number>((sum, x) => sum + (typeof x === 'number' ? x / PX_PER_CELL : x.length * CELLS_PER_CHAR), 0) + items.length - 1 + 2
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
  return cells + parts * (RULE_PX / PX_PER_CELL) + width(['ⓘ'])
}

// The fullest limit style that keeps the row on one line; the time to reset goes first, since the
// card still shows it.
export function limitStyle(v: View, columns: number): LimitStyle {
  const fits = (style: LimitStyle) => desktopCells(v, style) <= columns - 1
  return fits('bars') ? 'bars' : fits('bare') ? 'bare' : 'pies'
}
