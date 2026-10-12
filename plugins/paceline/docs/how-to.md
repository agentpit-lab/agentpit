# paceline how-to guides

Task guides for paceline. Each guide says when you need it, the steps, and how to tell it worked.
Run the `paceline` commands in a session's Bash tool, or ask the agent to run them. The
[reference](reference.md) lists every option.

## Run a night queue

**When you need it:** you want the agent to work through a list of tasks while you sleep, within
what the week can spare.

1. Keep the machine awake for the night.
   - Windows: `scripts/night-power.ps1` in the plugin directory turns off sleep on AC power and
     moves the Windows Update active hours over the night, so a restart does not land mid-queue.
     Without a switch it is a dry run that prints what it would change. Run it with `-Enable`
     before the night and with `-Disable` after it, in an elevated PowerShell. The previous values
     are saved in `<state>/night-power-state.json`. `-NightStart` and `-NightEnd` set the hours
     (default 0 and 8).
   - macOS: start the session under `caffeinate -i -s`, for example `caffeinate -i -s claude`.
   - Linux: start it under `systemd-inhibit --what=sleep`, for example
     `systemd-inhibit --what=sleep claude`.
2. Turn pace on, if it is off:

   ```
   paceline on
   ```

3. Add the items, each with an estimate in percent of the weekly limit:

   ```
   paceline queue add --title "Update the release notes" --repo agentpit-dev --estimate 2
   paceline queue add --title "Rename the store keys" --repo agentpit-dev --estimate 3 --depends Q1
   ```

   Items get the IDs `Q1`, `Q2`, and so on. Add `--independent` to an item that may run beside
   other independent items.
4. Tell the agent to run the queue. It starts the run once:

   ```
   paceline auto --start-run
   ```

   Then, before each item, it runs `paceline auto` and follows its exit code:
   - `0` (start): run `paceline queue start <id>` for the item named in the output, do the work,
     then `paceline queue done <id>`. When the item's checks fail, run `paceline queue fail <id>`
     and fix them; after `gate_fix_attempts` (3) failures the item is deferred. When an item needs
     your permission, run `paceline queue defer <id>`. Deferring an item also defers the items that
     depend on it.
   - `10` (wait): wait, then run `paceline auto` again. With `--json`, `wait_until` says until when.
   - `11` (stop): end the run.
5. In the morning, the agent fills the report template `templates/morning-report.md` from
   `paceline queue report` and the queue's state.

**How to tell it worked:** `paceline queue report` prints the done, deferred, running, and pending
items with their estimated and actual spend, for example
`done 2 (est 5.00% act 4.10%), deferred 1, running 0, pending 0`. `paceline queue list` shows each
item's status and, for a deferred item, the reason.

