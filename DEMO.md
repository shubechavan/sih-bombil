# DEMO — cue card

Terminal **≥ 110 columns**. Browser at <http://localhost:3000>. Total ~6 min.

---

## Before you start (do this early, not on stage)

```bash
cp .env.example .env
docker compose up -d --build       # 6 min cold, 25 s after
docker compose run --rm seed       # 20 s
curl -s localhost:8000/health      # want: "ready":true, "actors":14
```

`run --rm`, **not** `up seed` — `up` reuses a dead container and seeds nothing.

---

## 1 · The number nobody else has — 30 s

```bash
docker compose exec api python scripts/evaluate.py
```

> "This is a synthetic corpus with a known answer key, so we can print precision
> and recall instead of screenshots. Precision is 1.000 at every band. Recall is
> six of eight — and I want to be clear that the two we miss share no identifier
> at all, so pairwise scoring can't reach them. We report it as six of eight
> rather than rounding up."

Point at: **margin +0.508** — "the gap between the weakest pair we accept and
the strongest we reject. High recall with a thin margin is one corpus change
away from being wrong."

## 2 · The two we missed — 15 s

```bash
docker compose exec api python scripts/evaluate.py --transitive
```

> "The cluster pass reaches them through a shared third persona. It's reported
> separately and band-capped, because an inferred link is a lead with a path
> attached, not a second measurement. Eight of eight at POSSIBLE, nothing wrong."

## 3 · Evidence, not scores — 60 s

Browser → **`/actors`** → click **Dr3adPirat3**

> "Three personas, one actor, merged at 0.876 — that's the *weakest* link
> holding the cluster together, not the strongest. Every row says why in plain
> language: same PGP fingerprint, writeprint cosine 0.857, 84% posting-hour
> overlap."

Scroll to the **I row** — orange, "NOT ASSESSED":

> "Infrastructure wasn't assessed, and rather than print a zero we print the
> reason. That's the next thing I want to show you."

## 4 · The refusals — 45 s

Browser → **`/actors/7`** (paperghost)

> "152 characters of text. The floor is 300. Stylometry refuses, so every pair
> this persona appears in is scored with S unmeasured and its weight
> redistributed — not scored with a number from two sentences. And it reads NOT
> MERGED, not WEAK: WEAK would mean we compared it and weren't convinced."

## 5 · **The site-broadcast moment** — 60 s

```bash
docker compose exec api python scripts/evaluate.py --infra site-broadcast
```

**The three sentences:**

> "The obvious shortcut is to give every vendor the fingerprint of the market
> they trade on. We built that, because arguing a signal would hurt is worth
> much less than measuring it.
>
> It costs us two real migrations — 1~9 and 2~10 drop from CONFIRMED to
> PROBABLE — and it takes the separation margin from +0.508 to +0.287.
>
> And look at what gains the most: every one of those pairs contains persona 7,
> the one we just refused to score. It manufactures confidence about the single
> persona we were right to say nothing about."

Close: *"So the I term stays unmeasured. That's a result, not a gap."*

## 6 · Recon and the report — 45 s

```bash
docker compose exec api python -m recon.correlate --source fixtures --dry-run
```

> "Passive only — six GETs of paths the server already publishes. That found a
> clearnet host at 1.000 on a matching certificate serial, while unrelated hosts
> running the same nginx stay at 0.40."

Browser → **`/export`** → Download PDF (or show one prepared):

> "Court-style case report. Same rule as the screen — unmeasured prints the
> reason, and the synthetic-corpus caveat is on every page."

---

## If they ask: what's missing?

> "Three things. There are no live collectors — we reuse v1's Tor crawler but
> never wrote the forum and market parsers, so everything here runs on fixtures.
> The Shodan provider is written but has never run against a real key, and it
> says so at runtime. And the infrastructure term measures nothing on this
> corpus, for the reason I showed you.
>
> Every number is measured against a synthetic answer key. It's this engine on
> this corpus, not real-world accuracy."

## If they ask: is it just regex?

> "The identifiers are, with checksum validation — base58check, EIP-55 — and
> anything that fails is dropped, not stored. The linking is TF-IDF writeprints
> and a posting-hour histogram. The formula is deliberately transparent so every
> score can be read back as a sentence."

---

## If it breaks

| Symptom | Do this |
|---|---|
| **Terminal < 108 cols** | `evaluate.py` warns you. Widen it, or `... > /tmp/e.txt` and open the file. Prose wraps fine at any width; only the tables need the room. |
| **Docker slow / still building** | Skip Docker entirely: `python scripts/evaluate.py` runs offline with no database and no network. Steps 1, 2, 5 all work this way. |
| **A page 500s or hangs** | `curl -s localhost:8000/health`. `ready:false` → `docker compose run --rm seed`. No response → `docker compose restart api`, wait 15 s. |
| **`/actors` is empty** | Clustering hasn't run: `docker compose exec api python -m link.cluster --source db` (5 s). |
| **Console blank / won't load** | `docker compose restart ui`, wait 20 s. Fall back to the API: <http://localhost:8000/docs> has every endpoint with live responses. |
| **Everything is broken** | `docker compose --profile seed down -v && docker compose up -d && docker compose run --rm seed` — 45 s from nothing. |
| **No Docker at all** | `pip install -r requirements.txt && python scripts/evaluate.py`. The headline numbers need nothing else. |

**Fallback order if time runs out:** step 1 → step 5 → step 3. Those three are
the whole argument.

---

## Numbers, if you blank

| | |
|---|---|
| precision / recall | 1.000 at every band / 6 of 8 pairwise, 8 of 8 with closure |
| separation margin | +0.508 → +0.287 under site-broadcast |
| corpus | 20 personas, 3 sources, 200 posts, 14 actors — matches the answer key exactly |
| tests | 309 Python, 27 browser checks |
| the floor | 300 characters; paperghost has 152 |
| 3~18 | CONFIRMED 0.909 on PGP alone |
