// What context-watch keeps for the session it runs in; reset when the session id changes.
export type ContextSession = {
  sessionId: string | null
  // Warning levels (percent of the compact point) already shown since the last compaction.
  warned: number[]
  // The operator's last prompts, newest last.
  prompts: string[]
  // Every file the agent wrote or edited in the session, in first-seen order.
  written: string[]
  compactions: number
}

// The settings view's data: the last `paceline settings --json` result, when it was read, why the last
// read failed, the budget line a write printed, and the reason a row's last Save was refused.
export type SettingsView = {
  // The parsed `paceline settings --json` (hooks/settings.ts, Settings); a contract holds no import.
  result: unknown
  at: number | null
  error: string | null
  line: string | null
  note: { row: string; text: string } | null
}

// The style preview: the last `paceline simulate --preview --cached --json` result, its reset text,
// when it finished, why it failed, and whether a run is going.
export type PreviewView = {
  rows: Record<string, { unused_pct: number; unused_with_queue_pct?: number; asks_h?: number }> | null
  resetLocal: string | null
  at: number | null
  error: string | null
  pending: boolean
}

declare module 'claude-code' {
  interface PluginState {
    paceline: {
      session: ContextSession
      settings: SettingsView
      preview: PreviewView
      // The ids of the open Advanced sections and style rows.
      open: string[]
      // The row being edited, "<where>:<id>", or "".
      editing: string
      // The editor's value and typed text (settings.ts, Draft).
      draft: { value: unknown; text: string | null } | null
      // The time zone search text.
      zoneQuery: string
    }
  }
}
