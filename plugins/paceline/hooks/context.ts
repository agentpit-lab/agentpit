// Pure logic of context-watch: no engine calls, so tests reach it directly.

export type Settings = { on: boolean; line: boolean; levels: number[]; snapshotDays: number }

export const DEFAULT_SETTINGS: Settings = { on: true, line: false, levels: [85, 95], snapshotDays: 14 }

// cache_watch.py's WAKE_MARK: a cache-watch wake is not the operator's prompt.
export const WAKE_MARK = 'cache-watch woke this session'

const MACHINE_SOURCES = new Set(['system', 'loop_wakeup', 'schedule_wakeup', 'poll_event'])
const WRITE_TOOLS = new Set(['Write', 'Edit', 'NotebookEdit'])

export const PROMPTS_KEPT = 3
export const PROMPT_CHARS = 400
export const REPLIES_KEPT = 2
export const REPLY_CHARS = 400
export const TOOLS_KEPT = 5
export const INPUT_CHARS = 200

export type Fill = {
  used: number
  limit: number
  window: number
  isAutoCompact: boolean
}

export type SnapshotMessage = {
  role: 'user' | 'assistant'
  text: string
  toolUses: readonly { tool: string; input: Record<string, unknown> }[]
}

// Mirrors context_watch.py's load_settings: each field falls back to its default on its own.
export function readSettings(text: string | null): Settings {
  let stored: unknown = null
  try {
    stored = text ? JSON.parse(text) : null
  } catch {
    stored = null
  }
  const s = stored && typeof stored === 'object' && !Array.isArray(stored) ? (stored as Record<string, unknown>) : {}
  const levels = s.levels
  const isLevels =
    Array.isArray(levels) &&
    levels.length > 0 &&
    levels.every((v, i) => Number.isInteger(v) && v >= 50 && v <= 99 && (i === 0 || v > levels[i - 1]))
  const days = s.snapshot_days
  return {
    on: typeof s.on === 'boolean' ? s.on : DEFAULT_SETTINGS.on,
    line: typeof s.line === 'boolean' ? s.line : DEFAULT_SETTINGS.line,
    levels: isLevels ? (levels as number[]) : DEFAULT_SETTINGS.levels,
    snapshotDays: Number.isInteger(days) && (days as number) >= 1 && (days as number) <= 90 ? (days as number) : DEFAULT_SETTINGS.snapshotDays,
  }
}

export const kilo = (n: number) =>
  n >= 1_000_000 ? `${Number((n / 1_000_000).toFixed(1))}M` : n >= 1000 ? `${Math.round(n / 1000)}k` : String(n)

export const percent = (f: Fill) => (f.limit > 0 ? Math.round((f.used / f.limit) * 100) : 0)

export function formatLine(f: Fill): string {
  return f.isAutoCompact
    ? `context: ${kilo(f.used)} / ${kilo(f.limit)} auto-compact point (${percent(f)}%)`
    : `context: ${kilo(f.used)} / ${kilo(f.window)} window (${percent(f)}%), auto-compact off`
}

// The highest level the fill has reached that was not shown yet; the lower ones it passed on the
// way count as shown, so one warning stands for them.
export function crossedLevel(f: Fill, levels: readonly number[], warned: readonly number[]): { level: number | null; warned: number[] } {
  if (!f.isAutoCompact) return { level: null, warned: [...warned] }
  const pct = (f.used / f.limit) * 100
  const reached = levels.filter(l => pct >= l)
  const fresh = reached.filter(l => !warned.includes(l))
  if (!fresh.length) return { level: null, warned: [...warned] }
  return { level: Math.max(...fresh), warned: [...new Set([...warned, ...reached])].sort((a, b) => a - b) }
}

export function warningText(f: Fill, level: number): string {
  return (
    `context-watch: ${level}% of the auto-compact point (${kilo(f.used)} / ${kilo(f.limit)}). ` +
    "Write your recovery point to the task's status file now, then continue."
  )
}

export function isOperatorPrompt(prompt: string, source: string | undefined): boolean {
  if (source && MACHINE_SOURCES.has(source)) return false
  return Boolean(prompt.trim()) && !prompt.includes(WAKE_MARK)
}

export const keepLast = (list: readonly string[], item: string, count: number) => [...list, item].slice(-count)

export function writtenPaths(calls: readonly { tool_name: string; tool_input: unknown }[]): string[] {
  const paths: string[] = []
  for (const call of calls) {
    if (!WRITE_TOOLS.has(call.tool_name)) continue
    const input = call.tool_input as Record<string, unknown> | null
    const path = input?.file_path ?? input?.notebook_path
    if (typeof path === 'string' && path) paths.push(path)
  }
  return paths
}

export const addPaths = (list: readonly string[], paths: readonly string[]) => [...new Set([...list, ...paths])]

const cut = (text: string, chars: number) => (text.length > chars ? `${text.slice(0, chars)}…` : text)
const oneLine = (text: string) => text.replace(/\s+/g, ' ').trim()

export function snapshotText(input: {
  at: string
  trigger: string
  compaction: number
  prompts: readonly string[]
  messages: readonly SnapshotMessage[]
  written: readonly string[]
}): string {
  const replies = input.messages.filter(m => m.role === 'assistant' && m.text.trim()).slice(-REPLIES_KEPT)
  const calls = input.messages.filter(m => m.role === 'assistant').flatMap(m => m.toolUses).slice(-TOOLS_KEPT)
  const list = (items: readonly string[]) => (items.length ? items.map(i => `- ${i}`) : ['- none'])
  return [
    '# context-watch snapshot',
    '',
    `Written ${input.at}, before compaction ${input.compaction} (${input.trigger}).`,
    'Read this and the task\'s status files before the next step.',
    '',
    '## Operator\'s last prompts',
    ...list(input.prompts.slice(-PROMPTS_KEPT).map(p => cut(oneLine(p), PROMPT_CHARS))),
    '',
    '## Agent\'s last replies',
    ...list(replies.map(m => cut(oneLine(m.text), REPLY_CHARS))),
    '',
    '## Last tool calls (inputs only)',
    ...list(calls.map(c => `${c.tool}: ${cut(JSON.stringify(c.input), INPUT_CHARS)}`)),
    '',
    '## Files written or edited in this session',
    ...list(input.written),
    '',
  ].join('\n')
}

export function compactInstructions(given: string | undefined, path: string): string {
  const keep =
    `Keep these verbatim in the summary: the snapshot path ${path}, ` +
    "and the paths of the files that hold this task's status and decisions."
  return given?.trim() ? `${given}\n\n${keep}` : keep
}

export function recoverText(path: string): string {
  return (
    'context-watch: the context was just compacted. ' +
    `Before the next step, read the snapshot ${path} and the task's status files.`
  )
}

// Joins with the separator the root already uses, so a Windows path stays one style.
export function snapshotPaths(root: string, sessionId: string) {
  const sep = root.includes('\\') && !root.includes('/') ? '\\' : '/'
  const dir = `${root.replace(/[\\/]+$/, '')}${sep}.claude${sep}paceline`
  return { dir, file: `${dir}${sep}${sessionId}.md`, ignore: `${dir}${sep}.gitignore` }
}
