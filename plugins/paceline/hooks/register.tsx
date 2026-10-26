import { atom, read, update } from 'claude-code'
import type { CommandRunResult, EngineInterface, Register } from 'claude-code'

import type { ContextSession, PreviewView, SettingsView } from '../types'
import {
  addPaths,
  compactInstructions,
  crossedLevel,
  formatLine,
  isOperatorPrompt,
  keepLast,
  PROMPTS_KEPT,
  readSettings,
  recoverText,
  snapshotPaths,
  snapshotText,
  warningText,
  writtenPaths,
} from './context'
import type { Fill, Settings } from './context'
import { accountFileOf, accountKeyOf, accountKeyOfIds } from './account'
import { AWAY_FILE, CLEARED, isAwayOn, isPersonPrompt } from './away'
import { inboxKey, inboxPath, inboxReading, isDue, limitsKey, stateDirOf } from './inbox'
import type { RateLimit, Sent } from './inbox'
import { asRunError, inboxFingerprint, RUN_TIMEOUT_MS, runPythonScript, seenPath, WATCH_PERIOD_MS } from './watch'
import type { Exec, Interpreter, Ran } from './watch'
import {
  ADVANCED_COLOR,
  ADVANCED_TOPICS,
  advancedLabel,
  advancedListing,
  advancedOf,
  allZones,
  cell,
  clockText,
  DAYS_SHORT,
  defaultText,
  detailLine,
  differs,
  entryOf,
  formatValue,
  fromEdit,
  MARK,
  parseChange,
  parsePreview,
  parseSettings,
  previewHeader,
  previewLines,
  queueText,
  reasonOf,
  rejectedText,
  rowsOf,
  safeModel,
  STEP,
  stepped,
  STYLE_KEYS,
  STYLE_LABEL,
  styleArgs,
  styleDisplay,
  styleName,
  styleRows,
  summary,
  SWITCH_NAMES,
  timeoutText,
  switchArgs,
  switchStates,
  TIERS,
  toEdit,
  topicLabel,
  valueText,
  wholeOnly,
  writeArgs,
  zoneDraft,
  zoneMatches,
  zoneWriteArgs,
} from './settings'
import type { Draft, Item, Kind, Parsed, Settings as ViewSettings, ZoneDraft } from './settings'
import { gateOpen, inboxObservedAt, readGate, readingFrom, switchOn, tagOrDrop, USAGE_TICK_MS, USAGE_URL, usageGatePath, usageInboxPath, usageSwitchPath } from './usage'
import type { Outcome } from './usage'

// Every function that takes $, and every atom it reads, lives in this file: the engine follows $
// and state references only within the file that declares them.

const EMPTY: ContextSession = { sessionId: null, warned: [], prompts: [], written: [], compactions: 0 }
const session = atom({ plugin: 'paceline', key: 'session' } as const, EMPTY)

// <state>, resolved by the rule every paceline script uses, so the module and the scripts agree.
async function stateDir($: EngineInterface): Promise<string> {
  return stateDirOf({
    stateDir: await $.env.get('PACELINE_STATE_DIR'),
    configDir: await $.env.get('CLAUDE_CONFIG_DIR'),
    home: (await $.env.get('USERPROFILE')) ?? (await $.env.get('HOME')),
  })
}

// The settings file context_watch.py writes.
async function settingsOf($: EngineInterface): Promise<Settings> {
  try {
    return readSettings(await $.fs.read(`${await stateDir($)}/context-watch.json`))
  } catch {
    return readSettings(null)
  }
}

// /clear and a resume start another session in the same process; its state starts empty.
async function current($: EngineInterface): Promise<ContextSession> {
  const id = await $.session.id()
  const held = await read($, session)
  if (held.sessionId === id) return held
  const fresh = { ...EMPTY, sessionId: id }
  await update($, session, () => fresh)
  return fresh
}

async function change($: EngineInterface, fn: (s: ContextSession) => ContextSession) {
  const s = await current($)
  await update($, session, () => fn(s))
}

// The engine's own estimate of the whole window against its compact point, the figures /context shows.
async function fillOf($: EngineInterface): Promise<Fill | null> {
  const usage = await $.session.usage({ breakdown: 'summary' })
  const b = usage.context.breakdown
  const used = b?.totalTokens ?? usage.context.tokens
  if (used === undefined) return null
  const window = b?.rawMaxTokens ?? usage.context.window
  const isAutoCompact = Boolean(b?.isAutoCompactEnabled && b.autoCompactThreshold)
  return { used, window, isAutoCompact, limit: isAutoCompact ? (b!.autoCompactThreshold as number) : window }
}

async function warning($: EngineInterface, fill: Fill, settings: Settings): Promise<string | null> {
  const s = await current($)
  const { level, warned } = crossedLevel(fill, settings.levels, s.warned)
  if (level === null) return null
  await change($, x => ({ ...x, warned }))
  return warningText(fill, level)
}

async function snapshotPath($: EngineInterface) {
  return snapshotPaths(await $.session.root(), await $.session.id())
}

