#!/usr/bin/env python3
"""stint: a queue of unattended work, and the answer to "may the next item start?".

    stint add|list|report|start|done|fail|defer|retry|remove|clear-done ...   the queue
    stint next [--start-run]      start (exit 0), wait (10) or stop (11) for the next item
    stint config                  the stops Stint applies without PaceLine

bin/stint and bin/stint.cmd run this script. Stdlib only, Python 3.9+.
"""
import argparse
import contextlib
import datetime as dt
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import paceline_link as link  # noqa: E402
import stint_queue as q  # noqa: E402
import state  # noqa: E402

EXIT_OK, EXIT_WAIT, EXIT_STOP, EXIT_USAGE = 0, 10, 11, 2


class StintParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"stint: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def estimate_arg(text):
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number")
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError("must be a number of at least 0")
    return value


def build_parser():
    parser = StintParser(prog="stint", description="A queue of unattended work bounded by PaceLine's advice "
                                                    "or by Stint's own limit stops.")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=StintParser)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="print the payload as JSON")

    def command(name, help_text):
        return sub.add_parser(name, parents=[common], help=help_text, description=help_text)

    add = command("add", "Queue an item.")
    add.add_argument("--title", required=True, help="what the item does")
    add.add_argument("--repo", required=True, help="the repository it works in")
    add.add_argument("--estimate", required=True, type=estimate_arg, help="expected spend, percent of the week")
    add.add_argument("--independent", action="store_true", help="the item may run beside another independent item")
    add.add_argument("--depends", help="ids of items that must be done first, comma separated")
    command("list", "List the items.")
    command("report", "Count the items finished since the run started, and those running or pending.")
    start = command("start", "Start a pending item (takes a PaceLine hold when PaceLine is found).")
    start.add_argument("id")
    start.add_argument("--pid", type=int, help="the process the hold belongs to")
    done = command("done", "Finish an item (releases its hold).")
    done.add_argument("id")
    done.add_argument("--actual", type=estimate_arg, help="actual spend, percent of the week")
    fail = command("fail", "Count a failed attempt; the third defers the item and its dependents.")
    fail.add_argument("id")
    fail.add_argument("--reason", help="why the checks failed")
    defer = command("defer", "Defer an item and every item that depends on it.")
    defer.add_argument("id")
    defer.add_argument("--reason", help="why it is deferred")
    retry = command("retry", "Put an item back to pending.")
    retry.add_argument("id")
    remove = command("remove", "Remove an item.")
    remove.add_argument("id")
    command("clear-done", "Remove the done items.")
    config = command("config", "Show the settings, or change them.")
    config.add_argument("--week-stop", dest="week_stop", help="week stop, percent (50-100, default 85)")
    config.add_argument("--five-hour-stop", dest="five_hour_stop", help="five-hour stop, percent (50-100, default 95)")
    config.add_argument("--agents", help="parallel agents (1-6, default 3)")
    nxt = command("next", "Answer start, wait or stop for the next item (exit 0, 10 or 11).")
    nxt.add_argument("--start-run", action="store_true", help="start a run unless one is open")
    return parser


# ---------------------------------------------------------------- shared helpers

def emit(args, line, payload):
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    else:
        print(line)


def warn(text):
    print(f"stint: {text}", file=sys.stderr)


def get_now():
    text = os.environ.get("STINT_NOW")
    if not text:
        return dt.datetime.now(q.UTC)
    parsed = q.parse_iso(text)
    if parsed is None:
        raise q.QueueError(f"STINT_NOW is not an ISO 8601 instant: {text}")
    return parsed


def settings_now():
    return q.read_settings(state.read_json(state.state_dir() / "settings.json"))


def reading_now(now):
    return q.read_limits(state.read_json(state.state_dir() / "limits.json"), now)


def week_reading(now, status=None):
    """The week's used percentage: PaceLine's effective figure, else Stint's own fresh reading, else None."""
    if link.command() is not None:
        pct = link.week_pct(status if status is not None else link.status())
        if pct is not None:
            return pct
    reading = reading_now(now)
    return reading["week_pct"] if reading and not reading["stale"] else None


def release(item, actual=None):
    """Release the item's hold in PaceLine; a failure is reported and never fails the command. A hold that
    could not be released stays on the item, so a later done or defer tries again."""
    hold_id = item.get("hold_id")
    if hold_id and link.command() is not None:
        why = link.hold_release(hold_id, actual)
        if why:
            warn(f"PaceLine hold not released: {why}")
            return
    item["hold_id"] = None


def tonight_hint(status):
    """(the hint line or None, tonight's off-hours share or None)."""
    night = link.night_of(status)
    if not night or night.get("mark") is None:
        return None, None
    share = night.get("share_pct")
    if night.get("weight") != 0:
        return None, share
    plan_cmd = f"paceline plan set {night.get('date')} --off-hours 0.5"
    if not night.get("current"):
        return f"tonight's off-hours share is 0: the queue will not run. To plan some: {plan_cmd}", share
    return (f"tonight's off-hours share is 0 now: the queue will not run. To plan the rest of tonight: "
            f"{plan_cmd}"), share


