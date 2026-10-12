# Changelog

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