// The key of the account a Desktop host started this session with, from the two identifier variables
// it passes, or null when they are not both set. They belong to the process, not to the shared state
// file, which names the account of the last terminal login.
async function envAccountOf($: EngineInterface): Promise<string | null> {
  try {
    const account = await $.env.get('CLAUDE_CODE_ACCOUNT_UUID')
    const organization = await $.env.get('CLAUDE_CODE_ORGANIZATION_UUID')
    if (!account || !organization) return null
    return accountKeyOfIds(account, organization).key
  } catch {
    return null
  }
}

// Set by /login and /logout in a session that has the variables: they still name the old account, and
// the file may now name another one, so the session's account is unknown until the process restarts.
let accountLost = false

// The key of the account signed in now: the session's own variables first, else Claude Code's state
// file, or null when it cannot be told. The file is read at every use: a login in another session
// changes it under this process. Claude Code rewrites the file often, so a read that fails or does not
// parse is tried once more.
async function accountOf($: EngineInterface): Promise<string | null> {
  if (accountLost) return null
  const own = await envAccountOf($)
  if (own !== null) return own
  try {
    const file = accountFileOf({
      configDir: await $.env.get('CLAUDE_CONFIG_DIR'),
      home: (await $.env.get('USERPROFILE')) ?? (await $.env.get('HOME')),
    })
    if (!(await $.fs.exists(file))) return null
    for (let attempt = 0; attempt < 2; attempt++) {
      const account = accountKeyOf(await textAt($, file))
      if (account.reason !== 'unreadable') return account.key
      if (attempt === 0) await new Promise<void>(resolve => $.clock.after(50, () => resolve()))
    }
  } catch {
    // Whatever failed, the account is unknown; nothing of the file or the error is kept.
  }
  return null
}

// The last reading written to the inbox; limits_merge.py merges the inbox into rate-limits.json.
let sent: Sent | null = null
// The limits of the latest payload this process saw, whether or not it was written.
let seenLimits: string | null = null
// What stood at the last /login or /logout: the engine may hand over the old account's limits for a
// turn after the account changed, and a reading with those limits must not be tagged with the new one.
let hold: { limits: string | null; account: string | null } | null = null

async function writeInbox($: EngineInterface, limits: readonly RateLimit[]) {
  const limitsOnly = limitsKey(limits)
  if (limitsOnly === null) return
  seenLimits = limitsOnly
  const account = await accountOf($)
  if (hold) {
    if (limitsOnly === hold.limits && account !== hold.account) return
    hold = null
  }
  const key = inboxKey(limits, account)
  if (key === null) return
  const now = await $.clock.now()
  if (!isDue(key, sent, now)) return
  try {
    const [dir, id, model] = await Promise.all([stateDir($), $.session.id(), $.session.model()])
    await $.fs.write(inboxPath(dir, id), JSON.stringify(inboxReading(limits, now, id, model ?? null, account)))
    sent = { key, limits: limitsOnly, at: now }
  } catch {
    // Nothing depends on one reading; the next one tries again.
  }
}

// The inbox watch, one per process: /clear and a resume keep it.
let watching = false
let busy = false
// The fingerprint this process last saw merged.
let merged: string | null = null
// The interpreter that ran a paceline script last, shared by the merge and the view. It holds a found
// interpreter only; the merge's give-up is its own (below), so a merge that found no Python never stops
// the view from trying again.
const interpreter: Interpreter = { python: undefined }
let mergeGaveUp = false

// The process call with its timeout said out; a final rejection throws RunTimedOut (the command
// may have run), which runPythonScript does not answer by trying another interpreter, and an unknown
// one RunUnknown, which it answers that way for a read only.
function execOf($: EngineInterface): Exec {
  return async (argv, stdin) => {
    try {
      return await $.process.run(argv, { stdin, timeoutMs: RUN_TIMEOUT_MS })
    } catch (err) {
      throw asRunError(err)
    }
  }
}

async function runMerge($: EngineInterface): Promise<boolean> {
  if (!interpreter.python && mergeGaveUp) return false
  // limits_merge.py reads its input to the end; an empty one closes at once. The script always exits 0;
  // anything else is the interpreter, such as the Windows Store stub, and the next one is tried.
  const own: Interpreter = { python: interpreter.python }
  const ran = await runPythonScript(execOf($), own, `${$.plugin.root}/scripts/limits_merge.py`, [], true)
  if (own.python) interpreter.python = own.python
  else if (own.python === null) mergeGaveUp = true
  return ran !== null && ran.exitCode === 0
}

// Runs the merge only for an inbox no session has merged yet.
async function watchInbox($: EngineInterface) {
  if (busy) return
  busy = true
  try {
    const dir = await stateDir($)
    const print = inboxFingerprint(await $.fs.list(`${dir}/inbox`))
    if (print === merged) return
    const seen = await $.fs.read(seenPath(dir)).catch(() => null)
    if (seen !== print && !(await runMerge($))) return
    merged = print
    if (seen !== print) await $.fs.write(seenPath(dir), print)
  } catch {
    // No inbox yet, or a failed call: the next period tries again.
  } finally {
    busy = false
  }
}

// usage-watch: one tick at a time per process; the switch is read on every tick.
let usageBusy = false

async function textAt($: EngineInterface, path: string): Promise<string | null> {
  try {
    const t = await $.fs.read(path)
    return typeof t === 'string' ? t : null
  } catch {
    return null
  }
}

