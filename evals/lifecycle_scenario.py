"""A month of one broker's messages for the lifecycle simulation (plan 4.3 L3). Pure and deterministic.

One assistant owner, twelve clients, 25 matters that start, recur, go quiet and come back. The roster is
pre-seeded with agents last active up to 58 days before day 1, so idle agents are archived during the month
and some of their matters return. Every message carries its gold: the matter it belongs to, a new matter
(its first message), or ambiguous between a client's live matters. The people, businesses and domains are
invented and share no name with `evals/generate.py`.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

SEED = 20261030
DAYS = 30
START = datetime(2026, 11, 2, tzinfo=timezone.utc)  # day 1, a Monday
WEEKDAY_MESSAGES = (4, 7)
WEEKEND_MESSAGES = (4, 4)

# key -> (name, kind, contact, email, short references the owner uses)
CLIENTS: Dict[str, Tuple[str, str, str, str, Tuple[str, ...]]] = {
    "p-halloran": ("Priya Halloran", "person", "Priya Halloran", "priya.halloran@example.ca",
                   ("Priya", "Priya Halloran", "Ms. Halloran")),
    "p-brask": ("Teodor Brask", "person", "Teodor Brask", "teo.brask@example.ca",
                ("Teodor", "Teodor Brask", "Mr. Brask")),
    "p-strand": ("Wilhelmina Strand", "person", "Wilhelmina Strand", "w.strand@example.ca",
                 ("Wilhelmina", "Wilhelmina Strand", "Mrs. Strand")),
    "p-delgado": ("Cyrus Delgado", "person", "Cyrus Delgado", "cyrus.delgado@example.ca",
                  ("Cyrus", "Cyrus Delgado", "Mr. Delgado")),
    "p-mallory": ("Ingrid Mallory", "person", "Ingrid Mallory", "ingrid.mallory@example.ca",
                  ("Ingrid", "Ingrid Mallory", "Ms. Mallory")),
    "p-vollmer": ("Beatrix Vollmer", "person", "Beatrix Vollmer", "bea.vollmer@example.ca",
                  ("Beatrix", "Beatrix Vollmer", "Ms. Vollmer")),
    "b-saltmarsh": ("Saltmarsh Bakery", "business", "Linnea Cardew", "office@saltmarshbakery.example.com",
                    ("the bakery", "Saltmarsh", "Saltmarsh Bakery")),
    "b-lanternfield": ("Lanternfield Electrical", "business", "Arvid Penhale", "arvid@lanternfield.example.com",
                       ("Lanternfield", "Lanternfield Electrical", "Arvid's company")),
    "b-hearthstone": ("Hearthstone Dental", "business", "Maren Quaile", "admin@hearthstonedental.example.com",
                      ("Hearthstone", "Hearthstone Dental", "the dental office")),
    "b-bellwether": ("Bellwether Courier", "business", "Dagny Elsworth", "dagny@bellwethercourier.test",
                     ("Bellwether", "Bellwether Courier", "the courier company")),
    "b-mossbank": ("Mossbank Tiling", "business", "Rufus Kettering", "rufus@mossbanktiling.test",
                   ("Mossbank", "Mossbank Tiling", "Rufus's tiling company")),
    "b-orchardgate": ("Orchardgate Cidery", "business", "Seraphine Lowe", "seraphine@orchardgatecidery.test",
                      ("Orchardgate", "Orchardgate Cidery", "the cidery")),
    "owner": ("the owner's office", "owner", "", "", ("the office",)),
}

# One row per matter: (id, client, workflow, line, carrier, agent name, first day in the month, seeded days
# ago (None for a matter that starts in the month), days it stays active, facts used by the templates).
MATTERS: List[Tuple[str, str, str, str, str, str, int, Optional[int], int, Dict[str, str]]] = [
    ("m01", "p-halloran", "renewal", "home", "Bluefinch Mutual", "Halloran home renewal", 1, 4, 14,
     {"renews": "December 1"}),
    ("m02", "p-halloran", "claim", "auto", "Tamarack General", "Halloran auto claim", 5, None, 20,
     {"incident": "was rear-ended at a red light on Barton Street", "short": "rear-end collision"}),
    ("m03", "p-halloran", "endorsement", "home", "Bluefinch Mutual", "Halloran basement suite endorsement", 19,
     None, 11, {"change": "is finishing a basement apartment and will rent it out from January"}),
    ("m04", "p-brask", "quote", "auto", "Harrowgate Assurance", "Brask hybrid auto quote", 2, None, 12,
     {"detail": "He is buying a 2024 hybrid hatchback and wants it insured before pickup on the 16th.",
      "short": "hybrid"}),
    ("m05", "p-brask", "cancellation", "auto", "Harrowgate Assurance", "Brask pickup cancellation", 12, None, 10,
     {"thing": "his old pickup truck", "effective": "November 20"}),
    ("m06", "p-strand", "claim", "home", "Pinegrove Underwriters", "Strand water damage claim", 9, 47, 15,
     {"incident": "had a pipe burst in the upstairs bathroom", "short": "water damage"}),
    ("m07", "p-delgado", "quote", "home", "Tamarack General", "Delgado house quote", 3, None, 14,
     {"detail": "It is for the semi-detached house he closes on next month.", "short": "new house"}),
    ("m08", "p-delgado", "billing", "auto", "Tamarack General", "Delgado auto billing", 14, None, 8,
     {"issue": "his November auto payment was returned by the bank"}),
    ("m09", "p-mallory", "renewal", "auto", "Bluefinch Mutual", "Mallory auto renewal", 21, 26, 8,
     {"renews": "November 30"}),
    ("m10", "p-vollmer", "quote", "home", "Pinegrove Underwriters", "Vollmer townhouse quote", 7, None, 16,
     {"detail": "It is for her townhouse; her current policy lapses at the end of the month.",
      "short": "townhouse"}),
    ("m11", "p-vollmer", "quote", "seasonal dwelling", "Harrowgate Assurance", "Vollmer cottage quote", 16, None,
     14, {"detail": "It is for a lakeside cottage near Bancroft she just inherited.", "short": "cottage"}),
    ("m12", "b-saltmarsh", "renewal", "CGL", "Tamarack General", "Saltmarsh Bakery CGL renewal", 1, None, 18,
     {"renews": "December 15"}),
    ("m13", "b-saltmarsh", "coi", "CGL", "Tamarack General", "Saltmarsh Bakery winter market certificate", 6,
     None, 10, {"event": "the Rosebay Winter Market on November 22", "holder": "Rosebay Market Association",
                "short": "winter market"}),
    ("m14", "b-saltmarsh", "claim", "commercial property", "Tamarack General", "Saltmarsh Bakery freezer claim",
     20, None, 10, {"incident": "lost the walk-in freezer and its stock when the compressor failed",
                    "short": "freezer"}),
    ("m15", "b-lanternfield", "coi", "CGL", "Pinegrove Underwriters", "Lanternfield Halberd Lane certificate", 11,
     38, 10, {"event": "the Halberd Lane condo site", "holder": "Greyfriar Developments",
              "short": "Halberd Lane"}),
    ("m16", "b-lanternfield", "endorsement", "CGL", "Pinegrove Underwriters", "Lanternfield subcontractor endorsement",
     15, None, 10, {"change": "hired two subcontractors who need to be added to the policy"}),
    ("m17", "b-hearthstone", "billing", "commercial property", "Bluefinch Mutual", "Hearthstone Dental billing", 1,
     15, 11, {"issue": "the October premium was charged twice"}),
    ("m18", "b-hearthstone", "quote", "commercial property", "Bluefinch Mutual", "Hearthstone Ajax location quote",
     4, None, 18, {"detail": "It is for the second clinic they are leasing in Ajax.", "short": "Ajax clinic"}),
    ("m19", "b-bellwether", "quote", "CGL", "Harrowgate Assurance", "Bellwether Courier CGL quote", 10, None, 16,
     {"detail": "They run a same-day courier service with six drivers.", "short": "liability"}),
    ("m20", "b-bellwether", "claim", "CGL", "Harrowgate Assurance", "Bellwether gate damage claim", 23, None, 7,
     {"incident": "had a driver back a van into a customer's gate", "short": "gate"}),
    ("m21", "b-mossbank", "renewal", "CGL", "Pinegrove Underwriters", "Mossbank Tiling CGL renewal", 1, 2, 14,
     {"renews": "November 25"}),
    ("m22", "b-mossbank", "coi", "CGL", "Pinegrove Underwriters", "Mossbank Ellery Road certificate", 13, None, 6,
     {"event": "a bathroom renovation at 14 Ellery Road", "holder": "Elsmere Property Management",
      "short": "Ellery Road"}),
    ("m23", "b-orchardgate", "claim", "commercial property", "Bluefinch Mutual", "Orchardgate hail claim", 25, 33, 5,
     {"incident": "had hail damage to the tasting room roof", "short": "hail"}),
    ("m24", "b-orchardgate", "renewal", "commercial property", "Bluefinch Mutual",
     "Orchardgate property renewal", 17, None, 13, {"renews": "January 1"}),
    ("m25", "owner", "errand", "", "", "Networking breakfast booking", 8, None, 10,
     {"task": "book a private room for the broker networking breakfast on November 27, for 20 people",
      "short": "networking breakfast"}),
]

# Agents from before the month whose matters never come back: (id, client, agent name, days ago, first
# request, last response).
HISTORY: List[Tuple[str, str, str, int, str, str]] = [
    ("h01", "p-brask", "Brask deer collision claim", 52,
     "Open an auto claim for Teodor Brask <teo.brask@example.ca>: he hit a deer near Tilbury. Policy AU-731904 "
     "with Harrowgate Assurance.",
     "Claim closed; Harrowgate Assurance paid $4,180 for the front end and Teodor confirmed receipt."),
    ("h02", "owner", "Reminder: order new business cards", 58,
     "Remind me to order new business cards on October 1.",
     "Reminder set for October 1 at 9:00 AM: order new business cards."),
    ("h03", "p-delgado", "Email to Cyrus Delgado", 44,
     "Email Cyrus Delgado (cyrus.delgado@example.ca) to thank him for referring his sister.",
     "Sent to cyrus.delgado@example.ca. Cyrus replied: \"Happy to help, talk soon.\""),
    ("h04", "b-saltmarsh", "Saltmarsh Bakery wedding fair certificate", 41,
     "Issue a certificate of insurance for Saltmarsh Bakery <office@saltmarshbakery.example.com> for the "
     "Riverside wedding fair; holder Riverside Events. Policy CGL-208117 with Tamarack General.",
     "Certificate issued to Riverside Events for the wedding fair and sent to Linnea Cardew."),
    ("h05", "b-hearthstone", "Hearthstone Dental CGL renewal", 20,
     "Handle the CGL renewal for Hearthstone Dental <admin@hearthstonedental.example.com>. Policy CGL-554213 "
     "with Bluefinch Mutual.",
     "Renewal bound with Bluefinch Mutual; documents sent to Maren Quaile."),
    ("h06", "b-mossbank", "Mossbank Tiling CGL quote", 55,
     "Get a CGL quote for Mossbank Tiling <rufus@mossbanktiling.test>; Rufus Kettering is the contact.",
     "Pinegrove Underwriters bound policy CGL-660982; Rufus signed the application."),
    ("h07", "owner", "Reminder: renew the office internet plan", 34,
     "Remind me to renew the office internet plan on September 30.",
     "Reminder set for September 30 at 9:00 AM: renew the office internet plan."),
    ("h08", "owner", "Office cleaning service search", 12,
     "Find a commercial cleaning service for the office, twice a week.",
     "Found three services; Brightline Cleaning quoted $180 a visit. Waiting on your pick."),
    ("h09", "b-bellwether", "Email to Bellwether Courier", 22,
     "Email Dagny Elsworth at Bellwether Courier (dagny@bellwethercourier.test) to introduce the brokerage.",
     "Sent to dagny@bellwethercourier.test. Dagny replied: \"Thanks, we'll be in touch about our insurance.\""),
]

# workflow -> opening message templates (a new matter)
OPENERS: Dict[str, List[str]] = {
    "renewal": [
        "{name}'s {line} renewal is coming up on {renews} ({carrier}, policy {policy}). Start it and ask "
        "{contact_first} whether anything changed this year.",
        "Can you get the {line} renewal going for {name}? {carrier} renews on {renews}.",
    ],
    "claim": [
        "{name} {incident}. Open a {line} claim with {carrier} under policy {policy} and ask {contact_first} for "
        "photos.",
        "Bad news from {short_ref}: {pronoun_subject} {incident}. Please start a {line} claim with {carrier}.",
    ],
    "quote": [
        "New one: {name} wants {a_line} quote. {detail} Try {carrier} first.",
        "Please get {a_line} quote for {name} ({email}). {detail}",
    ],
    "coi": [
        "{name} needs a certificate of insurance for {event}; the holder is {holder}. Issue it under policy "
        "{policy}.",
        "Can you send a certificate for {short_ref} for {event}? Holder: {holder}.",
    ],
    "endorsement": [
        "{name} {change}. Please process an endorsement on the {line} policy ({policy}).",
        "Heads up, {short_ref} {change}. Ask {carrier} to endorse the {line} policy.",
    ],
    "billing": [
        "{name} says {issue}. Sort it out with {carrier}.",
        "Billing problem for {short_ref}: {issue}. Can you fix it with {carrier}?",
    ],
    "cancellation": [
        "{name} sold {thing}. Cancel it off the auto policy effective {effective} and ask {carrier} for the "
        "refund.",
        "Please cancel {thing} for {short_ref}, effective {effective}; get the refund from {carrier}.",
    ],
    "errand": [
        "Can you {task}? Somewhere near the office.",
        "Please {task}.",
    ],
}

# workflow -> follow-up templates; each names the matter by its kind or a detail, never by client alone.
FOLLOW_UPS: Dict[str, List[str]] = {
    "renewal": [
        "Any word from {carrier} on the renewal terms for {short_ref}?",
        "{contact_first} confirmed nothing changed. Tell {carrier} to go ahead with the {line} renewal as is.",
        "The renewal premium for {short_ref} came in high. Ask {carrier} to re-market it.",
        "Did {contact_first} sign the renewal forms for policy {policy} yet?",
        "Send {contact_first} the renewal summary and remind {pronoun_object} it renews on {renews}.",
        "Is the {line} renewal for {short_ref} bound?",
    ],
    "claim": [
        "Has an adjuster been assigned on the {claim_short} claim for {short_ref}?",
        "{contact_first} sent photos and a repair estimate for the {claim_short} claim. Pass them to the adjuster.",
        "What's the status of {short_ref}'s {claim_short} claim?",
        "Ask the adjuster when {short_ref} can expect the payout on the {claim_short} claim.",
        "{contact_first} wants to know if the deductible applies to the {claim_short} claim. Can you check?",
    ],
    "quote": [
        "Did {carrier} come back with a price on the {quote_short} quote for {short_ref}?",
        "{contact_first} wants one more market on the {quote_short} quote. Get a second number.",
        "Any update on the {quote_short} quote for {short_ref}?",
        "{short_ref} accepted the {quote_short} quote. Bind it and send the documents.",
        "The underwriter needs more details for the {quote_short} quote; ask {contact_first} for them.",
    ],
    "coi": [
        "Did the certificate for {coi_short} go out to {holder}?",
        "{holder} wants to be named additional insured on the {coi_short} certificate. Reissue it.",
        "{contact_first} says {holder} never got the {coi_short} certificate. Can you resend it?",
    ],
    "endorsement": [
        "Did {carrier} confirm the endorsement for {short_ref}?",
        "Send {contact_first} the endorsement paperwork for signature.",
        "Is the policy change for {short_ref} done yet? {contact_first} asked about the endorsement.",
    ],
    "billing": [
        "Did {carrier} fix the billing issue for {short_ref}?",
        "{contact_first} wants monthly pre-authorized payments after the billing mix-up. Set it up.",
        "Any reply from {carrier} accounting on {short_ref}'s billing?",
    ],
    "cancellation": [
        "Has the refund come through on the cancellation for {short_ref}?",
        "Did {carrier} confirm the cancellation for {short_ref}?",
    ],
    "errand": [
        "Any options yet for the {errand_short}?",
        "For the {errand_short}, go with whichever place has a projector.",
        "Did the {errand_short} booking get confirmed?",
    ],
}

AMBIGUOUS_TEMPLATES = [
    "Did {contact_first} get back to us?",
    "{contact_first} from {short_ref} called. Can you see what {pronoun_subject} needs?",
    "Can you follow up with {short_ref}? They're waiting on us.",
    "Any update for {short_ref}?",
]

# (day, client) pairs where a message names only the client while two of its matters are live.
AMBIGUOUS_DAYS: List[Tuple[int, str]] = [
    (6, "b-hearthstone"), (8, "p-halloran"), (9, "b-saltmarsh"), (13, "p-brask"), (14, "b-mossbank"),
    (15, "p-delgado"), (17, "b-lanternfield"), (18, "p-vollmer"), (25, "b-bellwether"), (27, "b-orchardgate"),
]

_POLICY_PREFIX = {"home": "HO", "auto": "AU", "CGL": "CGL", "commercial property": "CP", "seasonal dwelling": "SD"}


def day_start(day: int) -> datetime:
    """Midnight UTC at the start of simulated day `day` (day 1 is START)."""
    return START + timedelta(days=day - 1)


def _pronouns(client_key: str) -> Tuple[str, str]:
    kind = CLIENTS[client_key][1]
    if kind != "person":
        return "they", "them"
    she = {"p-halloran", "p-strand", "p-mallory", "p-vollmer"}
    return ("she", "her") if client_key in she else ("he", "him")


def _a(noun: str) -> str:
    return f"an {noun}" if noun[:1] in ("a", "e", "i", "o", "u") else f"a {noun}"


def _first(contact: str) -> str:
    return contact.split()[0] if contact else "you"


def _fields(matter: Dict[str, Any], rng: random.Random) -> Dict[str, str]:
    name, kind, contact, email, shorts = CLIENTS[matter["client_key"]]
    subject, obj = _pronouns(matter["client_key"])
    facts = matter["facts"]
    short = facts.get("short", "")
    return {
        "name": name, "contact_first": _first(contact), "email": email, "short_ref": rng.choice(shorts),
        "line": matter["line"], "a_line": _a(matter["line"]), "carrier": matter["carrier"], "policy": matter["policy"],
        "pronoun_subject": subject, "pronoun_object": obj, "claim_short": short, "quote_short": short,
        "coi_short": short, "errand_short": short, **facts,
    }


def _seed_texts(matter: Dict[str, Any], rng: random.Random) -> Tuple[str, str]:
    """The first request and last response of a matter's pre-seeded agent."""
    f = _fields(matter, rng)
    opener = OPENERS[matter["workflow"]][0].format(**f)
    request = f"{opener} Contact {CLIENTS[matter['client_key']][2]} <{f['email']}>."
    response = (f"Emailed {f['contact_first']} at {f['email']} (thread {matter['thread_id']}) about policy "
                f"{matter['policy']} with {matter['carrier']}; waiting to hear back.")
    return request, response


