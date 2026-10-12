import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { StatusCache, StatusLimit, StatusSample, StatusScoped } from '../types'
import { desktopKey } from './cards'
import { drawDesktop, drawPane, drawTerminal } from './draw'
import type { Els } from './draw'
import {
  buildView,
  effortAfterCommand,
  encodeProject,
  fileLimits as readFileLimits,
  freshScoped,
  mergeLimit,
  settingsEffort,
  stateDirOf,
} from './view'

// Every function that takes $, and every atom it reads, lives in this file: the engine follows $
// and state references only within the file that declares them.

const measure = atom({ plugin: 'pitboard', key: 'measure' } as const, null)
const CACHE_START: StatusCache = {
  anchorMs: null,
  isInFlight: false,
  hitLast: null,
  reads: 0,
  writes: 0,
  isRebuilt: false,
  regime: null,
}
const cache = atom({ plugin: 'pitboard', key: 'cache' } as const, CACHE_START)
const meta = atom({ plugin: 'pitboard', key: 'meta' } as const, null)
const scoped = atom({ plugin: 'pitboard', key: 'scoped' } as const, [] as StatusScoped[])
const fileLimits = atom({ plugin: 'pitboard', key: 'fileLimits' } as const, [] as StatusLimit[])
const nowMs = atom({ plugin: 'pitboard', key: 'nowMs' } as const, 0)
// The desktop row's clock: it moves only when the row would show something else.
const desktopNow = atom({ plugin: 'pitboard', key: 'desktopNow' } as const, 0)
const fiveSamples = atom({ plugin: 'pitboard', key: 'fiveSamples' } as const, [] as StatusSample[])
const weekSamples = atom({ plugin: 'pitboard', key: 'weekSamples' } as const, [] as StatusSample[])
const ctxSamples = atom({ plugin: 'pitboard', key: 'ctxSamples' } as const, [] as number[])
const detail = atom({ plugin: 'pitboard', key: 'detail' } as const, null as string | null)
const advisorPick = atom({ plugin: 'pitboard', key: 'advisorPick' } as const, null as { value: string | null; before?: string | null } | null)
const paneOpen = atom({ plugin: 'pitboard', key: 'paneOpen' } as const, false)

const toLimit = (l: StatusLimit): StatusLimit => ({ kind: l.kind, percentUsed: l.percentUsed, resetsAt: l.resetsAt })

async function configDir($: EngineInterface) {
  const home = (await $.env.get('USERPROFILE')) ?? (await $.env.get('HOME')) ?? ''
  return (await $.env.get('CLAUDE_CONFIG_DIR')) ?? `${home}/.claude`
}

// paceline's <state>, found by paceline's own rule, so the row reads the files paceline writes.
async function stateDir($: EngineInterface): Promise<string> {
  return stateDirOf({
    stateDir: await $.env.get('PACELINE_STATE_DIR'),
    configDir: await $.env.get('CLAUDE_CONFIG_DIR'),
    home: (await $.env.get('USERPROFILE')) ?? (await $.env.get('HOME')),
  })
}

// The module's Intl has no OS locale, so the weekday would come out in the default one. POSIX locale
// variables first (`uk_UA.UTF-8` -> `uk-UA`); Windows sets none, so pwsh where processes run.
async function osLocale($: EngineInterface): Promise<string | null> {
  for (const value of [await $.env.get('LC_ALL'), await $.env.get('LC_TIME'), await $.env.get('LANG')]) {
    const tag = value?.split(/[.@]/)[0]?.replace('_', '-')
    if (tag && tag !== 'C' && tag !== 'POSIX') return tag
  }
  try {
    const r = await $.process.run(['pwsh', '-NoProfile', '-Command', '(Get-Culture).Name'])
    return r.exitCode === 0 && r.stdout.trim() ? r.stdout.trim() : null
  } catch {
    return null
  }
}

let localeTried = false
// The main-loop model, as $.session.model() spells it, the last request reported its effort on.
let effortModel: string | null = null

