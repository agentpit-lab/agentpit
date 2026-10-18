# Changelog

## 0.1.14 - 2026-10-18

### Changed

- The limitations page says that PitBoard sees a compaction late where an organization manages
  Claude Code (Team and Enterprise logins, and machines with managed settings): Claude Code's
  sec-default guard holds back the compaction event. It gives an administrator's workaround for a
  small team, not tested. A fix is planned for a later version.

## 0.1.13 - 2026-10-17

### Changed

- The `pitboard` button toggles the pane, in the CLI row and in the Desktop footer: a click opens a closed
  pane and closes an open one. A pane behind another pane's tab comes to the front on the click; in a
  terminal narrower than 110 columns that can take a second click. `/pitboard` still only opens the pane.
  Verified in the CLI on Claude Code 2.1.302 and in Desktop on Claude Code 2.1.299.
- In the CLI the button is drawn bracketed, `[ pitboard ]`, as the row's one action beside the plain
  reading names.
- The row reads the burn snapshot of its own account, `five-hour-burn-<key>.json`, and no other burn file.
  With two accounts in use at once each row keeps its Threshold figure instead of losing it whenever the
  other account's snapshot is the newer one. A key that cannot be read for a moment keeps the last one;
  with no key at all the row reads no file. Update PitBoard and PaceLine together: a PaceLine of an
  earlier release writes no per-account file, and the row then projects to 100% from its own samples.

### Fixed

- A Claude Code Desktop session on one account no longer shows the figures of a terminal login on another.
  When the process has `CLAUDE_CODE_ACCOUNT_UUID` and `CLAUDE_CODE_ORGANIZATION_UUID`, as a Desktop session
  does, the row takes its account from them and reads `.claude.json` only without them. After a `/login`
  or `/logout` in such a session the account is marked unknown until the session restarts.

## 0.1.12 - 2026-10-17

### Added

- The 5h limit card follows PaceLine's five-hour projection when PaceLine publishes one for the signed-in
  account and the window on screen: the card shows `Threshold` (for example `80% at 18:40`) beside Pace and
  Budget, and the warning reads `5h window reaches 80% at 18:40`. Without a fresh matching projection the
  card keeps projecting to 100% from its own samples, and it does so as well once the window's use reaches
  the threshold, where PaceLine projects nothing.

## 0.1.11 - 2026-10-16

### Added

- The Ukrainian documentation pages ship with the plugin in `docs/uk/`.
- Every documentation page has an En/Uk switch that opens the page in the other language.
- The En/Uk switch remembers the reader's choice, and the first visit follows the browser's language.

### Changed

- The documentation pages mark product and command names as untranslated, so a translation
  tool leaves them as written.

## 0.1.10 - 2026-10-16

### Added

- The Ctx reading shows `↻ᶻ` (in Desktop, a drawn double arrow and sleep sign beside the pie) while
  idle compaction is expected: auto-compact is on, the `idleCompaction` setting and
  `DISABLE_AUTO_COMPACT` allow it, the cache lives an hour and the context has reached
  `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS` (200,000 by default, at least 100,000).
  The Ctx card gains an `Idle compact` row under `Auto-compact`.

### Changed

- The Desktop auto-compact mark is a drawn icon instead of the `↻` text.
- The terminal row draws `↻` and `ᶻ` before the percentage, as Desktop does.
- The Cache and Ctx cards and their alerts state what a cold turn writes and what compaction does
  instead of advising a step.
- In Desktop, an advisor whose model matches none of the known families is drawn in sand italics
  instead of white.
- An inactive advisor's name is dimmed and struck through in Desktop, in the terminal row and in the
  terminal info line; the `(inactive)` word stays plain.
- The documentation states the cache-write and cache-read price ratios with a link to Anthropic's
  prices, what `/compact` costs on a warm and a cold cache with a link to Claude Code's
  documentation, and what the idle compaction mark and the `Idle compact` row mean, including that
  PitBoard shows it as expected.
- The documentation states that the weekly bar has no projection by design, that PitBoard works
  without PaceLine, that weekday names use `LC_ALL`, `LC_TIME` and `LANG` before `pwsh` on every OS,
  and what the Desktop survey does before and since Claude Desktop 2.37337.1. The formula names in
  Try the formulas stand out, and the Colours section describes the model, effort and advisor styles.
- The documentation and this changelog write the plugin names PitBoard, PaceLine and dbWise in prose, and
  code-format commands, skill names and the `pitboard` button.

## 0.1.9 - 2026-10-15

### Changed

