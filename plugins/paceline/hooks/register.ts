import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { ContextSession } from '../types'
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
import { inboxKey, inboxPath, inboxReading, isDue, limitsKey, stateDirOf } from './inbox'
import type { RateLimit, Sent } from './inbox'
import { inboxFingerprint, PYTHONS, seenPath, WATCH_PERIOD_MS } from './watch'
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
// The interpreter that ran the merge; undefined until tried, null when none works.
let python: string | null | undefined

async function runMerge($: EngineInterface): Promise<boolean> {
  const script = `${$.plugin.root}/scripts/limits_merge.py`
  for (const exe of python === undefined ? PYTHONS : python ? [python] : []) {
    try {
      // limits_merge.py reads its input to the end; an empty one closes at once.
      const { exitCode } = await $.process.run([exe, script], { stdin: '' })
      // The script always exits 0; anything else is the interpreter, such as the Windows Store stub.
      if (exitCode === 0) {
        python = exe
        return true
      }
    } catch {
      // Not on PATH, or still running at the timeout.
    }
  }
  if (python === undefined) python = null
  return false
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

export const register: Register = on => {
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
