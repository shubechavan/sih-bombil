"""market_collector.py — vendor profiles, listings and buyer feedback.

    python -m collectors.market_collector --onion http://<addr>.onion --out docs.json
    python -m collectors.market_collector --onion http://<addr>.onion --verify

A market persona is a vendor: a profile with a bio, a trust score, a set of
listings (which are `posts` in the data model) and a feedback page. Feedback is
collected into its own structure and never merged into the document — a buyer
is a counterparty, not a persona, and `docs/BUILD_PLAN.md` records the
measurement behind that decision.
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

KIND = "market"
PREFIX = "vendor"


def _posts(page, profile_url: str) -> list[dict]:
    out = []
    for article in page.select('[data-f="post"]'):
        out.append({
            "title": field_text(article, "title"),
            "body": field_text(article, "body"),
            "category": field_text(article, "category"),
            "posted_at": field_text(article, "posted_at"),
            "url": profile_url,
        })
    return out


def collect_persona(crawler: Crawler, handle: str, source_name: str,
                    *, with_feedback: bool = True,
                    index_path: str = "/") -> tuple[Optional[dict], list[dict]]:
    """One vendor: the ingest document, and their feedback rows."""
    page = crawler.soup(f"/{PREFIX}/{handle}")
    if page is None:
        return None, []

    profile_url = f"{crawler.base}/{PREFIX}/{handle}"
    trust = field_text(page, "trust_score")
    document = {
        "source": source_name,
        "source_url": crawler.source_url(index_path),
        "kind": KIND,
        "handle": field_text(page, "handle") or handle,
        "profile_url": profile_url,
        "bio": field_text(page, "bio"),
        "category": field_text(page, "category"),
        "trust_score": float(trust) if trust else None,
        "first_seen": field_text(page, "first_seen") or None,
        "last_seen": field_text(page, "last_seen") or None,
        "posts": _posts(page, profile_url),
        # Multi-line and checksum-bearing: taken verbatim out of <pre>.
        "key_blocks": fields_text(page, "key_block"),
    }

    feedback: list[dict] = []
    if with_feedback and page.select_one('[data-f="feedback_link"]'):
        fb_page = crawler.soup(f"/{PREFIX}/{handle}/feedback")
        if fb_page is not None:
            for article in fb_page.select('[data-f="feedback"]'):
                rating = field_text(article, "rating")
                feedback.append({
                    "source": source_name,
                    "vendor_handle": document["handle"],
                    "buyer_handle": field_text(article, "buyer_handle"),
                    "rating": int(rating) if rating and rating.isdigit() else None,
                    "body": field_text(article, "body"),
                    "posted_at": field_text(article, "posted_at") or None,
                    "url": f"{profile_url}/feedback",
                })
    return document, feedback


def collect(onion: str, *, source_name: str = "lab_market",
            allow_external: bool = False, handles: Optional[Sequence[str]] = None,
            index_path: str = "/",
            with_feedback: bool = True, session=None,
            limiter=None) -> CollectResult:
    crawler = Crawler(onion, allow_external=allow_external, session=session,
                      limiter=limiter)
    result = CollectResult()

    wanted = list(handles) if handles else crawler.index_handles(index_path)
    if not wanted:
        result.errors.append(f"no vendor links found at {crawler.base}{index_path}")

    for handle in wanted:
        document, feedback = collect_persona(
            crawler, handle, source_name, with_feedback=with_feedback,
            index_path=index_path,
        )
        if document is None:
            result.errors.append(f"could not read vendor {handle}")
            continue
        result.documents.append(document)
        result.feedback += feedback
        result.fetched += 1 + (1 if feedback else 0)

    result.refused += crawler.refused
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    from collectors.verify import add_verify_args, report_verify  # noqa: PLC0415

    parser = argparse.ArgumentParser(
        description="Crawl a marketplace hidden service. Targets only --onion."
    )
    parser.add_argument("--onion", required=True,
                        help="the target. There is no default target list.")
    parser.add_argument("--source-name", default="lab_market",
                        help="name recorded in `sources`")
    parser.add_argument("--out", type=Path,
                        help="write the ingest documents here")
    parser.add_argument("--handle", action="append",
                        help="one vendor; repeatable. Default: every link on /")
    parser.add_argument("--index", default="/",
                        help="path listing the profiles to crawl (default /)")
    parser.add_argument("--allow-external", action="store_true",
                        help="permit hosts other than --onion. Audited.")
    parser.add_argument("--no-feedback", action="store_true")
    add_verify_args(parser)
    args = parser.parse_args(argv)

    result = collect(
        args.onion, source_name=args.source_name,
        allow_external=args.allow_external, handles=args.handle,
        index_path=args.index,
        with_feedback=not args.no_feedback,
    )

    print(f"  collected {len(result.documents)} vendor(s), "
          f"{sum(len(d['posts']) for d in result.documents)} listing(s), "
          f"{len(result.feedback)} feedback row(s) in {result.fetched} request(s)")
    for line in result.refused[:5]:
        print(f"  refused: {line}")
    for line in result.errors[:5]:
        print(f"  error:   {line}")

    if args.out:
        documents_to_json(result.documents, args.out)
        print(f"  wrote {args.out}")
        if result.feedback:
            fb = args.out.with_name(args.out.stem + "-feedback.json")
            documents_to_json(result.feedback, fb)
            print(f"  wrote {fb}")

    if args.verify:
        return report_verify(result.documents)
    return 0 if result.documents and not result.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
