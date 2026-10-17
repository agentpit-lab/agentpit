# PitBoard

PitBoard is a status row for Claude Code, drawn by the plugin itself through function hooks. It
shows the context against the real compaction point, the prompt cache's time to live and hit rate,
and the five-hour and weekly usage limits with their pace against an even budget, plus model-scoped
limits when PaceLine's usage-watch records them. The model, effort, advisor, branch, session time,
and API cost are a click or hover away. It works in the CLI and in Claude Code Desktop, on
Windows, macOS, and Linux.

## Install

```
/plugin install pitboard@agentpit
```

Requirements:

- Claude Code 2.1.287 or later.
- Optional, on the `PATH` of the environment Claude Code starts in: `git` for the branch, `pwsh`
  (PowerShell 7) for weekday names in the OS locale when none of `LC_ALL`, `LC_TIME`, `LANG` gives
  one, on any OS, and `node` for a session transcript over 4 MiB.
- Optional: [PaceLine](../paceline/README.md), for readings other sessions saw and, with its
  usage-watch, the model-scoped limits. With PaceLine, the limits are the signed-in account's, also
  after a `/login` to another account; see
  [Use two accounts on one machine](docs/how-to.md#use-two-accounts-on-one-machine).

## First run

Start a new session; the row loads at session start. In the CLI it sits under the prompt, below
Claude Code's own mode and hint line. In Desktop it sits above the prompt, and pointing at a reading shows its card. In both,
`/pitboard`, or a click on `pitboard`, opens a pane with every card and its explanations, and another
click on `pitboard` closes it: the button ends the row in the CLI and sits in the footer under the prompt in Desktop. In the CLI and the pane
the row draws in Claude Code's theme colours, so it reads on a light theme as on a dark one; Desktop
keeps its own fixed colours. PitBoard writes no file and has no settings.

## Contents

- [Getting started](docs/getting-started.html): the row's groups and five terms, in a browser
  ([online](https://agentpit-lab.github.io/agentpit/plugins/pitboard/docs/getting-started.html)).
- [Row anatomy](docs/row-anatomy.html): every reading, colour, and threshold, in common situations
  and over time ([online](https://agentpit-lab.github.io/agentpit/plugins/pitboard/docs/row-anatomy.html)).
- [How-to guides](docs/how-to.md): use PitBoard with PaceLine, move the state directory, use two
  accounts on one machine, read the row in Desktop and in a narrow terminal, open every card with `/pitboard`.
  Online page:
  [how-to guides](https://agentpit-lab.github.io/agentpit/plugins/pitboard/docs/how-to.html).
- [How PitBoard works](docs/explanation.md): where the numbers come from, the formulas, why the row
  can differ from `/usage`, colours.
  Online page:
  [explanation](https://agentpit-lab.github.io/agentpit/plugins/pitboard/docs/explanation.html).
- [Reference](docs/reference.md): readings and card rows, levels, environment variables, files read,
  requirements.
  Online page:
  [reference](https://agentpit-lab.github.io/agentpit/plugins/pitboard/docs/reference.html).
- [Troubleshooting](docs/troubleshooting.md): problems by symptom, each with cause, check, and fix.
  Online page:
  [troubleshooting](https://agentpit-lab.github.io/agentpit/plugins/pitboard/docs/troubleshooting.html).
- [Limitations](docs/limitations.md): what the plugin cannot do, and what still works.
  Online page:
  [limitations](https://agentpit-lab.github.io/agentpit/plugins/pitboard/docs/limitations.html).