The leftover pool works the same way with `--pool` (item IDs `L1`, `L2`, and so on). Approve it with
`paceline queue --pool approve`; it runs only on the last night before the weekly reset. The
[explanation](explanation.md#the-night-queues-decisions) says how `auto` decides.

## Declare, move, or cancel a plan

**When you need it:** you know a day ahead that you will be away, or that a day will be heavy, and
you want the budget to follow.

1. Declare the plan. Dates are in the profile's time zone, and a bare `--to` date includes that day:

   ```
   paceline plan add --type away --from 2026-10-16 --to 2026-10-16 --phrase "off on Friday"
   paceline plan add --type heavy --from tomorrow --to tomorrow --weight 1.5
   ```

   An `away` plan gives its day hours no budget; a `heavy` plan multiplies their weight by
   `--weight`, default `heavy_weight` (2).
2. To move or cancel a plan, find it first:

   ```
   paceline plan find --text "friday"
   ```

   Exit code 0 means one match: use its ID. 3 means several: narrow the search with `--on DATE` or
   `--type`. 4 means none.
3. Move it, or cancel it:

   ```
   paceline plan modify P3 --from 2026-10-17 --to 2026-10-17
   paceline plan cancel P3
   ```

4. While pace is on, a session start the day before reminds the agent of a plan that is not
   confirmed. After you say it still holds, the agent runs `paceline plan confirm P3`. Confirming
   stops the reminder and leaves the budget as it is.

**How to tell it worked:** `add`, `modify`, and `cancel` end with the new budget, for example
`plan P3 cancelled; budget 1.21%/h, night 5.0%`. `paceline plan list` shows every plan with its
local start and end.

## Hold the cache while away

**When you need it:** you step away from a session with a long context and want to come back to a
warm cache. When a session sits idle for an hour, its prompt cache expires, and the next turn
writes the whole context to the cache again.

1. In that session, run:

   ```
   /paceline:cache-watch on
   ```

   It starts only when the session's main thread writes one-hour cache entries. It refuses a
   session with five-minute entries, or with no cache entry yet, and says why.
2. Leave the session idle. After every turn, a `Stop` hook waits in the background and wakes the
   session `lead` minutes (5 by default) before the cache would expire. The woken turn keeps the
   cache for another hour. The agent posts one line, such as
   `Cache kept until 15:30 · wake 1 of 5 · reply stop to stop keeping it`, sends it as a push
   notification when the session can, and ends the turn. Wakes come every `60 - lead` minutes.
3. Any message you send in the session resets the count. After `max` wakes (5 by default) in a row
   without a message from you, watching stops. To stop earlier, reply `stop` or run
   `/paceline:cache-watch off`. Ending the session also stops it.

Each wake is one full-context turn: a cache read of the whole context plus a one-line reply, counted
against your five-hour limit. With the defaults, that is at most five turns per absence. To change
the defaults, run `/paceline:cache-watch config --lead 10 --max 3`; to change them for one session,
pass the same options to `on`.

**How to tell it worked:** `/paceline:cache-watch status` shows the session as `on`, with the cache
expiry and the next wake, for example
`<session id> · on · lead 5 min · wakes 0/5 · cache 1h · expires 15:30 · next wake 15:25`.

## Recover after a compaction

**When you need it:** a session was compacted, and you or the agent need what the summary dropped.
context-watch is on from install, so this works without setup.

1. Before the compaction, context-watch puts a line such as
   `context: 612k / 967k auto-compact point (63%)` on every prompt. When the context first passes
   85% and then 95% of the auto-compaction point, the agent is told once per level to write its
   recovery point to the task's status file. The levels reset after each compaction.
2. At each compaction of the main conversation, automatic or `/compact`, paceline writes
   `.claude/paceline/<session id>.md` in the project. It holds your last 3 prompts, the agent's last
   2 replies, the last 5 tool calls with their inputs cut to 200 characters (without results), and
   every file the agent wrote or edited in the session.
3. The summary is told to keep the snapshot path. Right after the compaction, the agent is told to
   read the snapshot and the task's status files before its next step.
4. To read a snapshot yourself, open the file. `paceline context-watch status` prints the
   project's snapshot folder.

context-watch never vetoes, delays, or starts a compaction, and leaves subagent compactions alone.
An error in context-watch leaves the turn as it was. With auto-compaction off, the line measures
against the context window, and no warnings come. Snapshots older than 14 days are removed when a
session starts in the project.

To change it, run `paceline context-watch config`, for example `--levels 80,90` for earlier
warnings, `--line off` to hide only the line, or `--days 30` to keep snapshots longer.

**How to tell it worked:** after a compaction, `paceline context-watch status` counts the snapshot,
for example `snapshots: /home/me/project/.claude/paceline (1 file)`, and the agent's next step
starts by reading it.

## Keep usage current while idle

**When you need it:** you want pace and status line plugins to show the exact figures `/usage`
shows, including the model-scoped weekly limits, while no session makes requests.

1. Turn usage-watch on:

   ```
   paceline usage-watch on
   ```

   The switch holds for every session of the same Claude Code configuration directory and takes
   effect within 10 seconds.
2. Keep at least one session with paceline open, busy or idle. usage-watch reads the account's usage every 125 seconds while a session is open. It needs
   a claude.ai login.
3. To let pace account for a model's own weekly limit, pass the model with `--model`, or set
   `PACELINE_MODEL`.

**How to tell it worked:** `paceline usage-watch status` shows a recent `last success` and
`last error: none`. `paceline status` shows a data age of a few minutes at most, and lists the
model-scoped buckets after the weekly figure, for example `7d 34% (Fable 7%)`. If nothing shows, see
[usage-watch shows nothing](troubleshooting.md#usage-watch-shows-nothing).

## Change a profile setting

**When you need it:** the budget counts in the wrong time zone, your working day is longer or
shorter than 08:00 to 22:00, or a limit in the profile does not fit your machine.

1. Look at the current values:

   ```
   paceline profile
   ```

2. Change one key. The value is read as JSON when it parses, else as text:

   ```
   paceline profile set timezone America/New_York
   paceline profile set day_hours '[8, 24]'
   paceline profile set min_free_commit_mb 1024
   ```

   Set these first: `timezone`, `day_hours`, `night_hours`, `models`, and the memory floor
   (`min_free_commit_mb` and `commit_reserve_mb`). The defaults for the memory floor and the hours
   are starting points. If a confirmation prompt appears at most Agent launches, lower
   `min_free_commit_mb`; if your day is longer, widen `day_hours`.
3. To keep `UTC` while this machine is in another zone, run `paceline profile set timezone UTC`.
   That also silences the time zone hint at session start.

**How to tell it worked:** `profile set` prints `KEY: OLD -> NEW` and the new `paceline status`
line, for example `day_hours: [8, 22] -> [8, 24]`. A refused value exits 2, prints the reason, and
changes nothing. The [profile keys](reference.md#profile-keys) list what each key does and its
range.
