"""The queue: its store, the item rules and the decision `stint next` makes.

The store reads and writes queue.json (reading PaceLine's night-queue.json until Stint writes its own).
Everything below the store is pure: the decision takes `now`, the queue, the advice or the readings and
the settings, and reads no file. Stdlib only, Python 3.9+.
"""
import contextlib
import datetime as dt
import math
import re
import sys

import state

UTC = dt.timezone.utc
GATE_FIX_ATTEMPTS = 3
PAST_RUNS_KEPT = 14
STALE_MIN = 30
NO_RUN = "no run: start one with stint next --start-run"
NO_READING = "no usage reading yet: Stint reads the limits Claude Code hands to a session"
# The advice's stop reasons that end a run before anything else is weighed (the others are the plan's).
SAFETY_STOPS = ("memory", "weekly", "weekly_scoped")

DEFAULTS = {"week_stop_pct": 85, "five_hour_stop_pct": 95, "agents": 3}
RANGES = {"week_stop_pct": (50, 100), "five_hour_stop_pct": (50, 100), "agents": (1, 6)}


class QueueError(Exception):
    """An input error: the message is printed after `stint: ` and the exit code is 2."""


# ---------------------------------------------------------------- time

_FRACTION = re.compile(r"\.(\d+)")


def parse_iso(value):
    """An ISO 8601 instant (any offset or Z; no offset reads as UTC) as an aware datetime, else None."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    # Python 3.9 reads only 3 or 6 fraction digits.
    text = _FRACTION.sub(lambda m: "." + (m.group(1) + "000000")[:6], text, count=1)
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def iso_ms(d):
    d = d.astimezone(UTC)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


def number(value):
    """value as a float when it is a real number, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


# ---------------------------------------------------------------- store

def empty_queue():
    return {"run": None, "past_runs": [], "next_id": 1, "items": []}


def normalized(raw):
    data = empty_queue()
    data.update({k: v for k, v in raw.items()})
    if not isinstance(data["items"], list):
        data["items"] = []
    if not isinstance(data["past_runs"], list):
        data["past_runs"] = []
    if not isinstance(data["run"], dict):
        data["run"] = None
    if not isinstance(data["next_id"], int) or isinstance(data["next_id"], bool):
        data["next_id"] = 1
    for item in data["items"]:
        if not isinstance(item, dict):
            raise ValueError("an entry of items is not an object")
        item.setdefault("depends_on", [])
    # The next id never falls behind an id in use.
    used = [int(m.group(1)) for m in (re.fullmatch(r"Q(\d+)", str(i.get("id"))) for i in data["items"]) if m]
    if used and data["next_id"] <= max(used):
        data["next_id"] = max(used) + 1
    return data


class Store:
    """queue.json of Stint's state directory; until it exists, PaceLine's night-queue.json is read."""

    def __init__(self):
        self.path = state.state_dir() / "queue.json"
        self.legacy = state.legacy_state_dir() / "night-queue.json"
        self.data = self._load(self.path)
        if self.data is None:
            try:
                self.data = self._load(self.legacy)
            except state.StateError as error:
                # PaceLine's file is only a fallback: a damaged one never blocks Stint, and is never written.
                why = str(error).replace(str(self.legacy), "the file")
                print(f"stint: PaceLine's queue {self.legacy} cannot be read ({why}); starting from an empty queue",
                      file=sys.stderr)
        if self.data is None:
            self.data = normalized({})

    @staticmethod
    def _load(path):
        """The normalized queue in a file, None when it is missing; a damaged file raises StateError."""
        raw = state.read_object(path)
        if raw is None:
            return None
        try:
            return normalized(raw)
        except ValueError as error:
            raise state.StateError(f"{path}: {error}")

    def save(self):
        state.write_json(self.path, self.data)


# ---------------------------------------------------------------- items

def item_view(item):
    return {k: item.get(k) for k in ("id", "title", "repo", "estimate_pct", "independent", "depends_on",
                                     "status", "reason", "attempts", "actual_pct")}


