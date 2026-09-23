"""descriptor.py — what a v3 onion service publishes about itself.

Every running v3 hidden service uploads a signed descriptor to a rotating set
of directory relays, so clients can find it. Anyone who knows the address can
fetch that descriptor. This module asks a Tor daemon to do exactly that
(HSFETCH over the control port), decrypts it, and reports what it says.

IS THIS PASSIVE?
----------------
Yes, and by a wider margin than the rest of `recon/`. Fingerprinting fetches
pages *from the service*. This never contacts the service at all: the
descriptor was published to a public directory, and we read it there. It is
strictly less contact than visiting the site.

The honest caveat is that it is not *invisible*. The fetch reaches an HSDir
relay, and a service operator running their own HSDir could in principle notice
descriptor requests. That is a property worth stating rather than glossing.

WHAT IT CANNOT TELL YOU — READ THIS BEFORE BELIEVING ANYTHING ELSE
------------------------------------------------------------------
**The descriptor does not contain the service's IP address.** Not encrypted,
not encoded, not derivable. This is the central design property of onion
routing, not an oversight, and no amount of descriptor parsing gets around it.

The intro points inside a descriptor carry link specifiers with IP addresses —
and those belong to *introduction point relays*, which are ordinary public Tor
relays chosen from the consensus. Reporting one as "the service's IP" would be
the single most damaging mistake this file could make, so `IntroPoint` names
the field `relay_address` and `docs/DESCRIPTORS.md` says it again in prose.

WHAT IT GENUINELY TELLS YOU
---------------------------
  * `is_single_service` — the operator turned on `HiddenServiceSingleHopMode`
    or `OnionServiceSingleHopMode`, trading their own location anonymity for
    latency. That is a deliberate posture choice and a real finding: this
    operator is not trying to hide where the service runs.
  * client authorisation is configured — the service is not meant to be
    reachable by anyone holding the address.
  * `pow-params` present — the service runs Tor >= 0.4.8 with the proof-of-work
    defence enabled, which usually means it has been under denial of service.
  * descriptor lifetime, intro point count, supported handshake formats —
    configuration detail, useful for telling two services apart, weak on its
    own.

Nothing here writes to the database and nothing feeds the attribution score.
Same restraint as recon/onion_location.py, for the same reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

__all__ = [
    "DEFAULT_CONTROL_PORT",
    "DescriptorFacts",
    "IntroPoint",
    "analyse",
    "fetch",
    "intro_point_overlap",
]

DEFAULT_CONTROL_PORT = 9051

#: `pow-params <scheme> <seed-b64> <suggested-effort> <expiration>`.
#: stem 1.8.2 predates proposal 327, so its InnerLayer does not model this and
#: it arrives as an unrecognized line. Parsed by hand rather than skipped,
#: because "the service is under enough DoS pressure to turn on PoW" is one of
#: the more informative things a descriptor says.
_POW_PARAMS = re.compile(
    r"^pow-params\s+(?P<scheme>\S+)\s+(?P<seed>\S+)\s+"
    r"(?P<effort>\d+)\s+(?P<expiration>\S+)",
    re.MULTILINE,
)


@dataclass(frozen=True)
class IntroPoint:
    """One introduction point.

    `relay_address` is an **introduction point relay** — a public Tor relay the
    service selected from the consensus. It is not the service's address and
    must never be presented as one.
    """

    relay_address: Optional[str] = None
    relay_port: Optional[int] = None
    #: Kept separate rather than overwriting `relay_address`. Most relays
    #: advertise both families, and letting whichever specifier came last win
    #: silently discarded the IPv4 address — measured against the lab service,
    #: where two of three intro points lost theirs.
    relay_address_v6: Optional[str] = None
    relay_port_v6: Optional[int] = None
    fingerprint: Optional[str] = None
    link_types: tuple[str, ...] = ()
    has_legacy_key: bool = False

    @property
    def endpoint(self) -> str:
        """How to write it down. IPv6 goes in brackets, per RFC 3986."""
        if self.relay_address:
            return f"{self.relay_address}:{self.relay_port}"
        if self.relay_address_v6:
            return f"[{self.relay_address_v6}]:{self.relay_port_v6}"
        return "(no address specifier)"


@dataclass(frozen=True)
class DescriptorFacts:
    """Everything the descriptor says, and nothing it does not."""

    address: str
    version: Optional[int] = None
    lifetime_minutes: Optional[int] = None
    revision_counter: Optional[int] = None
    is_single_service: bool = False
    client_auth: bool = False
    intro_points: tuple[IntroPoint, ...] = ()
    formats: tuple[int, ...] = ()
    pow_params: Optional[dict] = None
    #: True when the outer layer parsed but the inner layer did not decrypt —
    #: the usual cause is client authorisation, which is itself the finding.
    inner_locked: bool = False
    detail: dict = field(default_factory=dict)

    @property
    def intro_point_count(self) -> int:
        return len(self.intro_points)

    @property
    def fingerprints(self) -> tuple[str, ...]:
        return tuple(sorted(p.fingerprint for p in self.intro_points if p.fingerprint))

    def observations(self) -> list[str]:
        """Plain sentences an analyst can read, strongest first."""
        out: list[str] = []
        if self.is_single_service:
            out.append(
                "Runs as a single onion service: the operator disabled their own "
                "location anonymity in exchange for lower latency. This is a "
                "deliberate configuration, and it means the operator is not "
                "trying to hide where the service runs."
            )
        if self.client_auth:
            out.append(
                "Client authorisation is configured, so holding the address is "
                "not sufficient to reach the service."
            )
        if self.pow_params:
            out.append(
                f"Advertises proof-of-work parameters (scheme "
                f"{self.pow_params.get('scheme')!r}, suggested effort "
                f"{self.pow_params.get('effort')}), which requires Tor 0.4.8 or "
                f"later and is normally enabled in response to denial of service."
            )
        if self.inner_locked:
            out.append(
                "The encrypted layer did not open with the address alone, which "
                "is what a service using client authorisation looks like from "
                "outside."
            )
        if self.intro_points:
            out.append(
                f"Publishes {self.intro_point_count} introduction point(s). "
                f"These name public relays the service selected, not the "
                f"service's own address."
            )
        if self.lifetime_minutes and self.lifetime_minutes != 180:
            out.append(
                f"Descriptor lifetime is {self.lifetime_minutes} minutes rather "
                f"than the usual 180."
            )
        return out


def _parse_pow(text: str) -> Optional[dict]:
    match = _POW_PARAMS.search(text or "")
    if not match:
        return None
    return {
        "scheme": match.group("scheme"),
        "seed": match.group("seed"),
        "effort": int(match.group("effort")),
        "expiration": match.group("expiration"),
    }


def _intro_points_from(inner) -> tuple[IntroPoint, ...]:
    from stem.client.datatype import (  # noqa: PLC0415
        LinkByEd25519,
        LinkByFingerprint,
        LinkByIPv4,
        LinkByIPv6,
    )

    points: list[IntroPoint] = []
    for raw in getattr(inner, "introduction_points", ()) or ():
        address = port = v6 = v6_port = fingerprint = None
        types: list[str] = []
        for spec in getattr(raw, "link_specifiers", ()) or ():
            if isinstance(spec, LinkByIPv4):
                types.append("ipv4")
                address, port = spec.address, spec.port
            elif isinstance(spec, LinkByIPv6):
                types.append("ipv6")
                v6, v6_port = spec.address, spec.port
            elif isinstance(spec, LinkByFingerprint):
                types.append("fingerprint")
                fingerprint = spec.value.hex().upper()
            elif isinstance(spec, LinkByEd25519):
                types.append("ed25519")
            else:
                types.append(f"type-{getattr(spec, 'type', '?')}")
        points.append(IntroPoint(
            relay_address=address,
            relay_port=port,
            relay_address_v6=v6,
            relay_port_v6=v6_port,
            fingerprint=fingerprint,
            link_types=tuple(types),
            has_legacy_key=getattr(raw, "legacy_key_raw", None) is not None,
        ))
    return tuple(points)


def analyse(descriptor_text: str, address: str) -> DescriptorFacts:
    """Parse and, where possible, decrypt. Pure — no network, no database.

    A descriptor whose inner layer will not open is not an error: that is what
    client authorisation looks like from outside, and the outer layer still
    carries the version, lifetime and revision counter.
    """
    from stem.descriptor.hidden_service import (  # noqa: PLC0415
        HiddenServiceDescriptorV3,
    )

    host = address.strip().lower()
    if not host.endswith(".onion"):
        host = f"{host}.onion"

    # validate=True verifies the Ed25519 certificate chain. stem defaults it
    # off, which means a forged or corrupt descriptor parses happily and yields
    # facts that look exactly like real ones — measured: flipping the version
    # byte still returned a populated object. A descriptor is a signed
    # statement; if the signature does not check out there is nothing here
    # worth reporting, so this refuses rather than reports.
    #
    # It does not reject unrecognized lines, so descriptors carrying features
    # newer than stem 1.8.2 (pow-params, for one) still validate.
    outer = HiddenServiceDescriptorV3.from_str(descriptor_text, validate=True)

    inner = None
    locked = False
    try:
        inner = outer.decrypt(host)
    except Exception:  # noqa: BLE001 - client auth, or a descriptor we cannot read
        locked = True

    raw_inner = str(inner) if inner is not None else ""
    formats = tuple(getattr(inner, "formats", ()) or ()) if inner else ()

    # Client auth shows up two ways: real entries in the outer layer's client
    # list, or an inner layer that will not open at all.
    clients = getattr(getattr(outer, "_outer_layer", None), "clients", None)
    client_auth = locked or bool(getattr(inner, "intro_auth", None))

    return DescriptorFacts(
        address=host,
        version=getattr(outer, "version", None),
        lifetime_minutes=getattr(outer, "lifetime", None),
        revision_counter=getattr(outer, "revision_counter", None),
        is_single_service=bool(getattr(inner, "is_single_service", False)),
        client_auth=client_auth,
        intro_points=_intro_points_from(inner) if inner is not None else (),
        formats=formats,
        pow_params=_parse_pow(raw_inner),
        inner_locked=locked,
        detail={"outer_clients": len(clients)} if clients else {},
    )


def intro_point_overlap(
    a: DescriptorFacts,
    b: DescriptorFacts,
) -> tuple[int, str]:
    """Shared introduction point relays between two services, and the caveat.

    **This is not evidence of common operation and is not scored.** Services
    pick introduction points from the consensus weighted by bandwidth, so the
    same fast relays are chosen constantly by unrelated services; and the
    selection rotates within roughly a day, so any overlap seen once is a
    snapshot. Two services on the *same machine* choose independently and will
    usually share nothing at all — which makes the signal weak in the direction
    people expect it to be strong.

    It is computed because it is cheap and occasionally worth an analyst's
    glance, and it is returned with its own disclaimer attached so nothing
    downstream can quote the number without it.
    """
    shared = set(a.fingerprints) & set(b.fingerprints)
    note = (
        "Introduction points are chosen from the public relay consensus by "
        "bandwidth weight and rotate roughly daily, so unrelated services "
        "share them routinely and two services on one machine usually do not. "
        "Unmeasured on this corpus; treat as a curiosity, not as evidence."
    )
    return len(shared), note


def fetch(
    address: str,
    *,
    control_host: str = "127.0.0.1",
    control_port: int = DEFAULT_CONTROL_PORT,
    password: Optional[str] = None,
    timeout: float = 60.0,
) -> str:
    """Ask a Tor daemon to fetch a v3 descriptor, and return its text.

    Uses HSFETCH and waits for the matching HS_DESC_CONTENT event. Never
    contacts the hidden service — the descriptor comes from a directory relay.

    Raises RuntimeError with the daemon's own reason on failure, rather than
    returning an empty string that a caller might parse as "nothing published".
    """
    import queue  # noqa: PLC0415
    import socket  # noqa: PLC0415

    from stem import control  # noqa: PLC0415
    from stem.control import EventType  # noqa: PLC0415

    # stem 1.8.2's Controller.from_port validates its argument as an IP literal
    # and rejects hostnames outright, so a compose service name like `lab-tor`
    # has to be resolved here. Failing to resolve is worth its own message:
    # "invalid IP address: lab-tor" would send a reader looking for a typo
    # rather than for a container that is not running.
    try:
        control_host = socket.gethostbyname(control_host)
    except OSError as exc:
        raise RuntimeError(
            f"cannot resolve {control_host!r}; is the lab profile up? "
            f"(docker compose --profile lab up -d)"
        ) from exc

    host = address.strip().lower()
    if host.endswith(".onion"):
        host = host[: -len(".onion")]

    results: "queue.Queue[tuple[str, str]]" = queue.Queue()

    def on_content(event) -> None:
        if getattr(event, "address", None) == host:
            results.put(("content", str(getattr(event, "descriptor", "") or "")))

    def on_desc(event) -> None:
        if getattr(event, "address", None) != host:
            return
        if str(getattr(event, "action", "")).upper() == "FAILED":
            results.put(("failed", str(getattr(event, "reason", "") or "unknown")))

    with control.Controller.from_port(address=control_host, port=control_port) as c:
        c.authenticate(password=password)
        c.add_event_listener(on_content, EventType.HS_DESC_CONTENT)
        c.add_event_listener(on_desc, EventType.HS_DESC)
        c.signal  # noqa: B018 - touch, so a dead control connection fails here
        c.msg(f"HSFETCH {host}")

        try:
            kind, payload = results.get(timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - queue.Empty and friends
            raise RuntimeError(
                f"no descriptor for {host}.onion within {timeout:.0f}s; the "
                f"service may be offline or the address may never have been "
                f"published"
            ) from exc

    if kind == "failed":
        raise RuntimeError(f"tor refused the descriptor fetch: {payload}")
    if not payload.strip():
        raise RuntimeError("tor returned an empty descriptor")
    return payload


def main(argv: Optional[Sequence[str]] = None) -> int:
    """`python -m recon.descriptor <address> [--source fixtures|live]`."""
    import argparse
    import json
    import os
    import sys
    from pathlib import Path

    # The default Windows console codepage is cp1252 and raises on the box
    # rules below rather than degrading. Same fix as scripts/measure_reliability.py.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):  # redirected, or not a tty
            pass

    parser = argparse.ArgumentParser(
        description="Read what a v3 onion service publishes about itself.",
        epilog="The descriptor never contains the service's IP address. "
               "See docs/DESCRIPTORS.md.",
    )
    parser.add_argument("address", nargs="?",
                        help="onion address, or a fixture name with --source fixtures")
    parser.add_argument("--source", choices=("fixtures", "live"), default="fixtures",
                        help="fixtures reads fixtures/descriptors/ and needs no "
                             "network; live fetches over the control port")
    parser.add_argument("--control-host", default=os.environ.get("TOR_CONTROL_HOST",
                                                                 "lab-tor"))
    parser.add_argument("--control-port", type=int,
                        default=int(os.environ.get("TOR_CONTROL_PORT",
                                                   DEFAULT_CONTROL_PORT)))
    parser.add_argument("--control-password",
                        default=os.environ.get("TOR_CONTROL_PASSWORD"))
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    store = root / "fixtures" / "descriptors"

    if args.source == "fixtures":
        index = json.loads((store / "index.json").read_text(encoding="utf-8"))
        names = [args.address] if args.address else sorted(index)
        for name in names:
            if name not in index:
                print(f"no such fixture: {name}. have: {', '.join(sorted(index))}")
                return 2
            text = (store / f"{name}.txt").read_text(encoding="utf-8")
            _report(analyse(text, index[name]), name)
        return 0

    if not args.address:
        parser.error("--source live needs an address")
    text = fetch(
        args.address,
        control_host=args.control_host,
        control_port=args.control_port,
        password=args.control_password,
        timeout=args.timeout,
    )
    _report(analyse(text, args.address), "live")
    return 0


def _report(facts: "DescriptorFacts", label: str) -> None:
    print("─" * 74)
    print(f"{label}  {facts.address}")
    print("─" * 74)
    print(f"  version {facts.version}   lifetime {facts.lifetime_minutes}m   "
          f"revision {facts.revision_counter}")
    print(f"  single onion service : {facts.is_single_service}")
    print(f"  client authorisation : {facts.client_auth}")
    print(f"  introduction points  : {facts.intro_point_count}")
    for point in facts.intro_points:
        extra = ""
        if point.relay_address and point.relay_address_v6:
            extra = f"  also [{point.relay_address_v6}]:{point.relay_port_v6}"
        print(f"      relay {point.endpoint}{extra}  {','.join(point.link_types)}")
    if facts.pow_params:
        print(f"  proof-of-work        : {facts.pow_params}")
    print()
    for line in facts.observations():
        print(f"  * {line}")
    print()
    print("  The service's own IP address is not in this descriptor and cannot")
    print("  be derived from it. The relays above are introduction points the")
    print("  service selected from the public consensus.")
    print()


if __name__ == "__main__":
    raise SystemExit(main())
