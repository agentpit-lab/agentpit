# pitboard how-to guides

Task guides for pitboard. Each guide says when you need it, the steps, and how to tell it worked. For
the meaning of each reading, see the [reference](reference.md).

## Use pitboard with paceline

**When you need it:** you run several sessions and want an idle one to show the limits another session
saw, you want Pace from the first draw of a new session, or you want the model-scoped weekly limits
on the row.

pitboard alone shows the five-hour and weekly limits of its own session, as Claude Code reports them
after each model request. [paceline](../../paceline/README.md) records the readings of every session
in its limits file, `rate-limits.json`, and keeps a history of them. pitboard reads that file when it
exists; it needs no setting for it.

1. Install paceline:

   ```
   /plugin install paceline@agentpit
   ```

2. Start a new session.
3. To show the model-scoped weekly limits too, turn on paceline's usage-watch:

   ```
   /paceline:usage-watch on
   ```

   usage-watch is off until you turn it on. While it is on, paceline reads the account's usage, the
   figures `/usage` shows, every 125 seconds, and merges them with the model-scoped limits into the
   limits file.

**How to tell it worked:**

- A session you are not using shows a limit reading another session saw, within about 20 seconds of
  paceline writing it to the file.
- `Pace` in the `5h limit` and `Week` cards shows a rate from the first draw of a new session, when
  the file's history holds a reading of the current window at least 10 minutes old for the five-hour
  limit, or at least an hour old for the week.
- With usage-watch on, each model-scoped limit appears in the limits group under its model's name,
  such as `Fable`, after the next usage-watch reading. If it does not, see
  [A model-scoped limit disappears](troubleshooting.md#a-model-scoped-limit-disappears).

## Move the state directory

**When you need it:** you keep paceline's files somewhere other than `<config>/state/paceline`, for
example for a second, isolated setup. `<config>` is `$CLAUDE_CONFIG_DIR`, else `~/.claude`.

pitboard finds the limits file by paceline's own rule: `$PACELINE_STATE_DIR/rate-limits.json` when
the variable is set and not empty, else `<config>/state/paceline/rate-limits.json`. pitboard reads
the variable from the environment Claude Code starts in.

1. Set `PACELINE_STATE_DIR` in the environment that starts Claude Code: the shell you run `claude`
   from, or your user environment for Claude Code Desktop.
2. Start a new session.

**How to tell it worked:** `rate-limits.json` appears in the new directory once paceline records a
reading, and an idle session shows limits other sessions saw. If only one of the two plugins sees
the variable, they look in different directories.

## Read the row in Desktop

**When you need it:** you use Claude Code Desktop and want to know what each mark on the row means.

The row sits above the prompt. Context and cache are pies the size of Desktop's own context ring.
The five-hour and weekly limits are bars. Each model-scoped limit is a pie. The advisor has its own
group between the context and the cache while an advisor is set. `ⓘ` ends the row.

1. Point at a reading. pitboard shades it and shows its card: the reading's name, one row per
   value, a bar for a limit, and advice when the reading needs a decision.
2. Read a limit's bar. The fill is the share used. The tick marks where an even pace to the reset
   would stand now, which is the share of the window gone. A fill past the tick spends faster than
   the window allows.
3. Read the colours: grey needs nothing, amber is worth a glance, red changes what to do next. See
   [Levels](reference.md#levels) for the thresholds.
4. For the explanations behind a card, run `/pitboard`; see
   [Open every card with /pitboard](#open-every-card-with-pitboard).

The bars change with the window's width. pitboard keeps the row on one line where it can:

- In a wide window, `5h` and `7d` show a bar, the percentage, and the time to reset.
- When that does not fit, the bars drop the time to reset first. The card still shows it.
- When even that does not fit, `5h` and `7d` turn into pies, and show their numbers only once they
  leave calm.

**How to tell it worked:** a card appears when you point at a reading and goes when you move away. If
a card stays up, see [A Desktop card stays open](troubleshooting.md#a-desktop-card-stays-open).

## Read the row in a narrow terminal

**When you need it:** your terminal is too narrow for the whole row.

In the CLI the row sits under the prompt, above Claude Code's own hint line, with a short rule above
it. Each reading is its name and a two-cell gauge. A number appears only once the reading leaves calm,
which keeps the row short while nothing needs you.

1. Let the row wrap. It breaks between groups: context, cache, limits, and `ⓘ`. A group stays whole.
2. To read one reading's values, click its name, or press Enter while the name has focus. Its card
   opens as one line under the row, with `▲` under the name. The `ⓘ` card takes a second line from
   `Claude Code` on.
3. A detail line that is wider than the terminal is cut at its end. To read every value, run
   `/pitboard`.

**How to tell it worked:** the groups stack on separate lines, and a click on a name opens its line.
A second click on the same name closes it.

## Open every card with /pitboard

**When you need it:** you want every reading with its explanation at once, a card cut short in a
narrow terminal, or the readings in the Claude mobile app, which has no row.

1. Run:

   ```
   /pitboard
   ```

2. Read the pane, titled **Status**, from the top:
   - **Needs attention**, only while something needs a decision: each warning with its advice, the
     most urgent first.
   - Every card in the row's order, with all its rows and its explanations. The advisor's card
     follows the context's while an advisor is set; the session card comes last.
   - Under `5h limit` and `Week`, a bar with the used share and the even-pace tick. The `5h limit`
     bar also shows, paler, where the current pace leads by the reset.

**How to tell it worked:** Claude Code answers `Status pane opened.` and the pane shows the cards.
