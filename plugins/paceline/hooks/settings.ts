// Pure part of the settings view: the catalogue of settings, their formatting, the "differs from
// default" rule, the Markdown summary, the style match and the preview line. No engine calls, so
// tests reach it directly. Reads and writes go through the `paceline` CLI (see register.tsx); the
// defaults, values and style tables arrive in the JSON of `paceline settings --json`, so nothing
// here holds a default.

// The longest a paceline command may run (watch.ts, RUN_TIMEOUT_MS), in seconds.
const RUN_SECONDS = 30

export type Kind =
  | 'zone'
  | 'zonemode'
  | 'hours'
  | 'dayhour'
  | 'pct'
  | 'pctw'
  | 'num'
  | 'count'
  | 'mb'
  | 'min'
  | 'days'
  | 'models'
  | 'bool'
  | 'levels'
  | 'floorh'

export type Source = 'profile' | 'context_watch' | 'cache_watch'
export type Tier = 'day' | 'machine' | 'watchers' | 'style' | 'advanced'

export type Item = {
  id: string
  source: Source
  tier: Tier
  topic: string
  label: string
  kind: Kind
  meaning: string
  range: string
}

export const TOPIC = {
  time: 'Time and hours',
  five: 'Five-hour window',
  agents: 'Agents and models',
  machine: 'This machine',
  zone: 'Time zone',
  context: 'context-watch',
  cache: 'cache-watch',
  style: 'Spending style',
} as const

type Make = (id: string, tier: Tier, topic: string, label: string, kind: Kind, range: string, meaning: string) => Item
const make =
  (source: Source): Make =>
  (id, tier, topic, label, kind, range, meaning) => ({ id, source, tier, topic, label, kind, range, meaning })
// One catalogue line per setting; tests/test_settings_catalogue.py reads the `p('<key>'` lines.
const p = make('profile')
const c = make('context_watch')
const w = make('cache_watch')

const PCT = '0-100 %'
const NONNEG = '0 or more'
const WHOLE = 'a whole number, 0 or more'