async function refreshMeta($: EngineInterface) {
  const [model, cwd, version, usage, turns] = await Promise.all([
    $.session.model(),
    $.session.cwd(),
    $.session.version(),
    $.session.usage(),
    $.session.turns(),
  ])
  let branch: string | null = null
  try {
    const git = await $.process.run(['git', 'branch', '--show-current'], { cwd })
    branch = git.exitCode === 0 ? git.stdout.trim() || null : null
  } catch {
    branch = null
  }
  // The leaf keeps the separator the path itself uses.
  const sep = cwd.includes('\\') ? '\\' : '/'
  const leaf = `${sep}${cwd.split(/[\\/]/).filter(Boolean).pop() ?? cwd}`
  const settings = (await $.settings.read()) as Record<string, unknown>
  const advisor = await currentAdvisor($)
  const known = await read($, meta)
  // Looked up once: on a host with neither a locale variable nor pwsh every try fails alike.
  const locale = known?.locale ?? (localeTried ? null : await osLocale($))
  localeTried = true
  await update($, meta, prev => ({
    model,
    advisor,
    locale,
    // A request reports the effort it ran at; until one does on this model, the settings say.
    effort: effortModel === model || (prev && prev.model === model) ? (prev?.effort ?? null) : settingsEffort(settings, model),
    leaf,
    branch,
    version: version.version,
    startedAt: usage.startedAt,
    turns,
  }))
}

// The file itself: $.settings.read() is the engine's snapshot, which keeps the advisor a session
// started with after /advisor in another session rewrites the file. undefined: unreadable.
async function fileAdvisor($: EngineInterface): Promise<string | null | undefined> {
  try {
    const settings = JSON.parse(await $.fs.read(`${await configDir($)}/settings.json`)) as Record<string, unknown>
    return typeof settings.advisorModel === 'string' && settings.advisorModel.trim() ? settings.advisorModel.trim() : null
  } catch {
    return undefined
  }
}

// /advisor in this session shows at once, before the command's write reaches the file; the pick
// holds only while the file still says what it said at the command, so any later write wins.
async function currentAdvisor($: EngineInterface): Promise<string | null> {
  const [fromFile, pick, known] = await Promise.all([fileAdvisor($), read($, advisorPick), read($, meta)])
  if (fromFile === undefined) return pick ? pick.value : (known?.advisor ?? null)
  if (pick && pick.before !== undefined && pick.before === fromFile) return pick.value
  if (pick) await update($, advisorPick, () => null)
  return fromFile
}

// Settings are shared by every session; the advisor is re-read with the limits file.
async function refreshAdvisor($: EngineInterface) {
  const advisor = await currentAdvisor($)
  await update($, meta, m => (m && m.advisor !== advisor ? { ...m, advisor } : m))
}

// Returns /context's estimate of the whole window: system prompt, tools, memory and messages.
async function refreshThreshold($: EngineInterface, count: 'summary' | 'full' = 'summary'): Promise<number | undefined> {
  const usage = await $.session.usage({ breakdown: count })
  const breakdown = usage.context.breakdown
  await update($, measure, prev => ({
    // Absent until the first response of a live window; a compaction's own size stays until then.
    tokens: usage.context.tokens ?? prev?.tokens ?? null,
    window: usage.context.window,
    threshold: breakdown?.autoCompactThreshold ?? null,
    compactWindow: breakdown?.rawMaxTokens ?? prev?.compactWindow ?? null,
    compactSource: breakdown?.autocompactSource ?? prev?.compactSource ?? null,
    isAutoCompact: breakdown?.isAutoCompactEnabled ?? true,
    limits: usage.rateLimits.length ? usage.rateLimits.map(toLimit) : (prev?.limits ?? []),
    costUsd: usage.cost?.usd ?? prev?.costUsd ?? null,
  }))
  await recordLimits($)
  return breakdown?.totalTokens
}

type FileBucket = { used_percentage?: unknown; resets_at?: unknown }

// The limits file holds what every session that reports to it saw, and alone holds the
// model-scoped weekly buckets, when a writer adds them.
async function refreshShared($: EngineInterface) {
  const path = `${await stateDir($)}/rate-limits.json`
  let file: unknown = null
  try {
    file = JSON.parse(await $.fs.read(path))
  } catch {
    // A read that met a writer's rename keeps what the last read gave; no file yet draws the payload alone.
    if (await $.fs.exists(path)) return
  }
  const now = await $.clock.now()
  await update($, scoped, () => freshScoped(file, now))
  await update($, fileLimits, () => readFileLimits(file, now))
  await recordLimits($)
}

