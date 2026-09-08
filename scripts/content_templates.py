"""
content_templates.py — the content layer of the synthetic corpus.

Deliberately style-free. Every sentence here is written in a neutral register with
no greeting, no signoff, no terminal punctuation and no capitalisation
assumptions; style_profiles.render() supplies all of that. Keeping the two layers
apart is what stops the corpus from being one template with the author's name
swapped: two personas can draw the same beats and still read as different people,
and two personas of the same actor can draw different beats and still read as one.

Posts are assembled from *structures* — ordered sequences of beats — and the
structures vary in length (two to six beats) and in ordering, so a post is not a
fixed frame with slots filled in.

The prose deliberately uses the words that appear in the profiles' misspelling
tables (receive, separate, occurred, address, necessary, definitely, acquired,
business, recommend, delivery, package, purity, encryption, experience, ...) and
uses contractions freely, so that each profile's orthographic habits have
something to bite on in every post.
"""

from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# Slot fillers
# ─────────────────────────────────────────────────────────────────────────────

#: Product names are article-free and read naturally after "fresh", "new" or
#: "listing", so the templates do not end up with a doubled article.
PRODUCTS: dict[str, tuple[str, ...]] = {
    "drugs": ("blue pressed tabs", "afghan #4", "peruvian flake",
              "dutch mdma", "moroccan hash", "green crystals"),
    "pharma": ("alprazolam 2mg", "modafinil 200mg", "diazepam 10mg",
               "tramadol 100mg", "zopiclone 7.5mg", "pregabalin 300mg"),
    "data": ("fullz packs", "track2 dumps", "rdp access bundles",
             "email combo lists", "verified paypal logs", "bin lists"),
    "docs": ("eu id templates", "scanned licence sets", "passport scan packs",
             "utility bill templates", "proof of address bundles"),
    "hacking": ("loader builds", "crypter stubs", "exploit packs",
                "panel sources", "botnet builds"),
    "laundering": ("mixing services", "clean wallet setups",
                   "cash out routes", "verified exchange accounts"),
}

#: Head nouns the title_nouns capitalisation mode operates on. Curated rather
#: than derived by splitting every product phrase: deriving it swept in ordinary
#: words like "scan" and "pack" and produced "shows up on a Scan", and dosage
#: tokens like "100mg" became "100Mg".
TITLE_NOUNS: tuple[str, ...] = (
    "alprazolam", "modafinil", "diazepam", "tramadol", "zopiclone", "pregabalin",
    "mdma", "hash", "flake", "fullz", "dumps", "passport", "licence",
    "template", "templates", "crystals", "afghan", "peruvian", "moroccan",
)

FILLERS: dict[str, tuple[str, ...]] = {
    "qty": ("one gram", "five grams", "a sheet", "100 units", "a ten pack",
            "a bundle of fifty", "a quarter", "two boxes"),
    "price": ("£45", "$120", "€80", "$310", "€1200", "$75", "€240"),
    "coin": ("monero", "bitcoin", "xmr", "btc"),
    "origin": ("the netherlands", "germany", "the uk", "canada", "spain", "poland"),
    "days": ("two", "three", "five", "seven", "four"),
    "stealth": ("vacuum sealed", "double wrapped in mylar",
                "packed inside a hardback book", "sealed in a foil barrier bag",
                "hidden in a electronics return box"),
    "percent": ("94", "88", "97", "91", "86"),
    "market": ("the old market", "the new market", "that market", "the big market"),
}

# ─────────────────────────────────────────────────────────────────────────────
# Beats — one sentence each, no terminal punctuation, no leading capital
# ─────────────────────────────────────────────────────────────────────────────