def find_item(items, item_id):
    for item in items:
        if str(item["id"]).lower() == str(item_id).lower():
            return item
    raise QueueError(f"no item {item_id}")


def add_item(data, title, repo, estimate, independent, depends, now):
    """Append a pending item; unknown dependencies are an error. Ids match case-insensitively."""
    known = {str(i["id"]).lower(): i["id"] for i in data["items"]}
    missing = [d for d in depends if d.lower() not in known]
    if missing:
        raise QueueError(f"unknown dependencies: {', '.join(missing)}")
    item = {"id": f"Q{data['next_id']}", "title": title, "repo": repo, "estimate_pct": estimate,
            "independent": independent, "depends_on": [known[d.lower()] for d in depends], "status": "pending",
            "reason": None, "attempts": 0, "added_at": iso_ms(now), "started_at": None, "finished_at": None,
            "actual_pct": None, "hold_id": None, "used_at_start": None}
    data["next_id"] += 1
    data["items"].append(item)
    return item


def pending_total(items):
    return sum(i["estimate_pct"] for i in items if i["status"] == "pending")


def defer_with_dependents(items, root_id, reason, now):
    """The root and every pending or running item that depends on it, transitively, become deferred."""
    deferred = []
    todo = [root_id]
    while todo:
        current = todo.pop()
        for item in items:
            if item["status"] in ("pending", "running") and (
                    item["id"] == current or current in item.get("depends_on", [])):
                item["status"] = "deferred"
                item["reason"] = reason if item["id"] == root_id else f"depends on deferred {current}"
                item["finished_at"] = iso_ms(now)
                deferred.append(item)
                todo.append(item["id"])
    return deferred


def pick_next(items, budget, running_ids):
    """First runnable pending item that fits the budget. Returns (item, reason_when_none): the reason is
    "empty", "blocked", "waiting" or "budget"."""
    status = {i["id"]: i["status"] for i in items}
    pending = [i for i in items if i["status"] == "pending"]
    if not pending:
        if any(i["status"] == "running" for i in items):
            return None, "waiting"
        return None, "blocked" if any(i["status"] == "deferred" for i in items) else "empty"
    runnable, waiting = [], False
    for item in pending:
        deps = item.get("depends_on", [])
        if any(status.get(d) in ("deferred", None) for d in deps):
            continue
        if all(status.get(d) == "done" for d in deps):
            runnable.append(item)
        else:
            waiting = True
    if not runnable:
        return None, "waiting" if waiting else "blocked"
    running = [i for i in items if i["id"] in running_ids]
    exclusive = any(not i.get("independent") for i in running)
    for item in runnable:
        if running and (exclusive or not item.get("independent")):
            continue
        if item["estimate_pct"] <= budget + 1e-9:
            return item, None
    if running:
        return None, "waiting"
    return None, "budget"


# ---------------------------------------------------------------- settings and readings

def read_settings(raw):
    """The settings object of settings.json with each field falling back to its default on its own."""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for key, default in DEFAULTS.items():
        value = raw.get(key)
        low, high = RANGES[key]
        valid = isinstance(value, int) and not isinstance(value, bool) and low <= value <= high
        out[key] = value if valid else default
    return out


def read_limits(raw, now):
    """The reading of limits.json at `now`, or None when it holds no window. A bucket whose reset has
    passed reads as 0 %. A file without the week bucket holds no reading. Keys: week_pct, five_hour_pct,
    five_hour_resets_at (the file's text), age_min, stale."""
    if not isinstance(raw, dict):
        return None
    observed = parse_iso(raw.get("observed_at"))
    if observed is None:
        return None

    def bucket(name):
        b = raw.get(name)
        if not isinstance(b, dict) or number(b.get("used_percentage")) is None:
            return None, None
        used = number(b["used_percentage"])
        resets = b.get("resets_at") if isinstance(b.get("resets_at"), str) else None
        reset_at = parse_iso(resets)
        if reset_at is not None and reset_at <= now:
            used = 0.0
        return used, resets

    week, _ = bucket("seven_day")
    five, five_resets = bucket("five_hour")
    if week is None:
        return None
    age = max(0.0, (now - observed).total_seconds() / 60)
    return {"week_pct": week, "five_hour_pct": five, "five_hour_resets_at": five_resets,
            "age_min": round(age, 1), "stale": age > STALE_MIN}