# ---------------------------------------------------------------- queue commands

def cmd_add(a):
    now = get_now()
    depends = [d.strip() for d in (a.depends or "").split(",") if d.strip()]
    hint, share = None, None
    if link.command() is not None:
        hint, share = tonight_hint(link.status())
    with state.locked(state.state_dir()):
        store = q.Store()
        item = q.add_item(store.data, a.title, a.repo, a.estimate, a.independent, depends, now)
        store.save()
    pending = q.pending_total(store.data["items"])
    line = f"queue {item['id']} added ({a.estimate:.2f}%), pending total {pending:.2f}%"
    emit(a, line + (f"\n{hint}" if hint else ""),
         {"item": item, "pending_total_pct": pending, "tonight_off_share_pct": share, "tonight_off_hint": hint})
    return EXIT_OK


def cmd_list(a):
    items = q.Store().data["items"]
    text = "\n".join(f"{i['id']} [{i['status']}] {i['estimate_pct']:.2f}% {'indep ' if i['independent'] else ''}"
                     f"deps={','.join(i['depends_on']) or '-'} {i['title']} ({i['repo']})"
                     + (f" reason: {i['reason']}" if i.get("reason") else "") for i in items) or "queue empty"
    emit(a, text, {"items": items})
    return EXIT_OK


def cmd_report(a):
    data = q.Store().data
    run = data["run"]
    since = q.parse_iso(run.get("started_at")) if run else None
    touched = []
    for i in data["items"]:
        finished = q.parse_iso(i.get("finished_at"))
        if since is None or (finished is not None and finished >= since) or i["status"] in ("running", "pending"):
            touched.append(i)
    report = {"run": run}
    for status in ("done", "deferred", "running", "pending"):
        report[status] = [q.item_view(i) for i in touched if i["status"] == status]
    report["estimate_sum_pct"] = round(sum(i["estimate_pct"] for i in report["done"]), 3)
    report["actual_sum_pct"] = round(sum(i["actual_pct"] or 0 for i in report["done"]), 3)
    text = (f"done {len(report['done'])} (est {report['estimate_sum_pct']:.2f}% act {report['actual_sum_pct']:.2f}%),"
            f" deferred {len(report['deferred'])}, running {len(report['running'])}, pending {len(report['pending'])}")
    emit(a, text, report)
    return EXIT_OK


def cmd_start(a):
    now = get_now()
    week = week_reading(now)
    with state.locked(state.state_dir()):
        store = q.Store()
        item = q.find_item(store.data["items"], a.id)
        if item["status"] != "pending":
            raise q.QueueError(f"{item['id']} is {item['status']}, not pending")
        hold_id = None
        if link.command() is not None:
            hold_id, why = link.hold_acquire(item["estimate_pct"], item["title"], item["id"], a.pid)
            if why:
                warn(f"PaceLine hold not taken: {why}")
        item.update(status="running", started_at=q.iso_ms(now), hold_id=hold_id, used_at_start=week)
        store.save()
    emit(a, f"{item['id']} started" + (f", hold {hold_id}" if hold_id else ""), {"item": item})
    return EXIT_OK


def cmd_done(a):
    now = get_now()
    week = week_reading(now)
    with state.locked(state.state_dir()):
        store = q.Store()
        item = q.find_item(store.data["items"], a.id)
        actual = a.actual
        if actual is None and week is not None and q.number(item.get("used_at_start")) is not None:
            actual = max(0.0, week - item["used_at_start"])
        release(item, actual)
        item.update(status="done", finished_at=q.iso_ms(now), reason=None,
                    actual_pct=None if actual is None else round(actual, 3))
        store.save()
    shown = "?" if actual is None else f"{actual:.2f}"
    emit(a, f"{item['id']} done, estimate {item['estimate_pct']:.2f}% actual {shown}%", {"item": item})
    return EXIT_OK


def defer_item(a, store, item, reason, now):
    release(item)
    deferred = q.defer_with_dependents(store.data["items"], item["id"], reason, now)
    for other in deferred:
        if other is not item:
            release(other)
    store.save()
    emit(a, f"deferred {','.join(d['id'] for d in deferred)}: {reason}",
         {"deferred": [q.item_view(d) for d in deferred]})
    return EXIT_OK


def cmd_fail(a):
    now = get_now()
    with state.locked(state.state_dir()):
        store = q.Store()
        item = q.find_item(store.data["items"], a.id)
        item["attempts"] = item.get("attempts", 0) + 1
        if item["attempts"] < q.GATE_FIX_ATTEMPTS:
            store.save()
            emit(a, f"{item['id']} failed attempt {item['attempts']}/{q.GATE_FIX_ATTEMPTS}, keep fixing",
                 {"item": item, "deferred": []})
            return EXIT_OK
        return defer_item(a, store, item, a.reason or f"gates failed after {q.GATE_FIX_ATTEMPTS} fix attempts", now)


