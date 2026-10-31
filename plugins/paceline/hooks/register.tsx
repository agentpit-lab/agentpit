import { atom, read, update } from 'claude-code'
import type { CommandRunResult, EngineInterface, Register } from 'claude-code'

import type { CalendarView, ContextSession, SettingsView } from '../types'
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
import { ACCOUNT_KEY, accountFileOf, accountKeyOf, credentialsFileOf, PROFILE_URL, profileKeyOf } from './account'
import { AWAY_FILE, CLEARED, isAwayOn, isPersonPrompt } from './away'
import { inboxKey, inboxPath, inboxReading, isDue, limitsKey, stateDirOf } from './inbox'
import type { RateLimit, Sent } from './inbox'
import {
  asRunError,
  inboxFingerprint,
  planDue,
  readingWeekReset,
  RUN_TIMEOUT_MS,
  runPythonScript,
  seenPath,
  WATCH_PERIOD_MS,
  weekPlanPath,
} from './watch'
import type { Exec, Interpreter, Ran } from './watch'
import {
  ADVANCED_COLOR,
  ADVANCED_TOPICS,
  aboveScale,
  advancedLabel,
  advancedListing,
  advancedOf,
  allZones,
  barColors,
  calendarChange,
  cell,
  clockText,
  cutText,
  DAYS_SHORT,
  dayTitle,
  defaultText,
  detailLine,
  differs,
  entryOf,
  endedRun,
  endedText,
  formatValue,
  formatWorkHours,
  fromEdit,
  MARK,
  markLabel,
  nowText,
  numbersLine,
  reserveOpensText,
  WEEKDAY_KEYS,
  parseChange,
  parsePlan,
  parseSettings,
  parseWorkHours,
  planSetArgs,
  reasonOf,
  rejectedText,
  rowsOf,
  shareText,
  STEP,
  stepMark,
  stepped,
  stepWorkHours,
  summary,
  SWITCH_NAMES,
  timeoutText,
  switchArgs,
  switchStates,
  TIERS,
  TRACK_COLOR,
  toEdit,
  topicLabel,
  unplannedLine,
  valueText,
  warnText,
  weekLine,
  wholeOnly,
  workHoursLength,
  writeArgs,
  zoneDraft,
  zoneMatches,
  zoneWriteArgs,
} from './settings'
import type { Draft, Item, Kind, Parsed, PlanDay, PlanPart, PlanView, Settings as ViewSettings, ZoneDraft } from './settings'
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

// The account of the session's credential, from the profile endpoint. Until its first answer no reading
// is written; when it is refused, or three asks in a row give no key, the state file names the account
// (never under Desktop, whose state file names the last terminal login).
const profile = {
  ready: false,
  key: null as string | null,
  fallback: false,
  // Why a ready profile has no key and no fallback: no credential at all, or after /logout.
  silence: null as 'no-credential' | 'logged-out' | null,
  failures: 0,
  asking: false,
  // What stood at the last ask: the modification time of the credentials file, and the state file's key.
  // A change of either asks again; a rewrite of the state file that leaves its key alone does not.
  cred: null as string | null,
  stateKey: null as string | null,
  // The modification time of the state file when its key was last read, so an unchanged file is not parsed again.
  stateMtime: null as number | null,
  // Background re-asks while in fallback: how many were scheduled since the fallback began.
  retries: 0,
}
// While in fallback the profile is asked again after 5 minutes, then every 10 minutes.
const RETRY_FIRST_MS = 5 * 60_000
const RETRY_NEXT_MS = 10 * 60_000
// Numbers the pending retry timer; every ask and every new schedule invalidates the older one.
let retryGen = 0
// The newest ask owns the answer; an older one that returns late is dropped.
let asked = 0
// Set by /logout, cleared by the next answer that names an account.
let loggedOut = false

type Entry = {
  account: string | null
  source: 'profile' | 'state-file' | null
  reason: 'desktop' | 'refused' | 'no-state-key' | 'no-credential' | 'logged-out' | null
}
// The last entry written to session-accounts, with its session.
let noted: { session: string; entry: Entry } | null = null
// An entry older than this is written again, so a pruned or deleted one heals.
const ENTRY_MAX_AGE_MS = 24 * 60 * 60_000

async function configOf($: EngineInterface) {
  return { configDir: await $.env.get('CLAUDE_CONFIG_DIR'), home: (await $.env.get('USERPROFILE')) ?? (await $.env.get('HOME')) }
}

async function stateMtime($: EngineInterface): Promise<number | null> {
  try {
    return (await $.fs.stat(accountFileOf(await configOf($)))).mtimeMs
  } catch {
    return null
  }
}

