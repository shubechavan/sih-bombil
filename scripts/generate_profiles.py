"""generate_profiles.py — write a behavioural profile for each actor.

    python scripts/generate_profiles.py                  # rule-based, offline
    python scripts/generate_profiles.py --llm            # use LLM_* if set
    python scripts/generate_profiles.py --llm --actor 1  # one actor
    python scripts/generate_profiles.py --dry-run        # print, store nothing

This is the only place in the repo that makes an outbound model call, and it
only does so with `--llm`. Read paths — the actor page, the PDF — never call
out: they read this table or fall back to the template. Nobody should be
waiting on somebody else's API while a page loads, and a demo should not depend
on conference wifi.

Profiles are cached by a fingerprint of the features they were written from, so
`--llm` on a machine with a key produces text that survives into an offline
demo. Re-running without `--llm` does not overwrite an AI profile whose
fingerprint still matches; it skips it. Use `--force` to regenerate.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

import narrative  # noqa: E402
from db import Actor, SessionLocal, session_scope  # noqa: E402
from narrative.provider import ProviderError, from_env  # noqa: E402
from sqlalchemy import select  # noqa: E402

WIDTH = 78


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--llm", action="store_true",
                        help="call the configured model; without it, rule-based")
    parser.add_argument("--actor", type=int, help="one actor id")
    parser.add_argument("--force", action="store_true",
                        help="regenerate even when a fresh profile is cached")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the profiles, write nothing")
    args = parser.parse_args(argv)

    provider = None
    if args.llm:
        try:
            provider = from_env()
        except ProviderError as exc:
            print(f"  LLM configuration rejected: {exc}")
            print("  falling back to rule-based profiles for every actor.\n")
        if provider is None:
            print("  No LLM configured (LLM_PROVIDER / LLM_API_KEY unset).")
            print("  Writing rule-based profiles, labelled as such.\n")
        else:
            print(f"  Using {provider.name} / {provider.model}\n")

    written = ai = rule = skipped = 0

    with session_scope() as session:
        ids = [args.actor] if args.actor else [
            a.id for a in session.execute(select(Actor).order_by(Actor.id)).scalars()
        ]

        for actor_id in ids:
            features = narrative.derive(session, actor_id)
            if features is None:
                print(f"  actor {actor_id}: no personas, skipped")
                skipped += 1
                continue

            if not args.force:
                cached = narrative._load(session, actor_id, features.fingerprint())
                if cached is not None:
                    print(f"  actor {actor_id}: cached {cached.kind} profile is "
                          f"current, skipped")
                    skipped += 1
                    continue

            profile = narrative.generate(features, provider, allow_llm=args.llm)
            if profile.is_ai:
                ai += 1
            else:
                rule += 1

            print("─" * WIDTH)
            handles = ", ".join(p.handle for p in features.timeline)
            print(f"  actor {actor_id}  [{profile.kind}]  {handles}")
            if profile.fallback_reason:
                print(f"  fell back: {profile.fallback_reason}")
            print("─" * WIDTH)
            print(f"  {profile.text}")
            print()

            # Only AI profiles are cached. The rule-based one is deterministic
            # and costs microseconds, so a read path regenerates it rather than
            # reading a row — and caching it would make its fingerprint match
            # on the next `--llm` run, quietly skipping the actor the operator
            # just asked to have written properly.
            if not args.dry_run and profile.is_ai:
                narrative.store(session, actor_id, profile)
                written += 1

        if args.dry_run:
            session.rollback()

    print("─" * WIDTH)
    print(f"  {ai} AI, {rule} rule-based, {skipped} skipped, "
          f"{written} cached{' (dry run)' if args.dry_run else ''}")
    if rule and not args.dry_run:
        print("  Rule-based profiles are not cached — they are regenerated on")
        print("  read, so a later --llm run is not skipped as already current.")
    if rule and args.llm:
        print("  Rule-based profiles are labelled as rule-based everywhere they")
        print("  appear. Nothing is presented as AI output that is not.")
    print("─" * WIDTH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
