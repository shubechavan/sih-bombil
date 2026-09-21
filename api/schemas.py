"""schemas.py — the wire shapes, and the one invariant they protect.

Every persona-pair score in this system has four components, and each of them is
either a number or the fact that it could not be assessed. Collapsing the second
case to 0.0 is the single most damaging thing a client could do with this data:
it converts "we did not look" into "we looked and found nothing", which is
exactly the claim Phase 3 spent a module and a counter-experiment disproving.

So `Component` is a tagged union on the wire, not a nullable float. A client
cannot accidentally render a zero, because there is no zero to render — it gets
`measured: false` and a sentence explaining why.

`Identifier.derived` carries the same discipline one level down. The database
holds identifiers from two provenances: values Phase 1's extractor actually
found in prose (`meta.source = "ingest"`), and values the fixture corpus
declares that appear in no bio and no post (`meta.source = "fixtures"` — the six
recorded in docs/BUILD_PLAN.md as unreachable). Both are real, but only the
first is something an extractor could produce, and a link that cites the second
must say so rather than implying the pipeline found it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

__all__ = [
    "ActorDetail",
    "ActorSummary",
    "Component",
    "Components",
    "EvidenceEntry",
    "GraphEdge",
    "GraphNode",
    "GraphPayload",
    "Identifier",
    "LinkSummary",
    "PersonaDetail",
    "PersonaSummary",
    "PostSample",
    "ReconReport",
    "ScanJob",
    "TimelineBucket",
    "TrustEdge",
    "measured",
    "unmeasured",
]


class Component(BaseModel):
    """One term of A = 0.40H + 0.25S + 0.20B + 0.15I.

    Either a value, or a reason there is no value. Never both, never neither.
    """

    measured: bool
    value: Optional[float] = None
    reason: Optional[str] = Field(
        default=None,
        description="Why this component could not be assessed. Present exactly "
                    "when measured is false, and written for a human to read.",
    )
    weight: Optional[float] = Field(
        default=None, description="This component's weight in the active preset."
    )


def measured(value: float, weight: Optional[float] = None) -> Component:
    return Component(measured=True, value=round(float(value), 6), weight=weight)


def unmeasured(reason: str, weight: Optional[float] = None) -> Component:
    return Component(measured=False, reason=reason, weight=weight)


class Components(BaseModel):
    H: Component
    S: Component
    B: Component
    I: Component


class EvidenceEntry(BaseModel):
    """One reason, in the words the pipeline wrote it in.

    `detail` is passed through verbatim. It was composed for an analyst to read
    and must not be reformatted, summarised or truncated on the way to a screen.
    """

    type: str
    detail: str
    weight: Optional[float] = None
    identifier_type: Optional[str] = None
    value: Optional[str] = None
    component: Optional[str] = None
    label: Optional[str] = None
    derived: Optional[bool] = Field(
        default=None,
        description="For identifier evidence: whether this value was extracted "
                    "from prose (true) or only declared by the corpus (false).",
    )
    extra: dict[str, Any] = Field(default_factory=dict)


class Identifier(BaseModel):
    type: str
    value: str
    value_norm: Optional[str] = None
    validated: bool = False
    derived: bool = Field(
        description="True when Phase 1's extractor found this in prose. False "
                    "when the corpus declares it but no bio or post contains "
                    "it — see docs/BUILD_PLAN.md, 'Known corpus gaps'."
    )
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None


class PostSample(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    category: Optional[str] = None
    posted_at: Optional[datetime] = None


class PersonaSummary(BaseModel):
    id: int
    handle: str
    handle_normalized: Optional[str] = None
    source_id: Optional[int] = None
    source_name: Optional[str] = None
    category: Optional[str] = None
    post_count: int = 0
    #: Set when stylometry declined to build a writeprint for this persona.
    #: The UI must show this rather than an empty cell: refusing to score is a
    #: finding, and it is the strongest thing this engine does.
    stylometry_refused: bool = False
    stylometry_refused_reason: Optional[str] = None
    char_count: Optional[int] = None


class LinkSummary(BaseModel):
    persona_a: int
    persona_b: int
    handle_a: Optional[str] = None
    handle_b: Optional[str] = None
    score: float
    band: str
    method: Optional[str] = None
    components: Components
    evidence: list[EvidenceEntry] = Field(default_factory=list)
    computed_at: Optional[datetime] = None


class PersonaDetail(PersonaSummary):
    bio: Optional[str] = None
    identifiers: list[Identifier] = Field(default_factory=list)
    posts: list[PostSample] = Field(default_factory=list)
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None


class ActorSummary(BaseModel):
    id: int
    label: Optional[str] = None
    category: Optional[str] = None
    persona_count: int = 0
    source_count: int = 0
    band: Optional[str] = Field(
        default=None,
        description="Band of the weakest link holding this actor together. "
                    "None for a single-persona actor, which was never merged.",
    )
    confidence: Optional[float] = Field(
        default=None,
        description="The weakest merging link's score. A cluster is a chain; "
                    "the strongest edge would overstate it.",
    )
    identifier_count: int = 0
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    handles: list[str] = Field(default_factory=list)


class ActorDetail(ActorSummary):
    notes: Optional[str] = None
    personas: list[PersonaDetail] = Field(default_factory=list)
    links: list[LinkSummary] = Field(default_factory=list)
    #: Vendors outside this actor who share buyers with it. Relationship
    #: context for an analyst, with no bearing on the actor's confidence.
    trust_edges: list["TrustEdge"] = Field(default_factory=list)
    trust_note: str = ""
    timeline: list["TimelineBucket"] = Field(default_factory=list)


class TrustEdge(BaseModel):
    """Two vendors rated by the same buyers. Context, not a score.

    Deliberately not a `GraphEdge`: it has no `score`, no `band` and no
    `components`, so nothing downstream can mistake it for an attribution edge
    or add it to one. `affects_score` and `note` ride on every row for the same
    reason — a bare overlap number in a payload gets used.
    """

    persona_a: int
    persona_b: int
    handle_a: str
    handle_b: str
    shared_buyers: list[str] = Field(default_factory=list)
    shared_count: int = 0
    buyers_a: int = 0
    buyers_b: int = 0
    overlap: float = 0.0
    detail: str = ""
    affects_score: bool = False
    note: str = ""


class GraphNode(BaseModel):
    id: int
    handle: str
    source_id: Optional[int] = None
    source_name: Optional[str] = None
    category: Optional[str] = None
    actor_id: Optional[int] = None
    stylometry_refused: bool = False


class GraphEdge(BaseModel):
    source: int
    target: int
    score: float
    band: str
    method: Optional[str] = None
    components: Components
    evidence: list[EvidenceEntry] = Field(default_factory=list)


class GraphPayload(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    #: A separate list, not mixed into `edges`, so a client has to opt into
    #: drawing them and cannot sum them into a score by accident.
    trust_edges: list[TrustEdge] = Field(default_factory=list)
    trust_note: str = ""
    min_score: float
    note: str


class TimelineBucket(BaseModel):
    bucket: str
    posts: int = 0
    personas: int = 0


class ReconReport(BaseModel):
    onion_url: str
    source_id: Optional[int] = None
    source_name: Optional[str] = None
    server_banner: Optional[str] = None
    powered_by: Optional[str] = None
    etag: Optional[str] = None
    favicon_hash: Optional[str] = None
    status_exposed: bool = False
    default_page: bool = False
    dir_listing: bool = False
    tls_subject: Optional[str] = None
    tls_issuer: Optional[str] = None
    tls_serial: Optional[str] = None
    tls_sans: list[str] = Field(default_factory=list)
    robots_txt: Optional[str] = None
    sitemap_xml: Optional[str] = None
    html_comments: list[str] = Field(default_factory=list)
    generator_meta: Optional[str] = None
    clearnet_refs: list[str] = Field(default_factory=list)
    headers: dict[str, Any] = Field(default_factory=dict)
    header_order: list[str] = Field(default_factory=list)
    misconfig_score: Optional[float] = None
    scanned_at: Optional[datetime] = None
    correlations: list["Correlation"] = Field(default_factory=list)
    attribution_note: str = Field(
        default="",
        description="Why these findings do not feed the I term for any persona "
                    "pair on this corpus.",
    )


class Correlation(BaseModel):
    clearnet_host: str
    clearnet_ip: Optional[str] = None
    clearnet_port: Optional[int] = None
    match_type: str
    score: float
    provider: Optional[str] = None
    evidence: list[EvidenceEntry] = Field(default_factory=list)
    observed_at: Optional[datetime] = None


class ScanJob(BaseModel):
    job_id: str
    status: Literal["queued", "running", "ok", "failed"]
    stage: Optional[str] = None
    steps: list[str] = Field(default_factory=list)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    scan_ids: list[int] = Field(default_factory=list)


ActorDetail.model_rebuild()
ReconReport.model_rebuild()
