"""
db.py — SQLAlchemy 2.x engine, session factory and ORM models for Dark Sentinel v2.

`schema_v2.sql` is the single source of DDL. The models below mirror it; they must
never create it. There is deliberately no `Base.metadata.create_all()` call in any
normal code path — if the two ever drift, the SQL file wins and the models get
corrected. `require_schema()` exists to fail loudly when the schema has not been
applied at all.

All TIMESTAMP columns store naive UTC. Use `utcnow()`, never `datetime.now()`.

Usage:
    from db import session_scope, Persona

    with session_scope() as s:
        persona = s.get(Persona, 1)
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from dotenv import load_dotenv
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# Shared vocabulary
#
# Other phases import these instead of re-typing the strings. The same values are
# enforced as CHECK constraints in schema_v2.sql section 3.
# ─────────────────────────────────────────────────────────────────────────────

IDENTIFIER_TYPES: frozenset[str] = frozenset({
    "pgp_fpr", "btc", "eth", "xmr", "ltc",
    "email", "jabber", "session", "telegram", "onion_mirror",
})

#: Hard-identifier weights for the H term of the attribution formula (CLAUDE.md).
#: Phase 2's score/attribution.py owns the formula; this is the shared table so
#: the weights are stated in exactly one place.
IDENTIFIER_WEIGHTS: dict[str, float] = {
    "pgp_fpr":      1.00,
    "btc":          0.90,
    "eth":          0.90,
    "xmr":          0.90,
    "ltc":          0.90,
    "email":        0.85,
    "jabber":       0.85,
    "session":      0.85,
    "telegram":     0.85,
    "onion_mirror": 0.80,
}

BANDS: tuple[str, ...] = ("CONFIRMED", "PROBABLE", "POSSIBLE", "WEAK")

#: Inclusive lower bound for each band. CONFIRMED >=0.85, PROBABLE 0.65-0.85,
#: POSSIBLE 0.45-0.65, WEAK <0.45.
BAND_THRESHOLDS: tuple[tuple[str, float], ...] = (
    ("CONFIRMED", 0.85),
    ("PROBABLE",  0.65),
    ("POSSIBLE",  0.45),
    ("WEAK",      0.00),
)

#: The 300-character floor below which stylometry must not run (CLAUDE.md).
MIN_STYLOMETRY_CHARS = 300

#: Every table schema_v2.sql is responsible for. Used by require_schema().
REQUIRED_TABLES: tuple[str, ...] = (
    "sources", "actors", "personas", "identifiers", "persona_identifiers",
    "posts", "writeprints", "writeprint_vocab", "links", "infra_findings",
    "infra_correlations", "feedback", "scans", "users", "audit_log",
)


def band_for(score: float) -> str:
    """Map an attribution score to its confidence band."""
    for band, floor in BAND_THRESHOLDS:
        if score >= floor:
            return band
    return "WEAK"


def utcnow() -> datetime:
    """Naive UTC timestamp — the only time source any writer should use.

    Naive rather than aware because every TIMESTAMP column in schema_v2.sql is
    `TIMESTAMP WITHOUT TIME ZONE` holding UTC; see the schema header for why.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ─────────────────────────────────────────────────────────────────────────────
# Connection
# ─────────────────────────────────────────────────────────────────────────────

def pg_url(driver: bool = True) -> str:
    """Build the Postgres URL from the environment.

    Honours a full `PG_URL` override so the same variable serves both
    `psql $PG_URL` and SQLAlchemy; a bare `postgresql://` scheme is rewritten to
    `postgresql+psycopg2://` when a driver-qualified URL is wanted.

    Args:
        driver: include the `+psycopg2` driver qualifier. Pass False for a URL
            you intend to hand to `psql`.
    """
    override = os.environ.get("PG_URL", "").strip()
    if override:
        url = override
        if driver and url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
        elif not driver:
            url = url.replace("postgresql+psycopg2://", "postgresql://", 1)
        return url

    host = os.environ.get("PG_HOST", "localhost")
    port = os.environ.get("PG_PORT", "5432")
    name = os.environ.get("PG_DB", "darksentinel")
    user = os.environ.get("PG_USER", "postgres")
    pw = os.environ.get("PG_PASSWORD", "")

    from urllib.parse import quote_plus

    auth = quote_plus(user)
    if pw:
        auth += f":{quote_plus(pw)}"
    scheme = "postgresql+psycopg2" if driver else "postgresql"
    return f"{scheme}://{auth}@{host}:{port}/{name}"


