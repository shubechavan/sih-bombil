"""collect.py — crawl the lab target's three sources in one pass.

    python scripts/collect.py --onion http://<addr>.onion --out /tmp/collected.json
    python scripts/collect.py --onion http://<addr>.onion --verify

Order matters and is not cosmetic. `scripts/ingest.py` assigns persona ids from
a serial sequence in insertion order, and `fixtures/ground_truth.json` is keyed
by persona id — 1–8 market_alpha, 9–15 forum_beta, 16–20 market_gamma. Crawling
the sources in that order, and each index in the order the target lists them,
is what lets a collected corpus be scored against the same answer key as the
offline one. Any other order still ingests correctly; it just cannot be
compared pair-for-pair with fixtures mode.

There is no default target. `--onion` is required and the collectors refuse any
other host unless `--allow-external` is passed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

from collectors import forum_collector, market_collector  # noqa: E402
from collectors.base import CollectResult, documents_to_json  # noqa: E402
from recon.fingerprint import HostRateLimiter  # noqa: E402

__all__ = ["SOURCES", "collect_all"]

#: (source name, index path, collector). The order is the id order the answer
#: key expects — see the module docstring.
SOURCES = (
    ("market_alpha", "/market_alpha", market_collector),
    ("forum_beta", "/forum_beta", forum_collector),
    ("market_gamma", "/market_gamma", market_collector),
)

RULE = "─" * 78


def collect_all(onion: str, *, allow_external: bool = False,
                with_feedback: bool = True) -> CollectResult:
    """Every source, in answer-key order, sharing one rate limiter.

    One limiter across all three: the target is a single host, and three
    collectors each with their own budget would hit it three times as fast as
    the 1 req / 2 s CLAUDE.md mandates.
    """
    from collectors.base import Crawler  # noqa: PLC0415

    limiter = HostRateLimiter()
    # One session as well as one limiter: three sessions would each open their
    # own Tor circuit to the same hidden service, which is three times the
    # rendezvous cost for no benefit.
    session = Crawler(onion, allow_external=allow_external,
                      limiter=limiter).session
    combined = CollectResult()
    for name, index, module in SOURCES:
        kwargs = {"source_name": name, "allow_external": allow_external,
                  "index_path": index, "session": session, "limiter": limiter}
        if module is market_collector:
            kwargs["with_feedback"] = with_feedback
        result = module.collect(onion, **kwargs)
        print(f"  {name:<14} {len(result.documents):>2} persona(s), "
              f"{sum(len(d['posts']) for d in result.documents):>3} post(s)"
              + (f", {len(result.feedback)} feedback" if result.feedback else ""))
        combined.merge(result)
    return combined


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Crawl the lab target's three sources over Tor."
    )
    parser.add_argument("--onion", required=True,
                        help="the target. Required; there is no default.")
    parser.add_argument("--out", type=Path,
                        help="write the ingest documents here")
    parser.add_argument("--feedback-out", type=Path,
                        help="write the collected feedback rows here")
    parser.add_argument("--allow-external", action="store_true",
                        help="permit hosts other than --onion")
    parser.add_argument("--no-feedback", action="store_true")
    parser.add_argument("--verify", action="store_true",
                        help="diff collected text against fixtures/ field by field")
    args = parser.parse_args(argv)

    print(RULE)
    print("collect — live acquisition over Tor")
    print(RULE)
    print(f"  target    {args.onion}")
    print(f"  rate      1 request / 2 s per host, shared across all sources")

    result = collect_all(args.onion, allow_external=args.allow_external,
                         with_feedback=not args.no_feedback)

    posts = sum(len(d["posts"]) for d in result.documents)
    print(f"\n  total     {len(result.documents)} personas, {posts} posts, "
          f"{len(result.feedback)} feedback rows, {result.fetched} requests")
    for line in result.refused[:6]:
        print(f"  refused:  {line}")
    for line in result.errors[:6]:
        print(f"  error:    {line}")

    if args.out:
        documents_to_json(result.documents, args.out)
        print(f"  wrote     {args.out}")
    if result.feedback and (args.feedback_out or args.out):
        target = args.feedback_out or args.out.with_name(args.out.stem + "-feedback.json")
        documents_to_json(result.feedback, target)
        print(f"  wrote     {target}")

    if args.verify:
        from collectors.verify import report_verify  # noqa: PLC0415

        return report_verify(result.documents)
    return 0 if result.documents and not result.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
