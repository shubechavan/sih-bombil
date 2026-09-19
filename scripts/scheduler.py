"""scheduler.py — autonomous mode.

    python scripts/scheduler.py --run-once            # one tick, then exit
    python scripts/scheduler.py --start               # run on an interval
    python scripts/scheduler.py --start --interval 5m
    python scripts/scheduler.py --status              # what the last tick did
    Ctrl-C, SIGTERM, or `--stop`                      # graceful shutdown

**Nothing starts this.** The API does not launch it, no import has a side
effect, and `--start` is the only thing that begins a schedule. An attribution
tool that quietly re-scans the dark web because somebody imported a module is
not a tool anyone should run.

WHAT A TICK DOES
----------------
1. Re-read the sources and ingest anything new. Posts are deduplicated on
   `body_hash`, so re-reading an unchanged source writes nothing.
2. Decide whether linking needs to run at all, by comparing the corpus's
   stylometric fingerprint against the one the last tick recorded.
3. If it changed, re-link and re-cluster. If it did not, skip both and say so.

Every tick writes a `scans` row with a SHA-256 of what it observed, and each
step it invokes writes its own. A tick that changed nothing still leaves a row
saying it looked — an audit log with gaps in it is not an audit log.

"RE-LINK NEW PERSONAS ONLY" — AND WHY IT CANNOT BE DONE HERE
------------------------------------------------------------
The build plan asks for linking to re-run on new personas only. That is the
right instinct and it is not achievable for the S term, for a measurable
reason: the writeprint vectoriser fits its vocabulary over the whole corpus, so
adding one persona changes every other persona's vector. Measured on the
fixture corpus — drop one of twenty personas and S(1,16) moves from 0.857399 to
0.849284, and `feature_version` changes from `sty-1:78f0f5d646ef` to
`sty-1:74f21c4d161d`, which is exactly the guard `stylometry.load()` uses to
refuse mixing vectors from two fittings.

So scoring only the new pairs would leave the `links` table holding two
incompatible scorings, and the older half would silently disagree with what
`scripts/evaluate.py` reports. This job therefore does the saving where the
saving is real — **it skips linking entirely when no new text arrived**, which
is the common case for a polling scheduler — and does a full, consistent
re-score when it did. Each tick reports which of the two happened and why.

The genuine fix is a frozen vocabulary fitted once and reused, which changes
what a writeprint means and belongs in `link/stylometry.py` with its own
evaluation, not smuggled in here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# The default Windows console codepage is cp1252, which cannot encode the box
# rules or the em dashes below and raises rather than degrading. Ask for UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):  # already redirected, or not a tty
        pass

from sqlalchemy import text as sql  # noqa: E402

from db import Scan, require_schema, session_scope, utcnow  # noqa: E402
from link import cluster as cluster_module  # noqa: E402
from link import stylometry as stylometry_module  # noqa: E402
from link.resolve import load_corpus, resolve_pairs, store_links  # noqa: E402

__all__ = ["STATE_FILE", "TickResult", "parse_interval", "run_tick", "start"]

#: Where the last tick's outcome is recorded, so --status can answer without a
#: database and a restart can tell whether the corpus moved while it was down.
STATE_FILE = ROOT / ".scheduler-state.json"

RULE = "─" * 78

_DURATION = re.compile(r"^\s*(\d+)\s*([smhd])?\s*$", re.IGNORECASE)
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

#: Below this a "scheduler" is a busy loop against a Tor-backed crawler.
MIN_INTERVAL_SECONDS = 30


def parse_interval(value: str) -> int:
    """'90s', '15m', '2h', '1d', or a bare number of seconds."""
    match = _DURATION.match(value)
    if not match:
        raise argparse.ArgumentTypeError(
            f"cannot read {value!r} as an interval; use 30s, 15m, 2h or 1d"
        )
    seconds = int(match.group(1)) * _UNITS[(match.group(2) or "s").lower()]
    if seconds < MIN_INTERVAL_SECONDS:
        raise argparse.ArgumentTypeError(
            f"interval {value!r} is {seconds}s; the floor is "
            f"{MIN_INTERVAL_SECONDS}s. Rate limiting protects a host from one "
            f"scan, not from a scan restarting continuously."
        )
    return seconds


@dataclass
class TickResult:
    """What one pass did, in enough detail to audit it."""

    started_at: datetime
    finished_at: Optional[datetime] = None
    source: str = "fixtures"
    documents: int = 0
    personas_new: int = 0
    posts_written: int = 0
    identifiers_written: int = 0
    corpus_version: Optional[str] = None
    previous_version: Optional[str] = None
    relinked: bool = False
    relink_reason: str = ""
    links_written: int = 0
    actors_written: int = 0
    new_personas: list[int] = field(default_factory=list)
    scan_ids: list[int] = field(default_factory=list)
    action_hash: str = ""
    status: str = "ok"
    error: Optional[str] = None

    def as_dict(self) -> dict:
        out = {
            k: (v.isoformat() if isinstance(v, datetime) else v)
            for k, v in self.__dict__.items()
        }
        return out


def read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_state(result: TickResult) -> None:
    try:
        STATE_FILE.write_text(
            json.dumps(result.as_dict(), indent=2, default=str), encoding="utf-8"
        )
    except OSError as exc:  # a state file we cannot write is not fatal
        print(f"  warning: could not write {STATE_FILE.name}: {exc}", file=sys.stderr)


# ─────────────────────────────────────────────────────────────────────────────
# One tick
# ─────────────────────────────────────────────────────────────────────────────

def _ingest(session, source: str, input_path: Optional[Path]) -> tuple[int, int, int, int, list[int]]:
    """Read the sources and write anything new. Returns the counts and new ids."""
    import ingest  # noqa: PLC0415  — sibling script, added to sys.path above

    documents = (
        ingest.read_live_documents(input_path)
        if source == "live"
        else ingest.read_fixture_documents()
    )
    before = {row[0] for row in session.execute(sql("SELECT id FROM personas"))}

    extracted, _dropped = ingest.run_extraction(documents)
    source_ids = ingest.upsert_sources(session, documents)
    persona_ids, personas_new = ingest.upsert_personas(session, documents, source_ids)
    posts_written = ingest.upsert_posts(session, documents, persona_ids, source_ids)
    identifiers, _joins = ingest.write_identifiers(
        session, extracted, documents, persona_ids
    )
    session.flush()

    after = {row[0] for row in session.execute(sql("SELECT id FROM personas"))}
    return (len(documents), personas_new, posts_written, identifiers,
            sorted(after - before))


def run_tick(*, source: str = "fixtures", input_path: Optional[Path] = None,
             operator: str = "scheduler", threshold: Optional[float] = None,
             force: bool = False) -> TickResult:
    """One autonomous pass. Never raises; a failed tick is recorded as failed."""
    result = TickResult(started_at=utcnow(), source=source)
    previous = read_state()
    result.previous_version = previous.get("corpus_version")

    try:
        with session_scope() as session:
            require_schema(session)

            scan = Scan(
                operator_id=operator,
                mode="scheduled",
                data_source=source,
                query="scheduler.tick",
                started_at=result.started_at,
                status="running",
            )
            session.add(scan)
            session.flush()
            result.scan_ids.append(scan.id)

            (result.documents, result.personas_new, result.posts_written,
             result.identifiers_written, result.new_personas) = _ingest(
                session, source, input_path
            )

            corpus = load_corpus(session)
            # The same fingerprint stylometry uses to decide whether two vectors
            # may be compared. If it has not moved, no text the linker reads has
            # changed, and re-scoring would reproduce the rows already stored.
            masked = {
                pid: stylometry_module.mask_identifiers(text, values)
                for pid, text, values in (
                    (pid, corpus.texts()[pid], corpus.identifier_values().get(pid, []))
                    for pid in corpus.persona_ids
                )
            }
            result.corpus_version = stylometry_module.corpus_version(masked)

            unchanged = (
                result.corpus_version == result.previous_version and not force
            )
            if unchanged:
                result.relinked = False
                result.relink_reason = (
                    "corpus fingerprint unchanged since the last tick — no new "
                    "text reached the linker, so the stored links already are "
                    "what a re-score would produce"
                )
            else:
                result.relinked = True
                if force:
                    result.relink_reason = "forced with --force"
                elif result.previous_version is None:
                    result.relink_reason = "first tick: nothing to compare against"
                else:
                    result.relink_reason = (
                        f"corpus fingerprint moved "
                        f"{result.previous_version} → {result.corpus_version}. "
                        f"The writeprint vocabulary is fitted over the whole "
                        f"corpus, so every pair's S changes, not only pairs "
                        f"involving the {len(result.new_personas)} new "
                        f"persona(s) — a partial re-score would leave two "
                        f"incompatible scorings in one table"
                    )

                results, writeprints, behaviours, _infra = resolve_pairs(
                    corpus, session=session
                )
                stylometry_module.store(
                    session, writeprints,
                    hour_histograms={
                        pid: profile.hour_histogram
                        for pid, profile in behaviours.profiles.items()
                    },
                )
                result.links_written = store_links(session, results)

                clusters = cluster_module.build(
                    corpus, results,
                    threshold=threshold or cluster_module.DEFAULT_THRESHOLD,
                )
                result.actors_written = cluster_module.store_actors(session, clusters)

            result.action_hash = hashlib.sha256(json.dumps({
                "source": source,
                "corpus_version": result.corpus_version,
                "documents": result.documents,
                "personas_new": result.personas_new,
                "new_personas": result.new_personas,
                "relinked": result.relinked,
                "links": result.links_written,
                "actors": result.actors_written,
            }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

            scan.action_hash = result.action_hash
            scan.personas_new = result.personas_new
            scan.links_new = result.links_written
            scan.sources_touched = sorted({
                str(p.get("source_id")) for p in corpus.personas.values()
            })
            scan.status = "ok"
            scan.finished_at = utcnow()
            session.flush()

    except Exception as exc:  # noqa: BLE001 - a tick must not kill the schedule
        result.status = "failed"
        result.error = f"{type(exc).__name__}: {exc}"

    result.finished_at = utcnow()
    if result.status == "ok":
        write_state(result)
    return result


def describe(result: TickResult) -> str:
    stamp = result.started_at.strftime("%Y-%m-%d %H:%M:%S")
    if result.status == "failed":
        return f"[{stamp}] FAILED — {result.error}"
    head = (
        f"[{stamp}] {result.documents} document(s), "
        f"{result.personas_new} new persona(s), "
        f"{result.posts_written} post(s)"
    )
    if result.relinked:
        return (
            f"{head}\n    re-linked: {result.links_written} link(s), "
            f"{result.actors_written} actor(s)\n    {result.relink_reason}"
        )
    return f"{head}\n    linking skipped — {result.relink_reason}"


# ─────────────────────────────────────────────────────────────────────────────
# The schedule
# ─────────────────────────────────────────────────────────────────────────────

def start(interval: int, *, source: str, input_path: Optional[Path],
          operator: str, threshold: Optional[float], run_now: bool) -> int:
    """Run ticks on an interval until interrupted. Blocking and foreground.

    Foreground on purpose: a background daemon that scans hidden services is
    something an operator should have to keep a terminal open for, and killing
    it should be as easy as Ctrl-C.
    """
    from apscheduler.schedulers.background import BackgroundScheduler  # noqa: PLC0415

    stopping = threading.Event()

    def tick() -> None:
        if stopping.is_set():
            return
        print(describe(run_tick(
            source=source, input_path=input_path,
            operator=operator, threshold=threshold,
        )), flush=True)

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        tick, "interval", seconds=interval, id="rescan",
        max_instances=1,           # a slow tick must not overlap the next one
        coalesce=True,             # a backlog collapses to one run, not a burst
    )

    print(RULE)
    print("Dark Sentinel v2 — autonomous mode")
    print(RULE)
    print(f"  interval   every {interval}s")
    print(f"  source     {source}")
    print(f"  operator   {operator}")
    print(f"  state      {STATE_FILE.name}")
    print("  stop with Ctrl-C, SIGTERM, or `scheduler.py --stop` from another shell")
    print(RULE, flush=True)

    def shutdown(signum, _frame) -> None:
        if stopping.is_set():
            return
        stopping.set()
        print(f"\n  signal {signum} — finishing the current tick and stopping",
              flush=True)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, shutdown)
        except (ValueError, OSError):  # not the main thread, or unsupported
            pass

    scheduler.start()
    if run_now:
        tick()

    try:
        while not stopping.is_set():
            stopping.wait(1.0)
            if _stop_requested():
                print("\n  stop file seen — shutting down", flush=True)
                stopping.set()
    finally:
        scheduler.shutdown(wait=True)
        _clear_stop_request()
        print("  stopped. The audit trail is in `scans`.", flush=True)
    return 0


#: A second process asks the running one to stop by dropping this file. Simpler
#: and more portable than signalling a pid on Windows, and it leaves a trace.
STOP_FILE = ROOT / ".scheduler-stop"


def _stop_requested() -> bool:
    return STOP_FILE.exists()


def _clear_stop_request() -> None:
    try:
        STOP_FILE.unlink()
    except OSError:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def print_status() -> int:
    state = read_state()
    print(RULE)
    print("Dark Sentinel v2 — autonomous mode status")
    print(RULE)
    if not state:
        print("  no tick has run (no state file). The scheduler does not start")
        print("  on its own; run `python scripts/scheduler.py --start`.")
        print(RULE)
        return 0
    print(f"  last tick       {state.get('started_at')} → {state.get('finished_at')}")
    print(f"  status          {state.get('status')}")
    print(f"  source          {state.get('source')}")
    print(f"  documents       {state.get('documents')}")
    print(f"  new personas    {state.get('personas_new')} "
          f"{state.get('new_personas') or ''}")
    print(f"  corpus version  {state.get('corpus_version')}")
    print(f"  re-linked       {state.get('relinked')}")
    if state.get("relink_reason"):
        print(f"                  {state['relink_reason']}")
    print(f"  links / actors  {state.get('links_written')} / {state.get('actors_written')}")
    print(f"  action hash     {(state.get('action_hash') or '')[:32]}")
    print(f"  running now     {'yes' if STOP_FILE.exists() else 'unknown — '}"
          f"{'' if STOP_FILE.exists() else 'this file records ticks, not liveness'}")
    print(RULE)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Autonomous re-scan. Does not run unless you start it.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--start", action="store_true",
                      help="run ticks on an interval until stopped")
    mode.add_argument("--run-once", action="store_true",
                      help="run a single tick and exit")
    mode.add_argument("--status", action="store_true",
                      help="report what the last tick did, and exit")
    mode.add_argument("--stop", action="store_true",
                      help="ask a running scheduler to shut down")

    parser.add_argument("--interval", type=parse_interval, default="15m",
                        help="how often to tick: 30s, 15m, 2h, 1d (default 15m)")
    parser.add_argument("--source", choices=("fixtures", "live"), default="fixtures",
                        help="fixtures corpus (default) or documents from --input")
    parser.add_argument("--input", type=Path,
                        help="JSON document file, required for --source live")
    parser.add_argument("--operator", default=None,
                        help="operator id for every audit row (default: $OPERATOR_ID)")
    parser.add_argument("--threshold", type=float, default=None,
                        help="clustering threshold (default: the PROBABLE floor)")
    parser.add_argument("--force", action="store_true",
                        help="re-link even when the corpus fingerprint is unchanged")
    parser.add_argument("--no-run-now", action="store_true",
                        help="with --start, wait one interval before the first tick")
    args = parser.parse_args(argv)

    if args.status:
        return print_status()

    if args.stop:
        STOP_FILE.write_text(utcnow().isoformat(), encoding="utf-8")
        print(f"  stop requested ({STOP_FILE.name}). A running scheduler will "
              f"finish its current tick and exit.")
        return 0

    if args.source == "live" and not args.input:
        parser.error("--source live needs --input <documents.json>")

    operator = args.operator or os.environ.get("OPERATOR_ID") or "scheduler"

    if args.run_once:
        result = run_tick(source=args.source, input_path=args.input,
                          operator=operator, threshold=args.threshold,
                          force=args.force)
        print(describe(result))
        return 0 if result.status == "ok" else 1

    _clear_stop_request()
    return start(args.interval, source=args.source, input_path=args.input,
                 operator=operator, threshold=args.threshold,
                 run_now=not args.no_run_now)


if __name__ == "__main__":
    raise SystemExit(main())