// The request goes out through Claude Code, which adds the session's credential for this host only;
// the plugin never sees it.
async function usageTick($: EngineInterface) {
  if (usageBusy) return
  usageBusy = true
  try {
    const dir = await stateDir($)
    if (!switchOn(await textAt($, usageSwitchPath(dir)))) return
    const before = await accountOf($)
    const key = before ?? 'unknown'
    const gatePath = usageGatePath(dir, key)
    const gate = readGate(await textAt($, gatePath))
    const now = await $.clock.now()
    if (!gateOpen(gate, now, inboxObservedAt(await textAt($, usageInboxPath(dir, key))))) return
    const auth = await $.session.authorize()
    if (!auth) return
    // Recorded before the request, so other sessions of this account see the gate closed while it runs.
    await $.fs.write(gatePath, JSON.stringify({ ...gate, last_attempt: now, last_error: null }))
    let outcome: Outcome
    try {
      const r = await $.http.fetch(USAGE_URL, { auth: auth.handle })
      outcome = readingFrom(r.status, r.text, await $.clock.now())
    } catch {
      // Unreachable, or refused by the engine (CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC).
      outcome = { error: 'network error' }
    }
    if ('reading' in outcome) {
      // A dropped reading leaves the gate as written before the request: the next try is one interval away.
      const tagged = tagOrDrop(outcome.reading, before, await accountOf($))
      if (!tagged) return
      await $.fs.write(usageInboxPath(dir, key), JSON.stringify(tagged))
      await $.fs.write(gatePath, JSON.stringify({ last_attempt: now, last_success: outcome.at, last_error: null }))
    } else {
      await $.fs.write(gatePath, JSON.stringify({ ...gate, last_attempt: now, last_error: outcome.error }))
    }
  } catch {
    // A failed file call: the next tick tries again.
  } finally {
    usageBusy = false
  }
}

// ---------------------------------------------------------------------------------------------
// The settings view (/paceline:settings). Reads and writes go through the paceline CLI, never $.fs:
// the CLI holds the lock and the checks (spec, Shared design). The render hook draws from atoms only.

const PANE = 'paceline-settings'
const SETTINGS_FROM_NOTHING: SettingsView = { result: null, at: null, error: null, line: null, note: null }
const PREVIEW_FROM_NOTHING: PreviewView = { rows: null, resetLocal: null, at: null, error: null, pending: false }
const settingsView = atom({ plugin: 'paceline', key: 'settings' } as const, SETTINGS_FROM_NOTHING)
const previewView = atom({ plugin: 'paceline', key: 'preview' } as const, PREVIEW_FROM_NOTHING)
const openIds = atom({ plugin: 'paceline', key: 'open' } as const, [] as string[])
const editing = atom({ plugin: 'paceline', key: 'editing' } as const, '')
const draft = atom({ plugin: 'paceline', key: 'draft' } as const, null as Draft | null)
const zoneQuery = atom({ plugin: 'paceline', key: 'zoneQuery' } as const, '')

const NO_PYTHON = 'Python was not found (python3 or python)'

// Why a run gave no answer worth reading: no interpreter, a timeout, or the CLI's own reason.
const failure = (ran: Ran | null, write: boolean): string => (ran === null ? NO_PYTHON : ran.timedOut ? timeoutText(write) : ran.rejected !== undefined ? rejectedText(ran.rejected, write) : reasonOf(ran.stderr, ran.exitCode))

// Runs `paceline <args>` as the bin/ launchers do: the dispatcher script under the plugin's root.
// Only a read discovers the interpreter; a write uses the one a read found and never walks the list.
function paceline($: EngineInterface, args: readonly string[], write = false): Promise<Ran | null> {
  return runPythonScript(execOf($), interpreter, `${$.plugin.root}/scripts/paceline.py`, args, false, write)
}

// Reads are numbered when they start; a result older than one already drawn is dropped, so a slow
// read cannot put old values back over a newer one.
let readStarted = 0
let readDrawn = 0

// `paceline settings --json` again; keeps the last good result when the read fails.
async function reload($: EngineInterface): Promise<ViewSettings | null> {
  const mine = ++readStarted
  const ran = await paceline($, ['settings', '--json'])
  const at = await $.clock.now()
  const parsed = ran && ran.exitCode === 0 ? parseSettings(ran.stdout) : null
  const error = parsed ? null : ran && ran.exitCode === 0 ? 'paceline settings --json printed no settings' : failure(ran, false)
  if (mine < readDrawn) return parsed
  readDrawn = mine
  await update($, settingsView, v => (parsed ? { ...v, result: parsed, at, error: null } : { ...v, error }))
  return parsed
}

// One preview run at a time; a request during a run asks for one more, and only the newest result is kept.
let previewBusy = false
let previewAgain = false