export const CATALOGUE: readonly Item[] = [
  // You and your day
  p('timezone', 'day', TOPIC.zone, 'Time zone', 'zone', 'an IANA zone name, for example Europe/Madrid', 'The zone every wall-clock rule uses.'),
  p('timezone_mode', 'day', TOPIC.zone, 'Time zone follows', 'zonemode', 'machine or fixed', 'Whether the profile follows this machine’s zone as it changes, or keeps the stored one.'),
  p('day_hours', 'day', TOPIC.time, 'Working day', 'hours', 'two whole hours, start before end, 0-24', 'Hours that get a budget. Start inclusive, end exclusive.'),
  p('night_hours', 'day', TOPIC.time, 'Night queue hours', 'hours', 'two whole hours, start before end, 0-24', 'When the night queue runs. An hour in neither range gets no budget.'),
  p('models', 'day', TOPIC.agents, 'Preferred models', 'models', 'model names, in order of preference', 'Model families in order of preference.'),
  // This machine
  p('min_free_commit_mb', 'machine', TOPIC.machine, 'Minimum free memory', 'mb', WHOLE + ' of MB', 'Free memory below which no new work starts.'),
  p('commit_reserve_mb', 'machine', TOPIC.machine, 'Memory kept aside', 'mb', WHOLE + ' of MB', 'Memory left out when counting how many heavy runs fit.'),
  p('heavy_job_mb', 'machine', TOPIC.machine, 'Memory per heavy run', 'mb', WHOLE + ' of MB', 'Memory one heavy run is assumed to take.'),
  p('max_heavy_runs', 'machine', TOPIC.machine, 'Heavy runs at once, at most', 'count', WHOLE, 'Upper bound on parallel heavy runs, whatever the free memory.'),
  p('max_agents', 'machine', TOPIC.machine, 'Parallel agents, at most', 'count', WHOLE, 'The most parallel agents pace recommends.'),
  // Watchers
  c('line', 'watchers', TOPIC.context, 'Context gauge on every prompt', 'bool', 'on or off', 'One-line context gauge added to every prompt.'),
  c('levels', 'watchers', TOPIC.context, 'Warn at', 'levels', 'whole percentages 50-99, rising', 'Context fill, against the auto-compaction point, that triggers a warning.'),
  c('snapshot_days', 'watchers', TOPIC.context, 'Keep snapshots for', 'days', '1-90 days', 'Days a session snapshot is kept.'),
  w('lead_minutes', 'watchers', TOPIC.cache, 'Wake before cache expiry', 'min', '1-30 min', 'How long before the one-hour cache expires an idle session wakes.'),
  w('max_wakes', 'watchers', TOPIC.cache, 'Stop after unanswered wakes', 'count', '1-20', 'Wakes without a reply before watching stops.'),
  // Spending style: the nine values a style sets
  p('five_hour_threshold_day', 'style', TOPIC.style, 'Ask before launches from (day)', 'pct', PCT, 'Five-hour usage at which background work pauses and an Agent or Workflow launch asks first.'),
  p('five_hour_threshold_night', 'style', TOPIC.style, 'Ask before launches from (night)', 'pct', PCT, 'The same, during night hours.'),
  p('five_hour_hard_stop', 'style', TOPIC.style, 'Start nothing new from', 'pct', PCT, 'Five-hour usage at which the advice starts nothing new.'),
  p('reserve_pct', 'style', TOPIC.style, 'Weekly reserve', 'pctw', PCT, 'Share of the week held back until the reserve starts to fall.'),
  p('reserve_decay_start', 'style', TOPIC.style, 'Reserve starts to fall', 'dayhour', 'a weekday and an hour', 'When the held-back reserve starts to be released.'),
  p('throttle_weekly_pct', 'style', TOPIC.style, 'Throttle from weekly usage', 'pct', PCT, 'Weekly usage from which the advice switches to routine models, while more than 24 hours remain.'),
  p('weekend_cap_pct', 'style', TOPIC.style, 'Weekend spending cap', 'pctw', PCT, 'The most the weekend day hours may spend together.'),
  p('leftover_suggest_pct', 'style', TOPIC.style, 'Suggest leftover work above', 'pctw', PCT, 'Forecast unused share of the week above which pace suggests a leftover pool.'),
  p('reserve_release_min_hours', 'style', TOPIC.style, 'Release the reserve at the latest', 'floorh', '0-168 h, 0: no floor', 'The released reserve starts to be spendable no later than this many hours before the reset.'),
  // Advanced
  p('weekday_weight', 'advanced', TOPIC.time, 'Weekday hour weight', 'num', NONNEG, 'How much a weekday day hour gets relative to a weekend one.'),
  p('weekend_weight', 'advanced', TOPIC.time, 'Weekend hour weight', 'num', NONNEG, 'How much a weekend day hour gets relative to a weekday one.'),
  p('five_hour_to_week_pct', 'advanced', TOPIC.five, 'Cost of a full five-hour window', 'pctw', PCT, 'Sets the most any hour can get. paceline calibrate proposes a measured value.'),
  p('routine_models', 'advanced', TOPIC.agents, 'Models for routine work', 'models', 'model names, in order', 'Models the advice names while it throttles.'),
  p('scoped_exhausted_pct', 'advanced', TOPIC.agents, 'Drop a model when its bucket reaches', 'pct', PCT, 'A model-scoped weekly bucket at this level removes its model from the advice. Needs usage-watch.'),
  p('subagent_estimate_pct', 'advanced', TOPIC.agents, 'Expected cost of one subagent', 'pctw', PCT, 'What one subagent reservation holds.'),
]

export const TIERS: readonly { tier: Exclude<Tier, 'advanced'>; name: string; color: string }[] = [
  { tier: 'day', name: 'You and your day', color: '#4C8DF0' },
  { tier: 'machine', name: 'This machine', color: '#3BAF6B' },
  { tier: 'watchers', name: 'Watchers', color: '#B06FE8' },
  { tier: 'style', name: 'Spending style', color: '#E0609A' },
]
export const ADVANCED_COLOR = 'subtle'

export const ADVANCED_TOPICS: readonly string[] = [
  TOPIC.time,
  TOPIC.five,
  TOPIC.agents,
]

