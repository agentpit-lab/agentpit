import type { StatusMeta } from '../types'
import { barSvg, cacheIconSvg, type Card, type CardBar, DESKTOP_COLD, DESKTOP_LEVEL, DESKTOP_ON, pieSvg, ruleSvg, type Tone, wideBarSvg } from './desktop'
import type { CacheView, Level, View } from './view'

// The element tables differ per surface; these trees use only what all of them share,
// plus Svg where `isRemote` says the surface has it.
type Ctor = (props: any) => any
export type Els = { Box: Ctor; Text: Ctor; Svg?: Ctor; Button?: Ctor }

const LABEL = '#85877f'
const VALUE = '#cdcfc9'
const DIM = '#6e706b'
const RULE = '#4a4a4a'
const LEVEL: Record<Level, string> = { calm: '#a9aba5', watch: '#e0b000', act: '#f05a4f' }
// The terminal's counterparts of DESKTOP_COLD and DESKTOP_ON.
const COLD = '#5fd7ff'
const ON = '#5faf5f'
// Marks a context reading whose auto-compact is on; nothing marks it off.
const AUTO_MARK = '↻'
const MODEL_COLORS: [RegExp, string][] = [
  [/opus/i, '#d787ff'],
  [/sonnet/i, '#5fafff'],
  [/fable/i, '#ff87d7'],
  [/haiku/i, '#87d7af'],
]
const EFFORT_COLORS: Record<string, string> = {
  low: '#ffc107',
  medium: '#4eba65',
  high: '#b1b9f9',
  xhigh: '#d0b4ff',
  max: '#eb5f57',
}
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
type Part = { text: string; color?: string; bold?: boolean } | { wide: CardBar; cells: number }
type Row = Part[]

const t = (text: string, color = VALUE, bold = false): Part => ({ text, color, bold })
const dim = (text: string): Part => ({ text, color: DIM })
const label = (text: string): Part => ({ text, color: LABEL })
const SEP = dim(' · ')
const BAR = { text: '  │  ', color: RULE } as Part

export const displayModel = (id: string) => {
  const m = /^claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?(\[1m\])?$/i.exec(id.trim())
  if (!m || !m[1] || !m[2]) return FAMILIES[id.toLowerCase()] ?? id
  const family = FAMILIES[m[1].toLowerCase()] ?? m[1]
  return `${family} ${m[2]}${m[3] ? `.${m[3]}` : ''}`
}

// advisor_rank from the model catalog: the advisor only activates when its rank is at least the
// main model's; an unknown rank means active.
const rankOf = (model: string) => RANKS.find(([re]) => re.test(model.toLowerCase().replace(/\./g, '-')))?.[1]
const modelColor = (name: string) => MODEL_COLORS.find(([re]) => re.test(name))?.[1] ?? '#ffffff'

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

function clock(at: number, locale: string | null, weekday = false) {
  const options: Intl.DateTimeFormatOptions = { hour: '2-digit', minute: '2-digit', hour12: false }
  if (weekday) options.weekday = 'short'
  try {
    return new Date(at).toLocaleString(locale ?? undefined, options)
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
          ? `~${c.turnsLeft} turns to auto-compact · finish the step, or /compact at a clean point`
          : 'auto-compact is near · /compact at a clean point',
    })
  }
  const k = v.cache
  if (k.level !== 'calm') {
    const rewrite = k.rewrite !== null ? `${kilo(k.rewrite)} tokens` : 'the whole context'
    if (k.state === 'cold') out.push({ level: k.level, head: 'Cache is cold', advice: `the next turn writes ${rewrite} again · batch what you send next` })
    else if (k.state === 'warm' && k.leftMs !== null)
      out.push({
        level: k.level,
        head: k.level === 'act' ? `Cache cools in ${mmss(k.leftMs)}` : `Cache ${mmss(k.leftMs)} left`,
        advice: k.isRebuilt
          ? rebuiltNote(k)
          : `a reply now keeps it; a cold turn writes ${rewrite} again (~20× a warm one)`,
      })
  }
  const f = v.five
  if (f && f.level !== 'calm') {
    const reset = f.resetInMs !== null ? `resets in ${span(f.resetInMs)}` : 'reset unknown'
    if (f.fullAt !== null && f.resetInMs !== null && f.fullAt < v.now + f.resetInMs)
      out.push({
        level: 'act',
        head: `5h limit hits 100% at ${clock(f.fullAt, v.meta?.locale ?? null)}`,
        advice: `${reset} · pace ${rate(f.ratePerHour)} vs budget ${rate(f.evenPerHour)} · hold subagents`,
      })
    else if (f.pct >= 70) out.push({ level: f.level, head: `5h limit ${pct0(f.pct)}`, advice: reset })
    else out.push({ level: f.level, head: `5h pace ${rate(f.ratePerHour)}`, advice: `above the budget ${rate(f.evenPerHour)} · ${pct0(f.pct)} used, ${reset}` })
  }
  return out.sort((a, b) => (a.level === b.level ? 0 : a.level === 'act' ? -1 : 1))
}