async function runPreview($: EngineInterface) {
  if (previewBusy) {
    previewAgain = true
    return
  }
  previewBusy = true
  try {
    do {
      previewAgain = false
      await update($, previewView, p => ({ ...p, pending: true }))
      let ran: Ran | null = null
      try {
        ran = await paceline($, ['simulate', '--preview', '--cached', '--json'])
      } catch {
        ran = null
      }
      if (previewAgain) continue
      const at = await $.clock.now()
      const parsed = ran && ran.exitCode === 0 ? parsePreview(ran.stdout) : null
      const error = parsed ? null : ran && ran.exitCode === 0 ? 'the answer holds no preview' : failure(ran, false)
      await update($, previewView, () =>
        parsed ? { rows: parsed.rows, resetLocal: parsed.resetLocal, at, error: null, pending: false } : { rows: null, resetLocal: null, at, error, pending: false },
      )
    } while (previewAgain)
  } catch {
    await update($, previewView, p => ({ ...p, pending: false, error: 'the preview run failed' })).catch(() => undefined)
  } finally {
    previewBusy = false
  }
}

// Writes go one after another, so two quick presses cannot race each other's re-read.
let writeChain: Promise<unknown> = Promise.resolve()

async function perform($: EngineInterface, argv: readonly string[], row: string | null): Promise<boolean> {
  const ran = await paceline($, argv, true)
  const ok = ran !== null && ran.exitCode === 0
  if (ran && ok) {
    const change = parseChange(ran.stdout)
    if (change.text) $.ui.toast(change.text)
    await update($, settingsView, v => ({ ...v, line: change.statusLine ?? v.line, note: null }))
  } else {
    const reason = failure(ran, true)
    $.ui.toast(reason)
    await update($, settingsView, v => ({ ...v, note: row ? { row, text: reason } : v.note }))
  }
  // Whatever the exit, the next draw shows what is on disk.
  await reload($)
  // Any profile key can move the preview; the cache makes an unchanged input cheap.
  if (ok && argv[0] === 'profile') void runPreview($)
  return ok
}

function write($: EngineInterface, argv: readonly string[], row: string | null): Promise<boolean> {
  const next = writeChain.then(
    () => perform($, argv, row),
    () => perform($, argv, row),
  )
  writeChain = next.catch(() => undefined)
  return next
}

async function openRow($: EngineInterface, s: ViewSettings, item: Item, at: string) {
  const entry = entryOf(s, item)
  let value: unknown = entry?.value
  if (item.id === 'timezone') value = zoneDraft(s.timezone)
  // A stored value of the wrong type (a hand edit) starts from the default.
  else if (!formatValue(item.kind, value).valid) value = entry?.default
  await update($, draft, () => ({ value, text: null }))
  await update($, zoneQuery, () => '')
  await update($, settingsView, v => ({ ...v, note: null }))
  await update($, editing, () => at)
}

// Closes the open row; with `only`, just when that row is still the open one (a Save that answers
// late must not close the row the person has opened since).
async function closeRow($: EngineInterface, only?: string) {
  if (only !== undefined && (await read($, editing)) !== only) return
  await update($, editing, () => '')
  await update($, draft, () => null)
  await update($, settingsView, v => ({ ...v, note: null }))
}

// The value a draft stands for: the applied value, or the text typed since, parsed.
function effective(kind: Kind, d: Draft): Parsed {
  if (kind === 'models') {
    const list = Array.isArray(d.value) ? (d.value as string[]) : []
    const typed = (d.text ?? '').trim().toLowerCase()
    if (typed !== '' && safeModel(typed) === null) return { ok: false }
    return { ok: true, value: typed && !list.includes(typed) ? [...list, typed] : list }
  }
  return d.text !== null ? fromEdit(kind, d.text) : { ok: true, value: d.value }
}

type Ctor = (props: any) => any
type Els = { Box: Ctor; Text: Ctor; Button: Ctor; Input?: Ctor; Select?: Ctor }

const hh = (h: number) => `${String(h).padStart(2, '0')}:00`
const hourOptions = (from: number, to: number) => Array.from({ length: to - from + 1 }, (_, i) => ({ value: String(from + i), label: hh(from + i) }))

