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
  pickSettings,
  PROMPTS_KEPT,
  recoverText,
  snapshotPaths,
  snapshotText,
  stateDirsOf,
  warningText,
  writtenPaths,
} from './context'
import type { Fill, Settings } from './context'

// Every function that takes $, and every atom it reads, lives in this file: the engine follows $
// and state references only within the file that declares them.

const EMPTY: ContextSession = { sessionId: null, warned: [], prompts: [], written: [], compactions: 0 }
const session = atom({ plugin: 'pitcrew', key: 'session' } as const, EMPTY)

// PitCrew's state directory and PaceLine's legacy one, resolved by the rule state.py uses, so the module
// and the scripts agree.
async function stateDirs($: EngineInterface): Promise<{ own: string; legacy: string }> {
  return stateDirsOf({
    pitcrewStateDir: await $.env.get('PITCREW_STATE_DIR'),
    pacelineStateDir: await $.env.get('PACELINE_STATE_DIR'),
    configDir: await $.env.get('CLAUDE_CONFIG_DIR'),
    home: (await $.env.get('USERPROFILE')) ?? (await $.env.get('HOME')),
  })
}

// The settings file context_watch.py writes; until it exists, the one PaceLine wrote.
async function settingsOf($: EngineInterface): Promise<Settings> {
  try {
    const dirs = await stateDirs($)
    // The own file, once it exists, is the only source, even when it cannot be read.
    if (await existsOrFalse($, `${dirs.own}/context-watch.json`)) {
      return pickSettings(await readOrNull($, `${dirs.own}/context-watch.json`), null)
    }
    return pickSettings(null, await readOrNull($, `${dirs.legacy}/context-watch.json`))
  } catch {
    return pickSettings(null, null)
  }
}

// An exists that throws counts as "not there".
async function existsOrFalse($: EngineInterface, path: string): Promise<boolean> {
  try {
    return await $.fs.exists(path)
  } catch {
    return false
  }
}

// A read that throws (the file is missing) is null, the "no file" of pickSettings.
async function readOrNull($: EngineInterface, path: string): Promise<string | null> {
  try {
    return await $.fs.read(path)
  } catch {
    return null
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