def make_engine(echo: bool = False) -> Engine:
    """Create an Engine. Constructing one does not open a connection."""
    return create_engine(
        pg_url(),
        echo=echo,
        pool_pre_ping=True,
        future=True,
    )


engine: Engine = make_engine(echo=os.environ.get("SQL_ECHO", "").lower() in {"1", "true", "yes"})

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope. Commits on success, rolls back on any exception."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class SchemaNotApplied(RuntimeError):
    """Raised when the v2 tables are missing from the target database."""


def require_schema(session: Session) -> None:
    """Fail loudly and usefully if schema_v2.sql has not been applied."""
    found = {
        row[0]
        for row in session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = ANY(:names)"
            ),
            {"names": list(REQUIRED_TABLES)},
        )
    }
    missing = sorted(set(REQUIRED_TABLES) - found)
    if missing:
        raise SchemaNotApplied(
            f"missing table(s): {', '.join(missing)}\n"
            f"apply the schema first:  psql $PG_URL -f schema_v2.sql\n"
            f"(or: docker compose exec -T db psql -U $PG_USER -d $PG_DB < schema_v2.sql)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Models — one per table in schema_v2.sql, in the same order
# ─────────────────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class Source(Base):
    """A site we touch — an onion marketplace, forum, or clearnet property."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    kind: Mapped[Optional[str]] = mapped_column(Text)  # market | forum | paste | deepweb
    is_onion: Mapped[Optional[bool]] = mapped_column(Boolean, default=True)
    reliability: Mapped[Optional[float]] = mapped_column(Float, default=0.5)
    first_seen: Mapped[Optional[datetime]] = mapped_column(DateTime, default=utcnow)
    last_scan_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    active: Mapped[Optional[bool]] = mapped_column(Boolean, default=True)

    personas: Mapped[list["Persona"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Source {self.id} {self.name!r}>"


class Actor(Base):
    """A resolved entity. One actor owns one or more personas.

    Written by Phase 2's resolver, never by the fixture loader — pre-seeding this
    table would hand the answer to the engine that is supposed to derive it.
    """

    __tablename__ = "actors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[Optional[str]] = mapped_column(Text)
    category: Mapped[Optional[str]] = mapped_column(Text)
    first_seen: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_seen: Mapped[Optional[datetime]] = mapped_column(DateTime)
    #: The weakest link holding the cluster together, and NULL for a
    #: single-persona actor that was never merged. No default — see Link.
    max_confidence: Mapped[Optional[float]] = mapped_column(Float)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=utcnow)

    personas: Mapped[list["Persona"]] = relationship(back_populates="actor")

    def __repr__(self) -> str:
        return f"<Actor {self.id} {self.label!r}>"


class Persona(Base):
    """One handle on one source. The raw observation."""

    __tablename__ = "personas"
    __table_args__ = (UniqueConstraint("source_id", "handle", name="personas_source_id_handle_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("actors.id", ondelete="SET NULL")
    )
    handle: Mapped[str] = mapped_column(Text, nullable=False)
    handle_normalized: Mapped[str] = mapped_column(Text, nullable=False)
    profile_url: Mapped[Optional[str]] = mapped_column(Text)
    bio: Mapped[Optional[str]] = mapped_column(Text)
    category: Mapped[Optional[str]] = mapped_column(Text)
    trust_score: Mapped[Optional[float]] = mapped_column(Float)
    post_count: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    first_seen: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_seen: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_scan_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    source: Mapped["Source"] = relationship(back_populates="personas")
    actor: Mapped[Optional["Actor"]] = relationship(back_populates="personas")
    posts: Mapped[list["Post"]] = relationship(
        back_populates="persona", cascade="all, delete-orphan"
    )
    identifier_links: Mapped[list["PersonaIdentifier"]] = relationship(
        back_populates="persona", cascade="all, delete-orphan"
    )
    writeprint: Mapped[Optional["Writeprint"]] = relationship(
        back_populates="persona", uselist=False, cascade="all, delete-orphan"
    )
    #: Convenience read-only view through the association object.
    identifiers: Mapped[list["Identifier"]] = relationship(
        secondary="persona_identifiers", viewonly=True
    )

    def __repr__(self) -> str:
        return f"<Persona {self.id} {self.handle!r} src={self.source_id}>"


class Identifier(Base):
    """A hard identifier — PGP fingerprint, wallet, contact address, mirror onion.

    `validated` records whether the value passed its checksum. Values that fail a
    checksum must be dropped rather than stored (CLAUDE.md), so in practice this
    is True for every wallet row; it stays on the model because Phase 1 needs to
    distinguish "checked and passed" from "no checksum defined for this type".
    """

    __tablename__ = "identifiers"
    __table_args__ = (
        UniqueConstraint("type", "value", name="identifiers_type_value_key"),
        CheckConstraint(
            "type IN ('pgp_fpr','btc','eth','xmr','ltc','email','jabber','session',"
            "'telegram','onion_mirror')",
            name="ck_identifiers_type",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    value_norm: Mapped[Optional[str]] = mapped_column(Text)
    validated: Mapped[Optional[bool]] = mapped_column(Boolean, default=False)
    meta: Mapped[Optional[dict]] = mapped_column(JSONB)
    first_seen: Mapped[Optional[datetime]] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[Optional[datetime]] = mapped_column(DateTime, default=utcnow)

    persona_links: Mapped[list["PersonaIdentifier"]] = relationship(
        back_populates="identifier", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Identifier {self.id} {self.type}={self.value!r}>"


class PersonaIdentifier(Base):
    """persona ↔ identifier join.

    An association object rather than a plain `secondary=` table because it
    carries its own payload: where the identifier was seen and how confident the
    extractor was.
    """

    __tablename__ = "persona_identifiers"

    persona_id: Mapped[int] = mapped_column(
        ForeignKey("personas.id", ondelete="CASCADE"), primary_key=True
    )
    identifier_id: Mapped[int] = mapped_column(
        ForeignKey("identifiers.id", ondelete="CASCADE"), primary_key=True
    )
    context: Mapped[Optional[str]] = mapped_column(Text)
    confidence: Mapped[Optional[float]] = mapped_column(Float, default=1.0)
    observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=utcnow)

    persona: Mapped["Persona"] = relationship(back_populates="identifier_links")
    identifier: Mapped["Identifier"] = relationship(back_populates="persona_links")

    def __repr__(self) -> str:
        return f"<PersonaIdentifier p={self.persona_id} i={self.identifier_id}>"


class Post(Base):
    """A unit of text attributed to a persona. The stylometry corpus."""

    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    persona_id: Mapped[int] = mapped_column(
        ForeignKey("personas.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE")
    )
    url: Mapped[Optional[str]] = mapped_column(Text)
    title: Mapped[Optional[str]] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    body_hash: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[Optional[str]] = mapped_column(Text)
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    scraped_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=utcnow)

    persona: Mapped["Persona"] = relationship(back_populates="posts")

    def __repr__(self) -> str:
        return f"<Post {self.id} p={self.persona_id} {(self.title or '')[:30]!r}>"


class Writeprint(Base):
    """Cached stylometric feature vector for one persona.

    `feature_version` guards the cache: a vector computed by an older extractor
    must not be compared against one from a newer extractor.
    """

    __tablename__ = "writeprints"

    persona_id: Mapped[int] = mapped_column(
        ForeignKey("personas.id", ondelete="CASCADE"), primary_key=True
    )
    char_count: Mapped[Optional[int]] = mapped_column(Integer)
    vector: Mapped[Optional[bytes]] = mapped_column(LargeBinary)
    features: Mapped[Optional[dict]] = mapped_column(JSONB)
    hour_hist: Mapped[Optional[list]] = mapped_column(JSONB)
    feature_version: Mapped[Optional[str]] = mapped_column(Text)
    #: Set when the persona was below the stylometry floor: `vector` is NULL,
    #: `char_count` is what was actually available, and this says why no
    #: writeprint was built. A refusal is a finding, not an absence of data.
    refused_reason: Mapped[Optional[str]] = mapped_column(Text)
    computed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=utcnow)

    persona: Mapped["Persona"] = relationship(back_populates="writeprint")

    @property
    def refused(self) -> bool:
        return self.vector is None

    def __repr__(self) -> str:
        state = "REFUSED" if self.refused else f"chars={self.char_count}"
        return f"<Writeprint p={self.persona_id} {state}>"


class WriteprintVocab(Base):
    """The fitted TF-IDF vocabulary the writeprints of one version were built with.

    Stored so a text that was not in the corpus — pasted into /analyze after the
    fact — can be projected into the same space by `transform` alone. Refitting
    with the new text included would change `feature_version` and invalidate
    every cached vector, which is the opposite of what the caller wants.

    `terms` is ordered: a term's position in the list is its column index, and
    `idf` is aligned to it. The two are written and read together and must never
    be edited apart.
    """

    __tablename__ = "writeprint_vocab"

    feature_version: Mapped[str] = mapped_column(Text, primary_key=True)
    terms: Mapped[Optional[list]] = mapped_column(JSONB)
    idf: Mapped[Optional[bytes]] = mapped_column(LargeBinary)
    n_features: Mapped[Optional[int]] = mapped_column(Integer)
    #: scikit-learn and numpy versions that produced this fit. See the column
    #: note in schema_v2.sql: feature_version identifies the inputs, this
    #: identifies the code, and a vocabulary is only usable when both agree.
    toolchain: Mapped[Optional[str]] = mapped_column(Text)
    built_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=utcnow)

    def __repr__(self) -> str:
        return f"<WriteprintVocab {self.feature_version} n={self.n_features}>"


class Link(Base):
    """A scored persona↔persona edge.

    Pairs are stored once with `persona_a < persona_b`. `evidence` is never empty:
    a score with no reasons is not shippable (CLAUDE.md).
    """

    __tablename__ = "links"
    __table_args__ = (
        UniqueConstraint("persona_a", "persona_b", name="links_persona_a_persona_b_key"),
        CheckConstraint("persona_a < persona_b", name="ck_links_pair_order"),
        CheckConstraint("band IN ('CONFIRMED','PROBABLE','POSSIBLE','WEAK')", name="ck_links_band"),
        CheckConstraint("score >= 0 AND score <= 1", name="ck_links_score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    persona_a: Mapped[int] = mapped_column(
        ForeignKey("personas.id", ondelete="CASCADE"), nullable=False
    )
    persona_b: Mapped[int] = mapped_column(
        ForeignKey("personas.id", ondelete="CASCADE"), nullable=False
    )
    score: Mapped[float] = mapped_column(Float, nullable=False)
    band: Mapped[str] = mapped_column(Text, nullable=False)
    # No defaults on any of the four. NULL means "not assessed"; 0.0 means
    # "compared, and they had nothing in common". A default of 0 turned an
    # unmeasured component into a measured zero on the way into the table,
    # contradicting the distinction score/attribution.py exists to protect.
    h_score: Mapped[Optional[float]] = mapped_column(Float)
    s_score: Mapped[Optional[float]] = mapped_column(Float)  # NULL below the char floor
    b_score: Mapped[Optional[float]] = mapped_column(Float)
    i_score: Mapped[Optional[float]] = mapped_column(Float)  # NULL without a controlled host
    evidence: Mapped[list] = mapped_column(JSONB, nullable=False)
    method: Mapped[Optional[str]] = mapped_column(Text)
    reviewed: Mapped[Optional[bool]] = mapped_column(Boolean, default=False)
    analyst_note: Mapped[Optional[str]] = mapped_column(Text)
    computed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=utcnow)

    def __repr__(self) -> str:
        return f"<Link {self.persona_a}~{self.persona_b} {self.band} {self.score:.2f}>"


class InfraFinding(Base):
    """Passive fingerprint of one hidden service.

    Everything here is what the server already publishes to any visitor —
    headers, banners, certificates, favicon, robots.txt. Nothing in this table
    comes from exploitation, authentication or brute force.
    """

    __tablename__ = "infra_findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE")
    )
    onion_url: Mapped[str] = mapped_column(Text, nullable=False)
    server_banner: Mapped[Optional[str]] = mapped_column(Text)
    powered_by: Mapped[Optional[str]] = mapped_column(Text)
    etag: Mapped[Optional[str]] = mapped_column(Text)
    favicon_hash: Mapped[Optional[str]] = mapped_column(Text)
    status_exposed: Mapped[Optional[bool]] = mapped_column(Boolean, default=False)
    default_page: Mapped[Optional[bool]] = mapped_column(Boolean, default=False)
    dir_listing: Mapped[Optional[bool]] = mapped_column(Boolean, default=False)
    tls_subject: Mapped[Optional[str]] = mapped_column(Text)
    tls_issuer: Mapped[Optional[str]] = mapped_column(Text)
    tls_serial: Mapped[Optional[str]] = mapped_column(Text)
    tls_sans: Mapped[Optional[list]] = mapped_column(JSONB)
    tls_not_before: Mapped[Optional[datetime]] = mapped_column(DateTime)
    robots_txt: Mapped[Optional[str]] = mapped_column(Text)
    sitemap_xml: Mapped[Optional[str]] = mapped_column(Text)
    html_comments: Mapped[Optional[list]] = mapped_column(JSONB)
    generator_meta: Mapped[Optional[str]] = mapped_column(Text)
    clearnet_refs: Mapped[Optional[list]] = mapped_column(JSONB)
    headers: Mapped[Optional[dict]] = mapped_column(JSONB)
    #: Header names in the order the server sent them. Stored separately because
    #: JSONB re-sorts object keys, so `headers` alone cannot carry the sequence.
    header_order: Mapped[Optional[list]] = mapped_column(JSONB)
    misconfig_score: Mapped[Optional[float]] = mapped_column(Float, default=0)
    scanned_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=utcnow)

    correlations: Mapped[list["InfraCorrelation"]] = relationship(
        back_populates="finding", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<InfraFinding {self.id} {self.onion_url!r}>"


class InfraCorrelation(Base):
    """A candidate onion↔clearnet match, with the signal that produced it."""

    __tablename__ = "infra_correlations"
    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 1", name="ck_infra_corr_score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    finding_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("infra_findings.id", ondelete="CASCADE")
    )
    onion_url: Mapped[str] = mapped_column(Text, nullable=False)
    clearnet_host: Mapped[str] = mapped_column(Text, nullable=False)
    clearnet_ip: Mapped[Optional[str]] = mapped_column(Text)
    clearnet_port: Mapped[Optional[int]] = mapped_column(Integer)
    match_type: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    evidence: Mapped[Optional[list]] = mapped_column(JSONB)
    provider: Mapped[Optional[str]] = mapped_column(Text)
    #: The hosting entity behind `clearnet_host`. A host name is a label; the
    #: ASN and its registered org are the company that can be served process,
    #: which is what link/leads.py reports as the real-world lead.
    clearnet_asn: Mapped[Optional[str]] = mapped_column(Text)
    clearnet_org: Mapped[Optional[str]] = mapped_column(Text)
    clearnet_country: Mapped[Optional[str]] = mapped_column(Text)
    observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=utcnow)

    finding: Mapped[Optional["InfraFinding"]] = relationship(back_populates="correlations")

    def __repr__(self) -> str:
        return f"<InfraCorrelation {self.onion_url!r}~{self.clearnet_host!r} {self.score:.2f}>"


class Feedback(Base):
    """A buyer's rating of a vendor. Relationship context, not attribution.

    Two things this table deliberately is not.

    It is not `personas`. A buyer is a counterparty; promoting them to a
    persona would add roughly forty handles to the pairwise loop and the engine
    would start proposing that buyers are vendors' alt accounts.

    It is not `links`. Shared-buyer overlap was measured against ground truth
    before this model existed and it separates true pairs from false ones worse
    than chance — ROC-AUC 0.389 over the 78 pairs where the signal exists, mean
    overlap 0.0250 for true pairs against 0.0852 for false ones, and only four
    of the eight true positives are even measurable because the forum sells
    nothing. So it reaches the analyst as context on the graph and the actor
    profile, and reaches `score/` not at all. `python -m link.trust --measure`
    re-runs the whole argument.
    """

    __tablename__ = "feedback"
    __table_args__ = (
        UniqueConstraint("persona_id", "buyer_handle", "posted_at", "body_hash",
                         name="feedback_persona_id_buyer_handle_posted_at_body_hash_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    persona_id: Mapped[int] = mapped_column(
        ForeignKey("personas.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE")
    )
    buyer_handle: Mapped[str] = mapped_column(Text, nullable=False)
    buyer_normalized: Mapped[Optional[str]] = mapped_column(Text)
    rating: Mapped[Optional[int]] = mapped_column(Integer)
    body: Mapped[Optional[str]] = mapped_column(Text)
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    url: Mapped[Optional[str]] = mapped_column(Text)
    body_hash: Mapped[Optional[str]] = mapped_column(Text)
    collected_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=utcnow)

    def __repr__(self) -> str:
        return (f"<Feedback {self.buyer_handle!r}->persona {self.persona_id} "
                f"{self.rating}>")


class Scan(Base):
    """Audit log. Every run that touches data writes one row.

    An attribution claim that cannot be audited is worthless, so `operator_id`
    and `action_hash` (sha256 of the action payload) are not optional in practice.
    """

    __tablename__ = "scans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    operator_id: Mapped[Optional[str]] = mapped_column(Text)
    mode: Mapped[Optional[str]] = mapped_column(Text)  # manual | scheduled
    data_source: Mapped[Optional[str]] = mapped_column(Text)  # fixtures | live
    query: Mapped[Optional[str]] = mapped_column(Text)
    sources_touched: Mapped[Optional[list]] = mapped_column(JSONB)
    personas_new: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    links_new: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    action_hash: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    status: Mapped[Optional[str]] = mapped_column(Text, default="running")
    error: Mapped[Optional[str]] = mapped_column(Text)
    #: Set on rows belonging to an API-triggered scan job: one parent row with
    #: `stage` and `steps`, plus a child row per step that ran. The parent is
    #: job state and survives a restart; the children are the audit trail.
    job_id: Mapped[Optional[str]] = mapped_column(Text)
    stage: Mapped[Optional[str]] = mapped_column(Text)
    steps: Mapped[Optional[list]] = mapped_column(JSONB)

    def __repr__(self) -> str:
        return f"<Scan {self.id} {self.data_source} {self.status}>"


class User(Base):
    """An operator who can log in.

    The role vocabulary is deliberately two words. `analyst` reads, analyses and
    exports; `admin` additionally runs the pipeline and reads the audit log.
    Anything finer would be a permissions system nobody asked for.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, default="analyst")
    disabled: Mapped[Optional[bool]] = mapped_column(Boolean, default=False)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    def __repr__(self) -> str:
        return f"<User {self.username!r} {self.role}>"