def _times(rng: random.Random, day: int, count: int) -> List[datetime]:
    weekend = day_start(day).weekday() >= 5
    first, last = (9 * 60, 17 * 60) if weekend else (8 * 60, 18 * 60)
    minutes = sorted(rng.sample(range(first, last), count))
    return [day_start(day) + timedelta(minutes=m) for m in minutes]


def build_scenario(seed: int = SEED, days: int = DAYS) -> Dict[str, Any]:
    """Return {seed, start, days, clients, matters, seed_roster, messages}; the same seed gives the same month.

    Each message: {msg_id, day, at, text, client_key, gold: {kind: new | matter | ambiguous, matter_ids}}.
    """
    rng = random.Random(seed)
    used: set = set()

    def unique(make: Any) -> str:
        while True:
            value = make()
            if value not in used:
                used.add(value)
                return value

    matters: Dict[str, Dict[str, Any]] = {}
    for mid, client_key, workflow, line, carrier, agent_name, first_day, seeded, span, facts in MATTERS:
        prefix = _POLICY_PREFIX.get(line, "")
        matters[mid] = {
            "matter_id": mid, "client_key": client_key, "workflow": workflow, "line": line, "carrier": carrier,
            "agent_name": agent_name, "first_day": first_day, "seeded_days_ago": seeded, "span": span,
            "facts": dict(facts),
            "policy": unique(lambda: f"{prefix}-{rng.randint(100000, 999999)}") if prefix else "",
            "thread_id": unique(lambda: f"{rng.getrandbits(64):016x}"),
        }

    seed_roster: List[Dict[str, Any]] = []
    for matter in matters.values():
        if matter["seeded_days_ago"] is None:
            continue
        request, response = _seed_texts(matter, rng)
        seed_roster.append({"matter_id": matter["matter_id"], "client_key": matter["client_key"],
                            "name": matter["agent_name"], "first_request": request, "last_response": response,
                            "days_ago": matter["seeded_days_ago"]})
    for hid, client_key, agent_name, days_ago, request, response in HISTORY:
        seed_roster.append({"matter_id": hid, "client_key": client_key, "name": agent_name,
                            "first_request": request, "last_response": response, "days_ago": days_ago})
        matters[hid] = {"matter_id": hid, "client_key": client_key, "workflow": "history", "agent_name": agent_name,
                        "first_day": None, "seeded_days_ago": days_ago, "span": 0, "facts": {}}
    for row in seed_roster:
        hour = rng.randint(9, 16)
        row["last_active_at"] = (day_start(1) - timedelta(days=row["days_ago"]) + timedelta(hours=hour)).isoformat()
    seed_roster.sort(key=lambda row: row["last_active_at"])

    planned: Dict[int, List[Tuple[str, Any]]] = {day: [] for day in range(1, DAYS + 1)}
    for matter in matters.values():
        if matter["first_day"]:
            planned[matter["first_day"]].append(("open", matter["matter_id"]))
    for day, client_key in AMBIGUOUS_DAYS:
        planned[day].append(("ambiguous", client_key))
    used_follow_ups: Dict[str, List[int]] = {mid: [] for mid in matters}
    for day in range(1, DAYS + 1):
        low, high = WEEKEND_MESSAGES if day_start(day).weekday() >= 5 else WEEKDAY_MESSAGES
        target = rng.randint(low, high)
        live = [m for m in matters.values()
                if m["first_day"] and m["first_day"] < day <= m["first_day"] + m["span"]]
        rng.shuffle(live)
        live.sort(key=lambda m: len(used_follow_ups[m["matter_id"]]))
        for matter in live[: max(0, target - len(planned[day]))]:
            planned[day].append(("follow", matter["matter_id"]))
            used_follow_ups[matter["matter_id"]].append(day)

    messages: List[Dict[str, Any]] = []
    follow_index: Dict[str, int] = {}
    for day in range(1, days + 1):
        events = planned[day]
        rng.shuffle(events)
        # A matter's opening message comes before anything else about it that day.
        events.sort(key=lambda event: 0 if event[0] == "open" else 1)
        for n, ((kind, ref), at) in enumerate(zip(events, _times(rng, day, len(events))), start=1):
            if kind == "ambiguous":
                client_key = ref
                candidates = sorted(m["matter_id"] for m in matters.values() if m["client_key"] == client_key
                                    and m["first_day"] and m["first_day"] < day <= m["first_day"] + m["span"])
                if len(candidates) < 2:
                    raise ValueError(f"day {day}: {client_key} has fewer than two live matters")
                f = _fields(matters[candidates[0]], rng)
                text = rng.choice(AMBIGUOUS_TEMPLATES).format(**f)
                gold = {"kind": "ambiguous", "matter_ids": candidates}
            else:
                matter = matters[ref]
                client_key = matter["client_key"]
                f = _fields(matter, rng)
                if kind == "open" and matter["seeded_days_ago"] is None:
                    text = rng.choice(OPENERS[matter["workflow"]]).format(**f)
                    gold_kind = "new"
                else:  # a follow-up, or a pre-seeded matter coming back
                    pool = FOLLOW_UPS[matter["workflow"]]
                    start = follow_index.setdefault(ref, rng.randrange(len(pool)))
                    text = pool[start % len(pool)].format(**f)
                    follow_index[ref] = start + 1
                    gold_kind = "matter"
                gold = {"kind": gold_kind, "matter_ids": [ref]}
            text = text[0].upper() + text[1:]
            messages.append({"msg_id": f"d{day:02d}-{n}", "day": day, "at": at.isoformat(), "text": text,
                             "client_key": client_key, "gold": gold})

    clients = {key: {"name": row[0], "kind": row[1], "contact": row[2], "email": row[3]}
               for key, row in CLIENTS.items()}
    return {"seed": seed, "start": START.isoformat(), "days": days, "clients": clients, "matters": matters,
            "seed_roster": seed_roster, "messages": messages}