// Keeps the five-hour readings of the last 3 hours and the weekly ones of the last 6, in
// $.store too, so a new session starts with burn rates instead of waiting for them.
async function recordLimits($: EngineInterface) {
  const [m, fl, now] = await Promise.all([read($, measure), read($, fileLimits), $.clock.now()])
  const latest = (kind: string) => mergeLimit(m?.limits.find(l => l.kind === kind), fl.find(l => l.kind === kind))
  const five = latest('five_hour')
  if (five) await $.store.set('fiveSamples', await update($, fiveSamples, prev => nextSamples(prev, five, now, 3 * 3600_000)))
  const week = latest('seven_day')
  if (week) await $.store.set('weekSamples', await update($, weekSamples, prev => nextSamples(prev, week, now, 6 * 3600_000)))
}

// A reading joins the list only when it differs from the last one. Readings older than keepMs
// go, except the newest: it is the value still held, which the burn needs as its base.
function nextSamples(prev: StatusSample[], limit: StatusLimit, now: number, keepMs: number): StatusSample[] {
  const last = prev[prev.length - 1]
  const kept = prev.filter((s, i) => now - s.t < keepMs || i === prev.length - 1)
  if (last && last.pct === limit.percentUsed && last.resetsAt === (limit.resetsAt ?? null)) return kept
  return [...kept, { t: now, pct: limit.percentUsed, resetsAt: limit.resetsAt ?? null }]
}

// The writers' change log, so a burn shows from the first draw instead of after an hour of this
// session's own sampling. Past 1 MiB the log moves to `.1`, read first.
async function historySamples($: EngineInterface): Promise<{ five: StatusSample[]; week: StatusSample[] }> {
  const out = { five: [] as StatusSample[], week: [] as StatusSample[] }
  const base = `${await stateDir($)}/rate-limits.history.jsonl`
  let text = ''
  for (const path of [`${base}.1`, base]) {
    try {
      text += `${await $.fs.read(path)}\n`
    } catch {
      // Not there yet.
    }
  }
  for (const line of text.split(/\r?\n/)) {
    try {
      const r = JSON.parse(line) as { observed_at?: unknown; five_hour?: FileBucket; seven_day?: FileBucket }
      const t = typeof r.observed_at === 'string' ? Date.parse(r.observed_at) : NaN
      if (Number.isNaN(t)) continue
      const sample = (b: FileBucket | undefined): StatusSample | null =>
        b && typeof b.used_percentage === 'number'
          ? { t, pct: b.used_percentage, resetsAt: typeof b.resets_at === 'string' ? b.resets_at : null }
          : null
      const five = sample(r.five_hour)
      const week = sample(r.seven_day)
      if (five) out.five.push(five)
      if (week) out.week.push(week)
    } catch {
      // A torn last line while a writer appends.
    }
  }
  return out
}

// Stored and logged readings in time order, each kept only where the value changed.
function mergeSamples(a: StatusSample[], b: StatusSample[], now: number, keepMs: number): StatusSample[] {
  const merged: StatusSample[] = []
  for (const s of [...a, ...b].sort((x, y) => x.t - y.t)) {
    const last = merged[merged.length - 1]
    if (!last || last.pct !== s.pct || last.resetsAt !== s.resetsAt) merged.push(s)
  }
  return merged.filter((s, i) => now - s.t < keepMs || i === merged.length - 1)
}

// The state library needs each atom named at the update call, so the two lists restore apart.
async function restoreSamples($: EngineInterface) {
  const [five, week, logged, now] = await Promise.all([
    $.store.get('fiveSamples'),
    $.store.get('weekSamples'),
    historySamples($),
    $.clock.now(),
  ])
  const stored = (v: unknown) => (Array.isArray(v) ? (v as StatusSample[]) : [])
  await update($, fiveSamples, prev => mergeSamples(prev.length ? prev : stored(five), logged.five, now, 3 * 3600_000))
  await update($, weekSamples, prev => mergeSamples(prev.length ? prev : stored(week), logged.week, now, 6 * 3600_000))
}

