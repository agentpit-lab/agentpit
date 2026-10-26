// Pure part of the away marker's clearing: no engine calls, so tests reach it directly. The marker
// itself, <state>/away.json, belongs to scripts/away.py.

import { WAKE_MARK } from './context'

export const AWAY_FILE = 'away.json'

// What clears the marker: $.fs cannot delete, and away.py reads a marker whose "on" is not true as off.
export const CLEARED = '{"on": false}'

// The command that sets the marker: Claude Code submits it as a prompt before the model runs it.
// Its names derive from the plugin's own name, so no name is written here.
const escapeRegExp = (s: string): string => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
const commandNames = (plugin: string): string[] => [`/${plugin}:away`.toLowerCase(), '/away']
const expandedCommand = (plugin: string): RegExp =>
  new RegExp(`<command-name>\\s*(?:\\/${escapeRegExp(plugin)}:away|\\/away)\\s*<\\/command-name>`, 'i')

// The person's own message: typed at the terminal or in Desktop ('composer'), sent through Remote Control
// ('bridge') or a Slack ping. 'sdk' is left out: Desktop stamps it on a prompt it sends on its own, such as
// the first prompt of a session another session starts, and claude -p and the Agent SDK carry it too.
// Notifications, schedules, peers, channels, plugins and unstamped prompts are not the person.
const PERSON_ORIGINS = ['composer', 'bridge', 'slack-ping']

export type PromptSeen = {
  text: string
  command?: { name: string }
  origin?: object
}

export function isAwayOn(text: unknown): boolean {
  if (typeof text !== 'string') return false
  try {
    const marker = JSON.parse(text)
    return typeof marker === 'object' && marker !== null && !Array.isArray(marker) && marker.on === true
  } catch {
    return false
  }
}

// A prompt the person wrote: not a cache-watch wake, not the command that sets the marker, and from
// the person's own origin. An engine that stamps no origin leaves the text to decide.
export function isPersonPrompt(e: PromptSeen, plugin: string): boolean {
  if (e.origin !== undefined) {
    const kind = (e.origin as { kind?: unknown }).kind
    if (typeof kind !== 'string' || !PERSON_ORIGINS.includes(kind)) return false
  }
  if (e.command && [`${plugin}:away`, 'away'].includes(e.command.name)) return false
  const text = String(e.text ?? '').trim()
  if (!text || text.includes(WAKE_MARK) || expandedCommand(plugin).test(text)) return false
  const first = text.split(/\s+/, 1)[0]!.toLowerCase()
  return !commandNames(plugin).includes(first)
}