class AuditEntry(Base):
    """One authenticated request, including reads.

    Reads are here because for an attribution tool the sensitive act is usually
    looking: which analyst opened which actor profile is precisely what an
    oversight body asks about, and a log that only records writes cannot answer
    it. Kept separate from `scans` because `scans` describes work the pipeline
    did and most of its columns are meaningless for a GET.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    operator_id: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[Optional[str]] = mapped_column(Text)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    query: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[Optional[int]] = mapped_column(Integer)
    action_hash: Mapped[Optional[str]] = mapped_column(Text)
    at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=utcnow)

    def __repr__(self) -> str:
        return f"<AuditEntry {self.operator_id} {self.method} {self.path}>"


def record_scan(
    session,
    *,
    operator: str,
    mode: str,
    data_source: Optional[str] = None,
    query: Optional[str] = None,
    sources_touched: Optional[list] = None,
    action_hash: Optional[str] = None,
    status: str = "running",
    started_at: Optional[datetime] = None,
    job_id: Optional[str] = None,
    stage: Optional[str] = None,
    steps: Optional[list] = None,
) -> Scan:
    """Open an audit row and flush it, so the caller has its id.

    This block was copy-pasted at seven call sites, each slightly different in
    which fields it bothered to set. One helper means one place where a missing
    operator or an unrecorded failure can be caught.

    `job_id` falls back to $SCAN_JOB_ID. The API sets that in the environment of
    the subprocesses it runs, which is how a step launched by `POST /scan` ends
    up tied to the job that launched it without every CLI growing a flag.

    The caller still owns the ending: set `status`, `finished_at` and whatever
    counts it produced, then flush. `finish_scan()` below does the usual case.
    """
    import os  # noqa: PLC0415

    scan = Scan(
        operator_id=operator,
        mode=mode,
        data_source=data_source,
        query=query,
        sources_touched=sources_touched,
        action_hash=action_hash,
        started_at=started_at or utcnow(),
        status=status,
        job_id=job_id or os.environ.get("SCAN_JOB_ID") or None,
        stage=stage,
        steps=steps,
    )
    session.add(scan)
    session.flush()
    return scan


def finish_scan(session, scan: Scan, *, status: str = "ok",
                error: Optional[str] = None, **fields) -> Scan:
    """Close an audit row. `error` is truncated, never dropped."""
    for key, value in fields.items():
        setattr(scan, key, value)
    scan.status = status
    scan.error = str(error)[:2000] if error is not None else None
    scan.finished_at = utcnow()
    session.flush()
    return scan


__all__ = [
    "Base", "engine", "SessionLocal", "session_scope", "make_engine", "pg_url",
    "require_schema", "SchemaNotApplied", "utcnow", "band_for",
    "IDENTIFIER_TYPES", "IDENTIFIER_WEIGHTS", "BANDS", "BAND_THRESHOLDS",
    "MIN_STYLOMETRY_CHARS", "REQUIRED_TABLES",
    "Source", "Actor", "Persona", "Identifier", "PersonaIdentifier", "Post",
    "Writeprint", "WriteprintVocab", "Link", "InfraFinding", "InfraCorrelation",
    "Feedback", "Scan", "User", "AuditEntry",
    "record_scan", "finish_scan",
]