async function drawSettings($: EngineInterface, els: Els, surface: string) {
  const { Box, Text, Button, Input, Select } = els
  const view = await read($, settingsView)
  const pv = await read($, previewView)
  const opened = await read($, openIds)
  const edit = await read($, editing)
  const work = await read($, draft)
  const query = await read($, zoneQuery)
  const s = view.result as ViewSettings | null

  // Mobile draws neither Input nor Select: it shows values only. Claude Code 2.1.310 still lists both in
  // the table `$.ui.resolve` returns for mobile, so the surface decides, and a table without them does too.
  const canEdit = surface !== 'mobile' && 'Input' in els && 'Select' in els && Input !== undefined && Select !== undefined

  const toggle = (id: string) =>
    void update($, openIds, list => (list.includes(id) ? list.filter(x => x !== id) : [...list, id]))
  const setDraft = (value: unknown) => void update($, draft, () => ({ value, text: null }))
  const setText = (text: string) => void update($, draft, d => (d ? { ...d, text } : d))

  const header = (
    <Box flexDirection="row" gap={1} alignItems="center">
      <Text bold>PaceLine settings</Text>
      <Button
        key="refresh"
        label="Refresh"
        onPress={() => {
          void reload($)
          void runPreview($)
        }}
      />
      {view.at !== null && <Text dimColor>read {clockText(view.at)}</Text>}
    </Box>
  )

  if (!s) {
    return (
      <Box flexDirection="column" gap={1}>
        {header}
        <Text color="error">{view.error ? `Could not read the settings: ${view.error}` : 'Reading the settings…'}</Text>
      </Box>
    )
  }

  const rowKey = (where: string, id: string) => `${where}:${id}`

  const editorOf = (item: Item, where: string, d: Draft) => {
    if (!canEdit || !Input || !Select) return null
    const kp = `${where}-${item.id}`
    const v = d.value
    switch (item.kind) {
      case 'zone': {
        const z = v as ZoneDraft
        const found = zoneMatches(query, allZones())
        return (
          <Box flexDirection="column" gap={1}>
            <Select
              key={`${kp}-mode`}
              value={z.mode}
              options={[
                { value: 'machine', label: `Follow this machine (${s.timezone.detected ?? s.timezone.effective})` },
                { value: 'fixed', label: z.mode === 'fixed' ? `A fixed zone: ${z.zone}` : 'A fixed zone' },
              ]}
              onSelect={(m: string) => setDraft({ ...z, mode: m === 'machine' ? 'machine' : 'fixed' })}
            />
            {z.mode === 'fixed' && (
              <Box flexDirection="column" gap={1}>
                <Input
                  key={`${kp}-q`}
                  label="Find"
                  placeholder="city or region, for example tokyo"
                  value={query}
                  submitLabel="pick first"
                  onInput={(t: string) => void update($, zoneQuery, () => t)}
                  onSubmit={(t: string) => {
                    const first = zoneMatches(t, allZones())[0]
                    if (first) setDraft({ ...z, zone: first })
                  }}
                />
                {query.trim() !== '' && (
                  <Text dimColor>
                    {found.length === 0 ? 'No zone' : `${found.length}${found.length === 8 ? '+' : ''} found`} for "{query.trim()}"
                  </Text>
                )}
                {found.length > 0 && (
                  <Box flexDirection="row" flexWrap="wrap" gap={1}>
                    {found.map(name => (
                      <Button key={`${kp}-pick-${name}`} label={name === z.zone ? `✓ ${name}` : name} onPress={() => setDraft({ ...z, zone: name })} />
                    ))}
                  </Box>
                )}
              </Box>
            )}
          </Box>
        )
      }
      case 'hours': {
        const [a = 0, b = 0] = Array.isArray(v) ? (v as number[]) : []
        return (
          <Box flexDirection="row" gap={1}>
            <Select key={`${kp}-from`} label="from" value={String(a)} options={hourOptions(0, 23)} onSelect={(x: string) => setDraft([Number(x), b])} />
            <Select key={`${kp}-to`} label="to" value={String(b)} options={hourOptions(1, 24)} onSelect={(x: string) => setDraft([a, Number(x)])} />
          </Box>
        )
      }
      case 'dayhour': {
        const dh = (v ?? { weekday: 0, hour: 0 }) as { weekday: number; hour: number }
        return (
          <Box flexDirection="row" gap={1}>
            <Select
              key={`${kp}-day`}
              value={String(dh.weekday)}
              options={DAYS_SHORT.map((name, i) => ({ value: String(i), label: name }))}
              onSelect={(x: string) => setDraft({ ...dh, weekday: Number(x) })}
            />
            <Select key={`${kp}-hour`} value={String(dh.hour)} options={hourOptions(0, 23)} onSelect={(x: string) => setDraft({ ...dh, hour: Number(x) })} />
          </Box>
        )
      }
      case 'models': {
        const list = Array.isArray(v) ? (v as string[]) : []
        const move = (i: number, by: number) => {
          const next = [...list]
          const j = i + by
          if (j < 0 || j >= next.length) return
          const held = next[i] as string
          next[i] = next[j] as string
          next[j] = held
          setDraft(next)
        }
        return (
          <Box flexDirection="column">
            {list.map((m, i) => (
              <Box key={`${kp}-m-${m}`} flexDirection="row" gap={1} alignItems="center">
                <Text>
                  {i + 1}. {m}
                </Text>
                <Button key={`${kp}-up-${m}`} label="↑" onPress={() => move(i, -1)} />
                <Button key={`${kp}-down-${m}`} label="↓" onPress={() => move(i, 1)} />
                <Button key={`${kp}-rm-${m}`} label="×" onPress={() => setDraft(list.filter(x => x !== m))} />
              </Box>
            ))}
            <Input
              key={`${kp}-add`}
              label="Add"
              placeholder="a model name"
              value={d.text ?? ''}
              submitLabel="add"
              onInput={(t: string) => setText(t)}
              onSubmit={(t: string) => {
                const name = safeModel(t.trim().toLowerCase())
                if (name && !list.includes(name)) setDraft([...list, name])
              }}
            />
          </Box>
        )
      }
      case 'bool':
        return <Button key={`${kp}-bool`} label={v === true ? 'on → turn off' : 'off → turn on'} onPress={() => setDraft(v !== true)} />
      default: {
        const step = STEP[item.kind]
        const n = typeof v === 'number' ? v : null
        const apply = (t: string) => {
          const p = fromEdit(item.kind, t)
          if (p.ok) setDraft(p.value)
        }
        return (
          <Box flexDirection="row" gap={1} alignItems="center">
            {step !== undefined && n !== null && <Button key={`${kp}-minus`} label="−" onPress={() => setDraft(stepped(item.kind, n, -1))} />}
            <Input key={`${kp}-input`} value={d.text ?? toEdit(item.kind, v)} submitLabel="apply" onInput={(t: string) => setText(t)} onSubmit={apply} />
            {step !== undefined && n !== null && <Button key={`${kp}-plus`} label="+" onPress={() => setDraft(stepped(item.kind, n, 1))} />}
            <Text dimColor>= {formatValue(item.kind, v).text}</Text>
          </Box>
        )
      }
    }
  }

  const save = async (item: Item, where: string, d: Draft) => {
    const at = rowKey(where, item.id)
    let argv: string[] | null
    if (item.id === 'timezone') {
      argv = zoneWriteArgs(d.value as ZoneDraft, s.timezone)
    } else {
      const parsed = effective(item.kind, d)
      if (!parsed.ok) {
        await update($, settingsView, v => ({ ...v, note: { row: at, text: `"${d.text ?? ''}" is not a value for ${item.label}` } }))
        return
      }
      if (!wholeOnly(item, parsed.value)) {
        await update($, settingsView, v => ({ ...v, note: { row: at, text: `${item.label} takes whole numbers only` } }))
        return
      }
      argv = writeArgs(item, parsed.value)
    }
    if (!argv) return
    if (await write($, argv, at)) await closeRow($, at)
  }

  const reset = async (item: Item, where: string) => {
    const at = rowKey(where, item.id)
    const entry = entryOf(s, item)
    let argv: string[] | null = null
    if (item.id === 'timezone') argv = zoneWriteArgs({ mode: 'machine', zone: s.timezone.effective }, s.timezone)
    else if (entry && formatValue(item.kind, entry.default).valid) argv = writeArgs(item, entry.default)
    if (!argv) return
    if (await write($, argv, at)) await closeRow($, at)
  }

  // A row is a Button with the label and, beside it, a Text with the value: Desktop draws a Button
  // natively and drops styled children.
  const row = (item: Item, where: string, shown?: string) => {
    const at = rowKey(where, item.id)
    const isOpen = edit === at
    const changed = differs(s, item)
    const text = shown ?? valueText(s, item)
    const d = isOpen ? work : null
    const note = view.note && view.note.row === at ? view.note.text : null
    const entry = entryOf(s, item)
    const zoneRow = item.id === 'timezone'
    const canReset = zoneRow ? s.timezone.mode !== 'machine' : changed && entry !== undefined && formatValue(item.kind, entry.default).valid
    return (
      <Box key={`${where}-${item.id}`} flexDirection="column">
        <Box flexDirection="row" gap={1} alignItems="center">
          <Button
            key={`${where}-row-${item.id}`}
            label={`${isOpen ? '▾' : '▸'} ${item.label}`}
            onPress={() => {
              if (isOpen) void closeRow($)
              else void openRow($, s, item, at)
            }}
          />
          <Text color={changed ? 'warning' : undefined} bold={changed} dimColor={!changed}>
            {`${changed ? MARK : ''}${text}`}
          </Text>
        </Box>
        {isOpen && (
          <Box flexDirection="column" paddingLeft={2} paddingY={1} gap={1} borderStyle="round" borderDimColor>
            <Text>{item.meaning}</Text>
            {canEdit && d && editorOf(item, where, d)}
            {note && <Text color="error">{note}</Text>}
            {canEdit && d && (
              <Box flexDirection="row" gap={1}>
                <Button key={`${where}-${item.id}-save`} variant="primary" label="Save" onPress={() => void save(item, where, d)} />
                <Button key={`${where}-${item.id}-cancel`} label="Cancel" onPress={() => void closeRow($)} />
                {canReset && (
                  <Button
                    key={`${where}-${item.id}-reset`}
                    label={zoneRow ? 'Follow this machine' : `Reset to ${defaultText(s, item)}`}
                    onPress={() => void reset(item, where)}
                  />
                )}
              </Box>
            )}
            <Text dimColor>
              {zoneRow ? `default follows this machine · range ${item.range} · timezone, timezone_mode` : detailLine(s, item)}
            </Text>
          </Box>
        )}
      </Box>
    )
  }

  const selectStyle = (name: string) => {
    if (name === styleName(s) || !(name in STYLE_LABEL) || name === 'custom') return
    void write($, styleArgs(name), null)
  }

  const flip = async (name: (typeof SWITCH_NAMES)[number], on: boolean) => {
    await write($, switchArgs(name, !on, await $.session.id()), null)
  }

  const states = switchStates(s.switches)
  const section = (name: string, color: string, children: unknown) => (
    <Box key={`section-${name}`} flexDirection="column" borderStyle="round" borderColor={color} paddingX={1}>
      <Text bold color={color}>
        {name}
      </Text>
      {children}
    </Box>
  )

  const previewRows = pv.rows ?? s.preview
  const previewReset = pv.rows ? pv.resetLocal : s.preview_inputs?.reset_local
  const whyOpen = opened.includes('preview-why')
  const styleKey = styleName(s) === 'custom' ? 'current' : styleName(s)
  const queue = queueText(previewRows?.[styleKey])
  const styleOpen = opened.includes('style')
  const styleOptions = (['careful', 'balanced', 'full'] as const).map(name => ({ value: name, label: STYLE_LABEL[name] as string }))

  const preview = pv.pending ? (
    <Box flexDirection="column">
      <Text dimColor>{previewHeader(previewReset)}</Text>
      <Text dimColor>estimating…</Text>
    </Box>
  ) : pv.error ? (
    <Box flexDirection="column">
      <Box flexDirection="row" gap={1} alignItems="center">
        <Text dimColor>preview not available</Text>
        <Button key="preview-why" label={whyOpen ? '▾ why' : '▸ why'} onPress={() => toggle('preview-why')} />
      </Box>
      {whyOpen && <Text dimColor>{pv.error}</Text>}
    </Box>
  ) : previewLines(previewRows).length > 0 ? (
    <Box flexDirection="column">
      <Text dimColor>{previewHeader(previewReset)}</Text>
      {previewLines(previewRows).map(line => (
        <Text key={`preview-${line}`} dimColor>
          {line}
        </Text>
      ))}
    </Box>
  ) : null

  return (
    <Box flexDirection="column" gap={1}>
      {header}
      {view.error && <Text color="error">{`The last read failed, these values may be old: ${view.error}`}</Text>}
      <Box flexDirection="column">
        <Text bold>State</Text>
        <Box flexDirection="row" flexWrap="wrap" gap={1}>
          {states.map(x =>
            canEdit ? (
              <Button key={`switch-${x.name}`} label={`${x.on ? '●' : '○'} ${x.name}`} onPress={() => void flip(x.name, x.on)} />
            ) : (
              <Text key={`switch-${x.name}`}>{`${x.on ? '●' : '○'} ${x.name}`}</Text>
            ),
          )}
        </Box>
        {view.line && <Text dimColor>{view.line}</Text>}
      </Box>
      {TIERS.filter(t => t.tier !== 'style').map(t => section(t.name, t.color, rowsOf(t.tier).map(item => row(item, t.tier))))}
      {section(
        'Spending style',
        TIERS.find(t => t.tier === 'style')?.color ?? ADVANCED_COLOR,
        <Box flexDirection="column">
          {canEdit && Select ? (
            <Select
              key="style-select"
              value={styleName(s)}
              options={[...styleOptions, ...(styleName(s) === 'custom' ? [{ value: 'custom', label: STYLE_LABEL.custom as string }] : [])]}
              onSelect={selectStyle}
            />
          ) : (
            <Text>{styleDisplay(s)}</Text>
          )}
          {preview}
          <Button key="style-detail" label={`${styleOpen ? '▾' : '▸'} What it sets (${STYLE_KEYS.length})`} onPress={() => toggle('style')} />
          {styleOpen && (
            <Box flexDirection="column" paddingLeft={2}>
              {styleRows(s).map(r => row(r.item, 'style', `${r.line}`))}
              {queue && <Text dimColor>{queue}</Text>}
              {s.files.styles && <Text dimColor>{`overrides: ${s.files.styles}`}</Text>}
              {s.style_warnings.map(w => (
                <Text key={`warn-${w}`} color="warning">{`Style override ignored: ${w}`}</Text>
              ))}
            </Box>
          )}
        </Box>,
      )}
      {section(
        'Advanced',
        ADVANCED_COLOR,
        <Box flexDirection="column">
          <Button key="advanced" label={`${opened.includes('advanced') ? '▾' : '▸'} ${advancedLabel(s)}`} onPress={() => toggle('advanced')} />
          {opened.includes('advanced') &&
            ADVANCED_TOPICS.map(topic => (
              <Box key={`topic-${topic}`} flexDirection="column" paddingLeft={2}>
                <Button key={`topic-btn-${topic}`} label={`${opened.includes(topic) ? '▾' : '▸'} ${topicLabel(s, topic)}`} onPress={() => toggle(topic)} />
                {opened.includes(topic) && (
                  <Box flexDirection="column" paddingLeft={2}>
                    {advancedOf(topic).map(item => row(item, `t-${topic}`))}
                  </Box>
                )}
              </Box>
            ))}
        </Box>,
      )}
    </Box>
  )
}

