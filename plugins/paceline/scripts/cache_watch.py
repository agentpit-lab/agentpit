#!/usr/bin/env python3
"""cache-watch: keep an idle session's one-hour prompt cache alive while the operator is away.

The operator turns watching on for one session (`on`). After every turn of a watched session the
`Stop` hook waits in the background until a few minutes before the cache would expire, then exits 2,
which wakes the session (an `asyncRewake` hook). The woken turn is itself a request, so it keeps the
cache for another hour; it sends one short line as a push, and ends. A prompt from the operator
resets the count of wakes; after `max` wakes in a row without one, watching stops. A compaction
turns watching off: the waiter's cache anchor predates the compacted context, so a wake would keep
nothing. A waiter that finds the cache already expired (the computer slept past the wake) turns
watching off too, and the next prompt from the operator carries a one-time notice saying so.

CLI (through bin/paceline):
    paceline cache-watch on --session ID --data-dir DIR [--lead MIN] [--max N]
    paceline cache-watch off --session ID --data-dir DIR
    paceline cache-watch status --data-dir DIR
    paceline cache-watch config --data-dir DIR [--lead MIN] [--max N]
Hooks (through hooks/hooks.json; the data dir comes from CLAUDE_PLUGIN_DATA):
    cache_watch.py hook stop|prompt|compact|session-end

Stdlib only, Python 3.9+. A hook never fails the session: any error exits 0, and a cache state it
cannot read never wakes it.
"""
import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

HOUR_S = 3600
# Count the cache as gone a little early, so a wake never lands just after it expired.
MARGIN_S = 5
# Claude Code's idle compaction fires about 54 minutes after the last request; 90 s of margin keeps the wake ahead of it.
LATEST_WAKE_S = 3150
# The transcript can trail the Stop hook by a moment; the turn's last request must be in it.
SETTLE_S = 3
STEP_S = 60
STALE_S = 24 * HOUR_S
DEFAULTS = {"lead_minutes": 5, "max_wakes": 5}
LEAD_RANGE = (1, 30)
MAX_RANGE = (1, 20)
WAKE_MARK = "cache-watch woke this session"

# Replaced by tests.
now = time.time
sleep = time.sleep


def state_dir(data_dir):
    return Path(data_dir) / "cache-watch"


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_defaults(data_dir):
    stored = _read_json(state_dir(data_dir) / "defaults.json") or {}
    return {k: stored.get(k, v) for k, v in DEFAULTS.items()}


def state_path(data_dir, session):
    return state_dir(data_dir) / f"{session}.json"


def load_state(data_dir, session):
    return _read_json(state_path(data_dir, session))


def save_state(data_dir, session, state):
    _write_json(state_path(data_dir, session), state)