export const itemsOf = (tier: Tier): Item[] => CATALOGUE.filter(i => i.tier === tier)
// The rows a tier draws: the zone and its mode are one row, so the mode has none of its own.
export const rowsOf = (tier: Tier): Item[] => itemsOf(tier).filter(i => i.id !== 'timezone_mode')
export const advancedOf = (topic?: string): Item[] =>
  CATALOGUE.filter(i => i.tier === 'advanced' && (topic === undefined || i.topic === topic))
export const STYLE_KEYS: readonly string[] = itemsOf('style').map(i => i.id)

export const itemById = (id: string, source: Source = 'profile'): Item | undefined =>
  CATALOGUE.find(i => i.id === id && i.source === source)

// ---------------------------------------------------------------------------------------------
// The shape of `paceline settings --json` (spec, Shared design).

export type Entry = { value: unknown; default: unknown; range?: string }
export type StyleName = 'careful' | 'balanced' | 'full'
export type StyleValue = { value: unknown; builtin: unknown; source: 'builtin' | 'override' }
export type PreviewInputs = { reset?: string; reset_local?: string; reset_source?: string }
export type PreviewRow = { unused_pct: number; unused_with_queue_pct?: number; asks_h?: number }
export type Switches = {
  pace: boolean
  usage_watch: boolean
  away: { on: boolean; set_at?: string | null }
  context_watch: boolean
}
export type ZoneInfo = {
  mode: unknown
  stored: string
  effective: string
  detected: string | null
  usable: boolean
  hint: string | null
}
export type Settings = {
  profile: Record<string, Entry>
  context_watch: Record<string, Entry>
  cache_watch: Record<string, Entry>
  switches: Switches
  timezone: ZoneInfo
  styles: Record<string, Record<string, StyleValue>>
  style: string
  style_warnings: string[]
  preview: Record<string, PreviewRow> | null
  // The reset the cached preview was computed for; null without a cached preview.
  preview_inputs: PreviewInputs | null
  files: Record<string, string>
}

export const STYLE_LABEL: Record<string, string> = {
  careful: 'Careful: keep a margin',
  balanced: 'Balanced (default)',
  full: 'Use the whole limit',
  custom: 'Custom',
}
export const STYLE_SHORT: Record<string, string> = {
  careful: 'Careful',
  balanced: 'Balanced',
  full: 'Use the whole limit',
  current: 'Your values',
}

const rawEntry = (s: Settings, item: Item): unknown => s[item.source]?.[item.id]

// The entry of a setting, or undefined when it is missing or not an object (a hand edit, a null).
export const entryOf = (s: Settings, item: Item): Entry | undefined => {
  const raw = rawEntry(s, item)
  return typeof raw === 'object' && raw !== null ? (raw as Entry) : undefined
}

// ---------------------------------------------------------------------------------------------
// Formatting.

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const hh = (h: number) => `${String(h).padStart(2, '0')}:00`
const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const isWhole = (v: unknown): v is number => isNum(v) && Number.isInteger(v)
const isStrings = (v: unknown): v is string[] => Array.isArray(v) && v.every(x => typeof x === 'string')
const dayHour = (v: unknown): v is { weekday: number; hour: number } =>
  typeof v === 'object' &&
  v !== null &&
  isWhole((v as { weekday?: unknown }).weekday) &&
  isWhole((v as { hour?: unknown }).hour) &&
  (v as { weekday: number }).weekday >= 0 &&
  (v as { weekday: number }).weekday <= 6

const num = (n: number) => String(Math.round(n * 100) / 100)

// MB shown as GiB with one decimal, a trailing ".0" dropped: 2048 -> "2 GiB", 6000 -> "5.9 GiB".
export const gib = (mb: number) => `${Number((mb / 1024).toFixed(1))} GiB`

const daysText = (n: number) => `${num(n)} ${n === 1 ? 'day' : 'days'}`

export const minutesText = (m: number) => (m >= 60 && m % 60 === 0 ? `${m / 60} h` : `${m} min`)

