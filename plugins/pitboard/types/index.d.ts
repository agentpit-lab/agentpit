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
  // Idle compaction as the settings and the environment set it: the `idleCompaction` setting and
  // DISABLE_AUTO_COMPACT decide isOn, CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS (at least 100,000) the
  // size from which it applies. Absent until read; the engine's defaults (on, 200,000) then apply.
  idleCompact?: { isOn: boolean; minTokens: number } | null
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

// paceline's five-hour-burn-<key>.json: the threshold projection of one account's window. The rate fields
// are null while the window has no 10 minutes of readings.
export type StatusBurn = {
  computed_at: string
  account: string
  used_pct: number
  resets_at: string
  threshold_pct: number
  budget_per_hour_pct: number | null
  rate_per_hour_pct: number | null
  threshold_at: string | null
  threshold_at_local?: string | null
  base_at?: string | null
  span_min?: number | null
  limited: boolean
}

// paceline's week-plan-<key>.json: the current part of one account's week plan, as of computed_at. Every
// instant is ISO 8601 with a zone designator. share_pct is null for an unplanned part.
export type StatusWeekPlan = {
  version: 1
  computed_at: string
  account: string
  mode: 'inform' | 'limit'
  inform_until: string | null
  week_resets_at: string
  five_hour_to_week_pct: number
  reserve: {
    pct: number
    name?: string | null
    release?: string | null
    release_at: string | null
    error?: string | null
  }
  part: {
    day?: string
    kind?: string
    start: string
    end: string
    share_pct: number | null
    unplanned: boolean
    used_at_start: number
    estimated: boolean
    fixed_at?: string
    weight?: number
  }
  later: { day?: string; kind?: string; start?: string; end?: string; weight: number; share_pct?: number | null }[]
}

// A button another plugin adds to PitBoard's row and Desktop footer through $.pitboard.addButton; a press
// is that plugin's to answer (on('ui.press', { plugin: 'pitboard', element: `button-${key}` })).
export type PitboardButton = { key: string; label: string }
export type Pitboard = { addButton: (button: PitboardButton) => Promise<void> }

declare module 'claude-code' {
  interface EngineInterface {
    pitboard: Pitboard
  }
  interface PluginState {
    pitboard: {
      measure: StatusMeasure | null
      cache: StatusCache
      meta: StatusMeta | null
      scoped: StatusScoped[]
      fileLimits: StatusLimit[]
      // The account signed in at the last refresh (key null when it has never been told; a refresh that
      // cannot read it keeps the last key), and whether the limits drawn from the file come from
      // readings of unknown account.
      // paceline's five-hour burn snapshot, null when absent or unreadable.
      fiveBurn: StatusBurn | null
      // paceline's week plan file for the held account, null when absent or unreadable.
      weekPlan: StatusWeekPlan | null
      requestAt: number | null
      account: { key: string | null; isFileUnknown: boolean } | null
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
      // The buttons other plugins added, in the order added.
      buttons: PitboardButton[]
    }
  }
}
