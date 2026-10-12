# Changelog

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

- `docs/limitations.md` lists what pitboard cannot do, grouped by where you meet it, each entry with
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
  page has a step that opens it. Both are drawn from pitboard's own `drawPane`, and the docs check
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
  pitboard's own code, also on GitHub Pages, and how-to, reference, explanation and troubleshooting
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
- Limits come from the session itself; paceline's limits file, when it exists, adds the readings
  other sessions saw, the pace history and any model-scoped limits a writer adds. pitboard writes no
  file.
- `docs/anatomy.html`: every reading, colour and threshold on one page.