// What /paceline:settings answers; null means the module cannot answer and the skill's text runs.
async function answer($: EngineInterface, args: string): Promise<CommandRunResult | null> {
  const [word = '', ...rest] = args.trim().split(/\s+/).filter(Boolean)
  if (word === 'pane') {
    await update($, editing, () => '')
    // Opened first: the pane draws "Reading the settings…" while the read runs.
    const opened = await $.ui.open({ id: PANE, title: 'PaceLine settings' })
    // Not awaited: the pane never waits on a simulation.
    void runPreview($)
    const parsed = await reload($)
    if (opened.isPlaced) return { text: 'PaceLine settings opened in a pane.' }
    const text = `${parsed ? summary(parsed, $.plugin.name) : ''}\n\nThe pane could not be placed: ${opened.reason}`.trim()
    return { text, context: [text] }
  }
  const parsed = await reload($)
  if (!parsed) return null
  if (word === 'advanced') {
    const text = advancedListing(parsed, rest.join(' ') || undefined)
    return { text, context: [text] }
  }
  const text = word ? `Usage: /${$.plugin.name}:settings [pane | advanced [topic]]\n\n${summary(parsed, $.plugin.name)}` : summary(parsed, $.plugin.name)
  return { text, context: [text] }
}

// The person's own prompt in any session clears the away marker. Done before the turn starts, so a
// launch early in it already sees the marker off.
async function clearAway($: EngineInterface) {
  const path = `${await stateDir($)}/${AWAY_FILE}`
  if (isAwayOn(await textAt($, path))) await $.fs.write(path, CLEARED)
}