TICK_HOUR = 7  # the daily lifecycle tick runs at 07:00, before the first message


def tick_time(day: int) -> datetime:
    return day_start(day) + timedelta(hours=TICK_HOUR)


def ideal_timeline(scenario: Dict[str, Any], idle_days: int = 30, days: Optional[int] = None) -> Dict[str, Any]:
    """The roster a perfect router would keep: one agent per matter, archived by the code's rule.

    An agent is archived once idle for more than `idle_days` (checked at the daily tick and at every turn
    start, as the server does); a message for its matter revives it. Returns per-day live counts (end of
    day) and the archive and revive events, each as (day, matter id).
    """
    days = days or scenario["days"]
    last: Dict[str, datetime] = {row["matter_id"]: datetime.fromisoformat(row["last_active_at"])
                                 for row in scenario["seed_roster"]}
    archived: set = set()
    archives: List[Tuple[int, str]] = []
    revives: List[Tuple[int, str]] = []

    def sweep(now: datetime, day: int) -> None:
        for matter_id, moment in last.items():
            if matter_id not in archived and moment < now - timedelta(days=idle_days):
                archived.add(matter_id)
                archives.append((day, matter_id))

    live: List[int] = []
    for day in range(1, days + 1):
        sweep(tick_time(day), day)
        for message in (m for m in scenario["messages"] if m["day"] == day):
            at = datetime.fromisoformat(message["at"])
            sweep(at, day)
            if message["gold"]["kind"] == "ambiguous":
                continue
            matter_id = message["gold"]["matter_ids"][0]
            if matter_id in archived:
                archived.discard(matter_id)
                revives.append((day, matter_id))
            last[matter_id] = at
        live.append(len(last) - len(archived))
    return {"live": live, "archives": archives, "revives": revives}
