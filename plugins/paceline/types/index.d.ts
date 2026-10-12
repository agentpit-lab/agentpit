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

declare module 'claude-code' {
  interface PluginState {
    paceline: {
      session: ContextSession
    }
  }
}
