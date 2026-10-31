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

// The calendar tab's data: the last `paceline plan --json` view, when it was read, why the last read
// failed, and why the last mark change was refused.
export type CalendarView = {
  // The parsed plan view (hooks/settings.ts, PlanView); a contract holds no import.
  result: unknown
  at: number | null
  error: string | null
  note: string | null
}

declare module 'claude-code' {
  interface PluginState {
    paceline: {
      session: ContextSession
      settings: SettingsView
      // The ids of the open Advanced sections.
      open: string[]
      // The row being edited, "<where>:<id>", or "".
      editing: string
      // The editor's value and typed text (settings.ts, Draft).
      draft: { value: unknown; text: string | null } | null
      // The time zone search text.
      zoneQuery: string
      // The pane's shown tab.
      tab: 'settings' | 'calendar'
      calendar: CalendarView
      // Whether the calendar's ended days are unfolded.
      calendarEnded: boolean
      // True once PitBoard took this plugin's button into its row; its Desktop footer then draws it.
      inPitboard: boolean
    }
  }
}
