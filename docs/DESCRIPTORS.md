# Onion service descriptors — what they tell you, and what they don't

`recon/descriptor.py` reads the descriptor a v3 hidden service publishes about
itself. This document exists because the most useful thing to say about
descriptor analysis is what it **cannot** do, and that belongs somewhere a
reader will find it before they believe a number.

```bash
python -m recon.descriptor --source fixtures              # offline, all fixtures
python -m recon.descriptor --source fixtures single_service
docker compose exec api python -m recon.descriptor --source live <address>.onion
```

---

## The thing it cannot do

**A v3 descriptor does not contain the service's IP address.** Not encrypted,
not encoded, not derivable by any amount of parsing. This is the central design
property of onion routing rather than an oversight or a bug to be worked
around.

If a tool tells you it deanonymised a hidden service from its descriptor, one
of these is true: it is wrong, it is describing a different technique, or it is
lying.

### The trap that makes people think otherwise

A descriptor *does* contain IP addresses. Each introduction point carries link
specifiers, and those hold an address and port:

```
introduction points  : 3
    relay 90.125.30.141:9001  also [2a01:cb10:822e:1803:0215:5dff:fe00:0303]:9001
    relay 51.195.82.227:9100  also [2001:41d0:0701:1100:0000:0000:0000:b43a]:9100
    relay 65.109.108.233:9001
```

Those are **introduction point relays** — ordinary public Tor relays the
service picked out of the consensus. They are in the public relay list. They
have nothing to do with where the service runs, they rotate roughly daily, and
the service reaches them through a full circuit exactly so that they cannot
learn its location either.

Presenting one as "the service's IP" is the single most damaging mistake this
code could make, so the field is called `relay_address`, the CLI prints a
disclaimer under every report, and `tests/test_descriptor.py` asserts that no
field named `service_ip` or `ip_address` exists on the facts object.

---

## What it genuinely tells you

Measured against the lab service and the fixtures in `fixtures/descriptors/`.

| Finding | Worth | Why |
|---|---|---|
| `single-onion-service` | **Real** | The operator ran `HiddenServiceSingleHopMode`, giving up their own location anonymity for latency. A deliberate posture: this operator is not trying to hide where the service runs. |
| Client authorisation | **Real** | Holding the address is not enough to reach the service. Visible either as client entries or as an inner layer that will not decrypt. |
| `pow-params` | **Moderate** | Proof-of-work defence, which requires Tor ≥ 0.4.8 and is normally switched on in response to denial of service. Gives a version floor and an operational hint. |
| Intro point count, descriptor lifetime, handshake formats | **Weak** | Configuration detail. Useful for telling two services apart; meaningless alone. |
| Intro point overlap between two services | **Very weak** | See below. Computed, reported with its disclaimer, and not scored. |

### Why intro-point overlap is weaker than it sounds

The intuition is that two services sharing introduction points are related.
Measured against how selection actually works, it fails in both directions:

- Services choose intro points from the consensus **weighted by bandwidth**, so
  the same fast relays get chosen constantly by unrelated services. Overlap
  between strangers is common.
- Two services on the **same machine, in the same tor process**, choose
  independently and will usually share nothing at all. Overlap between
  genuinely related services is uncommon.
- Selection **rotates within roughly a day**, so any observation is a snapshot
  with a short shelf life.

`intro_point_overlap()` returns the count and the caveat together, as a tuple,
so nothing downstream can quote the number without it. It is unmeasured on this
corpus — there is one lab onion and no ground truth about related services — and
it feeds no score. The same restraint `scripts/measure_reliability.py` applies
to source reliability: plausible is not measured.

---

## Is fetching a descriptor passive?

Yes, and more so than the rest of `recon/`.

Fingerprinting fetches pages *from the service*. This never contacts the
service at all. The descriptor was uploaded by the service to a public
directory, and we read it from there — strictly less contact than visiting the
site, which is what any Tor Browser user does.

The honest caveat: it is passive, not invisible. The fetch reaches an HSDir
relay, and an operator running their own HSDir could in principle observe
descriptor requests for their address. Nothing in this repo hides that, and
nothing about the technique requires it to be hidden.

---

## Things that are not feasible, and why

Asked and answered, so nobody spends an afternoon on them:

**The service's IP.** Covered above. No.

**The service's Tor version.** Only a floor, and only by inference. `pow-params`
means ≥ 0.4.8. Nothing in the descriptor states a version.

**Clock skew of the service host.** v3 revision counters are encrypted with an
order-preserving scheme specifically so they cannot be read as a timestamp. The
live lab service returns `3274744996`; the synthetic descriptors that
`stem` generates return a plain unix time, which is why fixtures look like
timestamps and real descriptors do not. Do not build anything on that number
meaning a time.

**Uptime or restart history.** The revision counter increases across
republishes, so a series of observations shows *that* it republished, not when
or why. One observation shows nothing.

**Anything about v2 services.** v2 onion services were deprecated in 2020 and
switched off in 2021. They are gone, and their descriptors — which were
genuinely weaker — are not coming back. Any technique you read about that
depends on v2 descriptor properties does not apply.

**Enumerating a service's descriptor without the address.** v3 addresses are
public keys, and the HSDir index is derived from a blinded form of that key.
You cannot ask "what services exist"; you can only ask about an address you
already have. This is also why there is no "scan the dark web" mode in this
repo.

---

## One thing worth knowing about the library

`stem.descriptor.hidden_service.HiddenServiceDescriptorV3.from_str` defaults to
`validate=False`, and a corrupt descriptor parses happily into an object whose
fields look exactly like real ones. Measured: flipping the version byte still
returned a populated descriptor.

`recon/descriptor.py` passes `validate=True`, which verifies the Ed25519
certificate chain and rejects a forgery. A descriptor is a signed statement; if
the signature does not check out there is nothing here worth reporting.

Validation does *not* reject unrecognized lines, so descriptors carrying
features newer than stem 1.8.2 still parse. `pow-params` is one of those — the
library predates proposal 327 — and it is parsed by hand out of the inner
layer rather than dropped.