def cmd_defer(a):
    now = get_now()
    with state.locked(state.state_dir()):
        store = q.Store()
        item = q.find_item(store.data["items"], a.id)
        if item["status"] not in ("pending", "running"):
            raise q.QueueError(f"{item['id']} is {item['status']}, not pending or running")
        return defer_item(a, store, item, a.reason or "needs owner permission", now)


def cmd_retry(a):
    with state.locked(state.state_dir()):
        store = q.Store()
        item = q.find_item(store.data["items"], a.id)
        item.update(status="pending", reason=None, attempts=0, finished_at=None)
        store.save()
    emit(a, f"{item['id']} back to pending", {"item": item})
    return EXIT_OK


def cmd_remove(a):
    with state.locked(state.state_dir()):
        store = q.Store()
        items = store.data["items"]
        item = q.find_item(items, a.id)
        dependents = [i["id"] for i in items if item["id"] in i.get("depends_on", [])
                      and i["status"] in ("pending", "running")]
        if dependents:
            raise q.QueueError(f"{item['id']} has dependents {','.join(dependents)}; defer or remove them first")
        items.remove(item)
        store.save()
    emit(a, f"{item['id']} removed", {"removed": item})
    return EXIT_OK


def cmd_clear_done(a):
    with state.locked(state.state_dir()):
        store = q.Store()
        before = len(store.data["items"])
        store.data["items"] = [i for i in store.data["items"] if i["status"] != "done"]
        store.save()
    emit(a, f"cleared {before - len(store.data['items'])} done item(s)", {})
    return EXIT_OK


# ---------------------------------------------------------------- config

def parse_setting(name, text):
    low, high = q.RANGES[name]
    try:
        value = float(text)
    except (TypeError, ValueError):
        value = None
    if value is None or not math.isfinite(value) or not value.is_integer() or not low <= value <= high:
        raise q.QueueError(f"{name} must be {low}-{high}")
    return int(value)


def cmd_config(a):
    path = state.state_dir() / "settings.json"
    settings = q.read_settings(state.read_json(path))
    changes = {}
    for name, text in (("week_stop_pct", a.week_stop), ("five_hour_stop_pct", a.five_hour_stop), ("agents", a.agents)):
        if text is not None:
            changes[name] = parse_setting(name, text)
    if changes:
        settings.update(changes)
        state.write_json(path, settings)
    reading = reading_now(get_now())
    command = link.describe()
    age = reading["age_min"] if reading else None
    lines = [f"{name}: {settings[name]} (default {default})" for name, default in q.DEFAULTS.items()]
    lines.append(f"PaceLine: found, {command}" if command else "PaceLine: not found")
    lines.append(f"usage reading: {age} min old" if age is not None else "usage reading: none")
    emit(a, "\n".join(lines), {"settings": settings, "defaults": dict(q.DEFAULTS), "paceline": command,
                               "reading_age_min": age})
    return EXIT_OK


# ---------------------------------------------------------------- next

def cmd_next(a):
    now = get_now()
    settings = settings_now()
    reading = reading_now(now)
    advice, note, status = None, None, None
    if link.command() is not None:
        advice, why = link.advice()
        if advice is None:
            note = f"PaceLine's advice unavailable ({why}): Stint's own stops apply"
        if not (why or "").startswith("timed out"):
            status = link.status()
    week_now = link.week_pct(status) if status else None
    if week_now is None and reading and not reading["stale"]:
        week_now = reading["week_pct"]
    with state.locked(state.state_dir()):
        store = q.Store()
        data = store.data
        reused = False
        if a.start_run:
            account = advice.get("account") if advice else None
            if q.run_is_open(data["run"], now, account):
                reused = True
            else:
                allowance = round((q.number(advice.get("budget_pct")) or 0.0) if advice
                                  else q.own_budget(reading, settings), 3)
                night = link.night_of(status)
                end = night.get("end") if night and isinstance(night.get("end"), str) else None
                q.start_run(data, now, end, account, "paceline" if advice else "stint",
                            allowance, week_now)
                store.save()
        result = q.decide(now, data, settings, advice, note, reading, week_now, reused)
        run = data["run"]
        if result["action"] == "stop" and run and not run.get("ended_at"):
            # The run closes at its first stop: a later night starts a new one.
            run["ended_at"] = q.iso_ms(now)
            result["run"]["ended_at"] = run["ended_at"]
            store.save()
    emit(a, q.next_line(result), result)
    return {"start": EXIT_OK, "wait": EXIT_WAIT, "stop": EXIT_STOP}[result["action"]]


COMMANDS = {"add": cmd_add, "list": cmd_list, "report": cmd_report, "start": cmd_start, "done": cmd_done,
            "fail": cmd_fail, "defer": cmd_defer, "retry": cmd_retry, "remove": cmd_remove,
            "clear-done": cmd_clear_done, "config": cmd_config, "next": cmd_next}


def main(argv=None):
    # Every command prints the same text on a Windows console as elsewhere, whatever its code page.
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return COMMANDS[args.command](args)
    except (q.QueueError, state.StateError) as error:
        warn(str(error))
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
