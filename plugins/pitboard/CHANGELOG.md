# Changelog

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