// The text of one value, or null when its type does not suit the setting (a hand edit).
function shown(kind: Kind, v: unknown): string | null {
  switch (kind) {
    case 'zone':
      return typeof v === 'string' && v !== '' ? v : null
    case 'zonemode':
      return v === 'machine' ? 'follows this machine' : v === 'fixed' ? 'fixed' : null
    case 'hours':
      return Array.isArray(v) && v.length === 2 && v.every(isWhole) ? `${hh(v[0] as number)}–${hh(v[1] as number)}` : null
    case 'dayhour':
      return dayHour(v) ? `${DAYS[v.weekday]} ${hh(v.hour)}` : null
    case 'pct':
      return isNum(v) ? `${num(v)} %` : null
    case 'pctw':
      return isNum(v) ? `${num(v)} % of week` : null
    case 'num':
    case 'count':
      return isNum(v) ? num(v) : null
    case 'mb':
      return isNum(v) ? gib(v) : null
    case 'min':
      return isNum(v) ? minutesText(v) : null
    case 'days':
      return isNum(v) ? daysText(v) : null
    case 'models':
      return isStrings(v) && v.length > 0 ? v.join(' › ') : null
    case 'bool':
      return typeof v === 'boolean' ? (v ? 'on' : 'off') : null
    case 'levels':
      return Array.isArray(v) && v.length > 0 && v.every(isNum) ? v.map(n => `${num(n)} %`).join(', ') : null
    case 'floorh':
      return isWhole(v) ? (v === 0 ? 'no floor' : `${v} h before the reset`) : null
  }
}

export type Formatted = { text: string; valid: boolean }

// A value of an unexpected type shows as its JSON text with "not valid", never an exception.
export function formatValue(kind: Kind, v: unknown): Formatted {
  const text = shown(kind, v)
  if (text !== null) return { text, valid: true }
  let raw: string
  try {
    raw = JSON.stringify(v) ?? String(v)
  } catch {
    raw = String(v)
  }
  return { text: `${raw} (not valid)`, valid: false }
}

// ---------------------------------------------------------------------------------------------
// "Differs from default".

const same = (a: unknown, b: unknown): boolean => JSON.stringify(a) === JSON.stringify(b)

export const MARK = '● '

// The zone row is marked while the mode is "fixed" (the default is "machine"), whatever the zone.
export function differs(s: Settings, item: Item): boolean {
  const entry = entryOf(s, item)
  if (!entry) return rawEntry(s, item) !== undefined
  if (item.id === 'timezone') return s.timezone.mode !== 'machine'
  if (item.id === 'timezone_mode') return false
  return !same(entry.value, entry.default)
}

// The text of a row's value: "This machine (Europe/Kyiv)" while following; the stored zone, with the
// machine's when it differs, while fixed.
export function zoneText(z: ZoneInfo): string {
  if (z.mode === 'machine') {
    if (z.detected && z.usable) return `This machine (${z.effective})`
    // Detection failed: the stored zone stays in use, and the row does not claim it is the machine's.
    return `${z.effective} (machine zone not detected)${z.hint ? ` · ${z.hint}` : ''}`
  }
  if (z.mode === 'fixed') {
    const machine = z.detected && z.usable && z.detected !== z.effective ? ` · this machine is in ${z.detected}` : ''
    return `${z.effective} (fixed)${machine}`
  }
  return `${JSON.stringify(z.mode)} (not valid)`
}

export function valueText(s: Settings, item: Item): string {
  if (item.id === 'timezone') return zoneText(s.timezone)
  const entry = entryOf(s, item)
  if (entry) return formatValue(item.kind, entry.value).text
  const raw = rawEntry(s, item)
  return raw === undefined ? '?' : formatValue(item.kind, raw).text
}

export const defaultText = (s: Settings, item: Item): string => {
  const entry = entryOf(s, item)
  return entry === undefined ? '?' : formatValue(item.kind, entry.default).text
}

// The value as a row draws and the summary prints it: the marker first when it differs.
export const cell = (s: Settings, item: Item): string => `${differs(s, item) ? MARK : ''}${valueText(s, item)}`

// The dim line of an open row.
export const detailLine = (s: Settings, item: Item): string => {
  const entry = entryOf(s, item)
  const range = entry?.range ?? item.range
  return `default ${defaultText(s, item)} · range ${range} · ${item.id}`
}

// ---------------------------------------------------------------------------------------------
// Switches, Advanced, style.

export const SWITCH_NAMES = ['pace', 'usage-watch', 'away', 'context-watch'] as const

