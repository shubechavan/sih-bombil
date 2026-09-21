"""forum_collector.py — forum authors and their thread posts.

    python -m collectors.forum_collector --onion http://<addr>.onion --out docs.json

A forum persona is an author, not a vendor: no trust score that means anything,
no listings, and **no feedback** — there is nothing to buy, so there is nobody
to rate. That absence is not a gap in this collector; it is why only four of
the eight true positive pairs are even measurable by any buyer-derived signal,
which is one of the reasons feedback does not feed attribution.
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

from collectors.base import (  # noqa: E402
    CollectResult,
    Crawler,
    documents_to_json,
    field_text,
    fields_text,
)

__all__ = ["collect", "collect_persona"]

KIND = "forum"
PREFIX = "user"


def collect_persona(crawler: Crawler, handle: str, source_name: str,
                    *, index_path: str = "/") -> Optional[dict]:
    page = crawler.soup(f"/{PREFIX}/{handle}")
    if page is None:
        return None
    profile_url = f"{crawler.base}/{PREFIX}/{handle}"
    posts = [{
        "title": field_text(article, "title"),
        "body": field_text(article, "body"),
        "category": field_text(article, "category"),
        "posted_at": field_text(article, "posted_at"),
        "url": profile_url,
    } for article in page.select('[data-f="post"]')]

    return {
        "source": source_name,
        "source_url": crawler.source_url(index_path),
        "kind": KIND,
        "handle": field_text(page, "handle") or handle,
        "profile_url": profile_url,
        "bio": field_text(page, "bio"),
        "category": field_text(page, "category"),
        # A forum profile shows no vendor rating. Leaving it None is the honest
        # reading; inventing one would put a number in the database that the
        # site never published.
        "trust_score": None,
        "first_seen": field_text(page, "first_seen") or None,
        "last_seen": field_text(page, "last_seen") or None,
        "posts": posts,
        "key_blocks": fields_text(page, "key_block"),
    }


def collect(onion: str, *, source_name: str = "lab_forum",
            allow_external: bool = False, handles: Optional[Sequence[str]] = None,
            index_path: str = "/",
            session=None, limiter=None) -> CollectResult:
    crawler = Crawler(onion, allow_external=allow_external, session=session,
                      limiter=limiter)
    result = CollectResult()

    wanted = list(handles) if handles else crawler.index_handles(index_path)
    if not wanted:
        result.errors.append(f"no author links found at {crawler.base}{index_path}")

    for handle in wanted:
        document = collect_persona(crawler, handle, source_name,
                                   index_path=index_path)
        if document is None:
            result.errors.append(f"could not read author {handle}")
            continue
        result.documents.append(document)
        result.fetched += 1

    result.refused += crawler.refused
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    from collectors.verify import add_verify_args, report_verify  # noqa: PLC0415

    parser = argparse.ArgumentParser(
        description="Crawl a forum hidden service. Targets only --onion."
    )
    parser.add_argument("--onion", required=True,
                        help="the target. There is no default target list.")
    parser.add_argument("--source-name", default="lab_forum")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--handle", action="append")
    parser.add_argument("--index", default="/",
                        help="path listing the profiles to crawl (default /)")
    parser.add_argument("--allow-external", action="store_true",
                        help="permit hosts other than --onion. Audited.")
    add_verify_args(parser)
    args = parser.parse_args(argv)

    result = collect(args.onion, source_name=args.source_name,
                     allow_external=args.allow_external, handles=args.handle,
                     index_path=args.index)

    print(f"  collected {len(result.documents)} author(s), "
          f"{sum(len(d['posts']) for d in result.documents)} post(s) "
          f"in {result.fetched} request(s)")
    for line in result.refused[:5]:
        print(f"  refused: {line}")
    for line in result.errors[:5]:
        print(f"  error:   {line}")

    if args.out:
        documents_to_json(result.documents, args.out)
        print(f"  wrote {args.out}")

    if args.verify:
        return report_verify(result.documents)
    return 0 if result.documents and not result.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
