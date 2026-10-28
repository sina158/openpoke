"""Seeded generator for the synthetic agent pool and the golden routing set.

`python -m evals.generate` rewrites evals/fixtures/agents_pool.jsonl and evals/golden/routing.jsonl.
Each case is built from tuples first (client, policy line, carrier, workflow, recency), then phrased with one
of a few hand-written templates for its category. Gold labels come from the tuples, never from a model.
All people, businesses and addresses are invented; domains are example.com, example.ca or .test.
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

SEED = 20261028
EVALS_DIR = Path(__file__).resolve().parent
POOL_PATH = EVALS_DIR / "fixtures" / "agents_pool.jsonl"
GOLDEN_PATH = EVALS_DIR / "golden" / "routing.jsonl"
FILLER_COUNT = 5000

CATEGORY_COUNTS: Dict[str, int] = {
    "explicit": 20,
    "entity": 15,
    "paraphrase": 20,
    "pronoun": 15,
    "near_duplicate": 20,
    "should_create": 20,
    "ambiguous": 10,
    "parallel": 5,
    "agent_message": 5,
}

# workflow -> (label used in agent names, paraphrase that avoids the label)
WORKFLOWS: Dict[str, Tuple[str, str]] = {
    "quote": ("quote", "pricing"),
    "renewal": ("renewal", "upcoming term"),
    "endorsement": ("endorsement", "policy change"),
    "coi": ("COI", "certificate of insurance"),
    "claim": ("claim", "loss report"),
    "cancellation": ("cancellation", "request to cancel"),
    "billing": ("billing", "overdue invoice"),
}
PERSON_LINES = [("auto", "AU"), ("home", "HO")]
BUSINESS_LINES = [("CGL", "CGL"), ("commercial property", "CP")]
CARRIERS = ["Maplecrest Mutual", "Northern Shield Insurance", "Granite Bay Assurance", "Cobalt Lake General",
            "Lighthouse Point Insurance", "Fieldstone Underwriters"]
RECENCY_DAYS = [0, 7, 45, 350]

CASE_CITIES = ["Guelph", "Barrie", "Kingston", "Peterborough", "Oshawa", "Belleville", "Orillia", "Sudbury",
               "Kitchener", "Brantford", "Sarnia", "Cobourg", "Huntsville", "Stratford", "Waterloo"]
FILLER_CITIES = ["Timmins", "Kenora", "Pembroke", "Owen Sound", "Collingwood", "Midland", "Woodstock", "Simcoe",
                 "Lindsay", "Napanee", "Brockville", "Cornwall", "Bracebridge", "Parry Sound", "Hawkesbury"]

CASE_FIRST = {
    "she": ["Mara", "Lena", "Odile", "Farah", "Ines", "Talia", "Noor", "Greta", "Celine", "Yara", "Bianca",
            "Hana", "Rosalind", "Imke", "Delphine", "Anouk", "Marisol", "Tamsin", "Ottilie", "Saoirse"],
    "he": ["Tobias", "Rafael", "Emeka", "Lars", "Dmitri", "Kenji", "Bram", "Idris", "Mateo", "Callum", "Oskar",
           "Thiago", "Anselm", "Joaquin", "Ruben", "Felix", "Silas", "Ewan", "Marek", "Dario"],
}
CASE_SURNAMES = [
    "Okafor", "Lindqvist", "Varga", "Petrakis", "Abernathy", "Castellano", "Duquesne", "Halvorsen", "Iwasaki",
    "Kowalczyk", "Moreau", "Nakashima", "Ostrander", "Quintero", "Rasmussen", "Szabo", "Thorne", "Underhill",
    "Whitcombe", "Yilmaz", "Zielinski", "Achterberg", "Calloway", "Dimitriou", "Ekwueme", "Fairweather",
    "Grimaldi", "Hollis", "Ivanova", "Jansen", "Kaminski", "Lachance", "Marchetti", "Novak", "Oyelaran",
    "Pemberton", "Rinaldi", "Sorensen", "Ueda", "Valentin", "Wexler", "Yorke", "Zamora", "Aldous", "Corrigan",
    "Delacroix", "Esposito", "Falconer", "Hargreave", "Ibarra", "Jorgensen", "Kirkland", "Lowther", "Mbatha",
    "Northcote", "Oduya", "Prescott", "Radcliffe", "Tamura", "Uribe", "Vandermeer", "Winslow",
]
OCCUPATIONS = ["nurse", "teacher", "electrician", "pharmacist", "architect", "paramedic", "librarian", "chef",
               "accountant", "carpenter", "physiotherapist", "dental hygienist"]
CASE_STEMS = [
    "Harbourline", "Lakeshore", "Cedarvale", "Brightwater", "Ironbridge", "Foxglove", "Kestrel", "Millbrook",
    "Oakhaven", "Pinecrest", "Quarrystone", "Riverbend", "Silverleaf", "Thornbury", "Upland", "Wildrose",
    "Amberfield", "Bluestem", "Coldspring", "Driftwood", "Elmstead", "Fernhill", "Glenwood", "Hollowtree",
    "Kingsmere", "Larchmont", "Maplestone", "Northgate", "Pebblebrook", "Ravenwood", "Stonegate", "Tidewater",
    "Valleyview", "Westbrook", "Yellowbirch", "Ashgrove", "Birchmount", "Copperleaf", "Dovecote", "Eastwind",
    "Goldcrest", "Highmark", "Ivywood", "Kettle Creek", "Moonbeam", "Nettlefield", "Oxbow", "Redwing",
    "Sandpiper", "Verdant", "Willowmere", "Zephyr", "Larkspur", "Mistral", "Pinnacle",
]
# business kind -> paraphrase that shares no word with the kind
BUSINESS_KINDS: List[Tuple[str, str]] = [
    ("Plumbing", "pipe and drain contractor"), ("Bakery", "bread shop"), ("Dental Clinic", "dentist's office"),
    ("Landscaping", "yard-care crew"), ("Auto Body", "collision repair shop"), ("Catering", "event food company"),
    ("Roofing", "shingle contractor"), ("Florist", "flower shop"), ("Fitness Studio", "gym"),
    ("Printing", "signage company"), ("Daycare", "childcare centre"), ("Hardware", "tool store"),
    ("Brewing", "craft beer maker"), ("Veterinary", "animal clinic"), ("Electrical", "wiring contractor"),
]
CONTACT_FIRST = ["Jules", "Robin", "Morgan", "Casey", "Avery", "Quinn", "Rowan", "Sasha", "Devon", "Emerson"]
CONTACT_SURNAMES = ["Beaulieu", "Osei", "Lund", "Ferreira", "Mahler", "Ngata", "Rivas", "Sato", "Tull", "Brandt"]

FILLER_FIRST = ["Ada", "Bea", "Cora", "Dina", "Edda", "Fay", "Gwen", "Hope", "Iva", "June", "Kit", "Liv", "Mae",
                "Nell", "Opal", "Pia", "Rue", "Sage", "Tess", "Uma", "Vera", "Zoe", "Abe", "Ben", "Cal",
                "Dex", "Eli", "Gus", "Hal", "Ike", "Jon", "Kai", "Lou", "Max", "Ned", "Otis", "Rex", "Sam",
                "Ted", "Vic", "Wes"]
FILLER_SURNAME_HEADS = ["Ash", "Brook", "Clay", "Dun", "Elk", "Fen", "Gale", "Hart", "Kell", "Lang", "Mor",
                        "Nor", "Pell", "Rook", "Sand", "Tall", "Vey", "Wick", "Holt", "Marsh"]
FILLER_SURNAME_TAILS = ["ford", "by", "ley", "ton", "well", "more", "wood", "son", "ham", "stead"]
FILLER_STEMS = ["Alder", "Basalt", "Cinder", "Delta", "Ember", "Flint", "Garnet", "Heron", "Indigo", "Jasper",
                "Kelp", "Lumen", "Marble", "Nimbus", "Onyx", "Pewter", "Quill", "Rustic", "Slate", "Topaz",
                "Umber", "Vale", "Walnut", "Yarrow", "Zinc", "Beacon", "Clover", "Dune", "Gully"]
REMINDER_TASKS = ["renew the office lease", "call the bank about the line of credit", "book the team offsite",
                  "submit the licence CE credits", "pay the payroll remittance", "review the E&O policy",
                  "send the quarterly production report", "water the office plants", "back up the laptop",
                  "order printer toner", "file the HST return", "update the CRM export"]
SEARCH_TOPICS = ["flights to Halifax", "a caterer for the client breakfast", "the new flood mapping rules",
                 "a notary near the office", "used delivery vans", "hotels near the broker conference",
                 "the provincial auto reform summary", "a translator for a client meeting", "parking near the office",
                 "a cheaper phone plan"]
MONTHS = ["Nov", "Dec", "Jan", "Feb", "Mar"]


def _a(noun: str) -> str:
    return f"an {noun}" if noun[0] in "aeiou" else f"a {noun}"


def _slug(text: str) -> str:
    out = "".join(ch.lower() if ch.isalnum() else "-" for ch in text.strip()).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out


@dataclass
class Client:
    key: str
    name: str
    kind: str  # "person" | "business"
    city: str
    email: str
    contact: str
    descriptor: str  # occupation or business paraphrase; never part of an agent name
    pronoun: str = "they"
    stem: str = ""

    @property
    def first(self) -> str:
        return self.contact.split()[0]


@dataclass
class Agent:
    name: str
    client: Optional[Client]
    first_request: str
    last_response: str
    days_ago: int
    workflow: str = ""
    policy_no: str = ""
    thread_id: str = ""
    carrier: str = ""
    id: str = ""

    def to_pool(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "first_request": self.first_request,
            "last_response": self.last_response,
            "last_active_days_ago": self.days_ago,
            "client_key": self.client.key if self.client else "",
            "status": "active",
        }


class World:
    """Holds the RNG, mints unique entities and agents, and enforces unique agent names."""

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.agents: List[Agent] = []
        self.names: set = set()  # names in the current scope (one case, or the filler)
        self.used: set = set()  # entity values, unique pool-wide
        self._first = {p: list(v) for p, v in CASE_FIRST.items()}
        self.new_scope()

    def new_scope(self) -> None:
        """Start a case. Case agents only ever share a roster with their own case and the filler, so names
        and clients need to be distinct within a case, not across cases."""
        self.names = set()
        self._surnames = list(CASE_SURNAMES)
        self._stems = list(CASE_STEMS)
        self.rng.shuffle(self._surnames)
        self.rng.shuffle(self._stems)

    def _unique(self, make: Callable[[], str]) -> str:
        while True:
            value = make()
            if value not in self.used:
                self.used.add(value)
                return value

    def policy_no(self, prefix: str) -> str:
        return self._unique(lambda: f"{prefix}-{self.rng.randint(1000, 9999)}-{self.rng.randint(10, 99)}")

    def thread_id(self) -> str:
        return self._unique(lambda: f"{self.rng.getrandbits(64):016x}")

    def person(self, pronoun: Optional[str] = None, surname: Optional[str] = None, city: Optional[str] = None,
               key: Optional[str] = None) -> Client:
        pronoun = pronoun or self.rng.choice(["she", "he"])
        surname = surname or self._surnames.pop()
        first = self.rng.choice(self._first[pronoun])
        name = f"{first} {surname}"
        return Client(
            key=key or f"hh-{_slug(surname)}",
            name=name,
            kind="person",
            city=city or self.rng.choice(CASE_CITIES),
            email=f"{_slug(first)}.{_slug(surname)}@example.ca",
            contact=name,
            descriptor=self.rng.choice(OCCUPATIONS),
            pronoun=pronoun,
        )

    def business(self, stem: Optional[str] = None, kind: Optional[Tuple[str, str]] = None,
                 city: Optional[str] = None) -> Client:
        stem = stem or self._stems.pop()
        kind_name, descriptor = kind or self.rng.choice(BUSINESS_KINDS)
        name = f"{stem} {kind_name}"
        contact = f"{self.rng.choice(CONTACT_FIRST)} {self.rng.choice(CONTACT_SURNAMES)}"
        domain = _slug(name).replace("-", "")
        return Client(
            key=f"biz-{_slug(name)}",
            name=name,
            kind="business",
            city=city or self.rng.choice(CASE_CITIES),
            email=f"office@{domain}.example.com",
            contact=contact,
            descriptor=descriptor,
            stem=stem,
        )

    def add(self, agent: Agent) -> Agent:
        if agent.name in self.names:
            raise ValueError(f"duplicate agent name: {agent.name}")
        self.names.add(agent.name)
        self.agents.append(agent)
        return agent

    def matter(self, client: Client, workflow: str, *, name: Optional[str] = None, days_ago: Optional[int] = None,
               detail: str = "") -> Agent:
        rng = self.rng
        line, prefix = rng.choice(PERSON_LINES if client.kind == "person" else BUSINESS_LINES)
        carrier = rng.choice(CARRIERS)
        policy_no = self.policy_no(prefix)
        thread_id = self.thread_id()
        label = WORKFLOWS[workflow][0]
        if name is None:
            name = f"{client.name} {line} {label}" if client.kind == "person" else f"{client.name} {label}"
        first_request = (
            f"{_REQUEST[workflow].format(line=line, a_line=_a(line))} for {client.name}, {_a(client.descriptor)} in "
            f"{client.city}{detail}. {line[0].upper() + line[1:]} policy {policy_no} with {carrier}; "
            f"contact {client.contact} <{client.email}>."
        )
        status = _STATUS[workflow].format(carrier=carrier, claim_no=f"CL-{rng.randint(100000, 999999)}")
        last_response = (
            f"Emailed {client.contact} at {client.email} (thread {thread_id}) about policy {policy_no}; {status}."
        )
        return self.add(Agent(name, client, first_request, last_response,
                              rng.choice(RECENCY_DAYS) if days_ago is None else days_ago,
                              workflow, policy_no, thread_id, carrier))

    def email_task(self, client: Client, ask: str, reply: str, days_ago: int = 0) -> Agent:
        thread_id = self.thread_id()
        first_request = f"Email {client.name} ({client.email}) to ask {ask}."
        last_response = f"Sent to {client.email} on thread {thread_id}. {client.first} replied: \"{reply}\""
        return self.add(Agent(f"Email to {client.name}", client, first_request, last_response, days_ago,
                              "email", "", thread_id))

    def reminder(self, task: str, days_ago: int = 0) -> Agent:
        day = f"{self.rng.choice(MONTHS)} {self.rng.randint(1, 28)}"
        return self.add(Agent(f"Reminder: {task}", None, f"Remind me to {task} on {day}.",
                              f"Reminder set for {day} at 9:00 AM: {task}.", days_ago, "reminder"))


_REQUEST = {
    "quote": "Get {a_line} quote",
    "renewal": "Handle the {line} renewal",
    "endorsement": "Process {a_line} endorsement",
    "coi": "Issue a certificate of insurance under the {line} policy",
    "claim": "Open {a_line} claim",
    "cancellation": "Process the {line} cancellation",
    "billing": "Sort out the {line} billing question",
}
_STATUS = {
    "quote": "{carrier} needs square footage and five years of loss history before it can price it",
    "renewal": "{carrier} sent renewal terms with the premium up 6%; waiting on signed forms",
    "endorsement": "{carrier} confirmed the change, pending a signature",
    "coi": "certificate drafted for the job site; waiting on the holder's legal name",
    "claim": "adjuster assigned, claim number {claim_no}; photos requested",
    "cancellation": "{carrier} needs the signed cancellation request before issuing the refund",
    "billing": "{carrier} shows the last payment missed; asked whether to set up pre-authorized debit",
}


def _other_workflow(rng: random.Random, workflow: str) -> str:
    return rng.choice([w for w in WORKFLOWS if w != workflow])


def _case(category: str, template: int, message: str, gold_kind: str, targets: List[Agent],
          negatives: List[Agent], client_keys: List[str], history: Optional[List[Dict[str, str]]] = None,
          message_type: str = "user") -> Dict[str, Any]:
    return {
        "category": category,
        "template_id": f"{category}.t{template}",
        "split": "heldout" if template == 0 else "report",
        "message": message,
        "message_type": message_type,
        "history": history or [],
        "gold": {"kind": gold_kind, "agent_ids": list(targets)},
        "target_ids": targets,
        "hard_negative_ids": negatives,
        "client_key": "|".join(client_keys),
    }


# ---- one builder per category; `t` is the template index --------------------------------------------------

def _explicit(w: World, t: int) -> Dict[str, Any]:
    rng = w.rng
    if t == 2:
        client = w.person()
        target = w.email_task(client, "whether the new roof qualifies for a home discount",
                              "I'll dig out the invoice this weekend.", days_ago=7)
        negs = [w.matter(client, "renewal"), w.email_task(w.person(), "for the vehicle VIN", "Sent it over!")]
        msg = f"follow up with {client.name} on my last email, it's been a few days"
        return _case("explicit", t, msg, "reuse", [target], negs, [client.key])
    client = rng.choice([w.person, w.business])()
    wf = rng.choice(list(WORKFLOWS))
    target = w.matter(client, wf)
    other = w.business() if client.kind == "business" else w.person()
    negs = [w.matter(other, wf), w.matter(client, _other_workflow(rng, wf))]
    if t == 0 and client.kind == "business":
        msg = f"send the {WORKFLOWS[wf][0]} paperwork for {client.name} over to {client.first} today"
    elif t == 0:
        msg = f"send {client.name} the {WORKFLOWS[wf][0]} paperwork today"
    else:
        msg = f"what's the latest on the {target.name}?"
    return _case("explicit", t, msg, "reuse", [target], negs, [client.key])


def _entity(w: World, t: int) -> Dict[str, Any]:
    rng = w.rng
    client = rng.choice([w.person, w.business])()
    wf = rng.choice(list(WORKFLOWS))
    target = w.matter(client, wf)
    negs = [w.matter(w.business() if client.kind == "business" else w.person(), wf) for _ in range(2)]
    msg = [
        f"any update on policy {target.policy_no}?",
        f"did {client.email} ever get back to us?",
        f"the carrier wrote back on thread {target.thread_id}, can you pick that up again?",
    ][t]
    return _case("entity", t, msg, "reuse", [target], negs, [client.key])


def _paraphrase(w: World, t: int) -> Dict[str, Any]:
    rng = w.rng
    kind = rng.choice(BUSINESS_KINDS)
    client = w.business(kind=kind)
    wf = rng.choice(list(WORKFLOWS))
    target = w.matter(client, wf)
    same_kind = w.business(kind=kind, city=rng.choice([c for c in CASE_CITIES if c != client.city]))
    same_city = w.business(kind=rng.choice([k for k in BUSINESS_KINDS if k != kind]), city=client.city)
    negs = [w.matter(same_kind, wf), w.matter(same_city, wf)]
    syn = WORKFLOWS[wf][1]
    msg = [
        f"any word from the {client.descriptor} in {client.city} on the {syn}?",
        f"remind me where we landed with the {syn} for that {client.descriptor} up in {client.city}",
        f"has the {client.descriptor} in {client.city} sent back what we needed for the {syn}?",
    ][t]
    return _case("paraphrase", t, msg, "reuse", [target], negs, [client.key])


_PRONOUN_ASKS = [
    ("whether she can move the home inspection to Thursday", "Is Thursday at 10 still OK for the inspection?"),
    ("if the new car should go on the auto policy", "Should I add the new car from the 1st?"),
    ("for a signature on the renewal forms", "Can I sign electronically instead of printing?"),
]


def _pronoun(w: World, t: int) -> Dict[str, Any]:
    rng = w.rng
    client = w.person()
    ask, question = rng.choice(_PRONOUN_ASKS)
    ask = ask.replace("she", client.pronoun)
    target = w.email_task(client, ask, question)
    other = w.person(pronoun=client.pronoun)
    negs = [w.email_task(other, "for the updated mileage", "Around 18,000 km a year."),
            w.matter(client, "renewal")]
    obj = "her" if client.pronoun == "she" else "him"
    history = [
        {"role": "user", "text": f"email {client.name} {ask}"},
        {"role": "assistant", "text": f"On it, I'll reach out to {client.first}."},
        {"role": "agent", "text": f"[SUCCESS] {target.name}: {client.first} replied on thread "
                                  f"{target.thread_id}: \"{question}\""},
        {"role": "assistant", "text": f"{client.first} asked: \"{question}\" Want me to say yes?"},
    ]
    msg = [f"tell {obj} yes", f"ok, let {obj} know that works", f"sounds good, go ahead and confirm with {obj}"][t]
    return _case("pronoun", t, msg, "reuse", [target], negs, [client.key], history=history)


def _near_duplicate(w: World, t: int, i: int) -> Dict[str, Any]:
    rng = w.rng
    if t == 0:  # spouses on one home policy
        a = w.person(pronoun="she")
        b = w.person(pronoun="he", surname=a.name.split()[1], city=a.city, key=a.key)
        ta = w.email_task(a, "whether the basement was finished this year", "Yes, finished in June.")
        tb = w.email_task(b, "for the new alarm certificate", "I'll scan it tonight.")
        gold, other = (ta, tb) if i % 2 == 0 else (tb, ta)
        msg = f"tell {gold.client.first} the home policy change went through"
        return _case("near_duplicate", t, msg, "reuse", [gold], [other], [a.key])
    if t == 1:  # same stem, different business and city
        stem = w._stems.pop()
        a = w.business(stem=stem, kind=("Plumbing", "pipe and drain contractor"))
        b = w.business(stem=stem, kind=("Plumbing & Heating", "furnace and pipe contractor"),
                       city=rng.choice([c for c in CASE_CITIES if c != a.city]))
        ta, tb = w.matter(a, "coi"), w.matter(b, "coi")
        gold, other = (ta, tb) if i % 2 == 0 else (tb, ta)
        msg = f"the {stem} in {gold.client.city} needs their certificate resent, the site manager is asking"
        return _case("near_duplicate", t, msg, "reuse", [gold], [other], [gold.client.key])
    if t == 2:  # same client: auto endorsement vs new auto claim
        client = w.person()
        endo = w.matter(client, "endorsement", name=f"{client.name} auto endorsement")
        claim = w.matter(client, "claim", name=f"{client.name} auto claim")
        if i % 2 == 0:
            gold, other = claim, endo
            msg = f"the other driver's insurer called about {client.first}'s accident, pass our file details on"
        else:
            gold, other = endo, claim
            msg = f"{client.first} traded in the car, update the vehicle on the policy"
        return _case("near_duplicate", t, msg, "reuse", [gold], [other], [client.key])
    client = w.business()  # last year's renewal vs this year's
    old = w.matter(client, "renewal", name=f"{client.name} renewal 2025", days_ago=350)
    new = w.matter(client, "renewal", name=f"{client.name} renewal 2026", days_ago=3)
    if i % 2 == 0:
        gold, other = new, old
        msg = f"{new.carrier} just sent updated wording for this year's {client.name} renewal, check it"
    else:
        gold, other = old, new
        msg = f"pull the terms we agreed for {client.name} last year so we can compare"
    return _case("near_duplicate", t, msg, "reuse", [gold], [other], [client.key])


def _should_create(w: World, t: int) -> Dict[str, Any]:
    rng = w.rng
    if t == 0:  # a client with no agent at all
        client = w.business()
        negs = [w.matter(w.business(), "quote"), w.matter(w.business(), "quote")]
        msg = (f"get a commercial property quote for {client.name}, {_a(client.descriptor)} in {client.city}; "
               f"the owner is {client.contact}, {client.email}")
        return _case("should_create", t, msg, "create", [], negs, [client.key])
    if t == 1:  # same client, an agent for a different task holds related context
        client = w.person()
        negs = [w.matter(client, "renewal", name=f"{client.name} home renewal"),
                w.matter(w.person(), "claim")]
        msg = f"{client.first} {client.name.split()[1]} had a pipe burst in the basement, start a claim"
        return _case("should_create", t, msg, "create", [], negs, [client.key])
    task = rng.choice(REMINDER_TASKS)
    others = [x for x in REMINDER_TASKS if x != task]
    negs = [w.reminder(x) for x in rng.sample(others, 2) if f"Reminder: {x}" not in w.names]
    msg = f"remind me to {task} next Friday"
    return _case("should_create", t, msg, "create", [], negs, ["reminder"])


def _ambiguous(w: World, t: int) -> Dict[str, Any]:
    rng = w.rng
    client = w.business()
    sites = rng.sample(["Queen St site", "Dundas yard", "Lakeview tower", "Mill Rd warehouse", "Bay St lobby"], 2)
    a = w.matter(client, "coi", name=f"{client.name} COI - {sites[0]}", detail=f" ({sites[0]})")
    b = w.matter(client, "coi", name=f"{client.name} COI - {sites[1]}", detail=f" ({sites[1]})")
    negs = [w.matter(w.business(), "coi")]
    history = [
        {"role": "user", "text": f"I'm going through {client.name}'s files this morning"},
        {"role": "assistant", "text": "Sounds good, tell me what you need."},
    ]
    msg = [
        "can you send the certificate again?",
        "resend their COI to the general contractor",
        "the certificate needs the holder name fixed, can you handle it?",
    ][t]
    return _case("ambiguous", t, msg, "clarify", [a, b], negs, [client.key], history=history)


def _parallel(w: World, t: int) -> Dict[str, Any]:
    rng = w.rng
    ca, cb = w.business(), w.person()
    wa, wb = rng.sample(list(WORKFLOWS), 2)
    a, b = w.matter(ca, wa), w.matter(cb, wb)
    negs = [w.matter(w.business(), wa), w.matter(w.person(), wb)]
    la, lb = WORKFLOWS[wa][0], WORKFLOWS[wb][0]
    msg = [
        f"two things: follow up on the {ca.name} {la}, and see where {cb.name}'s {lb} stands",
        f"nudge {ca.name} about the {la} and ask {cb.name} about the {lb}",
        f"while you're at it, close out the {ca.name} {la} and get {cb.name}'s {lb} moving",
    ][t]
    return _case("parallel", t, msg, "parallel", [a, b], negs, [ca.key, cb.key])


def _agent_message(w: World, t: int) -> Dict[str, Any]:
    rng = w.rng
    client = rng.choice([w.person, w.business])()
    wf = rng.choice(list(WORKFLOWS))
    target = w.matter(client, wf)
    negs = [w.matter(w.business() if client.kind == "business" else w.person(), wf) for _ in range(2)]
    body = [
        f"From {client.contact} <{client.email}>, subject \"Re: policy {target.policy_no}\": signed forms "
        f"attached, please pass them to {target.carrier}.",
        f"{target.carrier} underwriting replied on thread {target.thread_id}: they need one more document "
        f"before they can finish the {WORKFLOWS[wf][1]}.",
        f"{client.contact} ({client.email}) is asking whether the {WORKFLOWS[wf][1]} is done; they need an "
        f"answer today.",
    ][t]
    msg = f"Important email watcher notification:\n{body}"
    return _case("agent_message", t, msg, "reuse", [target], negs, [client.key], message_type="agent")


_BUILDERS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "explicit": _explicit, "entity": _entity, "paraphrase": _paraphrase, "pronoun": _pronoun,
    "should_create": _should_create, "ambiguous": _ambiguous, "parallel": _parallel,
    "agent_message": _agent_message,
}
_TEMPLATES = {"near_duplicate": 4}


def _filler(w: World, count: int) -> List[Agent]:
    """Background agents from name lists disjoint from the case clients, so they never change a gold label."""
    rng = w.rng
    surnames = [h + t for h in FILLER_SURNAME_HEADS for t in FILLER_SURNAME_TAILS]
    out: List[Agent] = []
    while len(out) < count:
        roll = rng.random()
        if roll < 0.45:
            first, last = rng.choice(FILLER_FIRST), rng.choice(surnames)
            name = f"{first} {last}"
            client = Client(f"hh-{_slug(last)}", name, "person", rng.choice(FILLER_CITIES),
                            f"{_slug(first)}.{_slug(last)}@mail.test", name, rng.choice(OCCUPATIONS),
                            rng.choice(["she", "he"]))
        elif roll < 0.85:
            name = f"{rng.choice(FILLER_STEMS)} {rng.choice(BUSINESS_KINDS)[0]}"
            client = Client(f"biz-{_slug(name)}", name, "business", rng.choice(FILLER_CITIES),
                            f"info@{_slug(name).replace('-', '')}.example.ca",
                            f"{rng.choice(FILLER_FIRST)} {rng.choice(surnames)}", "shop")
        else:
            client = None
        if client is None:
            if rng.random() < 0.5:
                label = f"Reminder: {rng.choice(REMINDER_TASKS)} ({rng.choice(MONTHS)} {rng.randint(1, 28)})"
                req, resp = f"Set a reminder: {label[10:]}.", "Reminder scheduled."
            else:
                label = f"Search: {rng.choice(SEARCH_TOPICS)} ({rng.choice(FILLER_CITIES)})"
                req, resp = f"Look up {label[8:]}.", "Found three options and summarised them."
            if label in w.names:
                continue
            out.append(w.add(Agent(label, None, req, resp, rng.choice(RECENCY_DAYS))))
            continue
        wf = rng.choice(list(WORKFLOWS))
        line = rng.choice(PERSON_LINES if client.kind == "person" else BUSINESS_LINES)[0]
        name = f"{client.name} {line} {WORKFLOWS[wf][0]}" if client.kind == "person" \
            else f"{client.name} {WORKFLOWS[wf][0]}"
        if rng.random() < 0.15 and client.kind == "person":
            name = f"Email to {client.name}"
        if name in w.names:
            continue
        out.append(w.matter(client, wf, name=name))
    return out


def generate(seed: int = SEED, filler: int = FILLER_COUNT) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return (pool, cases). Deterministic for a given seed."""
    w = World(seed)
    cases: List[Dict[str, Any]] = []
    for category, count in CATEGORY_COUNTS.items():
        templates = _TEMPLATES.get(category, 3)
        for i in range(count):
            t = i % templates
            w.new_scope()
            case = _near_duplicate(w, t, i // templates) if category == "near_duplicate" \
                else _BUILDERS[category](w, t)
            case["case_id"] = f"{category}-{i + 1:03d}"
            cases.append(case)
    case_agents = list(w.agents)
    for n, agent in enumerate(case_agents, start=1):
        agent.id = f"g_{n:04d}"
    w.new_scope()
    case_names = {a.name for a in case_agents}
    w.names = set(case_names)
    for n, agent in enumerate(_filler(w, filler), start=1):
        agent.id = f"f_{n:04d}"

    keyed = []
    for case in cases:
        def ids(agents: List[Agent]) -> List[str]:
            return [a.id for a in agents]
        ordered = {"case_id": case["case_id"], "category": case["category"], "split": case["split"],
                   "template_id": case["template_id"], "message": case["message"],
                   "message_type": case["message_type"], "history": case["history"],
                   "gold": {"kind": case["gold"]["kind"], "agent_ids": ids(case["gold"]["agent_ids"])},
                   "target_ids": ids(case["target_ids"]), "hard_negative_ids": ids(case["hard_negative_ids"]),
                   "client_key": case["client_key"]}
        if ordered["gold"]["kind"] == "create":
            ordered["gold"]["agent_ids"] = []
        keyed.append(ordered)
    pool = [a.to_pool() for a in w.agents]
    return pool, keyed


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


@lru_cache(maxsize=4)
def _pool_cached(path: str) -> Dict[str, Dict[str, Any]]:
    return {row["id"]: row for row in _read_jsonl(Path(path))}


def load_pool(path: Path = POOL_PATH) -> Dict[str, Dict[str, Any]]:
    """Return the committed pool keyed by agent id, in file order."""
    return _pool_cached(str(path))


def load_cases(path: Path = GOLDEN_PATH, split: str = "all") -> List[Dict[str, Any]]:
    """Return the golden cases, optionally filtered to one split ("report" or "heldout")."""
    cases = _read_jsonl(path)
    return cases if split == "all" else [c for c in cases if c["split"] == split]


def roster_for(case: Dict[str, Any], n: int, seed: int = SEED,
               pool: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Return the case's roster of size n (at least its targets and hard negatives), in creation order.

    Filler comes from a case-seeded permutation of the filler pool, so for one case the roster at a smaller n
    is a subset of the roster at a larger n. The target block sits at a case-seeded position (start, middle or
    end) that does not depend on n.
    """
    pool = load_pool() if pool is None else pool
    rng = random.Random(f"{seed}:{case['case_id']}")
    filler = [agent_id for agent_id in pool if agent_id.startswith("f_")]
    rng.shuffle(filler)
    position = rng.choice(["start", "middle", "end"])
    block = list(case["target_ids"]) + list(case["hard_negative_ids"])
    rng.shuffle(block)
    fill = filler[: max(0, n - len(block))]
    at = {"start": 0, "middle": len(fill) // 2, "end": len(fill)}[position]
    ids = fill[:at] + block + fill[at:]
    return [pool[agent_id] for agent_id in ids]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--filler", type=int, default=FILLER_COUNT)
    args = parser.parse_args(argv)
    pool, cases = generate(args.seed, args.filler)
    _write_jsonl(POOL_PATH, pool)
    _write_jsonl(GOLDEN_PATH, cases)
    print(f"wrote {len(pool)} agents to {POOL_PATH.relative_to(EVALS_DIR.parent)}")
    print(f"wrote {len(cases)} cases to {GOLDEN_PATH.relative_to(EVALS_DIR.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