function drawRow(els: Els, row: Row, isRemote: boolean, key: string) {
  const { Box, Text, Svg } = els
  if (!isRemote || !Svg) {
    return (
      <Text key={key} wrap="truncate-end">
        {row.map((p, i) =>
          'wide' in p ? (
            <Text key={String(i)}>{wideCells(Text, p.wide, p.cells)}</Text>
          ) : (
            <Text key={String(i)} color={p.color} bold={p.bold}>
              {p.text}
            </Text>
          ),
        )}
      </Text>
    )
  }
  // Remote surfaces lay a row out in flex boxes, where each text run would wrap on its own.
  // A row is split at its `│` rules into groups that never break inside; only the gaps wrap.
  const groups: Part[][] = [[]]
  for (const p of row) {
    if (p === BAR) groups.push([])
    else groups[groups.length - 1]?.push(p)
  }
  const group = (parts: Part[], gk: string) => {
    const items: any[] = []
    let run: Part[] = []
    const flush = () => {
      if (!run.length) return
      items.push(
        <Text key={`t${items.length}`} wrap="truncate-end">
          {run.map((p, i) =>
            'text' in p ? (
              <Text key={String(i)} color={p.color} bold={p.bold}>
                {p.text}
              </Text>
            ) : null,
          )}
        </Text>,
      )
      run = []
    }
    for (const p of parts) {
      if ('wide' in p) {
        flush()
        const bar = wideBarSvg(p.wide, p.cells * 8)
        items.push(<Svg key={`w${items.length}`} source={bar.source} alt={bar.alt} width={bar.width} height={bar.height} />)
      } else run.push(p)
    }
    flush()
    return (
      <Box key={gk} flexDirection="row" alignItems="center" flexShrink={0} columnGap={1}>
        {items}
      </Box>
    )
  }
  return (
    <Box key={key} flexDirection="row" flexWrap="wrap" alignItems="center" columnGap={3}>
      {groups.filter(g => g.length).map((g, i) => group(g, `${key}-${i}`))}
    </Box>
  )
}

// The wide bar in cells: used in the level's colour, the projection dim, the even-pace tick as ┃.
function wideCells(Text: Ctor, b: CardBar, cells: number) {
  const at = (pct: number) => Math.round((Math.max(0, Math.min(100, pct)) / 100) * cells)
  const used = at(b.used)
  const projected = Math.max(used, b.projected !== undefined ? at(b.projected) : used)
  const tick = b.tick === null ? -1 : Math.min(cells - 1, at(b.tick))
  return Array.from({ length: cells }, (_, i) => (
    <Text key={String(i)} color={i === tick ? VALUE : i < used ? LEVEL[b.level] : i < projected ? DIM : RULE}>
      {i === tick ? '┃' : i < projected ? '━' : '─'}
    </Text>
  ))
}

// The pane: every card of the row, unfolded in the row's order, with all of its explanation; the
// mobile app's only view. What needs a decision leads, and only while something does.

// A pane bar spans the readings column, labels and values, within these bounds.
const PANE_BAR_MIN = 20
const PANE_BAR_MAX = 40
// Cells between a reading's label and its value.
const PANE_LABEL_GAP = 2

// Where readings run as one list (the pane, a CLI detail line) Budget follows Pace, as it stands
// under Pace in the card's two columns.
const listOrder = (rows: Card['rows']) => {
  const filled = rows.filter(([l]) => l)
  const budget = filled.find(([l]) => l === 'Budget')
  const at = filled.findIndex(([l]) => l === 'Pace')
  if (!budget || at < 0) return filled
  const rest = filled.filter(r => r !== budget)
  return [...rest.slice(0, at + 1), budget, ...rest.slice(at + 1)]
}

const toneColor = (tone?: Tone) => (tone === 'on' ? ON : tone && tone !== 'calm' ? LEVEL[tone] : VALUE)

