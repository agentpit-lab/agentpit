# Changelog

## 0.1.0 - 2026-11-01

### Added

- context-watch and cache-watch as a plugin of their own, moved from PaceLine 0.6.0 with their
  behaviour unchanged: warnings before auto-compaction, snapshots at each compaction, the optional
  per-prompt context line, and a session's prompt cache held through wakes while the operator is away.
- `pitcrew context-watch` and `pitcrew cache-watch` commands, and the `/pitcrew:context-watch` and
  `/pitcrew:cache-watch` skills.
- Settings in `state/pitcrew`, read from PaceLine's `state/paceline` files until PitCrew writes its
  own; snapshots in `.claude/pitcrew`, and old snapshots in PaceLine's `.claude/paceline` are still
  removed at session start.