export function switchStates(sw: Switches): { name: (typeof SWITCH_NAMES)[number]; on: boolean }[] {
  return [
    { name: 'pace', on: sw.pace === true },
    { name: 'usage-watch', on: sw.usage_watch === true },
    { name: 'away', on: sw.away?.on === true },
    { name: 'context-watch', on: sw.context_watch === true },
  ]
}

export const switchLine = (sw: Switches): string => switchStates(sw).map(x => `${x.on ? '●' : '○'} ${x.name}`).join(' · ')

export const advancedChanged = (s: Settings): Item[] => advancedOf().filter(i => differs(s, i))

export const advancedLabel = (s: Settings): string => {
  const n = advancedChanged(s).length
  return `Advanced (${advancedOf().length}, ${n} changed)`
}

export const topicLabel = (s: Settings, topic: string): string => {
  const items = advancedOf(topic)
  const n = items.filter(i => differs(s, i)).length
  return `${topic} (${items.length}${n ? `, ${n} changed` : ''})`
}

// The style name the settings report: one of the three, or "custom" for anything else.
export const styleName = (s: Settings): string => (Object.hasOwn(STYLE_LABEL, s.style) ? s.style : 'custom')

// "Careful: keep a margin" and so on; a style with values taken from the overrides file says so.
export function styleDisplay(s: Settings): string {
  const name = styleName(s)
  const overridden = name !== 'custom' && Object.values(s.styles[name] ?? {}).some(v => v?.source === 'override')
  return `${STYLE_LABEL[name]}${overridden ? ' (override)' : ''}`
}

export type StyleRow = { item: Item; text: string; override: boolean; line: string }

// The nine rows under "What it sets (9)": the values in force. For a named style, its table with
// the overridden values marked; for "custom", the profile's own values.
export function styleRows(s: Settings): StyleRow[] {
  const name = styleName(s)
  const table = name === 'custom' ? undefined : s.styles[name]
  return itemsOf('style').map(item => {
    const value = table?.[item.id]
    const override = value?.source === 'override'
    const text = value ? formatValue(item.kind, value.value).text : valueText(s, item)
    return { item, text, override, line: `${text}${override ? ' (override)' : ''}` }
  })
}

// ---------------------------------------------------------------------------------------------
// The preview line (R9), from the cached `preview` of the settings JSON or a `simulate` run.

const asks = (n: number | undefined) => (n && n > 0 ? ` · asks ${n} h` : '')

export function previewLine(name: string, row: PreviewRow): string {
  return `${STYLE_SHORT[name] ?? name} ≈ ${num(row.unused_pct)} % unused${asks(row.asks_h)}`
}

export const previewHeader = (resetLocal?: string | null): string =>
  `An on-track week${resetLocal ? ` to your reset ${resetLocal}` : ''}, without a night queue`

// One line per style, the three then the operator's own ("Your values", keyed `current` by
// `paceline simulate`) when the preview holds it.
export function previewLines(preview: Record<string, PreviewRow> | null | undefined): string[] {
  if (!preview) return []
  return ['careful', 'balanced', 'full', 'current'].filter(n => preview[n]).map(n => previewLine(n, preview[n] as PreviewRow))
}

export const queueText = (row: PreviewRow | undefined): string | null =>
  row && isNum(row.unused_with_queue_pct) ? `with a night queue ≈ ${num(row.unused_with_queue_pct)} % unused` : null

// ---------------------------------------------------------------------------------------------
// The Markdown summary (no bold: the terminal drops it inside tables) and the Advanced listing.

const table = (rows: string[][]): string[] => ['| Setting | Value |', '|---|---|', ...rows.map(r => `| ${r.join(' | ')} |`)]

// The two context-watch rows read as general settings without their owner, so the summary names it.
const summaryLabel = (i: Item): string =>
  i.source === 'context_watch' && (i.id === 'levels' || i.id === 'snapshot_days') ? `${i.label} (context-watch)` : i.label

