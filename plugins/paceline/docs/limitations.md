# paceline limitations

What paceline cannot do, grouped by where you meet it. Each entry says what you see and what still
works, with a link to the page that explains the workaround. For how the numbers are made, see
[How paceline works](explanation.md). Type the `/paceline:` commands in a session; run the
`paceline` commands in a session's Bash tool, or ask the agent to run them.

## Accounts and logins

### Limit readings need a claude.ai subscription login

pace, usage-watch, and the limits writer work only with a claude.ai login on a plan with usage
limits, such as Pro or Max. A session that signs in with an API key, or reaches Claude through a
gateway, carries no five-hour or weekly readings, and Claude Code gives usage-watch no login for its
request. A session whose requests carry no limit readings gives pace no data. Sessions through
Amazon Bedrock or Google Vertex AI are not tested; without limit readings they give no data either.

**What you see:** `/paceline:pace status` stops with `no usage data yet`, no `pace:` line comes
before an Agent launch, and `/paceline:usage-watch status` shows `last attempt: never`.

**What still works:** context-watch and cache-watch do not use limit readings. See
[Keep usage current while idle](how-to.md#keep-usage-current-while-idle).

### Two accounts in one configuration directory share one limits file

A reading in `rate-limits.json` carries no account. When you sign in to another account with
`/login` in the same Claude Code configuration directory, the readings of both accounts go to one
limits file. Within a window the higher reading wins, and a later window wins whichever account it
came from.

**What you see:** pace, the night queue, and status line plugins such as pitboard show a mix of the
two accounts.

**What still works:** give each account its own configuration directory, as the next entry
describes.

### Each configuration directory keeps its own state

The state directory sits under the Claude Code configuration directory: `$CLAUDE_CONFIG_DIR`, else
`~/.claude`. Two configuration directories on one machine have two state directories.

**What you see:** each has its own pace switch, profile, plans, reservations, night queue,
usage-watch switch, and usage-watch schedule. Turning pace on in one does not turn it on in the
other.

**What still works:** to share one state directory, set `PACELINE_STATE_DIR` to the same path for
both. See the [reference](reference.md#paceline-reference) for how `<state>` is found.

## Surfaces and session kinds

### Requirements: Claude Code 2.1.287 and Python 3.9

paceline needs Claude Code 2.1.287 or later, and Python 3.9 or later on `PATH` as `python3` or
`python`.

**What you see:** on an older Python, the hooks do nothing and write a `hook.python` line to
`<state>/debug.log`, and `paceline` stops with a message that names the version.

**What still works:** see [A hook reports a Python problem](troubleshooting.md#a-hook-reports-a-python-problem).

### cache-watch does nothing in non-interactive runs

cache-watch keeps no cache in `claude -p` runs. Its wake is a background `Stop` hook, and Claude Code
kills a background hook that is still running when a `-p` run ends. Ending the session also removes
the session's cache-watch state. Sessions of the Agent SDK are not tested.

**What you see:** the run ends after its turn, and no wake comes.

**What still works:** cache-watch in an interactive session, in the CLI or in Desktop. See
[Hold the cache while away](how-to.md#hold-the-cache-while-away).

### A hard limit refuses a launch in non-interactive runs

At a hard limit, the hook before an Agent or Workflow launch asks you to confirm. In a `claude -p`
run nobody can answer, and Claude Code denies such a request. This entry is from Claude Code's
documentation, not tested here.

**What you see:** an Agent or Workflow launch at a hard limit fails in the run.

**What still works:** below the hard limits the `pace:` line is advice only, and the launch goes
ahead. A night queue checks `paceline auto` before each item, and `auto` waits or stops at the same
limits. See [The confirmation at a hard limit](explanation.md#the-confirmation-at-a-hard-limit).

## context-watch

### context-watch leaves subagent compactions alone and gives no warnings with auto-compaction off

context-watch leaves subagent compactions alone: it writes a snapshot only for the main
conversation. With auto-compaction off, the context line measures against the context window, and
no warnings come.

**What you see:** no snapshot for a subagent's compaction, and no warning at 85% or 95% while
auto-compaction is off.

**What still works:** the line, and a snapshot at each compaction of the main conversation. See
[Recover after a compaction](how-to.md#recover-after-a-compaction).

## cache-watch

### cache-watch keeps only one-hour cache entries

cache-watch holds the one-hour prompt cache. A session whose main thread writes five-minute cache
entries is refused.

**What you see:** `on` prints
`not started: this session writes five-minute cache entries; cache-watch keeps only one-hour entries`.

**What still works:** the main thread's TTL is Claude Code's setting for the session. See
[Hold the cache while away](how-to.md#hold-the-cache-while-away).

### cache-watch keeps only the main thread's cache

cache-watch keeps only the main thread's cache. It reads the session's own transcript and skips
subagent rows; subagents write their rows to separate files. A wake refreshes the main thread's
cache and never a subagent's.

**What you see:** a finished subagent that you resume after its cache expired starts cold, whatever
its TTL.

**What still works:** the main conversation stays warm while cache-watch is on.

### A five-minute main thread is refused even with one-hour subagents

Only the main thread's cache entries decide. A session whose main thread writes five-minute entries
is refused even when its subagents write one-hour entries.

**What you see:** `on` prints `not started: this session writes five-minute cache entries`.

**What still works:** the main thread's TTL is Claude Code's setting for the session; subagent TTL
settings do not change the decision. See
[Hold the cache while away](how-to.md#hold-the-cache-while-away).

### cache-watch needs a cache entry first

cache-watch starts only after the main thread has written a cache entry, which tells it the TTL.

**What you see:** `on` in a session with no cache entry yet prints
`not started: this session has no one-hour cache entry yet`.

**What still works:** take one turn in the session, then run `on` again.

### A computer that sleeps past the cache's expiry loses the cache

The wake runs in a hook process on this computer. A computer that sleeps past the cache's expiry
misses the wake, and no message tells you.

**What you see:** after the computer wakes, the next turn writes the whole context to the cache
again. `/paceline:cache-watch status` shows the session as `on` with the cache `expired`.

**What still works:** keep the computer awake while you are away, as
[Run a night queue](how-to.md#run-a-night-queue) describes in its first step.

### Each wake is a full-context turn

Each wake is one turn: a cache read of the whole context plus a one-line push, counted against your
five-hour limit.

**What you see:** at most `max` (5) such turns per absence, with the default settings.

**What still works:** lower `--max`, or raise `--lead`, with `/paceline:cache-watch config`. See
[Hold the cache while away](how-to.md#hold-the-cache-while-away).

### The push reaches a phone only with Remote Control

The wake sends its one-line message as a push only when the session has the push tool: with Remote
Control on and "Push when Claude decides" enabled in `/config`.

**What you see:** without them, no push on your phone.

**What still works:** without the push tool, the wake posts the line in the session as a reply instead.

### cache-watch stops itself when the main thread switches to five-minute entries

When the main thread starts writing five-minute cache entries while cache-watch is on, watching
stops.

**What you see:** one wake tells you in one line that cache-watch stopped, and
`/paceline:cache-watch status` shows `off (five-minute cache)`.

**What still works:** once the main thread writes one-hour entries again, run `on` again.

### cache-watch shows times in the machine's time zone

The `Cache kept until` line and the times in `status` use the machine's time zone.

**What you see:** they can differ from pace's times when the profile counts in another zone.

**What still works:** pace's commands keep the profile's zone.

### cache-watch depends on Claude Code's private transcript format

cache-watch reads the session transcript, a private file of Claude Code, for the main thread's
cache writes and their TTL.

**What you see:** if the format changes, `on` can report no one-hour cache entry, or wakes can
stop.

**What still works:** the rest of paceline does not read the transcript.

## pace

### The pace line counts a model's own limit only when the model is set where Claude Code starts

The hook before a launch passes no model to pace. It counts a model-scoped weekly limit only when
`PACELINE_MODEL` or `ANTHROPIC_MODEL` is set where Claude Code starts.

**What you see:** the `pace:` line can advise a model whose own weekly limit is spent.

**What still works:** `paceline status --model <id>` shows the model-scoped limit. See
[The budget per hour](explanation.md#the-budget-per-hour).

### The pace line is skipped when pace takes longer than 3 seconds

The hook before a launch gives `paceline status` 3 seconds. When pace takes longer than 3 seconds,
the hook adds nothing.

**What you see:** no `pace:` line and no confirmation for that launch; the launch goes ahead. A
`hook.pre_agent` line with `"failure":true` goes to `<state>/debug.log`.

**What still works:** the next launch tries again. Run `paceline doctor` to find a slow step.

### Reservations may outlive their session

At session end, a hook asks pace to release the session's reservations. Claude Code gives all
`SessionEnd` hooks 1.5 seconds by default, and a plugin's own timeout does not raise it, according
to Claude Code's documentation. Reservations may outlive their session when the release does not
finish in time.

**What you see:** until the lease ends (30 minutes, or 240 minutes for a subagent) or pace finds the
owning process gone, the night queue counts the reservation as outstanding.

**What still works:** `paceline reserve list` shows the active reservations, and
`paceline reserve prune` drops the ones whose process is gone. See
[Reservations](explanation.md#reservations).

### Unknown memory turns off the memory checks

When pace cannot measure free memory, the memory checks do not apply.

**What you see:** `/paceline:pace status` says `host memory unknown`.

**What still works:** see [The recommendation](explanation.md#the-recommendation).

### Without the tzdata package, few time zones

Without the `tzdata` package, Python may have no zone data, and pace knows only `UTC` and a few
built-in European zones.

**What you see:** the profile stays `UTC`.

**What still works:** see [The time zone is wrong](troubleshooting.md#the-time-zone-is-wrong).

## usage-watch

### usage-watch is off by default

usage-watch is off until you run `/paceline:usage-watch on`.

**What you see:** without it, no model-scoped limits, and data only as of the last turn of a session
on this machine.

**What still works:** everything in [Data pace cannot see](#data-pace-cannot-see) about readings
without usage-watch. See [Keep usage current while idle](how-to.md#keep-usage-current-while-idle).

### usage-watch reads an endpoint outside the published API

usage-watch reads `https://api.anthropic.com/api/oauth/usage`, an endpoint outside Anthropic's
published API reference. Its address, answer format, or availability can change without notice.
paceline reaches it only through Claude Code, with the session's own login; paceline never sees a
token.

**What you see:** if the endpoint changes, `/paceline:usage-watch status` shows
`last error: HTTP <status>` or `unexpected response`, and usage-watch waits 10 minutes after any
error other than HTTP 429. pitboard's model-scoped limits disappear 15 minutes after the last good
reading, and pace's model-scoped rules stop applying.

**What still works:** the limits writer, the five-hour and weekly readings from each session's own
turns, pace's budget and recommendation, the night queue (with the data ageing while sessions are
idle), and pitboard's five-hour and weekly readings. See
[usage-watch shows nothing](troubleshooting.md#usage-watch-shows-nothing).

### usage-watch needs an open session

usage-watch runs inside an open session with paceline.

**What you see:** while every session is closed, nothing is read.

**What still works:** keep one session open, busy or idle. See
[Keep usage current while idle](how-to.md#keep-usage-current-while-idle).

### A sleeping computer pauses usage-watch

usage-watch runs in the session process, so it pauses while the computer sleeps.

**What you see:** no readings while the computer sleeps. When that lasts longer than 30 minutes, the
data turns `STALE`: the hard limits are skipped and the night queue waits. Readings resume within
about 10 seconds of waking.

**What still works:** keep the computer awake, as
[Run a night queue](how-to.md#run-a-night-queue) describes in its first step.

### CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC blocks usage-watch

While `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` is set, Claude Code refuses usage-watch's request.

**What you see:** `/paceline:usage-watch status` shows `last error: network error`, and usage-watch
asks again every 10 minutes.

**What still works:** the readings from each session's own turns. See
[Keep usage current while idle](how-to.md#keep-usage-current-while-idle).

## Data pace cannot see

### Without usage-watch, only the five-hour and weekly windows

Without usage-watch, only the five-hour and weekly windows: the session readings paceline records
hold only those two. A model's own weekly limit is not seen, and `--model` has no effect. Without
usage-watch, the data is as of the last turn of any session on this machine.

**What you see:** the figures lag `/usage`, and no model-scoped limits show.

**What still works:** turn on usage-watch. See
[Why the numbers can lag `/usage`](explanation.md#why-the-numbers-can-lag-usage).

### Measured costs and the forecast need care

The measured cost of a reservation or queue item is a change in account-wide weekly usage, so
parallel work inflates it. A reservation that overlapped another is marked `overlapped`, and
`paceline calibrate` leaves it out. The forecast and `paceline calibrate` need history: 3 hours of
the last day for the forecast, and `calibration_min_days` (14) days for calibration.

**What you see:** no forecast, or no calibration proposal, until the history is there.

**What still works:** the budget and the recommendation need no history. See
[The forecast](explanation.md#the-forecast).
