// Pure part of the inbox watch: no engine calls, so tests reach it directly.

import { ACCOUNT_KEY } from './account'

export type Listed = { name: string; kind: string; size: number; mtimeMs: number }

// A new reading reaches the limits file within one period; pitboard re-reads it every 20 s.
export const WATCH_PERIOD_MS = 15_000

// The order the command hooks try; a process run has no shell to test a command with.
export const PYTHONS = ['python3', 'python'] as const

// What a merge is compared by: each reading file's name, size and modification time.
export function inboxFingerprint(entries: readonly Listed[]): string {
  return entries
    .filter(e => e.kind === 'file' && e.name.endsWith('.json'))
    .map(e => `${e.name}:${e.size}:${e.mtimeMs}`)
    .sort()
    .join('\n')
}

// The fingerprint last handed to a merge, shared by every session on the machine.
export const seenPath = (stateDir: string) => `${stateDir}/inbox.seen`

// ---------------------------------------------------------------------------------------------
// The boundary trigger: the merge also runs when the week plan file does not hold now.

// The week plan file's name for an account key: limits_file.week_plan_path.
export const weekPlanPath = (stateDir: string, key: string) => `${stateDir}/week-plan-${key}.json`

// limits_file.WEEK_PLAN_VERSION and FUTURE_SLACK.
const WEEK_PLAN_VERSION = 1
const FUTURE_SLACK_MS = 5 * 60_000
// limits_file.SAME_WEEK_TOLERANCE_SEC: the file's week is the reading's when the two resets differ by less.
const PLAN_WEEK_TOLERANCE_MS = 600_000

// An ended file is tried every minute for ten minutes after the boundary it read, then every five
// minutes; a missing, unreadable or unusable file every five minutes.
export const PLAN_FAST_MS = 60_000
export const PLAN_FAST_FOR_MS = 10 * 60_000
export const PLAN_SLOW_MS = 5 * 60_000

// An instant of a week plan file, as PaceLine writes it (limits_file.iso_ms) and limits_merge.plan_holds reads
// it: ISO 8601 date and time with a "Z" or a +-HH:MM offset, or a number of seconds that limits_file.safe_iso
// takes (a year 1 to 9999). Nothing else counts: no text without a zone, lower-case letters, an hour 24, a
// day the month lacks, surrounding space, a boolean. Epoch milliseconds, or null. The twin rule is guaranteed
// only for the forms iso_ms writes: the parsers differ on a fraction of more than six digits and on a negative
// number of seconds, which no writer produces.
const INSTANT = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(?:Z|([+-])(\d{2}):(\d{2}))$/
const FIRST_SECOND = -62_135_596_800
const LAST_SECOND = 253_402_300_799

function instant(value: unknown): number | null {
  if (typeof value === 'number') return Number.isFinite(value) && value >= FIRST_SECOND && value <= LAST_SECOND ? value * 1000 : null
  if (typeof value !== 'string') return null
  const m = INSTANT.exec(value)
  if (m === null) return null
  const [year, month, day, hour, minute, second] = [m[1], m[2], m[3], m[4], m[5], m[6]].map(Number) as [number, number, number, number, number, number]
  const offsetHour = m[9] === undefined ? 0 : Number(m[9])
  const offsetMinute = m[10] === undefined ? 0 : Number(m[10])
  if (year < 1 || hour > 23 || minute > 59 || second > 59 || offsetHour > 23 || offsetMinute > 59) return null
  const d = new Date(0)
  d.setUTCFullYear(year, month - 1, day)
  d.setUTCHours(hour, minute, second, 0)
  if (d.getUTCFullYear() !== year || d.getUTCMonth() !== month - 1 || d.getUTCDate() !== day) return null
  const fraction = m[7] === undefined ? 0 : Number(`0.${m[7]}`) * 1000
  const offset = m[8] === undefined ? 0 : (m[8] === '-' ? -1 : 1) * (offsetHour * 60 + offsetMinute) * 60_000
  return d.getTime() + fraction - offset
}

type PlanState = { holds: boolean; endedAt: number | null }