export function summary(s: Settings, plugin: string): string {
  const out = [`PaceLine settings · ${switchLine(s.switches)}`, '']
  for (const { tier, name } of TIERS) {
    if (tier === 'style') {
      out.push(`### ${name}`, '', `${styleDisplay(s)}, sets ${STYLE_KEYS.length} settings.`)
      const lines = previewLines(s.preview)
      out.push('', ...(lines.length ? [`${previewHeader(s.preview_inputs?.reset_local)}:`, ...lines] : ['No preview yet: run `paceline simulate --preview`.']))
      out.push('')
      continue
    }
    out.push(`### ${name}`, '', ...table(rowsOf(tier).map(i => [summaryLabel(i), cell(s, i)])), '')
  }
  const changed = advancedChanged(s)
  const names = changed.length ? `: ${changed.map(i => i.label).join(', ')}` : ''
  out.push(`### Advanced`, '', `${advancedOf().length} settings, ${changed.length} changed${names}.`)
  out.push(`Topics: ${ADVANCED_TOPICS.join(', ')}.`, '')
  if (s.style_warnings.length) out.push(...s.style_warnings.map(x => `Style override ignored: ${x}`), '')
  out.push('A ● marks a value that differs from the default.')
  out.push(`\`/${plugin}:settings advanced [topic]\` lists the Advanced settings; \`/${plugin}:settings pane\` changes a value.`)
  return out.join('\n')
}

// `advanced [topic]`: the Advanced settings with their identifiers, all or one topic's.
export function advancedListing(s: Settings, topic?: string): string {
  const wanted = topic?.trim().toLowerCase()
  const found = wanted ? ADVANCED_TOPICS.find(t => t.toLowerCase().includes(wanted)) : undefined
  if (wanted && !found) return `No Advanced topic matches "${topic?.trim()}". Topics: ${ADVANCED_TOPICS.join(', ')}.`
  const topics = found ? [found] : ADVANCED_TOPICS
  const out = [advancedLabel(s), '']
  for (const t of topics) {
    out.push(`### ${t}`, '', '| Setting | Value | Identifier |', '|---|---|---|')
    out.push(...advancedOf(t).map(i => `| ${i.label} | ${cell(s, i)} | ${i.id} |`), '')
  }
  out.push('A ● marks a value that differs from the default.')
  return out.join('\n')
}

// ---------------------------------------------------------------------------------------------
// Reading the CLI's answer.

// The settings of a `paceline settings --json` text, or null when it is not that.
export function parseSettings(text: string): Settings | null {
  let raw: unknown
  try {
    raw = JSON.parse(text)
  } catch {
    return null
  }
  if (typeof raw !== 'object' || raw === null) return null
  const o = raw as Record<string, unknown>
  const isObj = (v: unknown) => typeof v === 'object' && v !== null && !Array.isArray(v)
  if (!isObj(o.profile) || !isObj(o.context_watch) || !isObj(o.cache_watch) || !isObj(o.switches) || !isObj(o.timezone)) return null
  return {
    profile: o.profile as Settings['profile'],
    context_watch: o.context_watch as Settings['context_watch'],
    cache_watch: o.cache_watch as Settings['cache_watch'],
    switches: o.switches as Switches,
    timezone: o.timezone as ZoneInfo,
    styles: (isObj(o.styles) ? o.styles : {}) as Settings['styles'],
    style: typeof o.style === 'string' ? o.style : 'custom',
    style_warnings: Array.isArray(o.style_warnings) ? o.style_warnings.map(String) : [],
    preview: isObj(o.preview) ? (o.preview as Settings['preview']) : null,
    preview_inputs: isObj(o.preview_inputs) ? (o.preview_inputs as PreviewInputs) : null,
    files: (isObj(o.files) ? o.files : {}) as Record<string, string>,
  }
}

// ---------------------------------------------------------------------------------------------
// Editing: what the pane's editors hold, what the CLI is asked to write, and how its answer reads.

// The row being edited: the value, and the text typed into its one Input that Enter has not applied.
export type Draft = { value: unknown; text: string | null }
// The time zone row's draft value.
export type ZoneDraft = { mode: 'machine' | 'fixed'; zone: string }

export const DAYS_SHORT: readonly string[] = DAYS

// How far − and + move a number, in the unit the row shows.
export const STEP: Partial<Record<Kind, number>> = { mb: 1024, count: 1, min: 5, days: 1, pct: 1, pctw: 1, num: 0.1, floorh: 6 }