BEATS: dict[str, tuple[str, ...]] = {
    # ── market beats ─────────────────────────────────────────────────────────
    "hook_listing": (
        "the new {product} batch is live on my page",
        "restock of {product} has landed and it's available now",
        "i've got fresh {product} listed today",
        "{product} is back after the last batch sold out",
        "listing {product} again because demand hasn't dropped",
        "{product} went up this morning and there's a decent quantity of it",
    ),
    "quality": (
        "every unit is tested and the purity came back at {percent} percent",
        "this batch is definitely the cleanest i've acquired this year",
        "lab results are on the listing page so you don't have to take my word",
        "quality is guaranteed and i'll refund anything that doesn't match",
        "the synthesis on this run was clean and the temperature was held steady",
        "i've had this batch tested twice because the last one was borderline",
    ),
    "price": (
        "{qty} goes for {price} in {coin}",
        "pricing is {price} per {qty} and bulk gets a discount",
        "i've kept the price at {price} for {qty} because the last batch moved fast",
        "{qty} is {price}, and anything over five units drops ten percent",
        "the price is {price} for {qty} which is lower than it was last month",
    ),
    "shipping": (
        "delivery is {days} days domestic and about double that overseas",
        "i ship from {origin} every weekday and the package goes out tomorrow",
        "orders placed before noon go out the same day, everything else waits until tomorrow",
        "shipping from {origin} takes {days} days and tracking is available on request",
        "the delivery window is {days} days, longer if customs decide to look",
    ),
    "stealth": (
        "the package is {stealth} so nothing shows up on a scan",
        "everything goes out {stealth} and the return address is a real business",
        "i use {stealth} because it has never failed a customs check",
        "packaging is {stealth} and the receipt inside is completely generic",
    ),
    "payment": (
        "{coin} only, and escrow is fine if you'd rather not go direct",
        "payment in {coin} through escrow, i don't ask for FE from new buyers",
        "i accept {coin} and finalising early is not necessary unless you want the discount",
        "escrow is available and i'd recommend it until we've done a few orders",
        "i've moved to {coin} only because the transfer fees on the alternative got silly",
    ),
    "contact": (
        "message me on {channel} if you need anything before you order",
        "my contact is {channel} and i usually answer within the hour",
        "reach me at {channel}, i check it every evening",
        "questions go to {channel} because market messages get lost a lot",
    ),
    "feedback_ack": (
        "thanks for the feedback, i appreciate you taking the time",
        "glad the order arrived, let me know if anything else comes up",
        "sorry the delivery was slow, the courier had a backlog last week",
        "noted, i'll separate that batch out and check the storage temperature",
        "appreciated, and the reship went out this morning",
    ),
    "dispute_policy": (
        "if the package doesn't arrive i'll reship once, no questions",
        "disputes go through the market and i've never lost one because i keep records",
        "i don't do partial refunds, but a reship is available if tracking shows a problem",
        "any issue, message me first and we'll sort it before it becomes a dispute",
        "a refund is possible but a reship is usually faster and i'd recommend it",
    ),
    "vacation": (
        "i'm on vacation mode until the end of the month",
        "orders are paused because i'm restocking, everything reopens tomorrow",
        "the shop is closed for a week while i move storage",
        "i'll be away until friday and orders will queue until i'm back",
    ),
    "pgp_notice": (
        "my pgp fingerprint is {pgp} and it has not changed",
        "verify anything signed against {pgp} before you send funds",
        "new key posted, the fingerprint is {pgp}",
        "the only fingerprint i sign with is {pgp}, anything else is not me",
    ),
    "wallet_notice": (
        "the wallet for direct payment is {wallet}, always confirm it against a signed message",
        "payments go to {wallet} and i'll never send you a different address in a message",
        "my address is {wallet} and it has been the same since i opened",
    ),
    "scam_warning": (
        "someone is impersonating me on another market, check the fingerprint before you pay",
        "there's a fake profile using my name and i've never asked for FE up front",
        "be careful, a vendor copied my listing text word for word last week",
        "an address was posted in my name that isn't mine, verify everything",
    ),

    # ── forum beats ──────────────────────────────────────────────────────────
    "opsec": (
        "encryption only helps if you actually verify the key you're sending to",
        "the biggest vulnerability is reusing a handle across markets",
        "i'd recommend a separate wallet for every vendor you deal with",
        "don't put a real address in the order notes, it's obviously a risk",
        "your posting times leak a lot more than people probably realise",
        "check the onion address character by character because phishing mirrors are everywhere",
    ),
    "market_review": (
        "the escrow on {market} has been reliable in my experience",
        "withdrawals took three days last time, which is probably normal",
        "support answered within a day and the dispute was resolved fairly",
        "i've had a good experience on {market} but the fees are high",
        "the interface is slow but i've never had an order go missing",
    ),
    "question": (
        "has anyone had a delivery from that vendor arrive late",
        "what's the current wait on withdrawals",
        "is there a mirror that's actually up right now",
        "does anyone know if that vendor moved to a new market",
        "is the escrow on {market} still working properly",
    ),
    "answer": (
        "yes, the same thing occurred to me last month",
        "the mirror is available again, the main address was down for maintenance",
        "that vendor moved, the new handle is different but the writing is the same",
        "i'd wait until the market sorts out its withdrawal backlog",
        "it's a known issue and support will fix it if you open a ticket",
    ),
    "gossip": (
        "the vendor who used to be on {market} is back under a new name",
        "prices have gone up across the board since the last exit scam",
        "a lot of people lost coins when that market went down",
        "there's a rumour the staff on {market} are the same people as before",
    ),
    "intro": (
        "been lurking for a while and finally made an account",
        "new here, mostly reading and occasionally buying",
        "long time reader, first post, mostly interested in the opsec threads",
    ),
    "rant": (
        "the fees on this place have gotten out of hand",
        "three days for a withdrawal is not acceptable",
        "vendors who don't answer messages shouldn't be selling",
        "half the mirrors in the sticky thread have been dead for a month",
    ),
    "warning": (
        "that address in the thread above is not the vendor's, check the signed message",
        "someone is phishing with a fake mirror and the onion is one character off",
        "don't finalise early for anyone who messaged you first",
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# Structures — ordered beat sequences. Varying length and order is what gives
# posts genuinely different shapes rather than one frame with different slots.
# ─────────────────────────────────────────────────────────────────────────────

STRUCTURES: dict[str, tuple[tuple[str, ...], ...]] = {
    "listing": (
        ("hook_listing", "quality", "price", "shipping", "payment"),
        ("hook_listing", "price", "stealth", "contact"),
        ("quality", "hook_listing", "shipping", "stealth", "payment", "contact"),
        ("hook_listing", "quality", "price"),
    ),
    "restock": (
        ("hook_listing", "quality", "shipping"),
        ("vacation", "hook_listing", "price", "contact"),
        ("hook_listing", "price", "payment", "stealth", "contact"),
    ),
    "shipping_note": (
        ("shipping", "stealth", "dispute_policy"),
        ("stealth", "shipping", "contact", "dispute_policy"),
        ("shipping", "dispute_policy"),
    ),
    "feedback_reply": (
        ("feedback_ack", "dispute_policy"),
        ("feedback_ack", "shipping", "contact"),
        ("feedback_ack", "quality", "feedback_ack"),
    ),
    "dispute": (
        ("dispute_policy", "shipping", "contact"),
        ("dispute_policy", "feedback_ack"),
    ),
    "contact_notice": (
        ("pgp_notice", "contact", "scam_warning"),
        ("scam_warning", "pgp_notice", "contact"),
        ("pgp_notice", "wallet_notice", "payment", "contact"),
        ("wallet_notice", "scam_warning", "contact"),
    ),
    "vacation_notice": (
        ("vacation", "shipping", "contact"),
        ("vacation", "dispute_policy"),
    ),

    # ── forum ────────────────────────────────────────────────────────────────
    "opsec_tip": (
        ("opsec", "opsec", "opsec"),
        ("opsec", "question", "opsec"),
        ("opsec", "answer"),
    ),
    "review": (
        ("market_review", "market_review", "gossip"),
        ("market_review", "question"),
        ("market_review", "opsec", "market_review"),
    ),
    "thread_question": (
        ("question", "question"),
        ("intro", "question"),
        ("question", "opsec"),
    ),
    "thread_answer": (
        ("answer", "answer"),
        ("answer", "opsec"),
        ("answer", "market_review", "answer"),
    ),
    "thread_gossip": (
        ("gossip", "answer"),
        ("gossip", "gossip", "question"),
    ),
    "thread_warning": (
        ("warning", "opsec"),
        ("warning", "answer", "opsec"),
    ),
    "thread_rant": (
        ("rant", "rant"),
        ("rant", "market_review"),
    ),
    "forum_contact": (
        ("pgp_notice", "contact"),
        ("wallet_notice", "contact", "opsec"),
    ),
}

#: Which post kinds each source type produces, and how often (weights).
MARKET_KINDS: tuple[tuple[str, int], ...] = (
    ("listing", 5),
    ("restock", 3),
    ("shipping_note", 2),
    ("feedback_reply", 3),
    ("dispute", 1),
    ("vacation_notice", 1),
)

FORUM_KINDS: tuple[tuple[str, int], ...] = (
    ("opsec_tip", 3),
    ("review", 3),
    ("thread_question", 2),
    ("thread_answer", 3),
    ("thread_gossip", 2),
    ("thread_warning", 1),
    ("thread_rant", 2),
)

#: Kinds that embed identifiers into the post body. Every persona that has
#: identifiers gets exactly one of these, so Phase 1's extractor has to find them
#: in prose rather than only in the profile bio.
MARKET_IDENTIFIER_KIND = "contact_notice"
FORUM_IDENTIFIER_KIND = "forum_contact"

TITLES: dict[str, tuple[str, ...]] = {
    "listing": ("{product} — in stock", "new listing: {product}", "{product} {qty}"),
    "restock": ("restock: {product}", "{product} back in stock"),
    "shipping_note": ("shipping and stealth policy", "how i pack and ship",
                      "delivery times this month"),
    "feedback_reply": ("re: feedback", "reply to buyer feedback",
                       "thanks for the review"),
    "dispute": ("dispute policy", "on refunds and reships"),
    "vacation_notice": ("vacation mode", "shop paused", "away this week"),
    "contact_notice": ("pgp key and contact", "verify before you pay",
                       "contact details and key"),
    "opsec_tip": ("opsec reminder", "small thing people get wrong",
                  "a note on handles"),
    "review": ("review: {market}", "my experience on {market}",
               "thoughts on {market}"),
    "thread_question": ("question about withdrawals", "anyone else seeing this",
                        "mirror status?"),
    "thread_answer": ("re: mirror status", "re: withdrawal delays",
                      "re: vendor question"),
    "thread_gossip": ("heard about the rebrand", "who else moved",
                      "market chatter"),
    "thread_warning": ("phishing mirror", "warning: fake address",
                       "careful with that thread"),
    "thread_rant": ("fees again", "withdrawal times", "vendor response times"),
    "forum_contact": ("my key", "contact and key"),
}
