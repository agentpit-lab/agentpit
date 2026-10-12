# pitboard

A status row for Claude Code, drawn by the plugin itself through function hooks: context against the
real compaction trigger, the prompt cache's time to live and hit rate, the five-hour and weekly usage
limits with their pace against an even budget (and model-scoped limits when the limits file carries
them), and, a click or hover away, the model, effort, advisor, branch, session time and API cost. It
works the same in the CLI and in Claude Code Desktop, on Windows, macOS and Linux.

Requires Claude Code 2.1.287 or later. Older versions load no function hooks and show nothing.

Optional, where Claude Code runs processes for a plugin (the CLI): `git` for the branch, `pwsh`
(PowerShell 7) for day and month names in the OS locale on Windows, where no locale variable is set,
and `node` for a session transcript over 4 MiB. Without them the names fall back to the default
locale, the branch reads `n/a`, and so do the session's cache hits once the transcript passes 4 MiB.

## Install

```
/plugin install pitboard@agentpit
```

Then start a new session: the row loads at session start. pitboard writes no file, `settings.json`
included.

## What you see

**CLI:** one row above the prompt's hint line, parted from it by a short rule.

```
 Ctx ▂▂ 82% ↻ │ Advisor Fable │ Cache ▅▅ · Hit 100% │ 5h ▃▃ · 7d ▇▇ 92% · Fable ▂▂ │ ⓘ
```

Each reading is its name, a two-cell gauge that rises in eighths over a grey track, and a number only
once the reading needs a look. Readings of one group are parted by a dot, groups by `│`. `↻` after the
context means auto-compact is on; nothing marks it off. `Advisor` shows only while an advisor is set:
its name in its model's colour, dimmed with `(inactive)` when it ranks below the main model. The cache
gauge fills as its time to live runs out; a flame replaces it while a reply runs, a snowflake once the
cache has expired. A click (or Enter under the focus) on a name, `Advisor` included, opens its readings
as one line under the row, with a `▲` under the opened name, and a line of advice under them when the
reading needs a decision; a second click closes it.

**Desktop:** the same row above the prompt, the context and cache as pies the size of Desktop's own
context ring, `5h` and `7d` as bars, the model-scoped limits as pies, the advisor only while one is
set, and `ⓘ`. A bar carries a tick where an even pace to the reset would stand now, the share of the
window gone; a fill past the tick spends faster than the window allows. `5h` and `7d` show their number
and time to reset at every level. When the row would not fit one line (about 100 columns), the bars
drop the time to reset first, which their cards still show; when even that does not fit, they turn
into pies too. Hovering a reading shades it and shows its card, drawn in Desktop's own text: the
reading's name, one reading to a line, a bar for a limit, and advice only when it needs a decision.

Every reading, colour and threshold is drawn on one page, light and dark, with the Desktop card under
the pointer: [`docs/anatomy.html`](docs/anatomy.html). Open it in a browser; it is one self-contained file.

**Everywhere, the mobile app included:** `/pitboard` opens a pane with every card unfolded in the
row's order, what needs a decision first, and every explanation.

### The cards

| Card | Readings |
|---|---|
| Context | Auto-compact `on`, `on (env)` when a smaller compact window is set (also `settings`, `account`), or `off`; Used `76k of 367k (20.8%)` against the compact point, or the full window when off; Left; Window `400k of 1M`; Turns left at the recent growth |
| Cache | Countdown (whole minutes while calm, `mm:ss` once it warns, `replying`, `expired`); TTL `1h`/`5m`; If cold: the context the next turn writes again, and how much dearer that turn is (about 20× with a 1h TTL, 12.5× with 5m) |
| Cache hits | Last turn; Session (since the last compaction): cache reads / (reads + writes) |
| 5h limit | Used; Resets in; Pace (the last 90 minutes); Budget (the pace that lasts to the reset); At reset, the projection at this pace, or Runs out when that comes before the reset |
| Week | Used; Resets; Pace (the last 6 hours); Budget; Week elapsed (the share of the week gone); Spare or Overspent, how far usage is from the week elapsed, in points and hours of budget |
| `Fable`, ... | Used; Resets: a model's own weekly limit, shown only while the limits file carries it (see [Limits](#limits)) |
| Advisor | Model; Main; State (`inactive` when its model ranks below the main one) |
| ⓘ | Effort, Advisor, Claude Code, Started in, Branch (CLI), Running, Turns, API cost (the session at API prices; a subscription is not charged per token) |

### Readings and when they turn

A reading stays grey while it needs nothing from you. It turns amber (watch) or red (act) past these
points, and only then shows its number on the row:

| Reading | Watch | Act |
|---|---|---|
| `Ctx` - context against the auto-compaction trigger | 60% | 80%, or three turns left at the recent growth |
| `Cache` - time left in the prompt cache window | under min(15 min, half the window) | under min(5 min, a fifth of the window) |
| `5h` - five-hour limit | 70% | 90%, or a pace that runs out before the reset |
| `7d` - weekly limit | 80% | 95% |
| Model-scoped weekly limits (`Fable`, ...) | 80% | 95% |

Inside the cards, a pace over its budget is red for `5h` (it runs out before the reset) and amber for
the week; Overspent is amber, red past a day of budget. Pace needs 10 minutes of readings for `5h`
and an hour for the week; until then it says so.

