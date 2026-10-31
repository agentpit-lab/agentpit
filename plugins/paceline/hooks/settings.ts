// Pure part of the settings view: the catalogue of settings, their formatting, the "differs from
// default" rule and the Markdown summary. No engine calls, so tests reach it directly. Reads and
// writes go through the `paceline` CLI (see register.tsx); the defaults and values arrive in the
// JSON of `paceline settings --json`, so nothing here holds a default.

// The longest a paceline command may run (watch.ts, RUN_TIMEOUT_MS), in seconds.
const RUN_SECONDS = 30

export type Kind =
  | 'zone'
  | 'zonemode'
  | 'hours'
  | 'pct'
  | 'pctw'
  | 'num'
  | 'count'
  | 'mb'
  | 'min'
  | 'days'
  | 'bool'
  | 'levels'
  | 'workhours'
  | 'weekday'
  | 'text'

export type Source = 'profile' | 'context_watch' | 'cache_watch' | 'plan'
export type Tier = 'day' | 'machine' | 'watchers' | 'advanced'

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
  holds: 'Holds',
  machine: 'This machine',
  zone: 'Time zone',
  context: 'context-watch',
  cache: 'cache-watch',
  reserve: 'Weekly reserve',
} as const

type Make = (id: string, tier: Tier, topic: string, label: string, kind: Kind, range: string, meaning: string) => Item
const make =
  (source: Source): Make =>
  (id, tier, topic, label, kind, range, meaning) => ({ id, source, tier, topic, label, kind, range, meaning })
// One catalogue line per setting; tests/test_settings_catalogue.py reads the `p('<key>'` lines.
const p = make('profile')
const c = make('context_watch')
const w = make('cache_watch')
// The settings kept in the week plan file of the session's account (not profile keys).
const h = make('plan')

const PCT = '0-100 %'
const NONNEG = '0 or more'
const WHOLE = 'a whole number, 0 or more'