export function drawPane(els: Els, v: View, isRemote: boolean) {
  const { Box, Text } = els
  const { gauges, info, advisor } = readings(v)
  const m = v.meta
  // A section's lines: a Row drawn as text runs, or a node laid out on its own (the readings grid).
  const sections: { key: string; rows: (Row | { node: any })[] }[] = []
  const urgent = alerts(v)
  const extras = (card: Card) => [...card.bullets, ...(isRemote ? (card.desktopBullets ?? []) : [])].filter(b => b !== 'Full detail: /pitboard')
  // One reading to a line, every card's labels in one column as wide as the longest, values left
  // after it: the pane reads top to bottom as one list, aligned in any font and at any width.
  const cards = [...gauges.map(g => g.card), ...(advisor ? [advisor] : []), info]
  const labelWidth = Math.max(0, ...cards.flatMap(c => c.rows.map(([l]) => l.length))) + PANE_LABEL_GAP
  const valueWidth = Math.max(0, ...cards.flatMap(c => c.rows.map(([, val]) => val.length)))
  const barCells = Math.min(PANE_BAR_MAX, Math.max(PANE_BAR_MIN, labelWidth + valueWidth))
  const grid = (key: string, card: Card) =>
    listOrder(card.rows)
      .map(([l, val, tone], i) => ({
        node: (
          <Box key={`${key}-r${i}`} flexDirection="row" paddingLeft={2}>
            <Box width={labelWidth} flexShrink={0}>
              <Text color={LABEL}>{l}</Text>
            </Box>
            <Text color={toneColor(tone)}>{val}</Text>
          </Box>
        ),
      }))
  // Bullets wrap under their own indent; the pane is read, not glanced at.
  const bullet = (key: string, text: string) => ({
    node: (
      <Box key={key} flexDirection="row" alignItems="flex-start" paddingLeft={2}>
        <Text color={DIM}>{'• '}</Text>
        <Text color={DIM} wrap="wrap">
          {text}
        </Text>
      </Box>
    ),
  })
  // What needs a decision leads: each warning's head on its own line, its advice wrapped under it.
  if (urgent.length)
    sections.push({
      key: 'attention',
      rows: [
        [t('Needs attention', LEVEL[urgent[0]?.level ?? 'watch'], true)],
        ...urgent.flatMap((a, i): (Row | { node: any })[] => [
          [t(a.head, LEVEL[a.level], true)],
          {
            node: (
              <Box key={`attention-${i}`} paddingLeft={2}>
                <Text color={VALUE} wrap="wrap">
                  {a.advice}
                </Text>
              </Box>
            ),
          },
        ]),
      ],
    })
  const unfold = (key: string, card: Card, head: Row) => {
    const rows: (Row | { node: any })[] = [[t(card.title, card.level && card.level !== 'calm' ? LEVEL[card.level] : VALUE, true), ...head], ...grid(key, card)]
    if (card.bar) rows.push([dim('  '), { wide: card.bar, cells: barCells }])
    extras(card).forEach((b, i) => rows.push(bullet(`${key}-b${i}`, b)))
    sections.push({ key, rows })
  }
  for (const g of gauges) {
    const head: Row = g.icon ? [t(` ${g.icon === 'hot' ? '🔥' : '❄'}`, g.icon === 'cold' ? COLD : LEVEL.act)] : g.mark ? [t(` ${g.mark}`, ON)] : []
    unfold(g.key, g.card, head)
    if (g.key === 'context' && advisor && m?.advisor) unfold('advisor', advisor, [])
  }
  // ⓘ's card is titled by the model; the pane names the section and keeps the model beside it.
  if (m) unfold('info', { ...info, title: 'Session' }, [t(` ${info.title}`, modelColor(info.title))])
  return (
    <Box flexDirection="column">
      {sections.map(s => (
        <Box key={s.key} flexDirection="column" marginBottom={1}>
          {s.rows.map((row, i) => ('node' in row ? row.node : drawRow(els, row, isRemote, `${s.key}-${i}`)))}
        </Box>
      ))}
    </Box>
  )
}