// Reads a week plan file's text at `now` (ms). This is limits_merge.plan_holds, the merge's own test of the
// same file, in TypeScript: keep the two the same. Holds: a version-1 object whose part has started and
// ends after now, whose week ends after now, and that was computed no more than five minutes after now,
// every instant one `instant` takes, and whose week is the reading's: `readingReset`, the session's current weekly
// reset (epoch ms; null when unknown), is not after now or within PLAN_WEEK_TOLERANCE_MS of the file's week
// (limits_merge.plan_holds, PitBoard's own test). `endedAt` is the latest boundary (part.end, week_resets_at) at or before now
// when the file is otherwise whole; null for a file that is missing, unreadable or unusable.
function planState(text: string | null, now: number, readingReset: number | null): PlanState {
  let plan: unknown = null
  try {
    plan = text === null ? null : JSON.parse(text)
  } catch {
    // Unreadable: treated as missing.
  }
  const none: PlanState = { holds: false, endedAt: null }
  if (typeof plan !== 'object' || plan === null || Array.isArray(plan)) return none
  const p = plan as Record<string, unknown>
  const part = p.part
  if (p.version !== WEEK_PLAN_VERSION || typeof part !== 'object' || part === null || Array.isArray(part)) return none
  const { start, end } = part as Record<string, unknown>
  const computed = instant(p.computed_at)
  const starts = instant(start)
  const ends = instant(end)
  const weekEnds = instant(p.week_resets_at)
  if (computed === null || starts === null || ends === null || weekEnds === null) return none
  const offSchedule = readingReset !== null && readingReset > now && Math.abs(readingReset - weekEnds) >= PLAN_WEEK_TOLERANCE_MS
  const holds = !offSchedule && computed <= now + FUTURE_SLACK_MS && starts <= now && now < ends && weekEnds > now
  const past = [ends, weekEnds].filter(t => t <= now)
  return { holds, endedAt: past.length > 0 ? Math.max(...past) : null }
}

// The weekly reset of an account's partition in rate-limits.json's text (epoch ms), null when it is unknown: the file
// is missing (`text` null) or unreadable, has no `accounts` object (a file without partitions is one of unknown
// account), no partition for `key` (a 16-digit hex account key), no `seven_day` bucket, or a `resets_at` that is not
// an `instant`. This is limits_merge.reading_week_reset in TypeScript, the one source of planDue's `readingReset`:
// keep the two the same (tests/fixtures/reading-reset.ts).
export function readingWeekReset(text: string | null, key: string): number | null {
  if (text === null || !ACCOUNT_KEY.test(key)) return null
  try {
    const accounts = (JSON.parse(text) as { accounts?: unknown } | null)?.accounts
    if (typeof accounts !== 'object' || accounts === null || Array.isArray(accounts)) return null
    const partition = Object.prototype.hasOwnProperty.call(accounts, key) ? (accounts as Record<string, unknown>)[key] : null
    if (typeof partition !== 'object' || partition === null || Array.isArray(partition)) return null
    const bucket = (partition as Record<string, unknown>).seven_day
    if (typeof bucket !== 'object' || bucket === null || Array.isArray(bucket)) return null
    return instant((bucket as Record<string, unknown>).resets_at)
  } catch {
    return null
  }
}

// Whether the merge should run now for a week plan file and when it may run next. `text` is the file's
// contents, null when it is missing or could not be read; `now` and `last` are epoch milliseconds, `last`
// the start of the previous trigger run (null: none yet). A file that holds now never runs (`next` null).
// Otherwise `run` is true once the step since `last` has passed, and `next` is the earliest start of the
// run after the one `run` allows (the time the caller keeps as `last` plus the step). A part end or week
// reset that passed after `last` makes `run` true whatever the step. A file that does not hold only because the
// reading's reset differs from its week has no boundary behind it: it is tried like a file with none, at once
// for a first run, else every PLAN_SLOW_MS (the merge's own plan step usually rewrites it sooner).
export function planDue(
  text: string | null,
  now: number,
  last: number | null,
  readingReset: number | null,
): { run: boolean; next: number | null } {
  const state = planState(text, now, readingReset)
  if (state.holds) return { run: false, next: null }
  const fast = state.endedAt !== null && now - state.endedAt < PLAN_FAST_FOR_MS
  const step = fast ? PLAN_FAST_MS : PLAN_SLOW_MS
  // A boundary that passed since the last run makes a run due at once: that run saw a part that held.
  if (last === null || (state.endedAt !== null && state.endedAt > last) || now - last >= step) {
    return { run: true, next: now + step }
  }
  return { run: false, next: last + step }
}

// ---------------------------------------------------------------------------------------------
// Running a paceline script: the one place that tries PYTHONS, shared by the merge and the settings view.

