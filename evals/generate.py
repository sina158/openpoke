"""Seeded generator for the synthetic agent pool and the golden routing set.

`python -m evals.generate` rewrites evals/fixtures/agents_pool.jsonl and evals/golden/routing.jsonl;
`--version 2` writes the round-two set (agents_pool_v2.jsonl, routing_v2.jsonl) instead.
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
SEED_V2 = 20261029
EVALS_DIR = Path(__file__).resolve().parent
POOL_PATH = EVALS_DIR / "fixtures" / "agents_pool.jsonl"
GOLDEN_PATH = EVALS_DIR / "golden" / "routing.jsonl"
POOL_V2_PATH = EVALS_DIR / "fixtures" / "agents_pool_v2.jsonl"
GOLDEN_V2_PATH = EVALS_DIR / "golden" / "routing_v2.jsonl"
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
CATEGORY_COUNTS_V2: Dict[str, int] = {
    "explicit": 14,
    "entity": 12,
    "paraphrase": 14,
    "pronoun": 12,
    "near_duplicate": 18,
    "should_create": 16,
    "ambiguous": 10,
    "parallel": 6,
    "agent_message": 8,
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

# Round-two case clients. Every list is disjoint from the round-one case lists and from the filler lists, so no
# fresh client shares a surname, stem, city or occupation with a round-one case or with the background pool.
CASE_CITIES_V2 = ["Dryden", "Welland", "St. Catharines", "Chatham", "Leamington", "Tillsonburg", "Goderich",
                  "Kincardine", "Perth", "Smiths Falls", "Gananoque", "Port Hope", "Orangeville", "Espanola",
                  "Renfrew"]
CASE_FIRST_V2 = {
    "she": ["Adwoa", "Brigid", "Clementine", "Dagny", "Elodie", "Freya", "Giulia", "Henrike", "Isolde", "Jovana",
            "Katrin", "Leonie", "Mirela", "Nadia", "Orla", "Priya", "Rhiannon", "Solveig", "Theodora", "Wilhelmina"],
    "he": ["Alistair", "Benedikt", "Cormac", "Desmond", "Evander", "Florian", "Gideon", "Hamish", "Ignatius",
           "Jonas", "Konrad", "Leopold", "Magnus", "Nikolai", "Orrin", "Pascal", "Quentin", "Ronan", "Severin",
           "Torsten"],
}
CASE_SURNAMES_V2 = [
    "Abara", "Bergstrom", "Cavanagh", "Dragomir", "Eriksen", "Fontaine", "Gallagher", "Haddad", "Ishikawa",
    "Jablonski", "Kovacs", "Laurentis", "Mwangi", "Nygaard", "Okonkwo", "Paquette", "Quarshie", "Rautio",
    "Sandoval", "Takahashi", "Vasquez", "Whitlock", "Xenakis", "Yamada", "Zubiri", "Albrecht", "Bellamy",
    "Cardenas", "Deschamps", "Engstrom", "Fitzgerald", "Gunnarsson", "Hakimi", "Iversen", "Jovanovic",
    "Karlsson", "Lindgren", "Montague", "Nakamura", "Ortega", "Pellegrini", "Quigley", "Rosales", "Sigurdsson",
    "Tremblay", "Ulloa", "Vidal", "Wainwright", "Yeboah", "Zeller", "Ambrose", "Brennan", "Coutinho", "Darrow",
    "Eastman", "Fenwick", "Galloway", "Holmgren", "Ingram", "Juarez",
]
CASE_STEMS_V2 = [
    "Arrowhead", "Bayfront", "Briarwood", "Cascade", "Chestnut", "Crestview", "Deerfield", "Edgewater",
    "Fairhaven", "Greystone", "Hawthorn", "Heatherfield", "Juniper", "Kingfisher", "Lantern", "Laurel",
    "Meadowlark", "Mosswood", "Orchard", "Osprey", "Prairie", "Ridgeline", "Rosewood", "Saltmarsh", "Shoreline",
    "Spruce", "Starling", "Summit", "Sunridge", "Thistle", "Timberline", "Trillium", "Wavecrest", "Whistler",
    "Windward", "Wren", "Bellwether", "Cobblestone", "Fiddlehead", "Goldenrod", "Hearthstone", "Lodgepole",
    "Marigold", "Nightjar", "Tamarack", "Waxwing",
]
OCCUPATIONS_V2 = ["veterinarian", "welder", "pilot", "translator", "optometrist", "surveyor", "midwife",
                  "sommelier", "glassblower", "beekeeper", "locksmith", "court reporter"]
CONTACT_FIRST_V2 = ["Ari", "Blair", "Cameron", "Dana", "Ellis", "Frankie", "Harper", "Jordan", "Kendall", "Reese"]
CONTACT_SURNAMES_V2 = ["Achebe", "Bianchi", "Delgado", "Eklund", "Gaspard", "Hosseini", "Moretti", "Nilsen",
                       "Okoro", "Petrov"]
LOSS_DATES = ["March 3", "April 19", "May 27", "June 18", "July 9", "August 30"]
PERILS = ["water damage", "hail damage", "theft", "wind damage"]
LINE_THINGS = {"auto": "car", "home": "house"}


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
    claim_no: str = ""

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
    """Holds the RNG, mints unique entities and agents, and enforces unique agent names.

    `version=2` draws case clients from the round-two lists and keeps a matter's policy line consistent with
    the line its caller named; version 1 reproduces the round-one files byte for byte.
    """

    def __init__(self, seed: int, version: int = 1) -> None:
        self.rng = random.Random(seed)
        self.version = version
        self.agents: List[Agent] = []
        self.names: set = set()  # names in the current scope (one case, or the filler)
        self.used: set = set()  # entity values, unique pool-wide
        v2 = version >= 2
        self.cities = CASE_CITIES_V2 if v2 else CASE_CITIES
        self.occupations = OCCUPATIONS_V2 if v2 else OCCUPATIONS
        self._first = {p: list(v) for p, v in (CASE_FIRST_V2 if v2 else CASE_FIRST).items()}
        self._contacts = (CONTACT_FIRST_V2, CONTACT_SURNAMES_V2) if v2 else (CONTACT_FIRST, CONTACT_SURNAMES)
        self.new_scope()

    def new_scope(self) -> None:
        """Start a case. Case agents only ever share a roster with their own case and the filler, so names
        and clients need to be distinct within a case, not across cases."""
        self.names = set()
        self._surnames = list(CASE_SURNAMES_V2 if self.version >= 2 else CASE_SURNAMES)
        self._stems = list(CASE_STEMS_V2 if self.version >= 2 else CASE_STEMS)
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
               key: Optional[str] = None, first: Optional[str] = None, descriptor: Optional[str] = None) -> Client:
        pronoun = pronoun or self.rng.choice(["she", "he"])
        surname = surname or self._surnames.pop()
        first = first or self.rng.choice(self._first[pronoun])
        name = f"{first} {surname}"
        return Client(
            key=key or f"hh-{_slug(surname)}",
            name=name,
            kind="person",
            city=city or self.rng.choice(self.cities),
            email=f"{_slug(first)}.{_slug(surname)}@example.ca",
            contact=name,
            descriptor=descriptor or self.rng.choice(self.occupations),
            pronoun=pronoun,
        )

    def business(self, stem: Optional[str] = None, kind: Optional[Tuple[str, str]] = None,
                 city: Optional[str] = None) -> Client:
        stem = stem or self._stems.pop()
        kind_name, descriptor = kind or self.rng.choice(BUSINESS_KINDS)
        name = f"{stem} {kind_name}"
        contact = f"{self.rng.choice(self._contacts[0])} {self.rng.choice(self._contacts[1])}"
        domain = _slug(name).replace("-", "")
        return Client(
            key=f"biz-{_slug(name)}",
            name=name,
            kind="business",
            city=city or self.rng.choice(self.cities),
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
               detail: str = "", line: Optional[str] = None, carrier: Optional[str] = None) -> Agent:
        rng = self.rng
        lines = PERSON_LINES if client.kind == "person" else BUSINESS_LINES
        drawn = rng.choice(lines)
        # Round one drew the line here even when the caller's name already carried one, so a name could say
        # "auto" over a home policy. Version 2 worlds keep the caller's line.
        line, prefix = next(pair for pair in lines if pair[0] == line) if line and self.version >= 2 else drawn
        carrier = carrier or rng.choice(CARRIERS)
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
        if self.version >= 2:
            claim_no = self._unique(lambda: f"CL-{rng.randint(100000, 999999)}")
        else:
            claim_no = f"CL-{rng.randint(100000, 999999)}"
        status = _STATUS[workflow].format(carrier=carrier, claim_no=claim_no)
        last_response = (
            f"Emailed {client.contact} at {client.email} (thread {thread_id}) about policy {policy_no}; {status}."
        )
        return self.add(Agent(name, client, first_request, last_response,
                              rng.choice(RECENCY_DAYS) if days_ago is None else days_ago,
                              workflow, policy_no, thread_id, carrier,
                              claim_no=claim_no if workflow == "claim" else ""))

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
          message_type: str = "user", discriminators: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "discriminators": discriminators or [],
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


# ---- round two: two new templates per category (three for near_duplicate); `i` alternates the gold side -----

def _v2_explicit(w: World, t: int, i: int) -> Dict[str, Any]:
    rng = w.rng
    client = rng.choice([w.person, w.business])()
    make = w.person if client.kind == "person" else w.business
    wf = rng.choice(list(WORKFLOWS))
    label = WORKFLOWS[wf][0]
    if t == 0:  # one workflow open on two lines for the same client: the line decides
        lines = PERSON_LINES if client.kind == "person" else BUSINESS_LINES
        line, other_line = [pair[0] for pair in rng.sample(lines, 2)]
        target = w.matter(client, wf, name=f"{client.name} {line} {label}", line=line)
        other = make()
        negs = [w.matter(client, wf, name=f"{client.name} {other_line} {label}", line=other_line),
                w.matter(other, wf, name=f"{other.name} {line} {label}", line=line)]
        msg = f"before Friday, chase the {line} {label} for {client.name}"
        return _case("explicit", t, msg, "reuse", [target], negs, [client.key], discriminators=[line])
    target = w.matter(client, wf)
    negs = [w.matter(make(), wf), w.matter(client, _other_workflow(rng, wf))]
    msg = f"{client.name} rang about the {label} - can you check what we still owe them?"
    return _case("explicit", t, msg, "reuse", [target], negs, [client.key], discriminators=[label])


def _v2_entity(w: World, t: int, i: int) -> Dict[str, Any]:
    rng = w.rng
    make = rng.choice([w.person, w.business])
    client = make()
    if t == 0:  # only the claim number from the target's last report
        target = w.matter(client, "claim")
        negs = [w.matter(make(), "claim") for _ in range(2)]
        msg = f"{target.claim_no}: the adjuster wants the repair estimate by Monday, send it over"
        return _case("entity", t, msg, "reuse", [target], negs, [client.key], discriminators=[target.claim_no])
    wf = rng.choice(list(WORKFLOWS))  # only the policy number; every agent here is with the same carrier
    target = w.matter(client, wf)
    negs = [w.matter(make(), wf, carrier=target.carrier) for _ in range(2)]
    msg = (f"{target.carrier} left a voicemail quoting {target.policy_no}; call them back on that file and find "
           f"out what they need")
    return _case("entity", t, msg, "reuse", [target], negs, [client.key], discriminators=[target.policy_no])


def _v2_paraphrase(w: World, t: int, i: int) -> Dict[str, Any]:
    rng = w.rng
    wf = rng.choice(list(WORKFLOWS))
    syn = WORKFLOWS[wf][1]
    if t == 0:  # occupation + workflow synonym, no name and no city
        job, other_job = rng.sample(w.occupations, 2)
        client = w.person(descriptor=job)
        target = w.matter(client, wf)
        negs = [w.matter(w.person(descriptor=other_job, city=client.city), wf),
                w.matter(w.person(descriptor=job), _other_workflow(rng, wf))]
        msg = f"the {job} who asked about the {syn} - has anything come back yet?"
        return _case("paraphrase", t, msg, "reuse", [target], negs, [client.key], discriminators=[job])
    kind = rng.choice(BUSINESS_KINDS)
    client = w.business(kind=kind)
    target = w.matter(client, wf)
    same_kind = w.business(kind=kind, city=rng.choice([c for c in w.cities if c != client.city]))
    same_city = w.business(kind=rng.choice([k for k in BUSINESS_KINDS if k != kind]), city=client.city)
    negs = [w.matter(same_kind, wf), w.matter(same_city, wf)]
    msg = f"quick one on the {client.city} {client.descriptor}: has the {syn} moved since last week?"
    return _case("paraphrase", t, msg, "reuse", [target], negs, [client.key],
                 discriminators=[client.city, client.descriptor])


_PRONOUN_ASKS_V2 = [
    ("which deductible they want on the new policy", "Would the $1,000 deductible keep it under $90 a month?"),
    ("whether the inspector can visit next week", "Could the inspector come Wednesday morning instead?"),
    ("to confirm the start date for the new coverage", "Can the coverage start on the 1st rather than the 15th?"),
    ("for the serial number of the new generator", "Is it fine if I send a photo of the label instead?"),
]


def _v2_pronoun(w: World, t: int, i: int) -> Dict[str, Any]:
    rng = w.rng
    if t == 0:  # replies in the history from a woman and a man; only the pronoun picks one
        client = w.person()
        other = w.person(pronoun="he" if client.pronoun == "she" else "she")
        (ask, question), (other_ask, other_question) = rng.sample(_PRONOUN_ASKS_V2, 2)
        target = w.email_task(client, ask, question)
        other_task = w.email_task(other, other_ask, other_question)
        negs = [other_task, w.matter(client, "renewal")]
        replies = [(target, client, question), (other_task, other, other_question)]
        if i % 2 == 0:
            replies.reverse()
        history = [{"role": "user", "text": f"email {client.name} {ask}, and email {other.name} {other_ask}"},
                   {"role": "assistant", "text": "Both emails are out."}]
        history += [{"role": "agent", "text": f"[SUCCESS] {agent.name}: {person.first} replied on thread "
                                              f"{agent.thread_id}: \"{q}\""} for agent, person, q in replies]
        history.append({"role": "assistant",
                        "text": " ".join(f"{person.first} asked: \"{q}\"" for _, person, q in replies)})
        obj = "her" if client.pronoun == "she" else "him"
        msg = f"for {obj}: yes, that works - confirm it"
        return _case("pronoun", t, msg, "reuse", [target], negs, [client.key], history=history,
                     discriminators=[client.first])
    client = w.business()  # one business reply in the history; "them" is the only pointer
    ask = "which deductible they prefer on the renewal"
    question = "Should we go with the $1,000 or the $2,500 deductible?"
    target = w.email_task(client, ask, question)
    negs = [w.email_task(w.business(), ask, "Whichever is cheaper."), w.matter(client, "renewal")]
    history = [
        {"role": "user", "text": f"ask {client.name} which deductible they want"},
        {"role": "assistant", "text": f"Emailing {client.contact} now."},
        {"role": "agent", "text": f"[SUCCESS] {target.name}: {client.first} replied on thread "
                                  f"{target.thread_id}: \"{question}\""},
        {"role": "assistant", "text": f"{client.first} wants to know: \"{question}\""},
    ]
    msg = "go back to them and say the $2,500 one"
    return _case("pronoun", t, msg, "reuse", [target], negs, [client.key], history=history,
                 discriminators=[target.thread_id])


def _v2_near_duplicate(w: World, t: int, i: int) -> Dict[str, Any]:
    rng = w.rng
    if t == 0:  # two businesses sharing a stem and a city; only the trade differs
        stem = w._stems.pop()
        city = rng.choice(w.cities)
        kind_a, kind_b = rng.sample(BUSINESS_KINDS, 2)
        a, b = w.business(stem=stem, kind=kind_a, city=city), w.business(stem=stem, kind=kind_b, city=city)
        wf = rng.choice(list(WORKFLOWS))
        ta, tb = w.matter(a, wf), w.matter(b, wf)
        gold, other = (ta, tb) if i % 2 == 0 else (tb, ta)
        msg = f"is the {stem} {gold.client.descriptor} still waiting on us for the {WORKFLOWS[wf][1]}?"
        return _case("near_duplicate", t, msg, "reuse", [gold], [other], [gold.client.key],
                     discriminators=[gold.client.descriptor])
    client = w.person()
    if t == 1:  # one client renewing both lines; car or house decides
        auto = w.matter(client, "renewal", name=f"{client.name} auto renewal", line="auto")
        home = w.matter(client, "renewal", name=f"{client.name} home renewal", line="home")
        if i % 2 == 0:
            gold, other, line = auto, home, "auto"
            msg = f"{client.name} wants to drop collision on the car at renewal; make the change on that one only"
        else:
            gold, other, line = home, auto, "home"
            msg = f"{client.name} put a new roof on the house, get it into the renewal before it's issued"
        return _case("near_duplicate", t, msg, "reuse", [gold], [other], [client.key], discriminators=[line])
    line = rng.choice(PERSON_LINES)[0]  # two open claims for one client; the date of loss decides
    perils, dates = rng.sample(PERILS, 2), rng.sample(LOSS_DATES, 2)
    claims = [w.matter(client, "claim", name=f"{client.name} {line} claim - {peril}", line=line,
                       detail=f", loss of {date}") for peril, date in zip(perils, dates)]
    g = i % 2
    msg = f"the adjuster on the {dates[g]} loss for {client.first} wants receipts, what do we have?"
    return _case("near_duplicate", t, msg, "reuse", [claims[g]], [claims[1 - g]], [client.key],
                 discriminators=[dates[g]])


def _v2_should_create(w: World, t: int, i: int) -> Dict[str, Any]:
    rng = w.rng
    client = w.person()
    if t == 0:  # the client has a quote on one line and now wants the other line priced
        have, want = [pair[0] for pair in rng.sample(PERSON_LINES, 2)]
        other = w.person()
        negs = [w.matter(client, "quote", name=f"{client.name} {have} quote", line=have),
                w.matter(other, "quote", name=f"{other.name} {want} quote", line=want)]
        msg = (f"{client.name} wants the {LINE_THINGS[want]} priced too - that's a new quote, separate from the one "
               f"we already ran")
        return _case("should_create", t, msg, "create", [], negs, [client.key], discriminators=[want])
    line = rng.choice(PERSON_LINES)[0]  # a new client who looks like an existing one: same job, same city
    lookalike = w.person(descriptor=client.descriptor, city=client.city)
    negs = [w.matter(lookalike, "quote", name=f"{lookalike.name} {line} quote", line=line),
            w.matter(w.person(), "quote")]
    msg = (f"new client: {client.name}, {_a(client.descriptor)} in {client.city} ({client.email}). "
           f"Needs {_a(line)} quote.")
    return _case("should_create", t, msg, "create", [], negs, [client.key], discriminators=[client.name])


def _v2_ambiguous(w: World, t: int, i: int) -> Dict[str, Any]:
    rng = w.rng
    if t == 0:  # two open claims for the client in the history; the message gives no date
        client = w.person()
        line = rng.choice(PERSON_LINES)[0]
        perils, dates = rng.sample(PERILS, 2), rng.sample(LOSS_DATES, 2)
        targets = [w.matter(client, "claim", name=f"{client.name} {line} claim - {peril}", line=line,
                            detail=f", loss of {date}") for peril, date in zip(perils, dates)]
        negs = [w.matter(w.person(), "claim")]
        history = [{"role": "user", "text": f"pulling up {client.name}'s file"},
                   {"role": "assistant", "text": f"Got it. What do you need on {client.first}'s file?"}]
        msg = "the adjuster called back about the claim, can you deal with it?"
        return _case("ambiguous", t, msg, "clarify", targets, negs, [client.key], history=history)
    pronoun = rng.choice(["she", "he"])  # two clients with the same first name, each with a renewal
    first = rng.choice(w._first[pronoun])
    a, b = w.person(pronoun=pronoun, first=first), w.person(pronoun=pronoun, first=first)
    targets = [w.matter(a, "renewal"), w.matter(b, "renewal")]
    negs = [w.matter(w.person(), "renewal")]
    poss, obj = ("her", "her") if pronoun == "she" else ("his", "him")
    msg = f"{first} called asking where {poss} renewal is at - what do I tell {obj}?"
    return _case("ambiguous", t, msg, "clarify", targets, negs, [a.key, b.key])


def _v2_parallel(w: World, t: int, i: int) -> Dict[str, Any]:
    rng = w.rng
    ca, cb = w.business(), w.person()
    wa, wb = rng.sample(list(WORKFLOWS), 2)
    a, b = w.matter(ca, wa), w.matter(cb, wb)
    other_kind = rng.choice([k for k in BUSINESS_KINDS if k[1] != ca.descriptor])
    negs = [w.matter(w.business(kind=other_kind), wa), w.matter(w.person(), wb)]
    if t == 0:  # a bulleted list: one matter by policy number, one by name
        msg = (f"can you:\n- follow up on policy {a.policy_no}\n- ask {cb.name} whether the {WORKFLOWS[wb][1]} "
               f"is sorted")
        keys = [a.policy_no, cb.name]
    else:
        msg = (f"once the {WORKFLOWS[wa][0]} for the {ca.descriptor} in {ca.city} is confirmed, nudge {cb.name} "
               f"about the {WORKFLOWS[wb][0]}")
        keys = [ca.descriptor, cb.name]
    return _case("parallel", t, msg, "parallel", [a, b], negs, [ca.key, cb.key], discriminators=keys)


def _v2_agent_message(w: World, t: int, i: int) -> Dict[str, Any]:
    rng = w.rng
    make = rng.choice([w.person, w.business])
    client = make()
    wf = rng.choice(list(WORKFLOWS))
    target = w.matter(client, wf)
    if t == 0:  # a carrier notice carrying only the policy number; every agent here is with that carrier
        negs = [w.matter(make(), wf, carrier=target.carrier) for _ in range(2)]
        body = (f"Automated notice from {target.carrier}: a document is still outstanding on policy "
                f"{target.policy_no}. Reply to this notice once it has been sent.")
        keys = [target.policy_no]
    else:  # the client's own reply; the same client has a second matter open
        negs = [w.matter(client, _other_workflow(rng, wf)), w.matter(make(), wf)]
        body = (f"{client.contact} answered our last email about the {WORKFLOWS[wf][1]}: \"Found the paperwork, "
                f"sending it Monday.\" (sent from {client.email})")
        keys = [WORKFLOWS[wf][0]]
    msg = f"Important email watcher notification:\n{body}"
    return _case("agent_message", t, msg, "reuse", [target], negs, [client.key], message_type="agent",
                 discriminators=keys)


_V2_BUILDERS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "explicit": _v2_explicit, "entity": _v2_entity, "paraphrase": _v2_paraphrase, "pronoun": _v2_pronoun,
    "near_duplicate": _v2_near_duplicate, "should_create": _v2_should_create, "ambiguous": _v2_ambiguous,
    "parallel": _v2_parallel, "agent_message": _v2_agent_message,
}
_V2_TEMPLATES = {"near_duplicate": 3}


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
        out.append(w.matter(client, wf, name=name, line=line))
    return out


def generate(seed: Optional[int] = None, filler: int = FILLER_COUNT,
             version: int = 1) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return (pool, cases) for the round-one set (version 1) or the round-two set (version 2).

    Deterministic for a given seed. Round-two cases are all `report` (nothing is tuned on them), use their own
    templates, and carry case ids prefixed `v2-` so they can never be mistaken for a round-one case.
    """
    v2 = version >= 2
    seed = (SEED_V2 if v2 else SEED) if seed is None else seed
    w = World(seed, version)
    cases: List[Dict[str, Any]] = []
    for category, count in (CATEGORY_COUNTS_V2 if v2 else CATEGORY_COUNTS).items():
        templates = (_V2_TEMPLATES.get(category, 2)) if v2 else _TEMPLATES.get(category, 3)
        for i in range(count):
            t = i % templates
            w.new_scope()
            if v2:
                case = _V2_BUILDERS[category](w, t, i // templates)
                case["template_id"] = f"{category}.v2t{t}"
                case["split"] = "report"
                case["case_id"] = f"v2-{category}-{i + 1:03d}"
            else:
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
        if v2:
            ordered["discriminators"] = case["discriminators"]
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


def hand_check_sample(cases: List[Dict[str, Any]], k: int = 20, seed: int = SEED_V2) -> List[str]:
    """Return the seeded sample of case ids the owner checks by hand (listed in LABELS.md)."""
    return sorted(random.Random(seed).sample([c["case_id"] for c in cases], k))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", type=int, choices=[1, 2], default=1)
    parser.add_argument("--seed", type=int, default=None, help="default: 20261028 for version 1, 20261029 for 2")
    parser.add_argument("--filler", type=int, default=FILLER_COUNT)
    args = parser.parse_args(argv)
    pool, cases = generate(args.seed, args.filler, args.version)
    pool_path, golden_path = (POOL_V2_PATH, GOLDEN_V2_PATH) if args.version == 2 else (POOL_PATH, GOLDEN_PATH)
    _write_jsonl(pool_path, pool)
    _write_jsonl(golden_path, cases)
    print(f"wrote {len(pool)} agents to {pool_path.relative_to(EVALS_DIR.parent)}")
    print(f"wrote {len(cases)} cases to {golden_path.relative_to(EVALS_DIR.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