// Desktop: one row of filled pies the size of the surface's own context ring. A calm pie is grey
// and silent; past a threshold it takes the level's colour and shows one number. A click on a
// reading's name gives it a detail card, shown while the pointer is over it; another click
// switches or removes it. Cards on every reading were dropped: desktop keeps a revealed card up
// after the pointer leaves, so several showed at once. Readings sit in groups (context | cache | limits) split by a thin rule.
// `opens` names the detail a label opens when two labels share one (TTL and Hit open the cache).
type Group = 'context' | 'cache' | 'limits'
type Gauge = {
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

function contextCard(v: View): Card {
  const c = v.context
  if (!c) return { title: 'Context', rows: [], bullets: ['No response in this window yet.'] }
  const hasTrigger = c.isAuto && c.limit < c.window
  const isNarrowed = c.compactWindow !== null && c.compactWindow < c.window
  // Who narrowed the window; the Window row carries its size.
  const via = isNarrowed ? ` (${SOURCES[c.compactSource ?? ''] ?? c.compactSource ?? 'settings'})` : ''
  const rows: Rows = [
    ['Auto-compact', c.isAuto ? `on${via}` : 'off', c.isAuto ? 'on' : undefined],
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
        ? 'Auto-compact is off: /compact before the window fills.'
        : 'Finish the step, or /compact at a clean point.'
  const explain = hasTrigger
    ? [isNarrowed ? 'Auto-compact uses a smaller window than the model; % counts toward it.' : 'Compacts at the window minus the buffer; % counts toward it.']
    : [c.isAuto ? '% counts the full window.' : '% counts the full window; /compact by hand.']
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
  // A cold turn writes the context at 1.25x (5m) or 2x (1h) the input price; a warm one reads it at 0.1x.
  const ratio = k.regime === '1h' ? 20 : 12.5
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
    ['If cold', k.rewrite !== null ? `${kilo(k.rewrite)} (~${ratio}×)` : 'n/a'],
  ]
  const advice =
    k.state === 'cold'
      ? 'The next turn rewrites the context: batch what you send.'
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

function fiveCard(v: View): Card {
  const f = v.five
  if (!f) return { title: '5h limit', rows: [], bullets: ['No reading yet.'] }
  const isShort = f.fullAt !== null && f.resetInMs !== null && f.fullAt < v.now + f.resetInMs
  // Where the current pace leaves the window at its reset.
  const projected = f.ratePerHour !== null && f.resetInMs !== null ? f.pct + f.ratePerHour * (f.resetInMs / HOUR_MS) : null
  const forecast: Rows =
    isShort && f.fullAt !== null
      ? [['Runs out', clock(f.fullAt, v.meta?.locale ?? null), 'act']]
      : [['At reset', projected !== null ? `~${pct0(projected)}` : 'n/a', projected !== null && projected >= 80 ? 'watch' : undefined]]
  const rows: Rows = [
    ['Used', pct0(f.pct), f.level],
    ['Resets in', f.resetInMs !== null ? span(f.resetInMs) : 'n/a'],
    ...paceRows(f.ratePerHour, f.evenPerHour, '10 min', isShort ? 'act' : 'watch', forecast),
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
        ['Resets', w.resetAt !== null ? clock(w.resetAt, v.meta?.locale ?? null, true) : 'n/a'],
        ...paceRows(w.ratePerHour, w.evenPerHour, '1 h', 'watch', [['Week elapsed', even !== null ? pct0(even) : 'n/a']]),
        [lead !== null && lead > 0 ? 'Overspent' : 'Spare', leadText, lead === null || lead <= 0 ? undefined : lead > 100 / 7 ? 'act' : 'watch'],
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
      ['Resets', resetAt !== null ? clock(resetAt, v.meta?.locale ?? null, true) : 'n/a'],
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

const GROUPS: Group[] = ['context', 'cache', 'limits']

const cardFor = (gauges: Gauge[], info: Card, key: string | null) => {
  if (key === 'info') return info
  const g = gauges.find(x => x.key === key)
  if (!g) return undefined
  return g.opens ? (gauges.find(x => x.key === g.opens)?.card ?? g.card) : g.card
}

// The advisor as both rows show it: its name, and whether its rank leaves it inactive.
function advisorView(advisorOn: string | null) {
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
// pointer leave, so cards stuck open.
export function desktopKey(v: View, open: string | null, cards?: View): string {
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
    shown.gauges.map(g => [g.key, g.label, g.level, g.value, g.extra, g.tone, g.reset]),
    shown.advisorOn,
    v.meta !== null,
    open,
    hidden.gauges.map(g => cardFor(hidden.gauges, hidden.info, g.key) ?? g.card),
    hidden.info,
    hidden.advisor,
  ])
}

// Every reading carries its card, shown while the pointer is over the reading. The redraw every
// second used to keep desktop from noticing the pointer leave, so cards stayed up; with the row
// redrawn only on a change, hover alone is tried again.
const HOVER_CARDS = true
// Desktop frames the popover at about 420 px and clips past it; its text runs wider than a cell a
// character. A bar or wrapped advice takes at most this many cells.
const NATIVE_CARD_CELLS = 44
// The narrowest a card gets, and desktop cells a character of its text takes (estimated from screenshots).
const NATIVE_CARD_MIN_CELLS = 26
const NATIVE_CELLS_PER_CHAR = 1.15
// A faint grey wash under the hovered reading: a border takes a whole cell on each side and wraps
// the row, a background takes no room. Half-transparent grey shades a light band and lifts a dark one.
const HOVER_FRAME = { backgroundColor: '#8080801f' }

// Desktop lays the row out in cells of about 8 px; its proportional text runs about 0.9 cell a
// character. Measured on Desktop at 95 columns, where the row with a cache countdown wraps and
// the row without it fits.
const PX_PER_CELL = 8
const CELLS_PER_CHAR = 0.9
const PIE_PX = 16
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
    ...(g.pct !== null ? [PIE_PX] : []),
    ...(isLoud && g.value && !g.icon ? [g.value] : []),
    ...(g.mark ? [g.mark] : []),
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

export function drawDesktop(els: Els, v: View, open: string | null, toggle: (key: string) => void, columns = Infinity) {
  const { Box, Button, Svg, Text } = els
  const { gauges, info, advisor, advisorOn } = readings(v)
  const style = limitStyle(v, columns)
  const bars = style !== 'pies'
  const name = (key: string, label: string) =>
    HOVER_CARDS ? (
      <Text key={`n-${key}`} dimColor>
        {label}
      </Text>
    ) : Button ? (
      <Button key={`b-${key}`} label={label} plain dimColor={open !== key} onPress={() => toggle(key)} />
    ) : (
      <Text dimColor>{label}</Text>
    )
  const content = (g: Gauge) => {
    if (bars && g.pace !== undefined) {
      const bar = barSvg(g.pct ?? 0, g.level, g.pace)
      return [
        name(g.key, g.label),
        Svg ? <Svg key={`bar-${g.key}`} source={bar.source} alt={`${g.label} ${bar.alt}`} width={bar.width} height={bar.height} /> : null,
        g.value ? (
          <Text key={`v-${g.key}`} bold color={g.level === 'calm' ? undefined : DESKTOP_LEVEL[g.level]}>
            {g.value}
          </Text>
        ) : null,
        style === 'bars' && g.reset ? (
          <Text key={`r-${g.key}`} dimColor>
            {g.reset}
          </Text>
        ) : null,
      ]
    }
    const ring = g.icon ? cacheIconSvg(g.icon) : g.pct === null ? null : pieSvg(g.pct, g.level)
    return [
      name(g.key, g.label),
      ring && Svg ? <Svg key={`pie-${g.key}`} source={ring.source} alt={`${g.label} ${g.icon ? ring.alt : (g.value ?? 'n/a')}`} width={ring.width} height={ring.height} /> : null,
      g.level !== 'calm' && g.value && !g.icon ? (
        <Text key={`v-${g.key}`} bold color={g.tone === 'cold' ? DESKTOP_COLD : DESKTOP_LEVEL[g.level]}>
          {g.value}
        </Text>
      ) : null,
      g.mark ? (
        <Text key={`m-${g.key}`} color={DESKTOP_ON}>
          {g.mark}
        </Text>
      ) : null,
      g.extra ? (
        <Text key={`x-${g.key}`} dimColor>
          {g.extra}
        </Text>
      ) : null,
      g.level !== 'calm' && g.reset ? (
        <Text key={`r-${g.key}`} dimColor>
          {g.reset}
        </Text>
      ) : null,
    ]
  }
  // A reading that opens another's card (Hit opens the cache's) is drawn inside that reading's Box,
  // so the two are one hover area with one card. The frame shows which reading the card belongs to.
  const reading = (g: Gauge) => (
    <Box key={g.key} position="relative" flexDirection="row" alignItems="center" columnGap={1} paddingX={1} flexShrink={0} hover={HOVER_FRAME}>
      {[g, ...gauges.filter(x => x.opens === g.key)].flatMap(content)}
      {HOVER_CARDS || open === g.key ? popover(cardFor(gauges, info, g.key) ?? g.card) : null}
    </Box>
  )
  // Only the opened reading has a card, revealed by the surface's hover popover (the only element
  // desktop frames and lets past the band's edge): one card at most, pointing at its reading.
  const popover = (card: Card) => nativeCard(card)
  // The card in desktop's own Box and Text: its font at its size, laid out by desktop, so nothing is
  // scaled. A title, one reading to a line with the value right-aligned, a limit's bar, and advice.
  const toneOf = (tone?: Tone) => (tone === 'on' ? DESKTOP_ON : tone && tone !== 'calm' ? DESKTOP_LEVEL[tone] : undefined)
  const nativeCard = (card: Card) => {
    // One reading to a line in the list order the pane uses: desktop's popover is about 420 px wide
    // and clips what runs past it, and one column always fits.
    const rows = listOrder(card.rows)
    const single = { label: Math.max(0, ...rows.map(([l]) => l.length)), value: Math.max(0, ...rows.map(([, val]) => val.length)) }
    // A line spans the card, its value at the right edge, in line with the end of a bar or advice.
    const lines = rows.map(([l, val, tone], i) => (
      <Box key={`s${i}`} flexDirection="row">
        <Box minWidth={single.label + 2} flexGrow={1} flexShrink={0}>
          <Text dimColor>{l}</Text>
        </Box>
        <Box minWidth={single.value} flexShrink={0} justifyContent="flex-end">
          <Text bold color={toneOf(tone)}>
            {val}
          </Text>
        </Box>
      </Box>
    ))
    // The bar and the advice take the readings' width, so the popover stays as narrow as they are.
    const cellsWide = Math.min(NATIVE_CARD_CELLS, Math.max(NATIVE_CARD_MIN_CELLS, Math.round((single.label + 2 + single.value) * NATIVE_CELLS_PER_CHAR)))
    const bar = card.bar ? wideBarSvg(card.bar, cellsWide * 8) : null
    // Explanations live in the pane; a card says only what to do when something needs it.
    const bullets = card.advice ? [card.advice] : []
    return (
      <Box position="absolute" top={0} left={0} display="none" hover={{ display: 'flex' }}>
        <Box flexDirection="column">
          <Text bold color={card.level && card.level !== 'calm' ? DESKTOP_LEVEL[card.level] : undefined}>
            {card.title}
          </Text>
          {lines}
          {bar && Svg ? <Svg source={bar.source} alt={bar.alt} width={bar.width} height={bar.height} /> : null}
          {bullets.map((b, n) => (
            <Box key={`b${n}`} width={cellsWide}>
              <Text dimColor wrap="wrap">
                {b}
              </Text>
            </Box>
          ))}
        </Box>
      </Box>
    )
  }
  const groups = GROUPS.map(id => gauges.filter(g => g.group === id)).filter(gs => gs.length)
  const rule = ruleSvg()
  // The advisor sits between context and cache, as on the terminal; model and effort are the
  // surface's own, under the prompt.
  const adv = advisorView(advisorOn)
  // Shown only while an advisor is set: off is the usual state and stays in ⓘ.
  const advisorGroup = v.meta?.advisor ? (
    <Box key="group-advisor" position="relative" flexDirection="row" alignItems="center" columnGap={1} paddingX={1} flexShrink={0} hover={HOVER_FRAME}>
      <Text dimColor>Advisor</Text>
      {adv.name ? (
        <Text color={modelColor(adv.name)} dimColor={adv.isInactive}>
          {adv.name}
        </Text>
      ) : (
        <Text dimColor>off</Text>
      )}
      {adv.isInactive ? <Text dimColor>(inactive)</Text> : null}
      {advisor ? popover(advisor) : null}
    </Box>
  ) : null
  const parts = groups.map(gs => (
    <Box key={`group-${gs[0]?.group}`} flexDirection="row" alignItems="center" flexShrink={0}>
      {gs
        .filter(g => !g.opens)
        .flatMap((g, i) => [
          ...(i > 0 ? [<Text key={`dot-${g.key}`} dimColor>·</Text>] : []),
          reading(g),
        ])}
    </Box>
  ))
  if (advisorGroup) parts.splice(groups[0]?.[0]?.group === 'context' ? 1 : 0, 0, advisorGroup)
  return (
    <Box flexDirection="column">
      {/* No gaps between parts: each reading's own padding spaces it and widens its hover wash. */}
      <Box flexDirection="row" flexWrap="wrap" alignItems="center">
        {parts.flatMap((part, i) => [
          ...(i > 0 && Svg ? [<Svg key={`rule-${i}`} source={rule.source} alt={rule.alt} width={rule.width} height={rule.height} />] : []),
          part,
        ])}
        {Svg ? <Svg key="rule-info" source={rule.source} alt={rule.alt} width={rule.width} height={rule.height} /> : null}
        <Box key="info" position="relative" flexShrink={0} paddingX={1} hover={HOVER_FRAME}>
          {name('info', 'ⓘ')}
          {HOVER_CARDS || open === 'info' ? popover(info) : null}
        </Box>
      </Box>
    </Box>
  )
}

// Terminal: the same row in cells. A gauge is two cells of a block that rises in eighths over a
// grey track: a whole cell tall, where a font draws pie glyphs (◔ ◑ ◕) small. In the level's
// colour past a threshold; a click (or Enter under the focus) on a name opens its detail as one
// line under the row.
const EIGHTHS = [' ', '▁', '▂', '▃', '▄', '▅', '▆', '▇', '█']
// Theme keys, so the gauge follows a light or dark terminal: the track is the theme's subtle
// grey (light rgb(175,175,175), dark rgb(80,80,80)) and a calm fill its inactive grey (light
// rgb(102,102,102), dark rgb(153,153,153)), which stands out from the track on either.
const TRACK = 'subtle'
const CALM_FILL = 'inactive'
const gaugeCells = (pct: number) => (EIGHTHS[Math.round(Math.max(0, Math.min(100, pct)) / 12.5)] ?? ' ').repeat(2)
// The cache icons, two cells like a gauge: the flame is a wide glyph, the snowflake a narrow one.
const ICON_CELLS = { hot: '🔥', cold: '❄ ' }
const shownValue = (g: Gauge) => g.level !== 'calm' && g.value !== null && !g.icon

// The terminal row's width in cells, from the same pieces drawTerminal draws, so the rule above spans
// the row alone. Gaps: 1 inside a reading, 1 + · + 1 between readings, 1 + │ + 1 between groups; ⓘ
// takes 2, and the rule runs 1 past it so ⓘ sits as far from its end as from the │ before it.
export function terminalCells(v: View): number {
  const { gauges, advisorOn } = readings(v)
  const readingWidth = (g: Gauge) =>
    [g.label.length, g.pct !== null || g.icon ? 2 : null, shownValue(g) && g.value ? g.value.length : null, g.mark ? 1 : null, g.extra?.length ?? null]
      .filter((n): n is number => n !== null)
      .reduce((sum, n, i) => sum + n + (i > 0 ? 1 : 0), 0)
  const groups = GROUPS.map(id => gauges.filter(g => g.group === id)).filter(gs => gs.length)
  // Readings within a group: gap, dot, gap.
  const groupWidths = groups.map(gs => gs.reduce((sum, g, i) => sum + readingWidth(g) + (i > 0 ? 3 : 0), 0))
  if (v.meta?.advisor) {
    const adv = advisorView(advisorOn)
    groupWidths.push(['Advisor', adv.name ?? 'off', ...(adv.isInactive ? ['(inactive)'] : [])].reduce((sum, w, i) => sum + w.length + (i > 0 ? 1 : 0), 0))
  }
  return [...groupWidths, 2].reduce((sum, w, i) => sum + w + (i > 0 ? 3 : 0), 0) + 1
}

export function drawTerminal(els: Els, v: View, open: string | null, toggle: (key: string) => void) {
  const { Box, Button, Text } = els
  const { gauges, info, advisor, advisorOn } = readings(v)
  const m = v.meta
  const model = m ? displayModel(m.model) : null
  const name = (key: string, label: string) =>
    Button ? (
      <Button key={`b-${key}`} label={label} plain dimColor={open !== key} onPress={() => toggle(key)} />
    ) : (
      <Text dimColor>{label}</Text>
    )
  // While a detail line is open every block gets a second row, blank except a ▲ under the opened
  // label, so the layout itself keeps the mark aligned whatever sits to its left.
  const pointer = (key: string, offset: number) =>
    open === null ? null : (
      <Text key="pointer" color={VALUE}>
        {open === key ? `${' '.repeat(offset)}▲` : ' '}
      </Text>
    )
  const reading = (g: Gauge) => (
    <Box key={g.key} flexDirection="column" flexShrink={0}>
    <Box flexDirection="row" columnGap={1}>
      {name(g.key, g.label)}
      {g.icon ? (
        <Text color={g.icon === 'cold' ? COLD : LEVEL.act}>{ICON_CELLS[g.icon]}</Text>
      ) : g.pct !== null ? (
        <Text color={g.level === 'calm' ? CALM_FILL : LEVEL[g.level]} backgroundColor={TRACK}>
          {gaugeCells(g.pct)}
        </Text>
      ) : null}
      {shownValue(g) ? (
        <Text bold color={g.tone === 'cold' ? COLD : LEVEL[g.level]}>
          {g.value}
        </Text>
      ) : null}
      {g.mark ? <Text color={ON}>{g.mark}</Text> : null}
      {g.extra ? <Text color={LABEL}>{g.extra}</Text> : null}
    </Box>
      {pointer(g.key, Math.floor((g.label.length - 1) / 2))}
    </Box>
  )
  const groups = GROUPS.map(id => gauges.filter(g => g.group === id)).filter(gs => gs.length)
  // Model and effort are needed rarely, so they live in the ⓘ line; the advisor shows in the row
  // while one is set, and in ⓘ always. In ⓘ each takes its own colour, the advisor its model's.
  const { name: advisorName, isInactive } = advisorView(advisorOn)
  const isInfoOf = (key: string) => key === 'info' && m !== null && model !== null
  const infoColor = (key: string, label: string): { color?: string; dimColor?: boolean } => {
    if (!isInfoOf(key) || !m) return {}
    if (label === 'Effort' && m.effort) return { color: EFFORT_COLORS[m.effort] ?? DIM }
    if (label === 'Advisor' && advisorName) return { color: modelColor(advisorName), dimColor: isInactive }
    return {}
  }
  const parts = groups.map(gs => (
    <Box key={`group-${gs[0]?.group}`} flexDirection="row" columnGap={1} flexShrink={0}>
      {gs.flatMap((g, i) => [...(i > 0 ? [<Text key={`dot-${g.key}`} color={RULE}>·</Text>] : []), reading(g)])}
    </Box>
  ))
  // Shown only while an advisor is set: off is the usual state and stays in ⓘ.
  if (m?.advisor) {
    parts.splice(
      groups[0]?.[0]?.group === 'context' ? 1 : 0,
      0,
      <Box key="group-advisor" flexDirection="column" flexShrink={0}>
        <Box flexDirection="row" columnGap={1}>
          {name('advisor', 'Advisor')}
          {advisorName ? (
            <Text color={modelColor(advisorName)} dimColor={isInactive}>
              {advisorName}
            </Text>
          ) : (
            <Text dimColor>off</Text>
          )}
          {isInactive ? <Text dimColor>(inactive)</Text> : null}
        </Box>
        {pointer('advisor', Math.floor(('Advisor'.length - 1) / 2))}
      </Box>,
    )
  }
  // The session card runs long: from Claude Code on it takes a second line.
  const detailLines = (card: Card) => {
    // A line has no columns to hold open, so the desktop card's empty cells go.
    const rows = listOrder(card.rows)
    const at = rows.findIndex(([label]) => label === 'Claude Code')
    return at > 0 ? [{ rows: rows.slice(0, at) }, { rows: rows.slice(at) }] : [{ rows }]
  }
  const detail = (key: string) => {
    // The advisor's card exists whenever meta does; with no advisor set its line has no label to hang from.
    const card = key === 'advisor' ? (m?.advisor ? advisor : undefined) : cardFor(gauges, info, key)
    if (!card) return null
    const titleColor = isInfoOf(key) && model ? modelColor(model) : undefined
    return (
      <Box key={`detail-${key}`} flexDirection="column">
        {detailLines(card).map((line, n) => (
          <Text key={`line-${n}`} wrap="truncate-end">
            {n === 0 ? (
              <Text bold color={titleColor ?? (card.level && card.level !== 'calm' ? LEVEL[card.level] : VALUE)}>
                {card.title}
              </Text>
            ) : null}
            {line.rows.map(([label, value, level], i) => (
              <Text key={String(i)}>
                <Text color={DIM}>{`${n === 0 || i > 0 ? ' · ' : ''}${label} `}</Text>
                <Text color={level === 'on' ? ON : level && level !== 'calm' ? LEVEL[level] : VALUE} {...infoColor(key, label)}>
                  {value}
                </Text>
              </Text>
            ))}
          </Text>
        ))}
        {/* As on desktop: explanations live in the pane; the line adds only advice, when there is one. */}
        {card.advice ? (
          <Text key="advice" color={DIM} wrap="truncate-end">
            {card.advice}
          </Text>
        ) : null}
      </Box>
    )
  }
  return (
    <Box flexDirection="column" paddingLeft={1}>
      {/* A rule parts the row from the engine's mode line above; a Box has no top-only border. */}
      <Box key="rule" height={1} marginTop={1} overflow="hidden">
        <Text color={RULE}>{'─'.repeat(terminalCells(v))}</Text>
      </Box>
      <Box flexDirection="row" flexWrap="wrap" columnGap={1}>
        {[
          ...parts,
          // The terminal counts ⓘ as one cell but Cascadia Mono draws it wider; the trailing space
          // widens the button so its hover highlight covers the whole glyph.
          <Box key="info" flexDirection="column" flexShrink={0}>
            {name('info', 'ⓘ ')}
            {pointer('info', 0)}
          </Box>,
        ].flatMap((part, i) => [...(i > 0 ? [<Text key={`rule-${i}`} color={RULE}>│</Text>] : []), part])}
      </Box>
      {open !== null ? detail(open) : null}
    </Box>
  )
}
