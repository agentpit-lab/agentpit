// Pure part of the inbox watch: no engine calls, so tests reach it directly.

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