async function recordContext($: EngineInterface) {
  const usage = await $.session.usage()
  const tokens = usage.context.tokens
  if (tokens === undefined) return
  await update($, ctxSamples, prev => [...prev, tokens].slice(-12))
}

// lastHit and lastAt describe the newest main-thread request: what a resumed session shows before
// its first turn.step.
type TranscriptStats = { reads: number; writes: number; regime: '1h' | '5m' | null; lastHit: number | null; lastAt: number | null }

// Found once per session id. Claude Code names the folder after the start directory, but cuts a
// name past 200 characters and appends a hash, and /cd moves the root, so the folders are searched
// when the encoded name misses.
let transcriptFound: { id: string; path: string } | null = null

// Bumped by each compaction and by each response, so a stale wait for the estimate gives up.
let compactRound = 0
const COMPACT_POLL_MS = 500
const COMPACT_POLLS = 20

// Usage keeps the last request before a compaction for a moment after the hook returns, and the
// estimate made then counts the old history. Once usage drops that request, the estimate counts
// the new one; the token-count API ("full") lands nearest to Desktop's own figure.
function settleCompaction($: EngineInterface, round: number, left: number) {
  $.clock.after(COMPACT_POLL_MS, () => {
    void (async () => {
      if (round !== compactRound) return
      const { context } = await $.session.usage()
      if (context.tokens !== undefined && context.tokens !== null) {
        if (left > 1) settleCompaction($, round, left - 1)
        return
      }
      const estimate = await refreshThreshold($, 'full')
      if (estimate === undefined || round !== compactRound) return
      await update($, measure, m => (m ? { ...m, tokens: estimate } : m))
    })()
  })
}

// Bumped by each new chain, by /clear and a resume, and by a compaction, so an older chain gives up.
let regimeRound = 0
const REGIME_RETRY_MS = 2000
const REGIME_RETRIES = 5

// A turn.step reports the tokens a request wrote but not the TTL they asked for, and the
// transcript may not hold the request's row yet when it returns: read it again a little later.
function watchRegime($: EngineInterface, round: number, left: number) {
  $.clock.after(REGIME_RETRY_MS, () => {
    void (async () => {
      if (round !== regimeRound) return
      await refreshTranscript($)
      if (round !== regimeRound) return
      if ((await read($, cache)).regime === null && left > 1) watchRegime($, round, left - 1)
    })()
  })
}

async function transcriptPath($: EngineInterface): Promise<string | null> {
  const [root, id, config] = await Promise.all([$.session.root(), $.session.id(), configDir($)])
  if (transcriptFound?.id === id) return transcriptFound.path
  const projects = `${config}/projects`
  const name = `${id}.jsonl`
  let path: string | null = `${projects}/${encodeProject(root)}/${name}`
  if (!(await $.fs.exists(path))) {
    path = null
    try {
      for (const dir of await $.fs.list(projects)) {
        if (dir.kind === 'dir' && (await $.fs.exists(`${projects}/${dir.name}/${name}`))) {
          path = `${projects}/${dir.name}/${name}`
          break
        }
      }
    } catch {
      return null
    }
  }
  if (path) transcriptFound = { id, path }
  return path
}