export const CATALOGUE: readonly Item[] = [
  // You and your day
  p('timezone', 'day', TOPIC.zone, 'Time zone', 'zone', 'an IANA zone name, for example Europe/Madrid', 'The zone every wall-clock rule uses.'),
  p('timezone_mode', 'day', TOPIC.zone, 'Time zone follows', 'zonemode', 'machine or fixed', 'Whether the profile follows this machine’s zone as it changes, or keeps the stored one.'),
  h(
    'work_hours',
    'day',
    TOPIC.time,
    'Working hours',
    'workhours',
    'HH:MM-HH:MM in steps of 15 min, within one day, at least 1 h of working hours and 1 h of off-hours',
    'Your working hours (☼), the same every day in the profile’s time zone; the rest of the day is off-hours (☾). Stored per account; a change applies from the end of the part that is running.',
  ),
  h('reserve_pct', 'day', TOPIC.reserve, 'Weekly reserve', 'pctw', 'a whole 0-50 % of the week, 0: off', 'Share of the week kept back from launches until the reserve opens. 0 turns it off.'),
  h('reserve_release', 'day', TOPIC.reserve, 'Reserve opens on', 'weekday', 'mon..sun, or last for the last day before the reset', 'The weekday the reserve opens, at the start of the working hours of that day; by default the last day before the reset.'),
  h('reserve_name', 'day', TOPIC.reserve, 'Reserve name', 'text', '1-32 printable characters, empty: none', 'What the reserve is for; shown in its text. Empty removes the name.'),
  p('five_hour_stop_pct', 'day', TOPIC.five, 'Five-hour stop', 'pct', PCT, 'Five-hour usage from which each Agent or Workflow launch asks first, in working hours and off-hours alike.'),
  // This machine
  p('memory_guard', 'machine', TOPIC.machine, 'Memory guard', 'bool', 'on or off', 'On: below the free memory floor a launch asks first and the night queue stops. Off: memory is not measured before a launch.'),
  p('min_free_commit_mb', 'machine', TOPIC.machine, 'Minimum free memory', 'mb', WHOLE + ' of MB', 'Free memory below which no new work starts. Used only while the memory guard is on.'),
  p('commit_reserve_mb', 'machine', TOPIC.machine, 'Memory kept aside', 'mb', WHOLE + ' of MB', 'Memory left out when counting how many heavy runs fit. Used only while the memory guard is on.'),
  p('heavy_job_mb', 'machine', TOPIC.machine, 'Memory per heavy run', 'mb', WHOLE + ' of MB', 'Memory one heavy run is assumed to take. Used only while the memory guard is on.'),
  p('max_heavy_runs', 'machine', TOPIC.machine, 'Heavy runs at once, at most', 'count', WHOLE, 'Upper bound on parallel heavy runs, whatever the free memory. Used only while the memory guard is on.'),
  p('max_agents', 'machine', TOPIC.machine, 'Parallel agents, at most', 'count', WHOLE, 'The most parallel agents `paceline auto` gives a queue item.'),
  // Watchers
  c('line', 'watchers', TOPIC.context, 'Context gauge on every prompt', 'bool', 'on or off', 'One-line context gauge added to every prompt.'),
  c('levels', 'watchers', TOPIC.context, 'Warn at', 'levels', 'whole percentages 50-99, rising', 'Context fill, against the auto-compaction point, that triggers a warning.'),
  c('snapshot_days', 'watchers', TOPIC.context, 'Keep snapshots for', 'days', '1-90 days', 'Days a session snapshot is kept.'),
  w('lead_minutes', 'watchers', TOPIC.cache, 'Wake before cache expiry', 'min', '1-30 min', 'How long before the one-hour cache expires an idle session wakes.'),
  w('max_wakes', 'watchers', TOPIC.cache, 'Stop after unanswered wakes', 'count', '1-20', 'Wakes without a reply before watching stops.'),
  // Advanced
  p('five_hour_to_week_pct', 'advanced', TOPIC.five, 'Cost of a full five-hour window', 'pctw', PCT, 'Sets the most any hour can get. paceline calibrate proposes a measured value.'),
  p('subagent_estimate_pct', 'advanced', TOPIC.holds, 'Expected cost of one subagent', 'pctw', PCT, 'What one subagent hold keeps back.'),
]

export const TIERS: readonly { tier: Exclude<Tier, 'advanced'>; name: string; color: string }[] = [
  { tier: 'day', name: 'You and your day', color: '#4C8DF0' },
  { tier: 'machine', name: 'This machine', color: '#3BAF6B' },
  { tier: 'watchers', name: 'Watchers', color: '#B06FE8' },
]
export const ADVANCED_COLOR = 'subtle'

export const ADVANCED_TOPICS: readonly string[] = [
  TOPIC.five,
  TOPIC.holds,
]

export const itemsOf = (tier: Tier): Item[] => CATALOGUE.filter(i => i.tier === tier)
// The rows a tier draws: the zone and its mode are one row, so the mode has none of its own.
export const rowsOf = (tier: Tier): Item[] => itemsOf(tier).filter(i => i.id !== 'timezone_mode')
export const advancedOf = (topic?: string): Item[] =>
  CATALOGUE.filter(i => i.tier === 'advanced' && (topic === undefined || i.topic === topic))

export const itemById = (id: string, source: Source = 'profile'): Item | undefined =>
  CATALOGUE.find(i => i.id === id && i.source === source)

// ---------------------------------------------------------------------------------------------
// The shape of `paceline settings --json` (spec, Shared design).

export type Entry = { value: unknown; default: unknown; range?: string; note?: string }
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
  plan: Record<string, Entry>
  context_watch: Record<string, Entry>
  cache_watch: Record<string, Entry>
  switches: Switches
  timezone: ZoneInfo
  files: Record<string, string | null>
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