def limits_view(reading, settings):
    """`limits` of the answer when Stint's own stops decide."""
    r = reading or {}
    return {"week_pct": r.get("week_pct"), "five_hour_pct": r.get("five_hour_pct"),
            "five_hour_resets_at": r.get("five_hour_resets_at"), "age_min": r.get("age_min"),
            "week_stop_pct": settings["week_stop_pct"], "five_hour_stop_pct": settings["five_hour_stop_pct"]}


def own_budget(reading, settings):
    """What is left of the week up to its stop, 0.0 without a reading."""
    if reading is None:
        return 0.0
    return max(0.0, settings["week_stop_pct"] - reading["week_pct"])


# ---------------------------------------------------------------- runs

def run_end(run):
    """The instant a run ends: its end, or the night_end of a run PaceLine stored; None for none."""
    if not run:
        return None
    end = parse_iso(run.get("end") or run.get("night_end"))
    if end is None and run.get("end") is None and run.get("night_date"):
        # A run PaceLine stored before the week plan: its night ends at 09:00 UTC of its night_date, the
        # default start of work (PaceLine used the profile's zone, which Stint does not read).
        with contextlib.suppress(ValueError):
            day = dt.date.fromisoformat(str(run["night_date"]))
            end = dt.datetime(day.year, day.month, day.day, 9, tzinfo=UTC)
    return end


def run_is_open(run, now, account=None):
    """A run is open until it has an ended_at; while its end is null or later than now; and, when both
    accounts are known, while the account is the same."""
    if not run or run.get("ended_at"):
        return False
    end = run_end(run)
    if end is not None and end <= now:
        return False
    stored = run.get("account")
    return not (stored and account and stored != account)


def start_run(data, now, end, account, source, allowance, used_at_start):
    """Archive the old run (the last PAST_RUNS_KEPT stay) and open a new one."""
    if data["run"]:
        data["past_runs"] = (data["past_runs"] + [data["run"]])[-PAST_RUNS_KEPT:]
    data["run"] = {"started_at": iso_ms(now), "end": end, "account": account, "source": source,
                   "allowance_pct": allowance, "used_at_start": used_at_start}
    return data["run"]


def run_view(run, week_now, reused):
    if not run:
        return None
    used = number(run.get("used_at_start"))
    spent = None if week_now is None or used is None else round(max(0.0, week_now - used), 3)
    end = run.get("end") or run.get("night_end")
    return {"started_at": run.get("started_at"), "ended_at": run.get("ended_at"), "end": end, "source": run.get("source") or "paceline",
            "allowance_pct": run.get("allowance_pct"), "spent_pct": spent, "reused": reused}


# ---------------------------------------------------------------- the decision