async function credStamp($: EngineInterface): Promise<string | null> {
  try {
    return JSON.stringify((await $.fs.stat(credentialsFileOf(await configOf($)))).mtimeMs)
  } catch {
    return null
  }
}

// One GET of the profile endpoint through Claude Code, which adds the session's credential; the plugin
// never sees it. Only the key of the two identifiers is kept, never the body. `reset` starts the count
// of failed answers again; an ask that repeats a failed one, or follows a change of the state file,
// keeps it.
async function askProfile($: EngineInterface, reset = false) {
  const mine = ++asked
  retryGen++
  profile.ready = false
  profile.asking = true
  if (reset) profile.failures = 0
  try {
    profile.cred = await credStamp($)
    profile.stateMtime = await stateMtime($)
    // Unreadable (undefined) is ignored; no key (null) is recorded.
    const stateKey = await fileKeyOf($)
    if (stateKey !== undefined) profile.stateKey = stateKey
    const auth = await $.session.authorize()
    if (!auth) {
      // No first-party credential: readings go out untagged, as before accounts were told apart.
      if (mine === asked) Object.assign(profile, { ready: true, key: null, fallback: false, silence: loggedOut ? 'logged-out' : 'no-credential' })
      return
    }
    const r = await $.http.fetch(PROFILE_URL, { auth: auth.handle })
    if (mine !== asked) return
    const got = profileKeyOf(r.status, r.text)
    if (got.key !== null) {
      loggedOut = false
      Object.assign(profile, { ready: true, key: got.key, fallback: false, silence: null, failures: 0 })
    } else {
      profile.failures++
      profile.fallback = profile.failures >= 3
      profile.ready = profile.fallback
    }
  } catch {
    // Refused by the engine (CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC), unreachable, or a failed call:
    // nothing of the error is kept.
    if (mine === asked) Object.assign(profile, { ready: true, key: null, fallback: true, silence: null })
  } finally {
    if (mine === asked) profile.asking = false
  }
  if (mine === asked) {
    if (profile.fallback) scheduleRetry($)
    else profile.retries = 0
  }
  // The answer is in (also a fallback one): readers get the session's entry without waiting for a reading.
  if (mine === asked && profile.ready) await accountOf($)
}

// In fallback, asks the profile again in the background (5 minutes, then 10 minutes apart). An ask in
// flight is never overlapped: its end schedules the next one. A success leaves the fallback through
// the normal path, and the session's entry then names the profile's account.
function scheduleRetry($: EngineInterface) {
  const gen = ++retryGen
  const delay = profile.retries === 0 ? RETRY_FIRST_MS : RETRY_NEXT_MS
  profile.retries++
  $.clock.after(delay, () => {
    if (gen !== retryGen || !profile.fallback || profile.asking) return
    void askProfile($)
  })
}

// On each usage-watch tick: asks again when the credentials file changed or the state file names
// another account, repeats an ask that failed, and otherwise sees that the session's entry stands.
async function profileTick($: EngineInterface) {
  if (profile.asking) return
  const cred = await credStamp($)
  // The state file is parsed only when its modification time moved.
  const mtime = await stateMtime($)
  let state: string | null | undefined
  if (mtime !== profile.stateMtime) {
    state = await fileKeyOf($)
    if (state !== undefined) profile.stateMtime = mtime
  }
  if (cred !== profile.cred) void askProfile($, true)
  else if (state !== undefined && state !== profile.stateKey) void askProfile($)
  else if (!profile.ready) void askProfile($)
  else void accountOf($, true)
}

// The key of the state file, for the fallback and the change check: null when it names no account,
// undefined when it could not be read. The file is read at every use: a login in another session
// changes it under this process. Claude Code rewrites the file often, so a read that fails or does not
// parse is tried once more.
async function fileKeyOf($: EngineInterface): Promise<string | null | undefined> {
  const file = accountFileOf(await configOf($))
  if (!(await $.fs.exists(file))) return null
  for (let attempt = 0; attempt < 2; attempt++) {
    const account = accountKeyOf(await textAt($, file))
    if (account.reason !== 'unreadable') return account.key
    if (attempt === 0) await new Promise<void>(resolve => $.clock.after(50, () => resolve()))
  }
  return undefined
}

// Tells readers (pace.py) which account this session runs under. Written when the entry's content
// changed; with `verify`, also when the file is missing or older than a day.
async function noteSession($: EngineInterface, entry: Entry, verify = false) {
  try {
    const id = await $.session.id()
    const path = `${await stateDir($)}/session-accounts/${id}.json`
    const same = noted !== null && noted.session === id && noted.entry.account === entry.account && noted.entry.source === entry.source && noted.entry.reason === entry.reason
    if (same) {
      if (!verify) return
      const now = await $.clock.now()
      const age = await $.fs.stat(path).then(
        s => now - s.mtimeMs,
        () => Infinity,
      )
      if (age < ENTRY_MAX_AGE_MS) return
    }
    const at = new Date(await $.clock.now()).toISOString()
    await $.fs.write(path, JSON.stringify({ ...entry, checked_at: at }))
    noted = { session: id, entry }
  } catch {
    // The next use tries again.
  }
}