def _parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class TranscriptReader:
    """Folds a transcript into the regime ('1h', '5m' or None) and anchor (epoch seconds) of the last
    main-thread cache write, and that request's context size, reading only what was appended since.

    Subagent rows are skipped, a message id counts once, and the TTL runs from the row that triggered
    the request (the prompt or a tool result), the nearest recorded moment before the request started.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.reset()

    def reset(self):
        self.offset = 0
        self.regime = None
        self.anchor = None
        self.trigger = None
        self.context_tokens = None
        self.seen = set()

    def update(self):
        """The fold so far, or None when the file can't be read."""
        try:
            if self.path.stat().st_size < self.offset:
                self.reset()
            with self.path.open("rb") as f:
                f.seek(self.offset)
                for raw in f:
                    # Claude Code may be mid-append: a line without its newline waits for the next step.
                    if not raw.endswith(b"\n"):
                        break
                    self.offset += len(raw)
                    self._fold(raw.decode("utf-8", errors="replace"))
        except OSError:
            return None
        return {"regime": self.regime, "anchor": self.anchor, "context_tokens": self.context_tokens}

    def _fold(self, line):
        if '"timestamp"' not in line and '"usage"' not in line:
            return
        try:
            row = json.loads(line)
        except ValueError:
            return
        if not isinstance(row, dict) or row.get("isSidechain"):
            return
        at = _parse_ts(row.get("timestamp"))
        message = row.get("message") if isinstance(row.get("message"), dict) else {}
        usage = message.get("usage")
        if row.get("type") != "assistant" or not isinstance(usage, dict):
            if row.get("type") == "user" and at is not None:
                self.trigger = at
            return
        msg_id = message.get("id")
        if msg_id:
            if msg_id in self.seen:
                return
            self.seen.add(msg_id)
        self.anchor = self.trigger if self.trigger is not None else (at if at is not None else self.anchor)
        sizes = [usage.get(k) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")]
        sizes = [v for v in sizes if isinstance(v, int)]
        self.context_tokens = sum(sizes) if sizes else None
        creation = usage.get("cache_creation") or {}
        if (creation.get("ephemeral_1h_input_tokens") or 0) > 0:
            self.regime = "1h"
        elif (creation.get("ephemeral_5m_input_tokens") or 0) > 0:
            self.regime = "5m"


def read_transcript(path):
    """The whole transcript's fold (see TranscriptReader), or None when it can't be read."""
    return TranscriptReader(path).update()


def deadline_of(info):
    return info["anchor"] + HOUR_S - MARGIN_S


def wake_at(info, lead):
    return min(deadline_of(info) - lead * 60, info["anchor"] + LATEST_WAKE_S)


def find_transcript(session):
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    projects = config / "projects"
    try:
        for project in projects.iterdir():
            candidate = project / f"{session}.jsonl"
            if candidate.is_file():
                return candidate
    except OSError:
        pass
    return None


def _clock(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def wake_text(state, session, data_dir, expires):
    data = Path(data_dir).as_posix()
    line = f"Cache kept until {_clock(expires)} · wake {state['wakes']} of {state['max']}"
    line += " · last wake, keeping stops now" if state["wakes"] >= state["max"] else " · reply stop to stop keeping it"
    return (
        f"paceline {WAKE_MARK} to keep its prompt cache. This turn already kept it. Do only this:\n"
        f"1. Send this one line, in the operator's language, as a push with the PushNotification tool: \"{line}\". "
        "If that tool is deferred, load it with ToolSearch first.\n"
        "2. End the turn without replying: the push is the line's only copy, even when its result says "
        "not sent. Run no other tool.\n"
        "Only if the PushNotification tool does not exist in this session, reply with the line instead "
        "and end the turn.\n"
        "If the operator later replies stop, run this and confirm in one line: "
        f"paceline cache-watch off --session {session} --data-dir \"{data}\" < /dev/null"
    )


EXPIRED_TEXT = (
    "paceline cache-watch: this session's cache expired at {clock}, before its wake (the computer was probably "
    "asleep), so this turn writes the whole context to the cache again, and cache-watch is now off for this "
    "session. After answering the operator's message, tell them that in one line, in their language; "
    "/paceline:cache-watch on watches the session again."
)

FIVE_MIN_TEXT = (
    "paceline cache-watch stopped: this session now writes five-minute cache entries, and "
    "cache-watch keeps only one-hour entries. Tell the operator that in one line and end the turn."
)


def _hook_payload():
    try:
        return json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return {}


def hook_stop(data_dir, payload):
    session = payload.get("session_id")
    if not session:
        return 0
    state = load_state(data_dir, session)
    if not state or not state.get("on"):
        return 0
    generation = uuid.uuid4().hex
    state["generation"] = generation
    if payload.get("transcript_path"):
        state["transcript"] = payload["transcript_path"]
    save_state(data_dir, session, state)
    sleep(SETTLE_S)
    reader = TranscriptReader(state["transcript"])
    while True:
        info = reader.update()
        if not info or info["regime"] is None or info["anchor"] is None:
            return 0
        if info["regime"] == "5m":
            state.update(on=False, stopped_reason="ttl-5m")
            save_state(data_dir, session, state)
            sys.stderr.write(FIVE_MIN_TEXT + "\n")
            return 2
        deadline = deadline_of(info)
        fire = wake_at(info, state["lead"])
        t = now()
        if t >= deadline:
            # A wake now would write the whole context anyway: stop watching, and let the next prompt say so.
            state = load_state(data_dir, session)
            if state and state.get("on") and state.get("generation") == generation:
                state.update(on=False, stopped_reason="expired", expired_at=deadline, notice=True,
                             generation=uuid.uuid4().hex)
                save_state(data_dir, session, state)
            return 0
        if t < fire:
            sleep(min(STEP_S, fire - t))
            state = load_state(data_dir, session)
            if not state or not state.get("on") or state.get("generation") != generation:
                return 0
            continue
        state["wakes"] = state.get("wakes", 0) + 1
        state["last_wake_at"] = t
        if state["wakes"] >= state["max"]:
            state.update(on=False, stopped_reason="no-reply")
        save_state(data_dir, session, state)
        sys.stderr.write(wake_text(state, session, data_dir, t + HOUR_S) + "\n")
        return 2


def hook_prompt(data_dir, payload):
    session = payload.get("session_id")
    state = load_state(data_dir, session) if session else None
    if not state:
        return 0
    if WAKE_MARK in str(payload.get("prompt") or ""):
        return 0
    if state.pop("notice", None):
        print(EXPIRED_TEXT.format(clock=_clock(state.get("expired_at") or now())))
    state.update(wakes=0, generation=uuid.uuid4().hex)
    save_state(data_dir, session, state)
    return 0


def hook_compact(data_dir, payload):
    session = payload.get("session_id")
    state = load_state(data_dir, session) if session else None
    if not state or not state.get("on"):
        return 0
    state.update(on=False, stopped_reason="compact", generation=uuid.uuid4().hex)
    save_state(data_dir, session, state)
    return 0


def hook_session_end(data_dir, payload):
    session = payload.get("session_id")
    if session:
        try:
            state_path(data_dir, session).unlink()
        except OSError:
            pass
    return 0


HOOKS = {"stop": hook_stop, "prompt": hook_prompt, "compact": hook_compact, "session-end": hook_session_end}


def run_hook(name):
    try:
        data_dir = os.environ.get("CLAUDE_PLUGIN_DATA")
        if not data_dir or name not in HOOKS:
            return 0
        return HOOKS[name](data_dir, _hook_payload())
    except Exception:  # noqa: BLE001 - a hook must never fail the session
        return 0


def _in_range(name, value, bounds, unit=""):
    lo, hi = bounds
    if value is not None and not lo <= value <= hi:
        raise SystemExit(f"paceline cache-watch: {name} must be {lo}-{hi}{unit}")


def _describe(session, state):
    if state.get("on"):
        head = "on"
    else:
        reasons = {
            "operator": "operator",
            "ttl-5m": "five-minute cache",
            "no-reply": f"no reply after {state.get('max')} wakes",
            "compact": "compacted",
            "expired": "cache expired before the wake",
        }
        head = f"off ({reasons.get(state.get('stopped_reason'), state.get('stopped_reason') or 'unknown')})"
    parts = [session, head, f"lead {state.get('lead')} min", f"wakes {state.get('wakes', 0)}/{state.get('max')}"]
    info = read_transcript(state.get("transcript") or "")
    if info and info["anchor"] is not None:
        parts.append(f"cache {info['regime'] or 'none'}")
        deadline = deadline_of(info)
        if deadline > now():
            parts.append(f"expires {_clock(deadline)}")
            if state.get("on"):
                parts.append(f"next wake {_clock(wake_at(info, state['lead']))}")
        else:
            parts.append("expired")
    return " · ".join(parts)


# A one-hour cache write is 2x the base input price and a cache read 0.1x or less (Anthropic's
# prompt-caching prices), so a rewrite after the expiry costs at least 20 reads.
COST_RATIO = ("which costs at least 20 times the read (Anthropic API prices: one-hour cache write 2x the input "
              "price, cache read 0.1x or less).")


def kilo(n):
    """A token count as pitboard's cards print it: 900, 506k, 1.2M."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    if n >= 1000:
        return f"{int(n / 1000 + 0.5)}k"
    return str(n)


def cmd_on(args):
    _in_range("lead", args.lead, LEAD_RANGE, " minutes")
    _in_range("max", args.max, MAX_RANGE)
    transcript = find_transcript(args.session)
    if not transcript:
        print(f"not started: no transcript found for session {args.session}")
        return 3
    info = read_transcript(transcript)
    if not info or info["regime"] is None:
        print("not started: this session has no one-hour cache entry yet")
        return 3
    if info["regime"] == "5m":
        print("not started: this session writes five-minute cache entries; cache-watch keeps only one-hour entries")
        return 3
    defaults = load_defaults(args.data_dir)
    state = {
        "on": True,
        "lead": args.lead if args.lead is not None else defaults["lead_minutes"],
        "max": args.max if args.max is not None else defaults["max_wakes"],
        "wakes": 0,
        "generation": uuid.uuid4().hex,
        "transcript": str(transcript),
        "last_wake_at": None,
        "stopped_reason": None,
    }
    save_state(args.data_dir, args.session, state)
    print(_describe(args.session, state))
    tokens = info.get("context_tokens")
    if tokens is not None:
        print(f"A wake reads the ~{kilo(tokens)}-token context from the cache and keeps it another hour. Once the "
              f"cache has expired, the next turn writes those ~{kilo(tokens)} tokens to the cache again, {COST_RATIO}")
    else:
        print("A wake reads the context from the cache and keeps it another hour. Once the cache has expired, the "
              f"next turn writes the whole context to the cache again, {COST_RATIO}")
    print("Each wake still counts against the five-hour limit; how much a cache read weighs in subscription limits "
          "is not published.")
    return 0


def cmd_off(args):
    state = load_state(args.data_dir, args.session)
    if not state:
        print(f"{args.session} is not watched")
        return 0
    state.update(on=False, stopped_reason="operator", generation=uuid.uuid4().hex)
    save_state(args.data_dir, args.session, state)
    print(_describe(args.session, state))
    return 0


def cmd_status(args):
    folder = state_dir(args.data_dir)
    rows = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        if path.name == "defaults.json":
            continue
        state = _read_json(path)
        transcript = Path(state.get("transcript") or "") if state else None
        try:
            fresh = transcript is not None and now() - transcript.stat().st_mtime < STALE_S
        except OSError:
            fresh = False
        if not fresh:
            try:
                path.unlink()
            except OSError:
                pass
            continue
        rows.append(_describe(path.stem, state))
    print("\n".join(rows) if rows else "no session is watched")
    print("A wake keeps the cache for far less than writing the whole context again after it expired; each wake "
          "still counts against the five-hour limit.")
    return 0


def cmd_config(args):
    _in_range("lead", args.lead, LEAD_RANGE, " minutes")
    _in_range("max", args.max, MAX_RANGE)
    values = load_defaults(args.data_dir)
    if args.lead is not None:
        values["lead_minutes"] = args.lead
    if args.max is not None:
        values["max_wakes"] = args.max
    if args.lead is not None or args.max is not None:
        _write_json(state_dir(args.data_dir) / "defaults.json", values)
    print(f"defaults: lead {values['lead_minutes']} min · max {values['max_wakes']} wakes without a reply")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["hook"]:
        return run_hook(argv[1] if len(argv) > 1 else "")
    parser = argparse.ArgumentParser(prog="paceline cache-watch", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("on", "off", "status", "config"):
        p = sub.add_parser(name)
        p.add_argument("--data-dir", required=True)
        if name in ("on", "off"):
            p.add_argument("--session", required=True)
        if name in ("on", "config"):
            p.add_argument("--lead", type=int)
            p.add_argument("--max", type=int)
    args = parser.parse_args(argv)
    return {"on": cmd_on, "off": cmd_off, "status": cmd_status, "config": cmd_config}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