def decide(now, data, settings, advice, note, reading, week_now, reused=False):
    """The answer of `stint next` (without the start-run side effect, which the caller did).

    advice is PaceLine's valid advice or None; note is the unavailable-advice reason when PaceLine was
    found but gave none; reading is read_limits(...) or None; week_now is the week reading the run's
    spent_pct is measured by."""
    run = data["run"]
    if run and run.get("ended_at"):
        run = None  # a closed run is no run
    items = data["items"]
    texts = [str(r.get("text", "")) for r in advice["reasons"] if isinstance(r, dict)] if advice else []
    running_ids = {i["id"] for i in items if i["status"] == "running"}
    if advice:
        budget = number(advice.get("budget_pct")) or 0.0
    else:
        budget = own_budget(reading, settings)
    result = {"action": "stop", "may_start_next_item": False, "max_parallel_agents": 0,
              "budget_for_next_item_pct": 0.0 if not run else round(budget, 3), "next_item": None,
              "wait_until": None, "reasons": [], "run": run_view(run, week_now, reused),
              "source": "paceline" if advice else "stint", "advice": advice,
              "limits": None if advice else limits_view(reading, settings)}

    def finish(action, reasons, item=None, wait_until=None):
        extra = [note] if note and not advice else []
        result.update(action=action, reasons=list(reasons) + extra, wait_until=wait_until)
        if action == "start":
            agents = int(number(advice.get("agents")) or 0) if advice else settings["agents"]
            result.update(may_start_next_item=True, next_item=item_view(item), max_parallel_agents=agents)
        return result

    if not run:
        return finish("stop", [NO_RUN])
    end = run_end(run)
    ended = end is not None and now >= end
    item, why = pick_next(items, budget, running_ids)

    def by_item_rules():
        if item is None and why == "empty":
            return "stop", ["queue empty"]
        if item is None and why == "blocked":
            return "stop", ["all remaining items need owner permission"]
        return None

    def budget_rules():
        if item is None and why == "budget":
            return finish("stop", [f"budget exhausted: no item fits {budget:.2f}%"])
        if item is None:
            return finish("wait", ["waiting for running items or dependencies"])
        return None

    if advice:
        run_account, account = run.get("account"), advice.get("account")
        if run_account and account and run_account != account:
            return finish("stop", ["account changed during the run"])
        if ended:
            return finish("stop", ["night ended"])
        if any(isinstance(r, dict) and r.get("effect") == "stop" and r.get("code") in SAFETY_STOPS
               for r in advice["reasons"]):
            return finish("stop", texts)
        if run_account and not account:
            return finish("wait", ["account unknown"])
        ruled = by_item_rules()
        if ruled:
            return finish(*ruled)
        if advice["action"] == "stop":
            return finish("stop", texts)
        if advice["action"] == "wait":
            until = parse_iso(advice.get("wait_until"))
            if until is None or end is None or until < end:
                return finish("wait", texts, wait_until=advice.get("wait_until"))
            return finish("stop", texts + ["nothing can start before the night ends"])
        done = budget_rules()
        if done:
            return done
        limit_texts = [str(r.get("text", "")) for r in advice["reasons"]
                       if isinstance(r, dict) and r.get("effect") == "limit"]
        return finish("start", limit_texts, item)

    # Stint's own stops.
    if ended:
        return finish("stop", ["night ended"])
    if reading is None:
        return finish("wait", [NO_READING])
    if reading["stale"]:
        return finish("wait", [f"usage data stale ({reading['age_min']:.0f} min)"])
    week, stop = reading["week_pct"], settings["week_stop_pct"]
    if week >= stop:
        return finish("stop", [f"week {week:.0f}% >= {stop:.0f}% stop"])
    ruled = by_item_rules()
    if ruled:
        return finish(*ruled)
    five, five_stop = reading["five_hour_pct"], settings["five_hour_stop_pct"]
    if five is not None and five >= five_stop:
        resets = reading["five_hour_resets_at"]
        when = f"resets {resets[:16]}" if resets else "resets later"
        return finish("wait", [f"5h window {five:.0f}% >= {five_stop:.0f}% stop, {when}"], wait_until=resets)
    done = budget_rules()
    if done:
        return done
    return finish("start", [], item)


def next_line(result):
    """The text line `stint next` prints."""
    reasons = "; ".join(result["reasons"])
    if result["action"] == "start":
        line = (f"next: START {result['next_item']['id']} | budget {result['budget_for_next_item_pct']:.2f}%"
                f" | agents <={result['max_parallel_agents']}")
        return line + (f" | {reasons}" if reasons else "")
    if result["action"] == "wait":
        head = f"next: WAIT until {result['wait_until'][:16]}" if result["wait_until"] else "next: WAIT"
    else:
        head = "next: STOP"
    return head + (f" | {reasons}" if reasons else "")