// The account to tag a reading with: { key } (null key: untagged), or null while it is not known and
// nothing may be written. The session's entry follows it; silence writes account null with its reason,
// so an old key never stays there.
async function accountOf($: EngineInterface, verify = false): Promise<{ key: string | null } | null> {
  try {
    if (!profile.ready) return null
    if (!profile.fallback) {
      const key = profile.key
      await noteSession($, { account: key, source: key === null ? null : 'profile', reason: key === null ? profile.silence : null }, verify)
      return { key }
    }
    if ((await $.env.get('CLAUDE_CODE_ENTRYPOINT')) === 'claude-desktop') {
      await noteSession($, { account: null, source: null, reason: 'desktop' }, verify)
      return null
    }
    const key = (await fileKeyOf($)) ?? null
    await noteSession($, { account: key, source: 'state-file', reason: key === null ? 'no-state-key' : null }, verify)
    return { key }
  } catch {
    // Whatever failed, the account is unknown; nothing of the file or the error is kept.
    return null
  }
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
  const known = await accountOf($)
  if (!known) return
  const account = known.key
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
      // The child reads readings under the account this session runs under, not the state file's.
      const known = await accountOf($)
      const env = known?.key ? { PACELINE_ACCOUNT_KEY: known.key } : undefined
      return await $.process.run(argv, { stdin, timeoutMs: RUN_TIMEOUT_MS, ...(env ? { env } : {}) })
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

// Runs the merge for an inbox no session has merged yet. True when it ran the merge.
async function mergeInbox($: EngineInterface, dir: string): Promise<boolean> {
  const print = inboxFingerprint(await $.fs.list(`${dir}/inbox`))
  if (print === merged) return false
  const seen = await $.fs.read(seenPath(dir)).catch(() => null)
  if (seen === print) {
    merged = print
    return false
  }
  if (!(await runMerge($))) return true
  merged = print
  try {
    await $.fs.write(seenPath(dir), print)
  } catch {
    // The merge ran; another session may merge once more, which does no harm.
  }
  return true
}

// The start of this process's last boundary-trigger run; none yet.
let lastPlanRun: number | null = null

// The boundary trigger: runs the merge whatever the seen file holds when the week plan file does not hold
// now, with pace on and the session's account key known. Nothing is spent: it tries again, at the pace
// planDue sets, until a re-read finds a part that holds now. The merge's plan step does the fixing.
async function watchPlan($: EngineInterface, dir: string, ran: boolean) {
  const now = await $.clock.now()
  // A merge just run, for the inbox, fixed what it could: the trigger waits one step before it looks again.
  if (ran) {
    lastPlanRun = now
    return
  }
  const sw = await textAt($, `${dir}/pace.json`)
  try {
    if ((JSON.parse(sw ?? 'null') as { on?: unknown } | null)?.on !== true) return
  } catch {
    return
  }
  const key = (await accountOf($))?.key
  if (typeof key !== 'string' || !ACCOUNT_KEY.test(key)) return
  if (!planDue(await textAt($, weekPlanPath(dir, key)), now, lastPlanRun, readingWeekReset(await textAt($, `${dir}/rate-limits.json`), key)).run) return
  lastPlanRun = now
  await runMerge($)
}

async function watchInbox($: EngineInterface) {
  if (busy) return
  busy = true
  try {
    const dir = await stateDir($)
    let ran = false
    try {
      ran = await mergeInbox($, dir)
    } catch {
      // No inbox yet, or a failed call: the trigger below does not depend on it.
    }
    await watchPlan($, dir, ran)
  } catch {
    // A failed call: the next period tries again.
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
  // Before the in-flight guard: a key that changes during a usage request must be asked about meanwhile.
  try {
    await profileTick($)
  } catch {
    // The next tick tries again.
  }
  if (usageBusy) return
  usageBusy = true
  try {
    const dir = await stateDir($)
    if (!switchOn(await textAt($, usageSwitchPath(dir)))) return
    const known = await accountOf($)
    if (!known) return
    const before = known.key
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
      const after = await accountOf($)
      const tagged = after ? tagOrDrop(outcome.reading, before, after.key) : null
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
// Whether this module placed the settings pane, kept outside the atoms: a press decides open or close
// before its first await, and an atom read is an await. The session start seeds it again after a reload.
let paneOpenNow = false
// Whether a press's open is still on its way (pressPane).
let opening = false
const UNPLACED = 'The pane could not be placed'
const SETTINGS_FROM_NOTHING: SettingsView = { result: null, at: null, error: null, line: null, note: null }
const settingsView = atom({ plugin: 'paceline', key: 'settings' } as const, SETTINGS_FROM_NOTHING)
const openIds = atom({ plugin: 'paceline', key: 'open' } as const, [] as string[])
const editing = atom({ plugin: 'paceline', key: 'editing' } as const, '')
const draft = atom({ plugin: 'paceline', key: 'draft' } as const, null as Draft | null)
const zoneQuery = atom({ plugin: 'paceline', key: 'zoneQuery' } as const, '')
const CALENDAR_FROM_NOTHING: CalendarView = { result: null, at: null, error: null, note: null }
const tab = atom({ plugin: 'paceline', key: 'tab' } as const, 'settings' as 'settings' | 'calendar')
// Whether the calendar's folded ended days are unfolded; the pane opens with them folded.
const calendarEnded = atom({ plugin: 'paceline', key: 'calendarEnded' } as const, false)
const calendarView = atom({ plugin: 'paceline', key: 'calendar' } as const, CALENDAR_FROM_NOTHING)
// Whether PitBoard took this plugin's button; its own Desktop footer button then stays out.
const inPitboard = atom({ plugin: 'paceline', key: 'inPitboard' } as const, false)

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

// The calendar's reads are numbered the same way, apart from the settings'. A write's own answer takes a
// number too, so an older read cannot put an old plan over it.
let calendarStarted = 0
let calendarDrawn = 0

// `paceline plan --json` again; keeps the last good plan when the read fails. A person's own press
// (`fresh`) clears the note of a refused change; the read that follows a refusal keeps it.
async function readCalendar($: EngineInterface, fresh = false): Promise<{ plan: PlanView | null; error: string | null }> {
  const mine = ++calendarStarted
  const ran = await paceline($, ['plan', '--json'])
  const at = await $.clock.now()
  const parsed = ran && ran.exitCode === 0 ? parsePlan(ran.stdout) : null
  const error = parsed ? null : ran && ran.exitCode === 0 ? 'paceline plan --json printed no plan' : failure(ran, false)
  if (mine < calendarDrawn) return { plan: parsed, error }
  calendarDrawn = mine
  await update($, calendarView, v => {
    const note = fresh ? null : v.note
    return parsed ? { result: parsed, at, error: null, note } : { ...v, error, note }
  })
  return { plan: parsed, error }
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
  // The working hours move the cut parts, so a calendar that is held or shown reads again.
  if (ok && argv[0] === 'profile' && argv[2] === 'work_hours') {
    if ((await read($, calendarView)).result !== null || (await read($, tab)) === 'calendar') await readCalendar($)
  }
  return ok
}

// One mark change of the calendar: `paceline plan set`, then the view its answer carries (built after the
// change in the same lock hold), or a read when the answer holds none or the write failed. The step starts
// from a plan read at that moment (the write chain runs one press after another, so quick presses on one
// part add up), never from the drawn one, which may be hours old: a mark set elsewhere is not lowered.
async function performMark($: EngineInterface, date: string, kind: 'work' | 'off', direction: 1 | -1) {
  const { plan: held, error } = await readCalendar($)
  if (!held) {
    const reason = error ?? 'the week plan could not be read'
    $.ui.toast(reason)
    await update($, calendarView, v => ({ ...v, note: `Nothing changed: ${reason}` }))
    return
  }
  const day = held.week_days.find(d => d.date === date)
  const part = kind === 'work' ? day?.work : day?.off
  if (part && part.state !== 'later') {
    // The part started (or ended) since the plan was drawn: nothing is written, and an older note goes.
    $.ui.toast(`${date} ${kind === 'work' ? '☼' : '☾'} has started; nothing changed`)
    await update($, calendarView, v => ({ ...v, note: null }))
    return
  }
  if (!part) return
  const mark = stepMark(part.mark, direction)
  if (mark === null) return
  const ran = await paceline($, planSetArgs(date, kind, mark), true)
  if (ran && ran.exitCode === 0) {
    const change = calendarChange(ran.stdout, date, kind, mark)
    if (change.text) $.ui.toast(change.text)
    if (change.plan) {
      const at = await $.clock.now()
      calendarDrawn = ++calendarStarted
      const plan = change.plan
      await update($, calendarView, () => ({ result: plan, at, error: null, note: null }))
      return
    }
    await update($, calendarView, v => ({ ...v, note: null }))
  } else {
    const reason = failure(ran, true)
    $.ui.toast(reason)
    await update($, calendarView, v => ({ ...v, note: reason }))
  }
  await readCalendar($)
}

function changeMark($: EngineInterface, date: string, kind: 'work' | 'off', direction: 1 | -1): Promise<void> {
  const next = writeChain.then(
    () => performMark($, date, kind, direction),
    () => performMark($, date, kind, direction),
  )
  writeChain = next.catch(() => undefined)
  return next
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
  return d.text !== null ? fromEdit(kind, d.text) : { ok: true, value: d.value }
}

type Ctor = (props: any) => any
type Els = { Box: Ctor; Text: Ctor; Button: Ctor; Input?: Ctor; Select?: Ctor }

const hh = (h: number) => `${String(h).padStart(2, '0')}:00`
const hourOptions = (from: number, to: number) => Array.from({ length: to - from + 1 }, (_, i) => ({ value: String(from + i), label: hh(from + i) }))

async function drawSettings($: EngineInterface, els: Els, surface: string) {
  const { Box, Text, Button, Input, Select } = els
  const view = await read($, settingsView)
  const opened = await read($, openIds)
  const edit = await read($, editing)
  const work = await read($, draft)
  const query = await read($, zoneQuery)
  const shown = await read($, tab)
  const cal = await read($, calendarView)
  const s = view.result as ViewSettings | null

  // Mobile draws neither Input nor Select: it shows values only. Claude Code 2.1.310 still lists both in
  // the table `$.ui.resolve` returns for mobile, so the surface decides, and a table without them does too.
  const canEdit = surface !== 'mobile' && 'Input' in els && 'Select' in els && Input !== undefined && Select !== undefined

  const toggle = (id: string) =>
    void update($, openIds, list => (list.includes(id) ? list.filter(x => x !== id) : [...list, id]))
  const setDraft = (value: unknown) => void update($, draft, () => ({ value, text: null }))
  const setText = (text: string) => void update($, draft, d => (d ? { ...d, text } : d))

  const readAt = shown === 'calendar' ? cal.at : view.at
  const header = (
    <Box flexDirection="row" flexWrap="wrap" justifyContent="space-between" alignItems="center">
      <Box flexDirection="row" gap={1} alignItems="center">
        <Text bold>PaceLine</Text>
        <Box key="tabs" flexDirection="row" gap={0} borderStyle="round">
          <Button key="tab-settings" label="Settings" variant={shown === 'settings' ? 'primary' : 'secondary'} dimColor={shown !== 'settings'} onPress={() => void update($, tab, () => 'settings')} />
          <Button
            key="tab-calendar"
            label="Calendar"
            variant={shown === 'calendar' ? 'primary' : 'secondary'}
            dimColor={shown !== 'calendar'}
            onPress={() => {
              void update($, tab, () => 'calendar')
              void readCalendar($, true)
            }}
          />
        </Box>
      </Box>
      <Box flexDirection="row" gap={1} alignItems="center">
        {readAt !== null && <Text dimColor>read {clockText(readAt as number)}</Text>}
        <Button
          key="refresh"
          label="↻ Refresh"
          dimColor
          onPress={() => {
            if (shown === 'calendar') {
              void readCalendar($, true)
              return
            }
            void reload($)
          }}
        />
      </Box>
    </Box>
  )

  // The calendar draws from its own atom and never waits on the settings.
  if (shown === 'calendar') return drawCalendar($, els, canEdit, header, cal, await read($, calendarEnded))

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
      case 'weekday':
        return (
          <Select
            key={`${kp}-weekday`}
            value={typeof v === 'string' ? v : 'last'}
            options={[{ value: 'last', label: 'last day before the reset' }, ...WEEKDAY_KEYS.map((value, i) => ({ value, label: DAYS_SHORT[i] ?? value }))]}
            onSelect={(x: string) => setDraft(x === 'last' ? null : x)}
          />
        )
      case 'workhours': {
        const text = typeof v === 'string' ? v : ''
        const parsed = parseWorkHours(text)
        if (!parsed) return null
        const [from, to] = formatWorkHours(parsed[0], parsed[1]).split('-')
        const step = (end: 'start' | 'end', direction: 1 | -1) => {
          const next = stepWorkHours(text, end, direction)
          return next === null ? null : (
            <Button key={`${kp}-${end}-${direction < 0 ? 'minus' : 'plus'}`} label={direction < 0 ? '−' : '+'} onPress={() => setDraft(next)} />
          )
        }
        return (
          <Box flexDirection="row" flexWrap="wrap" gap={1} alignItems="center">
            {step('start', -1)}
            <Text>{`start ${from}`}</Text>
            {step('start', 1)}
            {step('end', -1)}
            <Text>{`end ${to}`}</Text>
            {step('end', 1)}
            <Text dimColor>{`= ${formatWorkHours(parsed[0], parsed[1])} (${workHoursLength(text)} h)`}</Text>
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
    // A plan-file row cannot be written without a value to start from: no entry, or one that holds a reason instead.
    const editable = item.source !== 'plan' || (entry !== undefined && !(entry.value === null && typeof entry.note === 'string' && entry.note !== ''))
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
            {canEdit && editable && d && editorOf(item, where, d)}
            {note && <Text color="error">{note}</Text>}
            {canEdit && editable && d && (
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
      {TIERS.map(t => section(t.name, t.color, rowsOf(t.tier).map(item => row(item, t.tier))))}
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

// The cell widths of a calendar day, fixed so that every column sits in the same place whatever the pane's
// width. The button cell holds `[ − ] [ + ]` (11 cells in the terminal, one gap between the buttons) and a
// blank; a desktop draws its native buttons within it. The mark and the day share keep a trailing blank.
const CAL = { mark: 12, buttons: 12, share: 13, dayShare: 12 } as const
// One button slot of the button cell: `[ − ]` is 5 cells in the terminal; two slots and their gap fill 11.
const SLOT = 5

// The calendar tab: the week from one reset to the next, a card per day: its title, then three lines. The days
// that have ended fold into one line that unfolds when pressed. Every change goes through `paceline plan set`;
// without `canEdit` no button that changes a mark is drawn.
function drawCalendar($: EngineInterface, els: Els, canEdit: boolean, header: unknown, cal: CalendarView, unfolded: boolean) {
  const { Box, Text, Button } = els
  const view = cal.result as PlanView | null
  if (!view) {
    return (
      <Box flexDirection="column" gap={1}>
        {header}
        {cal.error ? <Text color="error">{`Could not read the week plan: ${cal.error}`}</Text> : <Text dimColor>Reading the week plan…</Text>}
      </Box>
    )
  }

  const { ended, rows } = endedRun(view)
  const anyWarn = rows.some(d => warnText(d) !== '')

  // One − / + pair of a part that has not started; a part's bound has no button.
  const marks = (day: PlanDay, kind: 'work' | 'off', part: PlanPart | null) => {
    const id = `cal-${day.date}-${kind}`
    const down = part && part.state === 'later' ? stepMark(part.mark, -1) : null
    const up = part && part.state === 'later' ? stepMark(part.mark, 1) : null
    if (down === null && up === null) return null
    // − and + each keep their own slot, so a part at a bound leaves the other button where it always is.
    return (
      <Box key={`${id}-buttons`} flexDirection="row" gap={1}>
        <Box key={`${id}-minus-slot`} width={SLOT} flexShrink={0}>
          {down !== null && <Button key={`${id}-minus`} label="−" onPress={() => void changeMark($, day.date, kind, -1)} />}
        </Box>
        <Box key={`${id}-plus-slot`} width={SLOT} flexShrink={0}>
          {up !== null && <Button key={`${id}-plus`} label="+" onPress={() => void changeMark($, day.date, kind, 1)} />}
        </Box>
      </Box>
    )
  }

  // The ☼ or ☾ line of a day: mark, the button cell (empty when the part has no button), share, cut hours.
  const partLine = (day: PlanDay, kind: 'work' | 'off') => {
    const part = kind === 'work' ? day.work : day.off
    const glyph = kind === 'work' ? '☼' : '☾'
    const id = `cal-${day.date}-${kind}`
    const share = part ? shareText(part) : ''
    const cut = cutText(part)
    return (
      <Box key={id} flexDirection="row">
        <Box key={`${id}-mark-cell`} width={CAL.mark} flexShrink={0}>
          {part ? (
            <Text key={`${id}-mark`} bold color={kind === 'work' ? 'suggestion' : 'merged'}>{`${glyph} ${markLabel(part.mark)}`}</Text>
          ) : (
            <Text key={`${id}-mark`} dimColor>{`${glyph} —`}</Text>
          )}
        </Box>
        <Box key={`${id}-buttons-cell`} width={CAL.buttons} flexShrink={0}>
          {canEdit ? marks(day, kind, part) : null}
        </Box>
        <Box key={`${id}-share-cell`} width={CAL.share} flexShrink={0}>
          {share !== '' && (
            <Text key={`${id}-share`} dimColor={part?.state === 'ended'}>
              {share}
            </Text>
          )}
        </Box>
        {cut !== '' && (
          <Text key={`${id}-cut`} dimColor>
            {cut}
          </Text>
        )}
      </Box>
    )
  }

  // A day is a card: the title line, the head line (bar and day share), then the ☼ line and the ☾ line. The
  // engine's Box has no border title, so the title is the card's first line.
  const row = (day: PlanDay) => {
    const colours = barColors(day, view)
    const warn = warnText(day)
    const live = [day.work, day.off].some(p => p !== null && p.state !== 'ended')
    const today = day.date === view.current.day
    const opens = reserveOpensText(view, day)
    return (
      <Box key={`cal-day-${day.date}`} flexDirection="column" borderStyle="round" borderColor={today ? 'warning' : undefined} paddingX={1}>
        <Box key={`cal-${day.date}-title`} flexDirection="row">
          <Text bold>{dayTitle(day)}</Text>
          {today && <Text color="warning">{' · today'}</Text>}
          {opens !== '' && <Text dimColor>{opens}</Text>}
        </Box>
        <Box key={`cal-${day.date}-head`} flexDirection="row">
          <Box key={`cal-${day.date}-bar`} flexDirection="row" flexShrink={0}>
            {colours.map((colour, i) => (
              <Box key={`cal-${day.date}-bar-${i + 1}`} width={1} height={1} backgroundColor={colour ?? TRACK_COLOR}>
                <Text> </Text>
              </Box>
            ))}
            <Text key={`cal-${day.date}-above`} color="error">
              {aboveScale(day, view) ? '▲' : ' '}
            </Text>
          </Box>
          <Box key={`cal-${day.date}-share-cell`} width={CAL.dayShare} flexShrink={0}>
            {live && (
              <Text key={`cal-${day.date}-share`} dimColor>
                {`day ${day.share_pct.toFixed(1)} %`}
              </Text>
            )}
          </Box>
          {warn !== '' && (
            <Text key={`cal-${day.date}-warn`} color="warning">
              {warn}
            </Text>
          )}
        </Box>
        {partLine(day, 'work')}
        {partLine(day, 'off')}
      </Box>
    )
  }

  const legend =
    '☼ working hours, ☾ off-hours. A mark is a multiple of the part’s share: off 0, light 0.5, normal 1, heavy 1.5, in steps of 0.25 up to 2. The bar runs to the bar top; grey is the room left, ▲ above the bar top. − / + change a part that has not started (paceline plan set).' +
    (anyWarn ? ' ⚠ N win: N five-hour windows cannot let the share through.' : '')

  const unplanned = unplannedLine(view)
  return (
    <Box flexDirection="column" gap={1}>
      {header}
      {cal.error && <Text color="error">{`The last read failed, this plan may be old: ${cal.error}`}</Text>}
      {cal.note && <Text color="error">{cal.note}</Text>}
      <Box flexDirection="column">
        <Text bold>{weekLine(view)}</Text>
        <Text dimColor>{numbersLine(view)}</Text>
        <Text color={view.current.over === true ? 'warning' : undefined}>{nowText(view)}</Text>
        {unplanned && <Text color="warning">{unplanned}</Text>}
      </Box>
      <Box flexDirection="column" gap={1}>
        {ended.length > 0 && (
          <Button key="cal-ended" plain dimColor label={`${unfolded ? '▾' : '▸'} ${endedText(ended)}`} onPress={() => void update($, calendarEnded, v => !v)} />
        )}
        {unfolded && ended.map(row)}
        {rows.map(row)}
      </Box>
      <Text dimColor>{legend}</Text>
    </Box>
  )
}

// What /paceline:settings answers; null means the module cannot answer and the skill's text runs.
async function answer($: EngineInterface, args: string): Promise<CommandRunResult | null> {
  const [word = '', ...rest] = args.trim().split(/\s+/).filter(Boolean)
  if (word === 'pane') {
    // Opened first, before any await: the engine places a pane at any width only while the person's
    // command or press is behind the call. The pane draws "Reading the settings…" while the read runs.
    const opened = await $.ui.open({ id: PANE, title: 'PaceLine settings' })
    paneOpenNow = opened.isPlaced
    // The pane is placed or refused: a press may close it now, while the read below runs.
    opening = false
    await update($, editing, () => '')
    await update($, tab, () => 'settings')
    await update($, calendarEnded, () => false)
    const parsed = await reload($)
    if (opened.isPlaced) return { text: 'PaceLine settings opened in a pane.' }
    const text = `${parsed ? summary(parsed, $.plugin.name) : ''}\n\n${UNPLACED}: ${opened.reason}`.trim()
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

// A press toggles the pane without waiting for it: the hook returns at once. A press on the open pane
// closes it, and one on a pane hidden behind another tab brings it back, as PitBoard's button does.
// A failure, and a pane that could not be placed, go to the log. A press while an open is still on its
// way does nothing, so a quick double press neither opens twice nor closes a pane not yet placed.
function pressPane($: EngineInterface): void {
  if (opening) return
  if (paneOpenNow) {
    void closeOrRaise($)
    return
  }
  openPaneFromPress($)
}

async function closeOrRaise($: EngineInterface) {
  try {
    const pane = (await $.ui.panes()).find(p => p.id === PANE)
    if (pane) {
      await $.ui.close({ id: PANE })
      paneOpenNow = false
      if (pane.isShown) return
    }
    paneOpenNow = false
    // No call brings a tab to the front, so a hidden pane is closed and opened again. That open comes
    // after awaits, no longer behind the press, so a terminal under 110 columns may not place it; the
    // log says so, and the next press, which is behind its open, places it at any width (as PitBoard's).
    openPaneFromPress($)
  } catch (error) {
    $.ui.log(`PaceLine settings pane could not close or raise: ${error instanceof Error ? error.message : String(error)}`)
  }
}

function openPaneFromPress($: EngineInterface): void {
  opening = true
  answer($, 'pane')
    .then(result => {
      if (result?.text?.includes(UNPLACED)) $.ui.log(result.text)
    })
    .catch(error => {
      $.ui.log(`PaceLine settings pane failed: ${error instanceof Error ? error.message : String(error)}`)
    })
    .finally(() => {
      opening = false
    })
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

  // The account has already changed when next(e) returns, so the hold starts before it, and no reading
  // is written until the profile endpoint has answered for the new login.
  for (const command of ['login', 'logout']) {
    on('command.run', { command }, async ($, e, next) => {
      try {
        hold = { limits: seenLimits, account: (await accountOf($))?.key ?? null }
        profile.ready = false
      } catch {
        hold = null
      }
      try {
        return await next(e)
      } finally {
        if (command === 'logout') {
          // The old key leaves the entry now, not when the answer comes.
          loggedOut = true
          await noteSession($, { account: null, source: null, reason: 'logged-out' })
        }
        void askProfile($, true)
      }
    })
  }

  on('session.start', async ($, e, next) => {
    const result = await next(e)
    // A module reload keeps the engine's panes but starts paneOpenNow over. A host that cannot list
    // panes must not stop the session start.
    try {
      paneOpenNow = (await $.ui.panes()).some(p => p.id === PANE && p.isPlaced)
    } catch {
      // paneOpenNow keeps its value: closed, unless this module placed the pane.
    }
    if (!watching) {
      watching = true
      // The first ask of the process, not awaited: a session start never waits on the network.
      void askProfile($)
      $.clock.every(WATCH_PERIOD_MS, () => {
        void watchInbox($)
      })
      // Not awaited: a session start never waits on the network.
      void usageTick($)
      $.clock.every(USAGE_TICK_MS, () => {
        void usageTick($)
      })
    }
    // A session after /clear or a resume has its own session-accounts entry.
    void accountOf($)
    // The inbox is taken in when the process starts, not one period later; the timer covers turn ends.
    void watchInbox($)
    // PitBoard offers the noun $.pitboard; where it is missing or denies the button, the call throws
    // and this plugin draws its own Desktop button. The call is written out at the site: a noun of $
    // read as a value fails `claude plugin validate`.
    try {
      await ($ as unknown as { pitboard: { addButton: (button: { key: string; label: string }) => Promise<void> } }).pitboard.addButton({ key: 'paceline', label: 'PaceLine' })
      await update($, inPitboard, () => true)
    } catch {
      await update($, inPitboard, () => false)
    }
    return result
  })

  // A press on this plugin's button in PitBoard's row toggles the settings pane, as /paceline:settings pane
  // opens it. Matched by the element alone: PitBoard keys an added button `button-<key>`, and naming
  // PitBoard here would miss a renamed development copy of it (.ci/dev_copy.py).
  on('ui.press', { element: 'button-paceline' }, async ($, e, next) => {
    pressPane($)
    return next(e)
  })

  // The person's close mark or Escape, or a plugin's close.
  on('ui.close', async ($, e, next) => {
    const result = await next(e)
    if (e.id === PANE) paneOpenNow = false
    return result
  })

  // The Desktop footer: this plugin's own button, unless PitBoard's row carries it.
  on('ui.render', { component: 'SessionMode' }, async ($, e, next) => {
    if (e.surface !== 'desktop') return next(e)
    const [engine, added] = await Promise.all([next(e), read($, inPitboard)])
    const { Box, Button } = $.ui.resolve(e) as Partial<Els>
    if (!Box || !Button || added) return engine
    return (
      <Box flexDirection="row" alignItems="center" columnGap={1}>
        {engine}
        <Button key="settings" label="[ PaceLine ]" dimColor onPress={() => pressPane($)} />
      </Box>
    )
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