- The five documents (how-to, reference, explanation, troubleshooting and limitations) are pages in
  `docs/` beside the getting-started page and the row anatomy page, readable from a local install
  without the network. `docs/*.md` is generated from them.

## 0.1.8 - 2026-10-13

### Changed

- The row and the pane show only the signed-in account's limits. When that account's readings are not
  known, the five-hour, Week and model-scoped cards show an `Account unknown` row and mark their
  figures.
- Burn-rate samples are kept per account. A change of account starts the new account's samples, and
  a figure of an unknown account shows no burn rate.

### Fixed

- In the CLI row, a `·` separates ⓘ from the `pitboard` button.
- A failed read keeps the last samples. Overlapping refreshes run one after another, and a refresh
  that finishes after a newer one is dropped.

## 0.1.7 - 2026-10-12

### Fixed

- In a new session `TTL` shows the cache's real TTL a few seconds after the first reply that writes
  it, and the countdown runs from it, instead of `5m?` for the whole session.
- The Context card shows a new compaction point as soon as `/autocompact`, `/model` or `/config`
  finishes, instead of after the turn ends.

### Changed

- `If cold` and the cache advice give the cold-turn ratio for the session's model, such as `~40×` on
  Opus 5.5; `≥` marks a model whose cache-read price PitBoard does not know.

## 0.1.6 - 2026-10-12

### Added

- A `pitboard` button opens the `/pitboard` pane: beside ⓘ in the CLI row, and on Desktop in the
  footer under the input, between the mode and the model. A click opens it at any terminal width.

### Changed

- The pane is titled `pitboard`, and `/pitboard` replies `pitboard pane opened.`
- While the pane is open, the Desktop row shows no hover cards: the pane already holds every card.
- The docs say where the row sits on each surface and why: in the CLI under Claude Code's own mode
  and hint line; on Desktop above the input, since Desktop offers a plugin no site under it.

## 0.1.5 - 2026-10-12

### Added

- `docs/limitations.md` lists what PitBoard cannot do, grouped by where you meet it, each entry with
  what you see and what still works; the row anatomy page links it.

### Changed

- The row anatomy page no longer repeats the `/pitboard` pane, which the getting-started page shows
  in step 3.

### Fixed

- The docs no longer say that only the CLI runs processes for a plugin. Desktop runs them too: the
  readings that need `git`, `pwsh` or `node`, such as the branch, show in Desktop's card as in the
  CLI.

## 0.1.4 - 2026-10-12

### Added

- The row anatomy page has a section on the `/pitboard` pane: for the moment chosen in the working
  day, the pane as the CLI and Desktop draw it, side by side on a wide screen. The getting-started
  page has a step that opens it. Both are drawn from PitBoard's own `drawPane`, and the docs check
  fails when the drawn pane no longer matches the code.

## 0.1.3 - 2026-10-12

- The terminal row, its detail line and the `/pitboard` pane draw in Claude Code's theme colours, so
  they read on a light theme as on a dark one: values in the theme's text colour, names and rules in
  its grey, watch, act, a cold cache and auto-compact in its warning, error, teal and success colours.
  Model and effort names take theme colours by hue. Desktop's row and cards do not change.
- The documentation pages draw the terminal in the same theme colours, light or dark with the page.

## 0.1.2 - 2026-10-12

### Added

- Documentation in `docs/`: a getting-started page and a row anatomy page that draws the row with
  PitBoard's own code, also on GitHub Pages, and how-to, reference, explanation and troubleshooting
  guides.

### Changed

- The README is a short entry point; its details moved into `docs/`.

### Removed

- `docs/anatomy.html`, replaced by `docs/row-anatomy.html`.

## 0.1.1 - 2026-10-12

- The CLI row shows the advisor between the context and the cache while one is set, as Desktop does:
  its model in the model's colour, dimmed with `(inactive)` when it ranks below the main model. A
  click opens its line (Model, Main, State). With no advisor set the row has no advisor group, and
  `ⓘ` still says `off`.

## 0.1.0 - 2026-10-11

First release.

- A status row drawn through function hooks, in the CLI above the prompt's hint line and in Claude
  Code Desktop above the prompt: context against the auto-compaction point, the prompt cache's time
  to live and hit rate, the five-hour and weekly limits with their pace, and on a click or hover the
  model, effort, advisor, branch, session time and API cost.
- `/pitboard` opens every reading in a pane, on every surface, the mobile app included.
- Limits come from the session itself; PaceLine's limits file, when it exists, adds the readings
  other sessions saw, the pace history and any model-scoped limits a writer adds. PitBoard writes no
  file.
- `docs/anatomy.html`: every reading, colour and threshold on one page.
