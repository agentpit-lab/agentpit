# pitboard troubleshooting

Problems by symptom. Each gives the cause, how to check it, and the fix. For how the numbers are made,
see [How pitboard works](explanation.md).

## The row does not appear

**Cause:** Claude Code is older than 2.1.287, the plugin is not installed or not enabled, or the
session started before the install. The row loads at session start, so a session that was already
running when you installed pitboard has no row.

**Check:**

1. Run `claude --version` and compare with 2.1.287.
2. Run `/plugin` and look for `pitboard` among the installed, enabled plugins.
3. Run `/pitboard`. If Claude Code answers `Status pane opened.`, the plugin runs.

**Fix:** update Claude Code, install or enable the plugin, and start a new session. In the CLI the row
sits under the prompt, above the hint line; in Desktop, above the prompt.

## The Branch row is missing

**Cause:** pitboard reads the branch with `git branch --show-current` in the start directory, and
shows the `Branch` row in the `ⓘ` card only when that returns a name. The row is missing when:

- `git` is not on the `PATH` of the environment Claude Code starts in.
- The start directory is not in a git repository.
- `HEAD` is detached, so there is no current branch.

**Check:** in the start directory, run `git branch --show-current`.

**Fix:** install `git` and put it on `PATH`, or check out a branch. The `Branch` row shows after the
next turn.

## Limits stay empty

The `5h` gauge stays empty, `7d` is missing from the row, and the `5h limit` card says
`No reading yet.`

**Cause:**

- The session has had no reply yet. Claude Code reports limits with each reply, and no paceline file
  holds an earlier reading.
- The session uses an API key, or a login without Pro or Max usage limits. Claude Code then reports
  no five-hour or weekly limits.

**Check:** send one message and wait for the reply. With paceline installed, look for
`rate-limits.json` in paceline's state directory; see [Files read](reference.md#files-read).

**Fix:** after the first reply the limits appear. With an API key there are no subscription limits
to show.

## The numbers lag

**Cause:**

- Limits move only when a session makes a request. With every session idle, nothing moves.
- An idle session re-reads paceline's limits file every 20 seconds, so it sees another session's
  reading up to about 20 seconds after paceline writes it.
- A Desktop card takes its paces and times at the start of the minute.
- `Pace` reads `n/a` until it has 10 minutes of readings for the five-hour limit, or an hour for the
  week.

**Check:** compare the row with `/usage`, and see
[Why the row can differ from /usage](explanation.md#why-the-row-can-differ-from-usage).

**Fix:** to keep the limits current while sessions are idle, turn on paceline's usage-watch; see
[Use pitboard with paceline](how-to.md#use-pitboard-with-paceline).

## A Desktop card stays open

**Cause:** Desktop tells a plugin nothing about the pointer, so a card is Desktop's own hover reveal.
A redraw of the row while a card is up can keep Desktop from noticing the pointer leave. pitboard
redraws the Desktop row only when what it shows changes, and keys the cards on the start of the
minute. Once the cache countdown drops under 5 minutes it shows seconds, and the row redraws every
second, so a card opened then can stay up.

**Check:** look at the cache reading. A countdown in `mm:ss` means the row redraws every second.

**Fix:** pitboard cannot close a Desktop card itself. To read the same values without hovering, run
`/pitboard`.

## Weekday names are in the wrong language

The `Resets` rows of the `Week` card and of a model-scoped limit show a short weekday name, such as
`Mon`, in a language you did not expect. pitboard shows no month names.

**Cause:** pitboard takes the locale from `LC_ALL`, `LC_TIME`, or `LANG`, in this order, and skips `C`
and `POSIX`. When none is set, as usual on Windows, it asks `pwsh` for the OS culture, once per
session. Without either, the weekday uses the default locale of Claude Code's plugin runtime.

**Check:** in the environment that starts Claude Code, print `LC_ALL`, `LC_TIME`, and `LANG`. On
Windows, run `pwsh -NoProfile -Command "(Get-Culture).Name"`.

**Fix:** set `LC_TIME` to your locale, such as `en_GB.UTF-8`, where Claude Code starts, or install
PowerShell 7 on Windows. Start a new session.

## A model-scoped limit disappears

A reading such as `Fable` leaves the limits group.

**Cause:** pitboard shows the model-scoped limits only from an exact reading by paceline's usage-watch
that is at most 15 minutes old, and only until the week they belong to resets. They disappear when:

- usage-watch is off, or every session with paceline is closed, so no new reading arrives.
- usage-watch failed. After an error other than HTTP 429, it waits 10 minutes before it asks again,
  so the newest exact reading can pass 15 minutes.
- The weekly window reset. The limits return with the next exact reading.

**Check:** run `/paceline:usage-watch status`. It shows the last attempt, the last success, and the
last error.

**Fix:** turn usage-watch on with `/paceline:usage-watch on`. After an error, the limits return with
the next successful reading.