// The transcript is the one place that keeps every request since the last compaction and the
// TTL each cache write asked for; the hooks API reports neither.
async function transcriptStats($: EngineInterface): Promise<TranscriptStats | null> {
  const path = await transcriptPath($)
  if (!path) return null
  let text: string
  try {
    text = await $.fs.read(path)
  } catch {
    // Over the 4 MiB read limit: take the tail through node; without it the transcript readings stay n/a.
    const tail =
      'const fs=require("fs"),p=process.argv[1],s=fs.statSync(p).size,n=Math.min(s,4e6),b=Buffer.alloc(n),fd=fs.openSync(p,"r");fs.readSync(fd,b,0,n,s-n);process.stdout.write(b)'
    try {
      const r = await $.process.run(['node', '-e', tail, path])
      if (r.exitCode !== 0) return null
      text = r.stdout
    } catch {
      return null
    }
  }
  const stats: TranscriptStats = { reads: 0, writes: 0, regime: null, lastHit: null, lastAt: null }
  const seen = new Set<string>()
  // The cache TTL runs from the request's start; the row that triggered a request (the prompt or
  // a tool result) is the nearest recorded moment before it.
  let triggerAt: number | null = null
  for (const line of text.split('\n')) {
    if (!line.includes('"timestamp"') && !line.includes('"compact_boundary"') && !line.includes('"usage"')) continue
    let row: {
      type?: string
      subtype?: string
      isSidechain?: boolean
      timestamp?: string
      message?: {
        id?: string
        usage?: {
          cache_read_input_tokens?: number
          cache_creation_input_tokens?: number
          cache_creation?: { ephemeral_1h_input_tokens?: number; ephemeral_5m_input_tokens?: number }
        }
      }
    }
    try {
      row = JSON.parse(line)
    } catch {
      continue
    }
    if (row.type === 'system' && row.subtype === 'compact_boundary') {
      stats.reads = 0
      stats.writes = 0
      seen.clear()
      continue
    }
    if (row.isSidechain) continue
    const at = row.timestamp ? Date.parse(row.timestamp) : NaN
    const u = row.message?.usage
    if (row.type !== 'assistant' || !u) {
      if (row.type === 'user' && Number.isFinite(at)) triggerAt = at
      continue
    }
    const msgId = row.message?.id
    if (msgId) {
      if (seen.has(msgId)) continue
      seen.add(msgId)
    }
    const read = u.cache_read_input_tokens ?? 0
    const written = u.cache_creation_input_tokens ?? 0
    stats.reads += read
    stats.writes += written
    if (read + written > 0) stats.lastHit = (read / (read + written)) * 100
    stats.lastAt = triggerAt ?? (Number.isFinite(at) ? at : stats.lastAt)
    if ((u.cache_creation?.ephemeral_1h_input_tokens ?? 0) > 0) stats.regime = '1h'
    else if ((u.cache_creation?.ephemeral_5m_input_tokens ?? 0) > 0) stats.regime = '5m'
  }
  return stats
}

async function refreshTranscript($: EngineInterface) {
  const stats = await transcriptStats($)
  if (!stats) return
  await update($, cache, c => ({
    ...c,
    reads: stats.reads,
    writes: stats.writes,
    regime: stats.regime ?? c.regime,
    // Live readings from turn.step win; the transcript fills a resumed session's first render.
    hitLast: c.hitLast ?? stats.lastHit,
    anchorMs: c.anchorMs ?? stats.lastAt,
  }))
}

const PANE = 'pitboard'

// The engine places a pane at any width only while the person's command or press is behind the
// call, so in a press this must run first, before any await; the atom follows the result.
async function openPane($: EngineInterface) {
  const opened = await $.ui.open({ id: PANE, title: 'pitboard', closeOnEscape: true })
  if (opened.isPlaced) await update($, paneOpen, () => true)
  return opened
}