export const WEEKDAY_KEYS: readonly string[] = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']

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
    case 'weekday':
      return v === null ? 'last day before the reset' : typeof v === 'string' && WEEKDAY_KEYS.includes(v) ? DAYS[WEEKDAY_KEYS.indexOf(v)] ?? null : null
    case 'text':
      return v === null || v === '' ? 'none' : typeof v === 'string' ? v : null
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
    case 'bool':
      return typeof v === 'boolean' ? (v ? 'on' : 'off') : null
    case 'levels':
      return Array.isArray(v) && v.length > 0 && v.every(isNum) ? v.map(n => `${num(n)} %`).join(', ') : null
    case 'workhours': {
      const parsed = parseWorkHours(v)
      return parsed ? formatWorkHours(parsed[0], parsed[1]) : null
    }
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
  if (entry.value === null && typeof entry.note === 'string' && entry.note !== '') return false
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
  if (entry && entry.value === null && typeof entry.note === 'string' && entry.note !== '') return entry.note
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
// Switches and Advanced.

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

// ---------------------------------------------------------------------------------------------
// The Markdown summary (no bold: the terminal drops it inside tables) and the Advanced listing.

const table = (rows: string[][]): string[] => ['| Setting | Value |', '|---|---|', ...rows.map(r => `| ${r.join(' | ')} |`)]

// The two context-watch rows read as general settings without their owner, so the summary names it.
const summaryLabel = (i: Item): string =>
  i.source === 'context_watch' && (i.id === 'levels' || i.id === 'snapshot_days') ? `${i.label} (context-watch)` : i.label

