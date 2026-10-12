# pitboard limitations

What pitboard cannot do, grouped by where you meet it. Each entry says what you see and what still
works, with a link to the page that explains the workaround. For how the numbers are made, see
[How pitboard works](explanation.md).

## Accounts and logins

### Limits need a claude.ai subscription login

pitboard takes the five-hour and weekly limits from what Claude Code reports, and from paceline's
limits file. A session that uses an API key, or a login without Pro or Max usage limits, gets no
limit readings. Sessions through a gateway, Amazon Bedrock, or Google Vertex AI are not tested;
without limit readings they show none either.

**What you see:** the `5h` gauge stays empty, `7d` is missing from the row, and the `5h limit` card
says `No reading yet.`

**What still works:** context, cache, and the session card. See
[Limits stay empty](troubleshooting.md#limits-stay-empty).

### Two accounts in one configuration directory show their own limits, with gaps

paceline keeps the readings of each account in a partition of its own in the limits file, and
pitboard draws the partition of the account that is signed in, found from Claude Code's
`.claude.json`. After a `/login` the row follows the new account within about 20 seconds, and the
samples behind `Pace` of the old account are dropped. Some gaps remain:

- A reading with no account, from an older paceline or another writer, sits in a partition that
  belongs to no account. pitboard draws it when the signed-in account has no partition yet, and marks
  it.
- When `.claude.json` is missing, does not parse, or names no account, pitboard cannot tell whose
  figures the file holds. It draws the top level of the file, which is the newest partition, and
  marks it. If the account was known a moment before, pitboard keeps its samples and takes a
  different account only when `.claude.json` names one.
- Figures from a file of unknown account show no `Pace`, and they are not added to the samples
  behind it.
- The session's own limits, which Claude Code reports with each reply, are never marked. For the
  turn after a `/login`, they may still be the old account's.
- A `/login` in another open session can misfile paceline's readings for a while, as paceline's
  limitations describe.

**What you see:** the `5h limit`, `Week` and model-scoped limit cards show an `Account` row with
`unknown` whenever a figure of the limit comes from a file whose account is not known. The row shows
no mark while the figures are the account's own.

**What still works:** give each account its own configuration directory, as the next entry
describes. paceline's [limitations](../../paceline/docs/limitations.md#two-accounts-in-one-configuration-directory-keep-their-limits-apart-with-gaps)
give the detail.

### Each configuration directory has its own row data

A session reads its settings and its transcript under its own configuration directory, and
paceline's limits file under `<config>/state/paceline` unless `PACELINE_STATE_DIR` is set. `<config>`
is `$CLAUDE_CONFIG_DIR`, else `~/.claude`.

**What you see:** a session of one configuration directory never shows the readings of a session of
another.

**What still works:** see [Move the state directory](how-to.md#move-the-state-directory).

## Surfaces

### The Claude mobile app has no row

The row draws only in the CLI and in Desktop. The Claude mobile app has no row.

**What you see:** no row in the Claude mobile app.

**What still works:** `/pitboard` opens a pane with every card on any surface. See
[Open every card with /pitboard](how-to.md#open-every-card-with-pitboard).

### Some readings need git, pwsh, or node

pitboard runs `git` for `Branch`, `pwsh` for weekday names in the OS locale on Windows, and `node`
for a transcript over 4 MiB. Each helps only when it is on the `PATH` of the environment Claude Code
starts in.

**What you see:** without `git`, no `Branch` row; without `pwsh`, weekday names in the default
locale of Claude Code's plugin runtime; without `node`, the transcript readings of a long session
stay `n/a`.

**What still works:** every other reading. See [Requirements](reference.md#requirements).

### The Desktop row gives way to a survey

While Desktop shows a survey above the prompt, pitboard draws no row there.

**What you see:** no row above the prompt while the survey shows.

**What still works:** `/pitboard` shows every card meanwhile.

## Data pitboard cannot see

### Limits while every session is idle

Limits while every session is idle, unless paceline's usage-watch is on.

**What you see:** the five-hour and weekly readings stand still, and the row lags `/usage`.

**What still works:** turn on paceline's usage-watch. See
[Use pitboard with paceline](how-to.md#use-pitboard-with-paceline).

### Other sessions' readings and model-scoped limits need paceline

Other sessions' readings, unless paceline records them. Model-scoped limits, unless paceline's
usage-watch writes them. pitboard shows them only while the newest exact reading is at most
15 minutes old.

**What you see:** without paceline, a session shows only the limits of its own requests; without
usage-watch, no model-scoped limits.

**What still works:** see [Model-scoped limits](explanation.md#model-scoped-limits).

### The advisor shown is the setting

The advisor of the running conversation. pitboard shows the setting, `advisorModel` in
`settings.json`.

**What you see:** the advisor on the row follows `settings.json`, which can differ from the advisor
the running conversation uses.

**What still works:** `/advisor` in this session shows at once.

### Desktop reports no pointer movement

Pointer movement in Desktop. Desktop tells a plugin nothing about hover, so a card is the surface's
own hover reveal.

**What you see:** a card can stay up after the pointer leaves.

**What still works:** see [A Desktop card stays open](troubleshooting.md#a-desktop-card-stays-open).

### The transcript format is private

A stable transcript format. The transcript is a private file of Claude Code. If pitboard cannot read
it, `Session` counts only the requests since pitboard loaded or since the last compaction, and `TTL`
reads `5m?`.

**What you see:** `Session` counts fewer requests, and `TTL` reads `5m?`.

**What still works:** context, the cache countdown, `Hit`, and the limits come from Claude Code.

### Only the last 4 MB of a long transcript counts

For a transcript over 4 MiB, pitboard reads its last 4 MB through `node`.

**What you see:** `Session` counts only what the last 4 MB holds.

**What still works:** `Hit` for the last turn comes from the session's own requests. See
[Files read](reference.md#files-read).

### Subagents do not show on the row

Subagents' requests and compactions leave the row alone; it describes the main conversation.

**What you see:** a subagent's context, cache, and compactions do not show.

**What still works:** the limits include what subagents spend, since they belong to the account.
See [Where the numbers come from](explanation.md#where-the-numbers-come-from).
