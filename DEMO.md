# DEMO — cue card

Terminal **≥ 110 columns**. Browser at <http://localhost:3000>. Total ~7 min,
or ~9 with the live crawl (step 8).

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

## 5 · Text from the room — 60 s

The one step they can drive. Ask someone to paste anything — their own email, a
paragraph off a news site, a vendor bio from step 3.

Browser → **`/analyze`**

> "Everything so far is us showing you our answer key. This takes text we have
> never seen. Paste whatever you like."

Then paste one persona's own prose back in (copy it from `/actors/1`):

> "Now watch the control. That's Dr3adPirat3's own text, and it comes back at
> S = 1.000 against Dr3adPirat3 — which only happens if we are really
> vectorising your paste, not looking anything up. Underneath it: BlackSailsRX
> at 0.857 and Dread_P1rate at 0.762. Same actor, three handles on three
> different markets, and nothing in what I pasted contained a single identifier.
> That is stylometry and posting rhythm alone."

Then paste two sentences:

> "Under 300 characters it refuses, exactly like paperghost. Same floor, same
> sentence, and it still ranks on behaviour rather than throwing the paste away."

Worth saying if nobody asks: **the vocabulary is never refitted on their text.**
It is transformed against the fit that built the stored writeprints, so their
paste cannot move anyone else's number.

## 6 · **The site-broadcast moment** — 60 s

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

## 7 · Buyer feedback, and not using it — 30 s

Browser → **`/graph`** → point at the **dashed grey edges**

> "Markets publish buyer ratings, so we collect them. The obvious move is to
> feed shared buyers into the score. We measured it first: ROC-AUC 0.389 — worse
> than a coin flip — and the wrong pairs score *higher* than the real ones,
> because buyers shop around. So they're drawn dashed, they carry
> `affects_score: false` on every API row, and they're worth an analyst's
> attention without being worth a point of confidence."

If challenged: `docker compose exec api python -m link.trust --measure`

## 8 · Live collection over Tor — 90 s

Only if the lab is already up (`docker compose --profile lab up -d`) and Tor has
bootstrapped. **Otherwise skip it** — the numbers in step 1 are the argument.

```bash
docker compose exec lab-tor cat /var/lib/tor/lab_hs/hostname
python scripts/collect.py --onion http://<that>.onion --verify
```

> "That's a real Tor v3 hidden service we host, crawled over a real circuit at
> one request every two seconds. 33 requests, 74 seconds. The last line is the
> one that matters: every one of 424 text fields came back byte-identical to
> the offline corpus. Ingest that and the evaluation prints the same numbers,
> line for line — which is how we know the collector isn't quietly losing
> anything."

## 9 · Recon and the report — 45 s

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

> "Two things. The Shodan provider is written but has never run against a real
> key, and it says so at runtime. And the infrastructure term measures nothing
> on this corpus, for the reason I showed you.
>
> On collection: we crawl a lab hidden service we host ourselves, not real
> marketplaces. The collectors have no default target list — you have to hand
> them an address — which is deliberate, but it does mean the parsers have only
> ever met one site's HTML.
>
> Every number is measured against a synthetic answer key. It's this engine on
> this corpus, not real-world accuracy."

## If they ask: is it just regex?

> "The identifiers are, with checksum validation — base58check, EIP-55 — and
> anything that fails is dropped, not stored. The linking is TF-IDF writeprints
> and a posting-hour histogram. The formula is deliberately transparent so every
> score can be read back as a sentence.
>
> And the honest test of that is the hard negatives. Regex can't refuse — pairs
> 2↔20 and 4↔15 were built to look like matches, and both stay WEAK. Same
> product category on both, scoring a perfect 1.00 on that signal; a matcher
> keying on category and register would confirm them. What refuses them is the
> posting-hour histogram — 0.08 and 0.18 — because two people don't sleep on the
> same schedule by coincidence."

Scroll up in step 1's output to show it — `[PASS] 2~20 … 0.242 WEAK`, with the
reason the corpus gives printed underneath.

If what they actually mean is *"is this canned?"*, that is step 5. Hand them the
keyboard.

---

## If it breaks

| Symptom | Do this |
|---|---|
| **Terminal < 108 cols** | `evaluate.py` warns you. Widen it, or `... > /tmp/e.txt` and open the file. Prose wraps fine at any width; only the tables need the room. |
| **Docker slow / still building** | Skip Docker entirely: `python scripts/evaluate.py` runs offline with no database and no network. Steps 1, 2 and 6 all work this way. Step 5 needs the stack — it is the only one that does. |
| **A page 500s or hangs** | `curl -s localhost:8000/health`. `ready:false` → `docker compose run --rm seed`. No response → `docker compose restart api`, wait 15 s. |
| **`/actors` is empty** | Clustering hasn't run: `docker compose exec api python -m link.cluster --source db` (5 s). |
| **Console blank / won't load** | `docker compose restart ui`, wait 20 s. Fall back to the API: <http://localhost:8000/docs> has every endpoint with live responses. |
| **Everything is broken** | `docker compose --profile seed down -v && docker compose up -d && docker compose run --rm seed` — 45 s from nothing. |
| **No Docker at all** | `pip install -r requirements.txt && python scripts/evaluate.py`. The headline numbers need nothing else. |
| **The lab onion won't resolve** | Tor needs the real network and a few minutes to publish the descriptor: `docker compose --profile lab logs lab-tor \| grep Bootstrapped`. Not at 100% → **skip step 8**. It is the only step that needs the internet. |
| **`/analyze` returns 503** | The fitted vocabulary is not stored for the current corpus version. `docker compose exec api python -m link.resolve --source db` writes it, and the vectors come back bit-identical. |
| **Step 8 crawl hangs** | Ctrl-C and skip it. Nothing else depends on it, and step 1 already ran offline. |

**Fallback order if time runs out:** step 1 → step 5 → step 6 → step 3. Those four
are the whole argument — the numbers, that the numbers are live, the evidence,
and the one moment the engine argues against itself. Steps 7 and 8 are the Phase
6 material; drop them first.

---

## Numbers, if you blank

| | |
|---|---|
| precision / recall | 1.000 at every band / 6 of 8 pairwise, 8 of 8 with closure |
| separation margin | +0.508 → +0.287 under site-broadcast |
| corpus | 20 personas, 3 sources, 200 posts, 14 actors — matches the answer key exactly |
| tests | 360 Python, 34 browser checks |
| the crawl | 20 personas, 200 posts, 33 requests, 74 s — all 424 fields byte-identical |
| shared buyers | ROC-AUC 0.389, worse than chance — measured, then not used |
| the floor | 300 characters; paperghost has 152 |
| the live paste | a persona's own text returns S 1.000 against itself, then 0.857 and 0.762 for its two other handles — with no identifier in the paste |
| 3~18 | CONFIRMED 0.909 on PGP alone |