const round2 = (n: number) => Math.round(n * 100) / 100

export const stepped = (kind: Kind, value: number, direction: 1 | -1): number =>
  Math.max(0, round2(value + direction * (STEP[kind] ?? 1)))

// The text an editor's Input shows for a value: MB as GiB, levels as a list, none as empty.
export function toEdit(kind: Kind, v: unknown): string {
  if (v === null || v === undefined) return ''
  if (kind === 'mb' && isNum(v)) return String(Number((v / 1024).toFixed(2)))
  if (kind === 'levels' && Array.isArray(v)) return v.join(', ')
  return String(v)
}

export type Parsed = { ok: true; value: unknown } | { ok: false }

// The value a typed text stands for; the CLI remains the judge of its range.
export function fromEdit(kind: Kind, text: string): Parsed {
  const t = text.trim()
  if (kind === 'levels') {
    const parts = t.split(/[,\s]+/).filter(Boolean).map(Number)
    return parts.length > 0 && parts.every(Number.isFinite) ? { ok: true, value: parts } : { ok: false }
  }
  if (t === '') return { ok: false }
  const n = Number(t.replace(',', '.'))
  if (!Number.isFinite(n)) return { ok: false }
  return { ok: true, value: kind === 'mb' ? Math.round(n * 1024) : n }
}