export const register: Register = on => {
  // /paceline:settings, answered before the model. Exactly this plugin's own name: another plugin's
  // `settings` skill, and a bare `settings`, run as they do.
  on('command.run', async ($, e, next) => {
    if (e.command !== `${$.plugin.name}:settings`) return next(e)
    let answered: CommandRunResult | null = null
    try {
      answered = await answer($, e.args)
    } catch {
      answered = null
    }
    // A null answer (no settings could be read) lets the skill's text run, so the model runs the CLI.
    // next(e) runs once, outside the try: a failure in the chain is not answered by running it again.
    return answered ?? next(e)
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => drawSettings($, $.ui.resolve(e) as Els, e.surface))

  on('prompt.submit', async ($, e, next) => {
    try {
      if (isPersonPrompt(e, $.plugin.name)) await clearAway($)
    } catch {
      // The marker stays; the prompt goes on.
    }
    return next(e)
  })

  on('classic.UserPromptSubmit', async ($, e, next) => {
    const result = await next(e)
    try {
      if (e.agent_id) return result
      const settings = await settingsOf($)
      if (!settings.on) return result
      if (isOperatorPrompt(e.prompt, e.source)) {
        await change($, s => ({ ...s, prompts: keepLast(s.prompts, e.prompt, PROMPTS_KEPT) }))
      }
      const fill = await fillOf($)
      if (!fill) return result
      const added: string[] = []
      if (settings.line) added.push(formatLine(fill))
      const warn = await warning($, fill, settings)
      if (warn) added.push(warn)
      return added.length ? { ...result, additionalContext: [...(result.additionalContext ?? []), added.join('\n')] } : result
    } catch {
      return result
    }
  })

  on('classic.PostToolBatch', async ($, e, next) => {
    const result = await next(e)
    try {
      if (e.agent_id) return result
      const settings = await settingsOf($)
      if (!settings.on) return result
      const paths = writtenPaths(e.tool_calls)
      if (paths.length) await change($, s => ({ ...s, written: addPaths(s.written, paths) }))
      const fill = await fillOf($)
      if (!fill) return result
      const warn = await warning($, fill, settings)
      return warn ? { ...result, additionalContext: [...(result.additionalContext ?? []), warn] } : result
    } catch {
      return result
    }
  })

  // Never vetoes, delays or replaces a compaction: next(e) always runs and its result goes back as is.
  on('session.compact', async ($, e, next) => {
    let down = e
    try {
      if (!e.agentId && e.trigger !== 'precompute' && (await settingsOf($)).on) {
        const s = await current($)
        const paths = await snapshotPath($)
        const text = snapshotText({
          at: new Date(await $.clock.now()).toISOString(),
          trigger: e.trigger,
          compaction: s.compactions + 1,
          prompts: s.prompts,
          messages: e.messages,
          written: s.written,
        })
        if (!(await $.fs.exists(paths.ignore))) await $.fs.write(paths.ignore, '*\n')
        await $.fs.write(paths.file, text)
        await change($, x => ({ ...x, compactions: x.compactions + 1 }))
        down = { ...e, instructions: compactInstructions(e.instructions, paths.file) }
      }
    } catch {
      down = e
    }
    return next(down)
  })

  // Runs whatever the context-watch settings and the pace switch say: the limits file serves every reader.
  on('session.measure', async ($, e, next) => {
    try {
      await writeInbox($, e.rateLimits ?? [])
    } catch {
      // writeInbox swallows its own errors; this guards the turn against anything else.
    }
    return next(e)
  })

  // The account in .claude.json has already changed when next(e) returns, so the hold starts before it.
  for (const command of ['login', 'logout']) {
    on('command.run', { command }, async ($, e, next) => {
      try {
        hold = { limits: seenLimits, account: await accountOf($) }
        if ((await envAccountOf($)) !== null) accountLost = true
      } catch {
        hold = null
      }
      return next(e)
    })
  }

  on('session.start', async ($, e, next) => {
    const result = await next(e)
    if (!watching) {
      watching = true
      $.clock.every(WATCH_PERIOD_MS, () => {
        void watchInbox($)
      })
      // Not awaited: a session start never waits on the network.
      void usageTick($)
      $.clock.every(USAGE_TICK_MS, () => {
        void usageTick($)
      })
    }
    // The inbox is taken in when the process starts, not one period later; the timer covers turn ends.
    void watchInbox($)
    return result
  })

  on('classic.SessionStart', async ($, e, next) => {
    const result = await next(e)
    try {
      if (e.source !== 'compact' || e.agent_id) return result
      if (!(await settingsOf($)).on) return result
      await change($, s => ({ ...s, warned: [] }))
      const paths = await snapshotPath($)
      if (!(await $.fs.exists(paths.file))) return result
      return { ...result, additionalContext: [...(result.additionalContext ?? []), recoverText(paths.file)] }
    } catch {
      return result
    }
  })
}
