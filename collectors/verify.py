"""verify.py — did the crawl lose anything?

The scoring engine reads `bio + key_blocks + post titles + bodies`. Change one
character and the writeprint vocabulary shifts, every pair's S moves, and the
evaluation quietly stops matching the offline run. A collector that drops a
trailing space is not obviously broken; it is broken in a way you only find by
comparing the numbers, days later, without knowing why.

So this compares the collected text against `fixtures/` field by field and
names the **first** divergence with both sides quoted and the exact character
offset. Run it with `--verify` on either collector.

It only means anything against the lab, which serves the fixture corpus. Point
a collector at a real site and there is nothing to diff — which is why
`--verify` reports that it could not match rather than claiming success.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "fixtures"

__all__ = ["add_verify_args", "compare", "report_verify"]


def _fixture_index() -> tuple[dict, dict, dict]:
    personas, posts, blocks = {}, {}, {}
    for name in ("market_alpha", "forum_beta", "market_gamma"):
        for persona in json.loads(
            (FIXTURES / name / "personas.json").read_text(encoding="utf-8")
        ):
            personas[persona["handle"]] = persona
        for post in json.loads(
            (FIXTURES / name / "posts.json").read_text(encoding="utf-8")
        ):
            posts.setdefault(post["persona_id"], []).append(post)
    for post_list in posts.values():
        # The lab serves posts in this order; the comparison has to use it too.
        post_list.sort(key=lambda p: (p.get("posted_at") or "", p["id"]))
    for block in json.loads((FIXTURES / "pgp_blocks.json").read_text(encoding="utf-8")):
        blocks.setdefault(block["persona_id"], []).append(block["armored"])
    return personas, posts, blocks


def _first_difference(got: str, want: str) -> str:
    for index, (a, b) in enumerate(zip(got, want)):
        if a != b:
            return (f"differs at character {index}: collected {a!r} "
                    f"vs fixture {b!r}\n        collected: ...{got[max(0,index-30):index+30]!r}"
                    f"\n        fixture  : ...{want[max(0,index-30):index+30]!r}")
    if len(got) != len(want):
        longer, label = (got, "collected") if len(got) > len(want) else (want, "fixture")
        return (f"lengths differ: collected {len(got)}, fixture {len(want)} — "
                f"{label} has trailing {longer[min(len(got), len(want)):][:40]!r}")
    return "identical"


def compare(documents: Iterable[dict]) -> list[str]:
    """Field-by-field diff against fixtures. Returns the problems found."""
    personas, posts, blocks = _fixture_index()
    problems: list[str] = []
    seen = 0

    for document in documents:
        handle = document.get("handle")
        persona = personas.get(handle)
        if persona is None:
            problems.append(f"{handle}: no fixture persona with this handle")
            continue
        seen += 1

        if document.get("bio") != persona.get("bio"):
            problems.append(
                f"{handle}: bio {_first_difference(document.get('bio') or '', persona.get('bio') or '')}"
            )

        want_posts = posts.get(persona["id"], [])
        got_posts = document.get("posts") or []
        if len(got_posts) != len(want_posts):
            problems.append(
                f"{handle}: collected {len(got_posts)} posts, fixture has {len(want_posts)}"
            )
        for index, (got, want) in enumerate(zip(got_posts, want_posts)):
            for key in ("title", "body"):
                if (got.get(key) or "") != (want.get(key) or ""):
                    problems.append(
                        f"{handle} post {index} {key}: "
                        f"{_first_difference(got.get(key) or '', want.get(key) or '')}"
                    )

        want_blocks = blocks.get(persona["id"], [])
        got_blocks = document.get("key_blocks") or []
        if len(got_blocks) != len(want_blocks):
            problems.append(
                f"{handle}: collected {len(got_blocks)} key blocks, "
                f"fixture has {len(want_blocks)}"
            )
        for index, (got, want) in enumerate(zip(got_blocks, want_blocks)):
            if got != want:
                problems.append(
                    f"{handle} key block {index}: {_first_difference(got, want)}"
                )

    if not seen:
        problems.append(
            "nothing matched a fixture persona — --verify only means something "
            "against the lab target, which serves fixtures/"
        )
    return problems


def add_verify_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--verify", action="store_true",
        help="diff the collected text against fixtures/ and name the first "
             "field that differs. Only meaningful against the lab target.",
    )


def report_verify(documents: Iterable[dict]) -> int:
    documents = list(documents)
    problems = compare(documents)
    total = sum(1 + len(d.get("posts") or []) * 2 + len(d.get("key_blocks") or [])
                for d in documents)
    print(f"\n  verify: {len(documents)} document(s), ~{total} text fields")
    if not problems:
        print("  every field is byte-identical to fixtures/ — the collector is lossless")
        return 0
    print(f"  {len(problems)} problem(s); the first few:")
    for line in problems[:6]:
        print(f"    - {line}")
    return 1