// Every IANA zone this runtime knows, UTC first; a short list where it knows none.
const FALLBACK_ZONES = ['UTC', 'Europe/Madrid', 'Europe/Kyiv', 'Europe/London', 'America/New_York']
export function allZones(): string[] {
  try {
    const list = (Intl as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf?.('timeZone')
    return list && list.length ? ['UTC', ...list.filter(z => z !== 'UTC')] : FALLBACK_ZONES
  } catch {
    return FALLBACK_ZONES
  }
}

// Up to `limit` zone names that contain the query (spaces read as "_"); none for an empty query.
export function zoneMatches(query: string, zones: readonly string[], limit = 8): string[] {
  const q = query.trim().toLowerCase().replace(/\s+/g, '_')
  return q ? zones.filter(z => z.toLowerCase().includes(q)).slice(0, limit) : []
}

// The zone row's draft at opening: the mode now, and the zone in use.
export const zoneDraft = (z: ZoneInfo): ZoneDraft => ({ mode: z.mode === 'machine' ? 'machine' : 'fixed', zone: z.effective })

export type WriteContext = { sessionId?: string | null }

// The `paceline` arguments that write one value (spec, Shared design: the write table).
export function writeArgs(item: Item, value: unknown, ctx: WriteContext = {}): string[] | null {
  if (item.source === 'profile') return ['profile', 'set', item.id, JSON.stringify(value), '--json']
  if (item.source === 'context_watch') {
    if (item.id === 'line') return ['context-watch', 'config', '--line', value === true ? 'on' : 'off']
    if (item.id === 'levels' && Array.isArray(value)) return ['context-watch', 'config', '--levels', value.join(',')]
    if (item.id === 'snapshot_days') return ['context-watch', 'config', '--days', String(value)]
    return null
  }
  const flag = item.id === 'lead_minutes' ? '--lead' : item.id === 'max_wakes' ? '--max' : null
  if (!flag) return null
  return ['cache-watch', 'config', flag, String(value)]
}

// The write that turns a time-zone draft into the profile's mode and zone.
export function zoneWriteArgs(draft: ZoneDraft, z: ZoneInfo): string[] {
  if (draft.mode === 'machine') return ['profile', 'set', 'timezone_mode', JSON.stringify('machine'), '--json']
  // A fixed zone equal to the one in use only switches the mode off; another name sets the zone, which also fixes the mode.
  if (draft.zone === z.effective && z.mode !== 'fixed') return ['profile', 'set', 'timezone_mode', JSON.stringify('fixed'), '--json']
  return ['profile', 'set', 'timezone', JSON.stringify(draft.zone), '--json']
}

// A model name the CLI is given: lower-case letters, digits, dot, underscore and hyphen, nothing else.
export const safeModel = (name: string): string | null => (/^[a-z0-9._-]+$/.test(name) ? name : null)

// The watcher values the CLIs take as integers; a fraction is refused here, before anything is written.
export function wholeOnly(item: Item, value: unknown): boolean {
  if (item.source === 'profile') return true
  const whole = (n: unknown) => typeof n === 'number' && Number.isInteger(n)
  return item.id === 'levels' ? Array.isArray(value) && value.every(whole) : item.id === 'line' ? true : whole(value)
}

// What a run that ran into its timeout shows; a write may have been applied before the cut.
export const timeoutText = (write: boolean): string =>
  `paceline did not answer within ${RUN_SECONDS} s${write ? '; the change may have been applied: press Refresh' : ''}`

// What a run shows when it was refused with a rejection the module does not know (every interpreter for a read, the one used for a write).
export const rejectedText = (message: string, write: boolean): string =>
  `paceline could not be run: ${message}${write ? '; the change may have been applied: press Refresh' : ''}`

export const styleArgs = (name: string): string[] => ['profile', 'style', name, '--json']

export function switchArgs(name: (typeof SWITCH_NAMES)[number], on: boolean, sessionId?: string | null): string[] {
  switch (name) {
    case 'pace':
      return [on ? 'on' : 'off']
    case 'usage-watch':
      return ['usage-watch', on ? 'on' : 'off']
    case 'away':
      return on ? ['away', 'on', ...(sessionId ? ['--session', sessionId] : [])] : ['away', 'off']
    case 'context-watch':
      return ['context-watch', 'config', on ? '--on' : '--off']
  }
}

export type Change = { text: string; statusLine: string | null }

const firstLine = (text: string): string => text.split(/\r?\n/).find(l => l.trim() !== '')?.trim() ?? ''

// What a successful write reports: the CLI's change line (`key: old -> new`) and, for a profile write,
// the budget's status line. Other commands print text; its first line is the report.
export function parseChange(stdout: string): Change {
  let raw: unknown
  try {
    raw = JSON.parse(stdout)
  } catch {
    return { text: firstLine(stdout), statusLine: null }
  }
  if (typeof raw !== 'object' || raw === null) return { text: firstLine(stdout), statusLine: null }
  const o = raw as Record<string, unknown>
  const statusLine = typeof o.status_line === 'string' && o.status_line !== '' ? o.status_line : null
  if (typeof o.key === 'string') return { text: `${o.key}: ${JSON.stringify(o.old)} -> ${JSON.stringify(o.new)}`, statusLine }
  if (typeof o.style === 'string') {
    const n = Array.isArray(o.changes) ? o.changes.length : 0
    return { text: `Spending style: ${o.style}${n ? ` (${n} changed)` : ' (nothing to change)'}`, statusLine }
  }
  return { text: firstLine(stdout), statusLine }
}

// Why a command failed: its standard error (the CLI's own reason), trimmed to a readable length.
export function reasonOf(stderr: string, exitCode: number): string {
  // The CLI's own line is the last one that starts with `paceline`: argparse prints its usage first and
  // `paceline <command>: error: <reason>` last. Without such a line, the whole text is the reason.
  const lines = stderr.trim().split(/\r?\n/)
  const own = lines.filter(l => /^paceline\b/.test(l)).pop()
  const text = (own ?? stderr.trim()).replace(/^paceline[^:]*: (error: )?/, '').trim()
  return text === '' ? `exit ${exitCode}` : text.length > 400 ? `${text.slice(0, 400)}…` : text
}

// The preview of `paceline simulate --preview --json`, or null when the answer holds none.
export function parsePreview(text: string): { rows: Record<string, PreviewRow>; resetLocal: string | null } | null {
  let raw: unknown
  try {
    raw = JSON.parse(text)
  } catch {
    return null
  }
  if (typeof raw !== 'object' || raw === null) return null
  const o = raw as { preview?: unknown; inputs?: { reset_local?: unknown } | null }
  if (typeof o.preview !== 'object' || o.preview === null || Array.isArray(o.preview)) return null
  const reset = o.inputs?.reset_local
  return { rows: o.preview as Record<string, PreviewRow>, resetLocal: typeof reset === 'string' ? reset : null }
}

// The time of a read as the pane prints it, local, to the second.
export function clockText(ms: number): string {
  const d = new Date(ms)
  const two = (n: number) => String(n).padStart(2, '0')
  return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`
}