// A given time keeps the caller off nowMs, so it is not redrawn every second.
async function currentView($: EngineInterface, at?: number) {
  const [m, c, mt, tick, sc, fl, fs, cs, ws] = await Promise.all([
    read($, measure),
    read($, cache),
    read($, meta),
    at ?? read($, nowMs),
    read($, scoped),
    read($, fileLimits),
    read($, fiveSamples),
    read($, ctxSamples),
    read($, weekSamples),
  ])
  const now = tick || (await $.clock.now())
  return buildView({ now, measure: m, cache: c, meta: mt, scoped: sc, fileLimits: fl, fiveSamples: fs, weekSamples: ws, ctxSamples: cs })
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const result = await next(e)
    await $.command.register({ name: 'pitboard', description: 'Open every status reading, with what it means, in a pane' })
    // A module reload keeps the engine's panes but starts the atom over. A host that cannot list
    // panes must not stop the row from starting.
    try {
      const panes = await $.ui.panes()
      await update($, paneOpen, () => panes.some(p => p.id === PANE && p.isPlaced))
    } catch {
      // The atom keeps its value: closed, unless this module placed the pane.
    }
    await restoreSamples($)
    await refreshMeta($)
    // The shared file first: the session's own reading can be a turn old at start and would
    // enter the samples as a false step.
    await refreshShared($)
    await refreshThreshold($)
    await refreshTranscript($)
    await recordContext($)
    let shown = ''
    $.clock.every(1000, () => {
      void $.clock.now().then(async t => {
        await update($, nowMs, () => t)
        const [view, minute, open, pane] = await Promise.all([currentView($, t), currentView($, t - (t % 60_000)), read($, detail), read($, paneOpen)])
        const key = desktopKey(view, open, minute, pane)
        if (key === shown) return
        shown = key
        await update($, desktopNow, () => t)
      })
    })
    $.clock.every(20_000, () => {
      void refreshShared($)
      void refreshAdvisor($)
    })
    return result
  })

  // /effort sends no request, so no turn.step reports the new level until the next one.
  on('command.run', { command: 'effort' }, async ($, e, next) => {
    const result = await next(e)
    const model = await $.session.model()
    let text = ''
    try {
      text = await $.fs.read(`${await configDir($)}/settings.json`)
    } catch {
      // A level given in the command still shows.
    }
    const effort = effortAfterCommand(e.args, text, model)
    if (effort === undefined) return result
    effortModel = model
    await update($, meta, m => (m ? { ...m, effort } : m))
    return result
  })

  on('command.run', { command: 'advisor' }, async ($, e, next) => {
    const before = (await fileAdvisor($)) ?? null
    const result = await next(e)
    const asked = (e.args ?? '').trim()
    let value: string | null
    if (/^off$/i.test(asked)) value = null
    else if (asked) value = asked
    else {
      value = (await fileAdvisor($)) ?? null
    }
    await update($, advisorPick, () => ({ value, before }))
    await update($, meta, m => (m ? { ...m, advisor: value } : m))
    return result
  })

  // These change the compaction trigger without a turn or a compaction; turn.complete still
  // catches a change that lands after the command returns, as a picker's does.
  for (const command of ['autocompact', 'model', 'config']) {
    on('command.run', { command }, async ($, e, next) => {
      const result = await next(e)
      await refreshThreshold($)
      return result
    })
  }

  on('command.run', { command: 'pitboard' }, async $ => {
    await openPane($)
    return { text: 'pitboard pane opened.' }
  })

  on('session.measure', async ($, e, next) => {
    await update($, measure, prev => ({
      tokens: e.context.tokens ?? prev?.tokens ?? null,
      window: e.context.window,
      threshold: prev?.threshold ?? null,
      compactWindow: prev?.compactWindow ?? null,
      compactSource: prev?.compactSource ?? null,
      isAutoCompact: prev?.isAutoCompact ?? true,
      limits: e.rateLimits.length ? e.rateLimits.map(toLimit) : (prev?.limits ?? []),
      costUsd: e.cost?.usd ?? prev?.costUsd ?? null,
    }))
    await recordLimits($)
    return next(e)
  })

  on('session.compact', async ($, e, next) => {
    const result = await next(e)
    // A precompute, a subagent's compaction or a veto leaves this conversation as it was.
    if (e.agentId || e.trigger === 'precompute' || result.skip !== undefined) return result
    await update($, cache, c => ({ ...c, reads: 0, writes: 0, isRebuilt: true }))
    regimeRound++
    await update($, ctxSamples, () => [])
    await refreshThreshold($)
    // tokensAfter counts the messages alone, without the system prompt and tools; it stands until
    // the estimate of the whole window is ready.
    const after = result.tokensAfter
    if (after !== undefined) await update($, measure, m => (m ? { ...m, tokens: after } : m))
    settleCompaction($, ++compactRound, COMPACT_POLLS)
    return result
  })

  // /clear and a resume inside the process start another conversation without a session.start;
  // its readings come from the new session id once the end chain is over.
  on('session.end', async ($, e, next) => {
    const result = await next(e)
    if (e.reason !== 'clear' && e.reason !== 'resume') return result
    await update($, measure, m => (m ? { ...m, tokens: null } : m))
    await update($, cache, () => ({ ...CACHE_START }))
    await update($, ctxSamples, () => [])
    regimeRound++
    $.clock.after(0, () => {
      void (async () => {
        await refreshMeta($)
        await refreshThreshold($)
        await refreshTranscript($)
      })()
    })
    return result
  })

  on('turn.step', async function* ($, e, next) {
    if (e.agentId) return yield* next(e)
    const started = await $.clock.now()
    await update($, cache, c => ({ ...c, isInFlight: true }))
    try {
      const result = yield* next(e)
      const u = result.usage
      await update($, cache, c => {
        if (!u) return { ...c, isInFlight: false }
        const total = u.cache_read_input_tokens + u.cache_creation_input_tokens
        return {
          ...c,
          anchorMs: started,
          isInFlight: false,
          hitLast: total > 0 ? (u.cache_read_input_tokens / total) * 100 : c.hitLast,
          reads: c.reads + u.cache_read_input_tokens,
          writes: c.writes + u.cache_creation_input_tokens,
          isRebuilt: c.isRebuilt && u.cache_read_input_tokens === 0,
        }
      })
      if (u && u.cache_creation_input_tokens > 0 && (await read($, cache)).regime === null) watchRegime($, ++regimeRound, REGIME_RETRIES)
      // session.measure fires once per turn; a long turn makes dozens of requests, so the
      // context fill follows each one.
      if (u) {
        compactRound++
        const tokens = u.input_tokens + u.cache_read_input_tokens + u.cache_creation_input_tokens
        await update($, measure, m => (m ? { ...m, tokens } : m))
      }
      // Absent for a model without effort: the level of the model before it no longer applies.
      const effort = e.effort !== undefined ? String(e.effort) : null
      effortModel = await $.session.model()
      await update($, meta, m => (m && m.effort !== effort ? { ...m, effort } : m))
      return result
    } finally {
      // An error or an interrupt that closes the stream ends the request too.
      await update($, cache, c => (c.isInFlight ? { ...c, isInFlight: false } : c))
    }
  })

  on('turn.complete', async ($, e, next) => {
    const result = await next(e)
    // A subagent's run is a turn of its own loop; the row describes the main conversation.
    if (e.agentId) return result
    await update($, cache, c => (c.isInFlight ? { ...c, isInFlight: false } : c))
    await refreshMeta($)
    await refreshShared($)
    // /model and /config change the compaction trigger without a compaction.
    await refreshThreshold($)
    await refreshTranscript($)
    await recordContext($)
    return result
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey) return next(e)
    // Only desktop draws here; the terminal row lives under the prompt, in the hint line's site,
    // and the other surfaces have the pane.
    if (e.surface !== 'desktop') return next(e)
    const [at, open, pane] = await Promise.all([read($, desktopNow), read($, detail), read($, paneOpen)])
    const view = await currentView($, at || (await $.clock.now()))
    return drawDesktop(
      $.ui.resolve(e) as Els,
      view,
      open,
      key => {
        void update($, detail, current => (current === key ? null : key))
      },
      e.props.bodyColumns,
      pane,
    )
  })

  // Terminal: the row under the engine's own mode and hint line, which the terminal keeps above it.
  on('ui.render', { component: 'PromptHint' }, async ($, e, next) => {
    if (e.surface !== 'terminal') return next(e)
    const [engine, view, open] = await Promise.all([next(e), currentView($), read($, detail)])
    const els = $.ui.resolve(e) as Els
    const { Box } = els
    return (
      <Box flexDirection="column">
        {drawTerminal(
          els,
          view,
          open,
          key => {
            void update($, detail, current => (current === key ? null : key))
          },
          () => {
            void openPane($)
          },
        )}
        {engine}
      </Box>
    )
  })

  // Desktop raises no PromptHint, so the footer under the input, between the mode selector and the
  // model, is the one site there a plugin can reach; the row itself stays above the input.
  on('ui.render', { component: 'SessionMode' }, async ($, e, next) => {
    if (e.surface !== 'desktop') return next(e)
    const engine = await next(e)
    const { Box, Button } = $.ui.resolve(e) as Els
    if (!Button) return engine
    return (
      <Box flexDirection="row" alignItems="center" columnGap={1}>
        {engine}
        <Button
          key="pane"
          label="pitboard"
          dimColor
          onPress={() => {
            void openPane($)
          }}
        />
      </Box>
    )
  })

  // The person's close mark or Escape, or a plugin's close. An unload runs no hook of this module;
  // the reloaded module reads the panes at its session start.
  on('ui.close', async ($, e, next) => {
    const result = await next(e)
    if (e.id === PANE) await update($, paneOpen, () => false)
    return result
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    return drawPane($.ui.resolve(e) as Els, await currentView($), e.surface !== 'terminal')
  })
}
