export type StatusLimit = { kind: string; percentUsed: number; resetsAt?: string }

export type StatusMeasure = {
  tokens: number | null
  window: number
  threshold: number | null
  // The window auto-compact measures against: the model's, or a smaller one set by
  // CLAUDE_CODE_AUTO_COMPACT_WINDOW, the settings or the account. Absent before a breakdown.
  compactWindow?: number | null
  // Who set the compact window (the engine's ContextWindowSource); 'auto' is the model's own.
  compactSource?: string | null
  isAutoCompact: boolean
  limits: StatusLimit[]
  costUsd: number | null
}

export type StatusCache = {
  // Start of the last main-thread request; the TTL counts from it.
  anchorMs: number | null
  isInFlight: boolean
  hitLast: number | null
  reads: number
  writes: number
  isRebuilt: boolean
  // TTL of the last cache write, read off the transcript; null until one is seen.
  regime: '1h' | '5m' | null
}

// resetsAt: the bucket's own reset, when the limits file gives one.
export type StatusScoped = { name: string; percentUsed: number; resetsAt?: string }

export type StatusMeta = {
  model: string
  advisor: string | null
  locale: string | null
  // Unset in a session, so clock times follow the machine; the documentation specimens pin it.
  timeZone?: string
  effort: string | null
  leaf: string
  branch: string | null
  version: string
  startedAt: number
  turns: number
}

// One reading of a limit window (five-hour or weekly), for its burn rate.
export type StatusSample = { t: number; pct: number; resetsAt: string | null }

declare module 'claude-code' {
  interface PluginState {
    pitboard: {
      measure: StatusMeasure | null
      cache: StatusCache
      meta: StatusMeta | null
      scoped: StatusScoped[]
      fileLimits: StatusLimit[]
      nowMs: number
      desktopNow: number
      fiveSamples: StatusSample[]
      weekSamples: StatusSample[]
      // Context tokens after each main-thread turn, newest last.
      ctxSamples: number[]
      // The desktop reading whose detail line is open.
      detail: string | null
      // The advisor /advisor set in this session; null until it runs.
      advisorPick: { value: string | null; before?: string | null } | null
      // True while the /pitboard pane is placed: the desktop row then leaves its cards to the pane.
      paneOpen: boolean
    }
  }
}