**Colours mean one thing everywhere:** none, nothing to do (good values stay uncoloured too); amber,
worth a glance; red, changes what to do next; blue, a cold cache, lost rather than urgent; green, a
state that is on (auto-compact). Model and effort colours name the model.

## Where the numbers come from

| Reading | Source |
|---|---|
| Context, cost, five-hour and weekly limits | Claude Code after each model request (the API's headers, one request behind), merged with the limits file when it exists; from a compaction to the next request, context is `/context`'s estimate of the whole window, counted by the token-count API once usage drops the request before the compaction (the compaction's own size until then) |
| Readings other sessions saw, the model-scoped limits and their resets | The limits file (see [Limits](#limits)) |
| Auto-compact on or off, the compact point, the compact window and who set it | `/context`'s breakdown (`autoCompactThreshold`, `rawMaxTokens`, `autocompactSource`), read at session start and after each compaction |
| Branch | `git branch --show-current` in the start directory; CLI only, since Desktop runs no processes for a plugin |
| Cache countdown, cache hits for the last turn | The session's own requests; the countdown runs from the last one |
| Cache hits for the session, the TTL regime (`1h`/`5m`) | The session transcript since the last compaction |
| Model, effort, Claude Code version, turns | Claude Code; effort falls back to `modelSettings` / `effortLevel` in settings until the first request reports it; `/effort` in this session shows at once |
| Advisor | `advisorModel` in `settings.json`, re-read every 20 seconds; `/advisor` in this session shows at once |
| Session time | From the session's launch as Claude Code reports it: a resumed session counts from its first launch, `/clear` starts it over |

The row re-reads the limits file and `settings.json` every 20 seconds, so a session you are not using
still shows limits another session saw. `/clear` and a resume inside a running session drop the
previous conversation's context, cache and session readings at once; the limits stay, since they
belong to the account. A model switch or an auto-compact change in `/config` takes effect on the
next completed turn.

## Limits

pitboard reads limits and never writes them. What it shows depends on what else runs:

- **Alone:** the five-hour and weekly limits of its own session, as Claude Code reports them after
  each model request. Pace needs 10 minutes of readings for `5h` and an hour for the week; the
  readings are kept between sessions, so a new session continues from the last one's.
- **With [paceline](../paceline/README.md):** paceline keeps every session's readings in its limits
  file. pitboard reads it at session start, after each turn and every 20 seconds, so an idle session
  shows the limits another session saw, and the file's history gives a pace from the first draw.
- **With a tool that adds model-scoped limits to the file:** the `scoped` buckets (`Fable`, ...) show
  while the file holds a five-hour or weekly reading tagged `"source": "usage-endpoint"` at most 15
  minutes old, and until the week they belong to ends. paceline writes no such reading, so with
  paceline alone they stay hidden.

**Where the file is.** `$PACELINE_STATE_DIR/rate-limits.json` when that variable is set and not
empty, else `<config>/state/paceline/rate-limits.json`, where `<config>` is `$CLAUDE_CONFIG_DIR` or
`~/.claude`: the lookup paceline uses. The history, `rate-limits.history.jsonl` and its rotated
`.1`, lies beside it. Set `PACELINE_STATE_DIR` where Claude Code starts, or pitboard and paceline
look in different directories. The format is paceline's:
[the limits file](../paceline/README.md#the-limits-file-for-readers).

**How the row combines the file with its own session.** Readings are grouped by window, the reset
time rounded to the minute. A later window wins, even with a lower reading; within one window the
higher reading wins, and a tie keeps the file's. A file reading observed more than 5 minutes ahead of
the clock, or with a window ending later than its length allows (5 hours, 7 days, plus 5 minutes),
counts as absent. A window whose reset has passed reads 0%: the weekly one with its next reset, the
five-hour one with its reset unknown until the next window opens.

## Known compromises

* **Limits move only while some session is active.** Claude Code reports them after each model request. With every session idle nothing moves, and the model-scoped limits drop off the row after 15 minutes.
* **A plugin update takes effect in new sessions.**
* **An idle session sees another session's reading up to about 20 seconds late**, the interval of its re-read, once paceline has merged that reading into its file.
* **The advisor shown is the setting.** `/advisor` may say the running conversation keeps its previous advisor until `/clear`; the plugin cannot read that conversation-level state.
* **Desktop cards follow the pointer only.** Desktop tells a plugin nothing about hover, so a card is the surface's own hover reveal; the terminal's detail line opens on a click instead.
* **A Desktop card can lag a minute.** The row redraws only when what it shows changes, since a redraw under the pointer keeps a card open; a card's paces and times are taken at the start of the minute. In the last 5 minutes of the cache the row counts seconds, so a card opened then can stay up.
* **The transcript is a private file format.** The session's cache hits and the TTL regime come from it; a format change turns them into `n/a`, not into a wrong number.

## Notes

- `CLAUDE_CONFIG_DIR` replaces `~/.claude` in every path on this page, as it does for Claude Code's own files;
  `PACELINE_STATE_DIR` moves the limits files (see [Limits](#limits)).
- The layout is not configurable. The plugin has no setting of its own.
