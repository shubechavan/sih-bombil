"""report.py — the PDF case report.

    python -m export.report --actor 1 --out case.pdf
    python -m export.report --all --out cases.pdf

Sections, in order: a cover carrying the operator and the generated-at stamp, a
methodology note, the actor profile, the identifiers table, the link graph, the
evidence for every link that merged the actor, and a closing scope statement.

THREE THINGS THIS FILE REFUSES TO DO
------------------------------------
**Print a number for a component that was not measured.** `_component_rows`
renders an unmeasured term as the word "not assessed" and the pipeline's own
sentence explaining why. On this corpus that is the I term on every link, and
it is the most defensible claim in the report — a `0.00` there would assert the
opposite of what the engine decided. api/schemas.py enforces the same thing on
the wire; this enforces it on paper.

**Let a reader mistake the evaluation for real-world accuracy.** The cover and
the closing section both carry the synthetic-corpus caveat, in the same words
`scripts/evaluate.py` prints. A figure quoted out of a PDF three months from now
has to arrive with that sentence attached.

**Present a lead as a conclusion.** CLAUDE.md is explicit that outputs are
investigative leads requiring corroboration. The methodology note says so, in
the report, where an analyst will actually read it.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_JUSTIFY  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    HRFlowable,
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from db import utcnow  # noqa: E402

__all__ = ["CAVEAT", "METHODOLOGY", "build_report", "render_actor"]

# ─────────────────────────────────────────────────────────────────────────────
# Fixed text
# ─────────────────────────────────────────────────────────────────────────────

#: The same sentence scripts/evaluate.py closes with. Kept word-for-word so a
#: figure quoted from the PDF and one quoted from the terminal carry the same
#: qualification.
CAVEAT = (
    "Figures in this report are measured on a synthetic corpus with known "
    "ground truth (fixtures/ground_truth.json). They measure this engine "
    "against that answer key, not real-world accuracy."
)

METHODOLOGY = [
    (
        "Scoring",
        "Each persona pair is scored A = w<sub>H</sub>·H + w<sub>S</sub>·S + "
        "w<sub>B</sub>·B + w<sub>I</sub>·I, with the active weights printed "
        "beside every link below. H is hard identifier overlap combined by "
        "noisy-OR, S is the cosine between writeprint vectors, B is "
        "behavioural overlap dominated by the posting-hour histogram, and I is "
        "infrastructure overlap between hosts a persona controls.",
    ),
    (
        "Unmeasured components",
        "A component that could not be assessed is <b>not</b> scored as zero. "
        "Its weight is redistributed over the components that were measured, "
        "and the reason is recorded with the link. Where this report says "
        "“not assessed” it means the system declined to measure, not "
        "that it measured nothing in common.",
    ),
    (
        "Refusals",
        "Stylometry does not run below 300 characters of prose, wallets that "
        "fail their checksum are dropped rather than stored, and clustering "
        "merges two personas only on a link at or above the PROBABLE floor. "
        "Refusals are reported rather than hidden.",
    ),
    (
        "Standing of these findings",
        "Every figure here is an <b>investigative lead requiring "
        "corroboration, never a conclusion</b>. Attribution is probabilistic; "
        "the confidence band states how probabilistic. Every action that "
        "produced this report is recorded in the <font face='Courier'>scans"
        "</font> audit table with an operator identity and a SHA-256 of the "
        "action.",
    ),
]

#: One source of truth for the band colours. Kept as plain hex because both
#: reportlab and matplotlib consume them, and each wants a different object.
BAND_HEX = {
    "CONFIRMED": "#b3261e",
    "PROBABLE": "#c05621",
    "POSSIBLE": "#8a6d1f",
    "WEAK": "#2f6b3a",
}
BAND_COLOR = {band: colors.HexColor(hexval) for band, hexval in BAND_HEX.items()}

INK = colors.HexColor("#1b1f27")
MUTED = colors.HexColor("#5b6472")
RULE = colors.HexColor("#c8cdd6")
FLAG = colors.HexColor("#8a4b12")


def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontSize=20,
                                textColor=INK, spaceAfter=2 * mm),
        "h1": ParagraphStyle("h1", parent=base["Heading1"], fontSize=13,
                             textColor=INK, spaceBefore=6 * mm, spaceAfter=2 * mm),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=10.5,
                             textColor=INK, spaceBefore=3 * mm, spaceAfter=1 * mm),
        "body": ParagraphStyle("b", parent=base["BodyText"], fontSize=9,
                               leading=13, textColor=INK, alignment=TA_JUSTIFY),
        "small": ParagraphStyle("s", parent=base["BodyText"], fontSize=7.8,
                                leading=11, textColor=MUTED),
        "mono": ParagraphStyle("m", parent=base["BodyText"], fontName="Courier",
                               fontSize=7.5, leading=10, textColor=INK),
        "cell": ParagraphStyle("c", parent=base["BodyText"], fontSize=7.8,
                               leading=10.5, textColor=INK),
        "flag": ParagraphStyle("f", parent=base["BodyText"], fontSize=7.8,
                               leading=10.5, textColor=FLAG),
    }


def _esc(value) -> str:
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _fmt_date(value) -> str:
    if not value:
        return "—"
    if isinstance(value, str):
        return value[:19].replace("T", " ")
    return value.strftime("%Y-%m-%d %H:%M")


# ─────────────────────────────────────────────────────────────────────────────
# The link graph image
# ─────────────────────────────────────────────────────────────────────────────

def graph_image(personas: Sequence[dict], links: Sequence[dict],
                width_px: int = 1400) -> Optional[io.BytesIO]:
    """Render the actor's personas and the links between them to a PNG.

    Laid out with networkx's spring layout, drawn with matplotlib on the Agg
    backend so it works headless. Returns None when there is nothing to draw —
    a single-persona actor gets no picture rather than an empty box.
    """
    if len(personas) < 2 or not links:
        return None
    try:
        import matplotlib  # noqa: PLC0415
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: PLC0415
        import networkx as nx  # noqa: PLC0415
    except ImportError:
        return None

    graph = nx.Graph()
    labels = {}
    for persona in personas:
        graph.add_node(persona["id"])
        labels[persona["id"]] = persona["handle"]
    for link in links:
        graph.add_edge(link["persona_a"], link["persona_b"],
                       score=link["score"], band=link["band"])

    layout = nx.spring_layout(graph, seed=1337, k=1.4)
    figure, axis = plt.subplots(figsize=(width_px / 160, width_px / 260), dpi=160)
    nx.draw_networkx_nodes(graph, layout, ax=axis, node_size=1500,
                           node_color="#e8eef7", edgecolors="#3c5a80",
                           linewidths=1.4)
    for a, b, data in graph.edges(data=True):
        nx.draw_networkx_edges(
            graph, layout, ax=axis, edgelist=[(a, b)],
            width=1 + 5 * data["score"],
            edge_color=BAND_HEX.get(data["band"], "#888888"),
        )
    nx.draw_networkx_labels(graph, layout, labels, ax=axis, font_size=8,
                            font_family="DejaVu Sans")
    for (a, b, data) in graph.edges(data=True):
        x = (layout[a][0] + layout[b][0]) / 2
        y = (layout[a][1] + layout[b][1]) / 2
        axis.text(x, y, f"{data['score']:.3f}", fontsize=7, ha="center",
                  va="center", color="#333333",
                  bbox={"facecolor": "white", "edgecolor": "none", "pad": 1.2})
    axis.set_axis_off()
    figure.tight_layout(pad=0.2)

    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", bbox_inches="tight", facecolor="white")
    plt.close(figure)
    buffer.seek(0)
    return buffer


# ─────────────────────────────────────────────────────────────────────────────
# Sections
# ─────────────────────────────────────────────────────────────────────────────

def _component_rows(components: dict, s) -> list[list]:
    """H/S/B/I as table rows. An unmeasured term gets words, never a figure."""
    rows = [[Paragraph("<b>Term</b>", s["cell"]),
             Paragraph("<b>Weight</b>", s["cell"]),
             Paragraph("<b>Value</b>", s["cell"]),
             Paragraph("<b>Basis</b>", s["cell"])]]
    labels = {"H": "hard identifiers", "S": "stylometry",
              "B": "behaviour", "I": "infrastructure"}
    for key in ("H", "S", "B", "I"):
        component = components.get(key) or {}
        weight = component.get("weight")
        weight_text = "—" if weight is None else f"×{weight:.2f}"
        if component.get("measured"):
            value = Paragraph(f"<b>{component['value']:.3f}</b>", s["cell"])
            basis = Paragraph(_esc(labels[key]), s["cell"])
        else:
            # The point of the whole exercise: words, not a zero.
            value = Paragraph("<b>not assessed</b>", s["flag"])
            basis = Paragraph(_esc(component.get("reason") or
                                   "no reason recorded"), s["flag"])
        rows.append([Paragraph(f"<b>{key}</b>", s["cell"]),
                     Paragraph(weight_text, s["cell"]), value, basis])
    return rows


def _table(rows, widths, s, zebra: bool = True) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.7, RULE),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
    ]
    if zebra:
        for index in range(1, len(rows)):
            if index % 2 == 0:
                style.append(("BACKGROUND", (0, index), (-1, index),
                              colors.HexColor("#f6f8fb")))
    table.setStyle(TableStyle(style))
    return table


def render_actor(actor: dict, s, *, page_width: float) -> list:
    """Every flowable for one actor."""
    flow: list = []
    band = actor.get("band")
    confidence = actor.get("confidence")

    flow.append(Paragraph(f"Actor {actor['id']} — {_esc(actor.get('label') or '')}",
                          s["h1"]))

    band_text = (
        f"<font color='{BAND_HEX.get(band, '#5b6472')}'><b>{band}</b></font>"
        f" &nbsp;{confidence:.3f}" if band else
        "<b>NOT MERGED</b> — a single persona; no link reached the clustering "
        "threshold, so nothing was compared and there is no band"
    )
    flow.append(Paragraph(band_text, s["body"]))
    flow.append(Spacer(1, 2 * mm))

    profile = [
        ["Personas", str(actor.get("persona_count", 0))],
        ["Sources", str(actor.get("source_count", 0))],
        ["Category", actor.get("category") or "—"],
        ["First seen", _fmt_date(actor.get("first_seen"))],
        ["Last seen", _fmt_date(actor.get("last_seen"))],
        ["Handles", ", ".join(actor.get("handles") or []) or "—"],
    ]
    if actor.get("confidence") is not None:
        profile.insert(0, ["Confidence", f"{actor['confidence']:.3f} "
                                         f"(the weakest merging link)"])
    flow.append(_table(
        [[Paragraph(f"<b>{_esc(k)}</b>", s["cell"]), Paragraph(_esc(v), s["cell"])]
         for k, v in profile],
        [35 * mm, page_width - 35 * mm], s, zebra=False))

    if actor.get("notes"):
        flow.append(Spacer(1, 1.5 * mm))
        flow.append(Paragraph(_esc(actor["notes"]), s["small"]))

    # ── identifiers ─────────────────────────────────────────────────────────
    identifiers = [
        (p["handle"], i) for p in actor.get("personas", [])
        for i in p.get("identifiers", [])
    ]
    flow.append(Paragraph("Identifiers", s["h2"]))
    if identifiers:
        rows = [[Paragraph("<b>Persona</b>", s["cell"]),
                 Paragraph("<b>Type</b>", s["cell"]),
                 Paragraph("<b>Value</b>", s["cell"]),
                 Paragraph("<b>Provenance</b>", s["cell"])]]
        for handle, identifier in identifiers:
            derived = identifier.get("derived", True)
            rows.append([
                Paragraph(_esc(handle), s["cell"]),
                Paragraph(_esc(identifier["type"]), s["cell"]),
                Paragraph(_esc(identifier["value"]), s["mono"]),
                Paragraph(
                    "extracted from prose" if derived else
                    "declared only — found in no bio or post",
                    s["cell"] if derived else s["flag"]),
            ])
        flow.append(_table(rows, [24 * mm, 20 * mm, page_width - 84 * mm, 40 * mm], s))
    else:
        flow.append(Paragraph("No identifiers were extracted for this actor.",
                              s["small"]))

    # ── refusals ────────────────────────────────────────────────────────────
    refused = [p for p in actor.get("personas", []) if p.get("stylometry_refused")]
    if refused:
        flow.append(Paragraph("Refusals", s["h2"]))
        for persona in refused:
            flow.append(Paragraph(
                f"<b>{_esc(persona['handle'])}</b> — "
                f"{_esc(persona.get('stylometry_refused_reason') or 'stylometry declined')}",
                s["flag"]))
            flow.append(Paragraph(
                "Every pair this persona appears in is scored with S unmeasured "
                "and its weight redistributed, rather than with a number derived "
                "from too little text.", s["small"]))

    # ── graph ───────────────────────────────────────────────────────────────
    links = actor.get("links") or []
    picture = graph_image(actor.get("personas", []), links)
    if picture is not None:
        flow.append(Paragraph("Link graph", s["h2"]))
        image = Image(picture)
        scale = min(1.0, (page_width) / image.drawWidth)
        image.drawWidth *= scale
        image.drawHeight *= scale
        flow.append(image)
        flow.append(Paragraph(
            "Edge thickness is the attribution score; colour is the confidence "
            "band. Nodes are personas, not actors.", s["small"]))

    # ── evidence ────────────────────────────────────────────────────────────
    flow.append(Paragraph("Evidence", s["h2"]))
    if not links:
        flow.append(Paragraph(
            "This actor is a single persona. No link reached the clustering "
            "threshold, so there is no merging evidence and no confidence band.",
            s["body"]))
    for link in links:
        block: list = [Paragraph(
            f"<b>{_esc(link.get('handle_a'))} ~ {_esc(link.get('handle_b'))}</b> "
            f"&nbsp; <font color='{BAND_HEX.get(link['band'], '#5b6472')}'>"
            f"<b>{link['band']}</b></font> {link['score']:.3f}", s["body"])]
        block.append(Spacer(1, 1.5 * mm))
        block.append(_table(
            _component_rows(link.get("components") or {}, s),
            [12 * mm, 16 * mm, 22 * mm, page_width - 50 * mm], s))
        block.append(Spacer(1, 1.5 * mm))

        reasons = [
            e for e in (link.get("evidence") or [])
            if e.get("type") not in {"component_breakdown", "component_not_assessed"}
        ]
        if reasons:
            rows = [[Paragraph("<b>Kind</b>", s["cell"]),
                     Paragraph("<b>Reason</b>", s["cell"])]]
            for entry in reasons:
                detail = _esc(entry.get("detail") or "")
                if entry.get("derived") is False:
                    detail += ("<br/><font color='#8a4b12'>declared only — this "
                               "value appears in no bio or post</font>")
                rows.append([Paragraph(_esc(entry.get("type")), s["cell"]),
                             Paragraph(detail, s["cell"])])
            block.append(_table(rows, [30 * mm, page_width - 30 * mm], s))
        flow.append(KeepTogether(block))
        flow.append(Spacer(1, 3 * mm))

    return flow


def _cover(actors: Sequence[dict], operator: str, generated: datetime,
           s, page_width: float) -> list:
    flow = [
        Paragraph("Dark Sentinel v2", s["title"]),
        Paragraph("Actor attribution case report", s["h2"]),
        HRFlowable(width="100%", color=RULE, spaceBefore=2 * mm, spaceAfter=4 * mm),
    ]
    meta = [
        ["Generated at", generated.strftime("%Y-%m-%d %H:%M:%S UTC")],
        ["Operator", operator],
        ["Actors in this report", str(len(actors))],
        ["Personas covered", str(sum(a.get("persona_count", 0) for a in actors))],
        ["Classification", "Authorized investigative use only"],
    ]
    flow.append(_table(
        [[Paragraph(f"<b>{_esc(k)}</b>", s["cell"]), Paragraph(_esc(v), s["cell"])]
         for k, v in meta],
        [45 * mm, page_width - 45 * mm], s, zebra=False))
    flow.append(Spacer(1, 4 * mm))
    flow.append(Paragraph(f"<b>Scope.</b> {_esc(CAVEAT)}", s["body"]))
    flow.append(Spacer(1, 2 * mm))
    flow.append(Paragraph(
        "<b>Standing.</b> Every finding below is an investigative lead requiring "
        "corroboration, never a conclusion.", s["body"]))

    flow.append(Paragraph("Methodology", s["h1"]))
    for heading, text in METHODOLOGY:
        flow.append(Paragraph(f"<b>{_esc(heading)}.</b> {text}", s["body"]))
        flow.append(Spacer(1, 1.5 * mm))
    return flow


def build_report(actors: Sequence[dict], *, operator: str,
                 generated: Optional[datetime] = None) -> bytes:
    """Render the case report. Returns PDF bytes.

    `actors` are ActorDetail dicts exactly as api/routers/actors.py serves them,
    so the report and the screen cannot drift: both read the same Component
    shape, where an unmeasured term carries a reason and no value.
    """
    generated = generated or utcnow()
    s = _styles()
    buffer = io.BytesIO()
    margin = 16 * mm
    page_width = A4[0] - 2 * margin

    document = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=margin, rightMargin=margin,
        topMargin=14 * mm, bottomMargin=16 * mm,
        title="Dark Sentinel v2 — case report",
        author=operator,
        subject="Actor attribution; investigative leads requiring corroboration",
    )

    flow = _cover(actors, operator, generated, s, page_width)
    for actor in actors:
        flow.append(PageBreak())
        flow.extend(render_actor(actor, s, page_width=page_width))

    flow.append(PageBreak())
    flow.append(Paragraph("Scope of these figures", s["h1"]))
    flow.append(Paragraph(_esc(CAVEAT), s["body"]))
    flow.append(Spacer(1, 2 * mm))
    flow.append(Paragraph(
        "Outputs are investigative leads requiring corroboration, never "
        "conclusions. All reconnaissance is passive: the system reads what a "
        "server already publishes and performs no exploitation, authentication "
        "bypass, brute force or credential use.", s["body"]))

    def footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(margin, 9 * mm,
                          f"Dark Sentinel v2 — {operator} — "
                          f"{generated.strftime('%Y-%m-%d %H:%M:%S UTC')} — "
                          f"investigative leads requiring corroboration")
        canvas.drawRightString(A4[0] - margin, 9 * mm, f"page {doc.page}")
        canvas.restoreState()

    document.build(flow, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def _load_actors(actor_ids: Optional[Iterable[int]]) -> list[dict]:
    """Read ActorDetail payloads straight from the API's own router layer."""
    from api.deps import get_session  # noqa: PLC0415
    from api.routers.actors import (  # noqa: PLC0415
        collect_actor_detail,
        collect_actors,
    )

    generator = get_session()
    session = next(generator)
    try:
        wanted = (list(actor_ids) if actor_ids is not None
                  else [a.id for a in collect_actors(session, limit=1000)])
        return [collect_actor_detail(session, actor_id).model_dump()
                for actor_id in wanted]
    finally:
        generator.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Render a PDF case report.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--actor", type=int, action="append",
                        help="actor id; repeatable")
    target.add_argument("--all", action="store_true",
                        help="every actor in the database")
    parser.add_argument("--out", type=Path, default=Path("case-report.pdf"))
    parser.add_argument("--operator", default=None,
                        help="printed on every page (default: $OPERATOR_ID)")
    args = parser.parse_args(argv)

    operator = args.operator or os.environ.get("OPERATOR_ID") or "unknown"
    actors = _load_actors(None if args.all else args.actor)
    if not actors:
        print("no actors to report — run `python -m link.cluster --source db`",
              file=sys.stderr)
        return 1

    args.out.write_bytes(build_report(actors, operator=operator))
    size = args.out.stat().st_size
    print(f"  wrote {args.out} ({size:,} bytes) covering {len(actors)} actor(s)")
    print(f"  operator {operator}; every page carries the synthetic-corpus caveat")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