// `rejected` is the engine's message when a run was refused with a rejection nobody recognises (every
// interpreter of a read, the one used for a write); the caller shows it instead of guessing a cause.
export type Ran = { exitCode: number; stdout: string; stderr: string; timedOut?: boolean; rejected?: string }
export type Exec = (argv: string[], stdin: string) => Promise<Ran>
// An interpreter memo: undefined until tried, the name once one ran the script, null after a first
// failure of every interpreter when the caller asked to give up. A memo that outlives one caller
// holds a found interpreter only (undefined or a name); a give-up stays with the caller that asked.
export type Interpreter = { python: string | null | undefined }

// How long a script may run before the process call is cut (spec R9: the engine's default, said out).
export const RUN_TIMEOUT_MS = 30_000

// What an Exec throws for a rejection that is final: the engine's timeout or its abort. The command
// may have run, so it must not be tried again with another interpreter.
export class RunTimedOut extends Error {}

// What an Exec throws for a rejection of a kind nobody recognises (the engine's wording is not
// checked). A read tries the next interpreter, since reads write nothing; a write treats it as final.
export class RunUnknown extends Error {}

const messageOf = (err: unknown): string => (err instanceof Error ? err.message : String(err))

// The engine rejects a process call with `failed to start: <code>: <message>` when the program is
// missing, `$.process.run (...): aborted: still running after 30000ms` at the timeout, a plain
// `aborted` for another abort. Three kinds follow: not started (the next interpreter is tried), final
// (the command may have run: no retry) and unknown (anything else, a hook's deny and the operating
// system's `permission denied` included, since their wording is not the engine's own).
export const failedToStart = (err: unknown): boolean => /failed to start/i.test(messageOf(err))

// A rejection that says the command may have run: the engine's timeout or its abort, nothing else.
export const finalRejection = (err: unknown): boolean => /still running after|\baborted\b/i.test(messageOf(err))

// What execOf throws for a rejected process call: the rejection itself when nothing started, a
// RunTimedOut for a final one, else a RunUnknown that keeps the engine's message.
export function asRunError(err: unknown): unknown {
  if (failedToStart(err)) return err
  return finalRejection(err) ? new RunTimedOut() : new RunUnknown(messageOf(err))
}

// A command line of the CLI ended by its own doing: it worked, or it refused with a `paceline ...`
// message, or argparse refused it with its `usage: paceline ...` text (exit 2). Anything else (the
// Windows Store stub, "can't open file", a traceback) says the interpreter is not the one.
export const cliOwned = (r: Ran): boolean => r.exitCode === 0 || /^(usage: )?paceline\b/.test(r.stderr.trim())

// Runs `[python, script, ...args]` with the interpreter that worked before, else the first of PYTHONS
// that starts. Null when none started. A write (`write` true: Save, Reset, switches) never walks
// the interpreter list: it uses the interpreter a read found, and is null when none was found. A
// command that is rejected with a timeout or an abort is final: the interpreter did
// start, the command may have run, so it is neither repeated nor tried with the next one, and the
// result says `timedOut`. A rejection of an unknown kind is final for a write too, and the result
// carries `rejected`, the engine's message; on a read the next interpreter is tried, and when every
// one fails that way the result carries `rejected` too. `giveUp` makes a first failure of every
// interpreter final in `memo` (null from then on), which is what the merge wants, unless one of them
// was refused with an unknown rejection (that may pass); pass a memo of the caller's own with it.
export async function runPythonScript(
  exec: Exec,
  memo: Interpreter,
  script: string,
  args: readonly string[] = [],
  giveUp = false,
  write = false,
): Promise<Ran | null> {
  let last: Ran | null = null
  let unknown: string | null = null
  const candidates = memo.python ? [memo.python] : memo.python === undefined && !write ? PYTHONS : []
  for (const exe of candidates) {
    try {
      const ran = await exec([exe, script, ...args], '')
      if (cliOwned(ran)) {
        memo.python = exe
        return ran
      }
      last = ran
    } catch (err) {
      if (err instanceof RunTimedOut) {
        if (memo.python === undefined) memo.python = exe
        return { exitCode: 1, stdout: '', stderr: '', timedOut: true }
      }
      if (err instanceof RunUnknown) {
        if (write) {
          if (memo.python === undefined) memo.python = exe
          return { exitCode: 1, stdout: '', stderr: '', rejected: err.message }
        }
        unknown = err.message
      }
      // Failed to start (not on PATH) or an unknown rejection of a read: the next interpreter.
    }
  }
  if (memo.python === undefined && giveUp && unknown === null) memo.python = null
  if (last === null && unknown !== null) return { exitCode: 1, stdout: '', stderr: '', rejected: unknown }
  return last
}