export function summary(s: Settings, plugin: string): string {
  const out = [`PaceLine settings · ${switchLine(s.switches)}`, '']
  for (const { tier, name } of TIERS) {
    out.push(`### ${name}`, '', ...table(rowsOf(tier).map(i => [summaryLabel(i), cell(s, i)])), '')
  }
  const changed = advancedChanged(s)
  const names = changed.length ? `: ${changed.map(i => i.label).join(', ')}` : ''
  out.push(`### Advanced`, '', `${advancedOf().length} settings, ${changed.length} changed${names}.`)
  out.push(`Topics: ${ADVANCED_TOPICS.join(', ')}.`, '')
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
    plan: (isObj(o.plan) ? o.plan : {}) as Settings['plan'],
    context_watch: o.context_watch as Settings['context_watch'],
    cache_watch: o.cache_watch as Settings['cache_watch'],
    switches: o.switches as Switches,
    timezone: o.timezone as ZoneInfo,
    files: (isObj(o.files) ? o.files : {}) as Record<string, string | null>,
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
export const STEP: Partial<Record<Kind, number>> = { mb: 1024, count: 1, min: 5, days: 1, pct: 1, pctw: 1, num: 0.1 }

const round2 = (n: number) => Math.round(n * 100) / 100

export const stepped = (kind: Kind, value: number, direction: 1 | -1): number =>
  Math.max(0, round2(value + direction * (STEP[kind] ?? 1)))

// The text an editor's Input shows for a value: MB as GiB, levels as a list, none as empty.
export function toEdit(kind: Kind, v: unknown): string {
  if (v === null || v === undefined) return ''
  if (kind === 'mb' && isNum(v)) return String(Number((v / 1024).toFixed(2)))
  if (kind === 'text' && typeof v !== 'string') return ''
  if (kind === 'levels' && Array.isArray(v)) return v.join(', ')
  return String(v)
}

export type Parsed = { ok: true; value: unknown } | { ok: false }

// The value a typed text stands for; the CLI remains the judge of its range.
export function fromEdit(kind: Kind, text: string): Parsed {
  const t = text.trim()
  if (kind === 'text') return { ok: true, value: t }
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
  if (item.source === 'plan') {
    // The working hours: the CLI takes the text as it is. The reserve has its own command; a null release is `last`.
    if (item.id === 'work_hours') return ['profile', 'set', 'work_hours', String(value), '--json']
    if (item.id === 'reserve_pct') return ['reserve', String(value), '--json']
    if (item.id === 'reserve_release') return ['reserve', '--release', value === null || value === undefined ? 'last' : String(value), '--json']
    if (item.id === 'reserve_name') return ['reserve', `--name=${value === null || value === undefined ? '' : String(value)}`, '--json']
    return null
  }
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

// The watcher values the CLIs take as integers; a fraction is refused here, before anything is written.
export function wholeOnly(item: Item, value: unknown): boolean {
  if (item.source === 'profile' || item.source === 'plan') return true
  const whole = (n: unknown) => typeof n === 'number' && Number.isInteger(n)
  return item.id === 'levels' ? Array.isArray(value) && value.every(whole) : item.id === 'line' ? true : whole(value)
}

// What a run that ran into its timeout shows; a write may have been applied before the cut.
export const timeoutText = (write: boolean): string =>
  `paceline did not answer within ${RUN_SECONDS} s${write ? '; the change may have been applied: press Refresh' : ''}`

// What a run shows when it was refused with a rejection the module does not know (every interpreter for a read, the one used for a write).
export const rejectedText = (message: string, write: boolean): string =>
  `paceline could not be run: ${message}${write ? '; the change may have been applied: press Refresh' : ''}`

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

const RESERVE_KEYS: readonly string[] = ['reserve_pct', 'reserve_release', 'reserve_name']

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
  if (typeof o.key === 'string') {
    const base = `${o.key}: ${JSON.stringify(o.old)} -> ${JSON.stringify(o.new)}`
    if (RESERVE_KEYS.includes(o.key) && o.written === false) return { text: `${base} · nothing written`, statusLine }
    if (o.key === 'work_hours' && o.written === false) return { text: `${base} · nothing written`, statusLine }
    if (o.key === 'work_hours' && typeof o.applies_from === 'string') return { text: `${base} · applies from the end of the current part`, statusLine }
    return { text: base, statusLine }
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

// The time of a read as the pane prints it, local, to the second.
export function clockText(ms: number): string {
  const d = new Date(ms)
  const two = (n: number) => String(n).padStart(2, '0')
  return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`
}

// ---------------------------------------------------------------------------------------------
// Working hours: the same rules as week.parse_work_hours in scripts/week.py.

export const WORK_STEP_MIN = 15

// [start, end] in minutes of the local day, or null when the text breaks a rule.
export function parseWorkHours(text: unknown): [number, number] | null {
  if (typeof text !== 'string') return null
  const m = /^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$/.exec(text)
  if (!m) return null
  const [h1, m1, h2, m2] = [m[1], m[2], m[3], m[4]].map(Number) as [number, number, number, number]
  if (h1 > 23 || h2 > 23 || m1 > 59 || m2 > 59) return null
  const start = h1 * 60 + m1
  const end = h2 * 60 + m2
  if (start % WORK_STEP_MIN !== 0 || end % WORK_STEP_MIN !== 0) return null
  if (start >= end) return null
  if (end - start < 60) return null
  if (1440 - (end - start) < 60) return null
  return [start, end]
}

const clock = (minutes: number): string => `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`

export const formatWorkHours = (start: number, end: number): string => `${clock(start)}-${clock(end)}`

// The value after one − or + on one end, or null when the result breaks a rule (the button is not drawn).
export function stepWorkHours(value: string, end: 'start' | 'end', direction: 1 | -1): string | null {
  const parsed = parseWorkHours(value)
  if (!parsed) return null
  const next: [number, number] =
    end === 'start' ? [parsed[0] + direction * WORK_STEP_MIN, parsed[1]] : [parsed[0], parsed[1] + direction * WORK_STEP_MIN]
  if (next[0] < 0 || next[1] < 0) return null
  const text = formatWorkHours(next[0], next[1])
  return parseWorkHours(text) ? text : null
}

// The length of working hours as the editor prints it: up to two decimals, no trailing zeros.
export function workHoursLength(value: string): string | null {
  const parsed = parseWorkHours(value)
  return parsed ? String(Math.round(((parsed[1] - parsed[0]) / 60) * 100) / 100) : null
}

// ---------------------------------------------------------------------------------------------
// The calendar tab: the pure part (the view of `paceline plan --json`, labels, bars, columns).

export type PlanPart = {
  day: string
  kind: 'work' | 'off'
  start_local: string
  end_local: string
  hours: number
  cut: boolean
  mark: number
  state: 'ended' | 'current' | 'later'
  share_pct: number | null
  unplanned: boolean
  estimated: boolean
  spent_pct: number | null
  allowed_pct: number | null
  over: boolean | null
  unreachable_windows: number | null
}
export type PlanDay = {
  date: string
  weekday: string
  work: PlanPart | null
  off: PlanPart | null
  share_pct: number
  above_scale: boolean
}
// The `reserve` object of `paceline plan --json`; the error form holds only `error`, `over`, `kept_pct` and `text`.
export type ReserveView = {
  text: string
  over: boolean
  error?: string
  pct?: number
  name?: string | null
  release?: string | null
  release_at?: string | null
  release_at_local?: string | null
  release_day?: string | null
  release_text?: string | null
  reset_text?: string
  kept_to_reset?: boolean
  opens_clock?: string | null
  released?: boolean
  kept_pct?: number
  stop_at_pct?: number | null
  used_pct?: number
}
export type PlanView = {
  work_hours: string
  mode: string
  effective_mode: string
  week: { reset_local: string }
  reserve: ReserveView
  left_pct: number
  even_pct_per_day: number
  bar_scale_pct: number
  unplanned_pct: number | null
  ratio_estimated: boolean
  current: PlanPart
  week_days: PlanDay[]
}

const isRec = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)

// The view of a `paceline plan --json` text or an already parsed object, or null when it is not one.
export function parsePlan(value: unknown): PlanView | null {
  let raw: unknown = value
  if (typeof value === 'string') {
    try {
      raw = JSON.parse(value)
    } catch {
      return null
    }
  }
  if (!isRec(raw)) return null
  if (!isRec(raw.week) || typeof raw.week.reset_local !== 'string') return null
  for (const key of ['left_pct', 'even_pct_per_day', 'bar_scale_pct']) if (!isNum(raw[key])) return null
  if (!isRec(raw.reserve) || typeof raw.reserve.text !== 'string') return null
  if (!isRec(raw.current)) return null
  const list = Array.isArray(raw.week_days) ? raw.week_days : null
  if (!list) return null
  return { ...(raw as unknown as PlanView), week_days: list.filter(isRec) as unknown as PlanDay[] }
}

// How many cells wide a day's bar is.
export const BAR_CELLS = 16

const trimmed = (n: number): string => String(Number(n.toFixed(2)))

// The CLI's names for the common marks, the multiple for the others.
export function markLabel(mark: number): string {
  if (mark === 0) return 'off'
  if (mark === 0.5) return 'light'
  if (mark === 1) return 'normal'
  if (mark === 1.5) return 'heavy'
  return `${trimmed(mark)}×`
}

// The mark one − or + away, in steps of 0.25 from 0 to 2, or null at the bound.
export function stepMark(mark: number, direction: 1 | -1): number | null {
  const next = Math.round((mark + direction * 0.25) * 4) / 4
  return next < 0 || next > 2 ? null : next
}

// The `paceline` arguments that store one mark; the date is the part's ISO date, never a weekday word.
export const planSetArgs = (date: string, kind: 'work' | 'off', mark: number): string[] => [
  'plan',
  'set',
  date,
  kind === 'work' ? '--work' : '--off-hours',
  String(mark),
  '--json',
]

const cap = (s: string): string => (s === '' ? s : s.charAt(0).toUpperCase() + s.slice(1))

// "Tue 27": the weekday and the day of the month without a leading zero.
export const dayTitle = (day: PlanDay): string => `${cap(day.weekday ?? '')} ${Number(String(day.date).slice(8, 10))}`

const one = (n: number): string => n.toFixed(1)

// What a part's row shows under its mark: its share, or what an ended part spent ("not recorded" when
// the plan holds no spend for it).
export function shareText(part: PlanPart | null): string {
  if (!part) return ''
  if (part.state === 'ended') return isNum(part.spent_pct) ? `spent ${one(part.spent_pct)}` : 'not recorded'
  return isNum(part.share_pct) ? `${one(part.share_pct)} %` : 'unplanned'
}

// The hours of a part cut by the week's edge, "06:00-09:00"; empty for a whole part.
export const cutText = (part: PlanPart | null): string =>
  part?.cut ? `${String(part.start_local).slice(11, 16)}-${String(part.end_local).slice(11, 16)}` : ''

// The filled cells of a bar for a share, against the bar's top.
export const barCells = (pct: number, scale: number, cells: number = BAR_CELLS): number =>
  scale > 0 ? Math.min(cells, Math.max(0, Math.round((pct / scale) * cells))) : 0

const SUN_COLOR = 'suggestion'
const MOON_COLOR = 'merged'
// The background of the bar's empty cells, the room left to the bar top. An ended part fills in its own
// colour: a grey of its own read as the track on Desktop, and the card says the part has ended.
export const TRACK_COLOR = 'subtle'

// What a part fills of the bar: its share, or for an ended part what it spent (0 when none is recorded).
const barShare = (part: PlanPart | null): number => {
  if (!part) return 0
  if (part.state === 'ended') return isNum(part.spent_pct) ? part.spent_pct : 0
  return isNum(part.share_pct) ? part.share_pct : 0
}

// One colour (or null for an empty cell, drawn as the track) per bar cell, left first: the ☼ cells, then the
// ☾ cells after them.
export function barColors(day: PlanDay, view: PlanView, cells: number = BAR_CELLS): (string | null)[] {
  const scale = view.bar_scale_pct
  const work = barShare(day.work)
  const off = barShare(day.off)
  const w = barCells(work, scale, cells)
  const t = barCells(work + off, scale, cells)
  return Array.from({ length: cells }, (_, i) => (i + 1 <= w ? SUN_COLOR : i + 1 <= t ? MOON_COLOR : null))
}

// Whether the bar of a day runs past the bar top: what its parts fill (ended parts by spend), as the bar draws it.
export const aboveScale = (day: PlanDay, view: PlanView): boolean => barShare(day.work) + barShare(day.off) > view.bar_scale_pct

// The ⚠ text of a day: the five-hour windows its current and later parts cannot reach their share in.
export function warnText(day: PlanDay): string {
  const live = (p: PlanPart | null) => (p && p.state !== 'ended' && isNum(p.unreachable_windows) ? p.unreachable_windows : null)
  const a = live(day.work)
  const b = live(day.off)
  if (a !== null && b !== null) return `⚠ ☼${a} ☾${b} win`
  if (a !== null) return `⚠ ☼ ${a} win`
  if (b !== null) return `⚠ ☾ ${b} win`
  return ''
}

// The days that fold into one line: the longest run from the week's start whose every part has ended.
// A day with neither part ends the run wherever it is, and a run of one day still folds.
export function endedRun(view: PlanView): { ended: PlanDay[]; rows: PlanDay[] } {
  const days = view.week_days
  const over = (d: PlanDay) => {
    const parts = [d.work, d.off].filter((p): p is PlanPart => p !== null)
    return parts.length > 0 && parts.every(p => p.state === 'ended')
  }
  let n = 0
  while (n < days.length && over(days[n] as PlanDay)) n++
  return { ended: days.slice(0, n), rows: days.slice(n) }
}

// The one line of the folded days: "Sat 24 – Tue 27 · ended · spent 43.0 %".
export function endedText(days: PlanDay[]): string {
  const first = days[0]
  const last = days[days.length - 1]
  if (!first || !last) return ''
  const spent = days.reduce((sum, d) => sum + [d.work, d.off].reduce((t, p) => t + (p && isNum(p.spent_pct) ? p.spent_pct : 0), 0), 0)
  const span = first === last ? dayTitle(first) : `${dayTitle(first)} – ${dayTitle(last)}`
  return `${span} · ended · spent ${one(spent)} %`
}

const resetText = (view: PlanView): string => String(view.week.reset_local).slice(0, 16)

export const weekLine = (view: PlanView): string =>
  `Week to ${resetText(view)} · work hours ${view.work_hours} · mode ${view.effective_mode !== view.mode ? `${view.mode} (${view.effective_mode} until this part ends)` : view.mode}`

export const numbersLine = (view: PlanView): string =>
  `Left ${one(view.left_pct)} % · ${view.reserve.text} · even pace ${view.even_pct_per_day.toFixed(2)} %/day · bar top ${one(view.bar_scale_pct)} %/day`

// The suffix of the card of the day the reserve opens on, while it is still kept: " · reserve opens 09:00".
export function reserveOpensText(view: PlanView, day: PlanDay): string {
  const r = view.reserve
  if (!r || r.error || typeof r.opens_clock !== 'string' || r.release_day !== day.date) return ''
  return ` · reserve opens ${r.opens_clock}`
}

export function nowText(view: PlanView): string {
  const c = view.current
  const date = String(c.day)
  const found = view.week_days.find(d => d.date === date)
  const title = dayTitle({ date, weekday: found?.weekday ?? '' } as PlanDay)
  const head = `Now ${title} ${c.kind === 'work' ? '☼' : '☾'} ${markLabel(c.mark)}`
  const spent = isNum(c.spent_pct) ? c.spent_pct : 0
  const est = c.estimated ? ' · estimated' : ''
  const until = ` · until ${String(c.end_local).slice(11, 16)}`
  if (isNum(c.share_pct)) {
    const pro = isNum(c.allowed_pct) ? ` (pro rata ${one(c.allowed_pct)})` : ''
    return `${head} · spent ${one(spent)} of ${one(c.share_pct)} %${pro}${est}${until}${c.over === true ? ' · over its share' : ''}`
  }
  return `${head} · unplanned · spent ${one(spent)} %${est}${until}`
}

export const unplannedLine = (view: PlanView): string | null =>
  isNum(view.unplanned_pct) ? `${one(view.unplanned_pct)} % unplanned, expires at ${resetText(view)}` : null

const NO_EFFECT_TEXT: Record<string, string> = {
  current: 'is the current part, fixed at its start',
  ended: 'has ended',
  'outside-week': 'lies outside this week',
}

// What a `paceline plan set --json` answer reports, and the view it carries.
export function calendarChange(stdout: string, date: string, kind: 'work' | 'off', mark: number): { text: string; plan: PlanView | null } {
  let raw: unknown
  try {
    raw = JSON.parse(stdout)
  } catch {
    return { text: firstLine(stdout), plan: null }
  }
  if (!isRec(raw)) return { text: firstLine(stdout), plan: null }
  const glyph = (k: unknown) => (k === 'work' ? '☼' : '☾')
  let text = `${date} ${glyph(kind)} ${markLabel(mark)}`
  if (Array.isArray(raw.no_effect)) {
    for (const item of raw.no_effect) {
      if (!isRec(item)) continue
      const why = NO_EFFECT_TEXT[String(item.reason)] ?? String(item.reason)
      text += ` · ${glyph(item.kind)} ${why}: stored, this week's plan does not change`
    }
  }
  if (raw.written === false) text += ' · clock is ahead of the wall clock: nothing written'
  // A clock ahead of the wall clock wrote nothing: the carried plan is not the stored one, so the pane reads again.
  return { text, plan: raw.written === false ? null : parsePlan(raw.plan) }
}
