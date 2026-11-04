"""Raw location text -> (country, region).

Sources write locations every way imaginable ("Ariana, Ariana, Tunisie", "Remote - EMEA",
"Paris, Île-de-France", "Anywhere in the World", "USA Only"). Matching is done on folded text
against country names (FR/EN), demonyms, major cities and remote phrasings. Unknown stays None:
a wrong guess would hide a job behind a filter, an unknown never does.
"""

from __future__ import annotations

import re

from jobpilot_core.enums import Region
from jobpilot_security.text import fold

# ISO alpha-2 -> region, for every country we have names for
COUNTRY_REGION: dict[str, Region] = {
    "tn": Region.TUNISIA,
    # Middle East / Gulf / North Africa neighbours grouped with the Middle East
    **dict.fromkeys(
        ["ae", "sa", "qa", "kw", "bh", "om", "jo", "lb", "eg", "iq", "tr", "ma", "dz", "ly"],
        Region.MIDDLE_EAST,
    ),
    **dict.fromkeys(
        [
            "fr", "de", "gb", "nl", "be", "ch", "es", "it", "pt", "ie", "lu", "at", "se", "no",
            "dk", "fi", "pl", "cz", "ro", "gr", "hu", "ee", "lv", "lt", "bg", "hr", "sk", "si",
            "mt", "cy", "ua", "rs",
        ],
        Region.EUROPE,
    ),
    "us": Region.USA,
    "ca": Region.CANADA,
}  # fmt: skip

# folded name -> ISO alpha-2. Longest names are tried first.
_NAMES: dict[str, str] = {
    # Tunisia + its governorates / main cities
    "tunisie": "tn", "tunisia": "tn", "tunis": "tn", "ariana": "tn", "ben arous": "tn",
    "manouba": "tn", "la marsa": "tn", "lac 1": "tn", "lac 2": "tn", "les berges du lac": "tn",
    "sfax": "tn", "sousse": "tn", "monastir": "tn", "mahdia": "tn", "nabeul": "tn",
    "hammamet": "tn", "bizerte": "tn", "gabes": "tn", "gafsa": "tn", "kairouan": "tn",
    "kasserine": "tn", "medenine": "tn", "djerba": "tn", "beja": "tn", "jendouba": "tn",
    "zaghouan": "tn", "siliana": "tn", "tozeur": "tn", "kebili": "tn", "tataouine": "tn",
    "sidi bouzid": "tn", "le kef": "tn", "charguia": "tn", "megrine": "tn",
    # Middle East / Gulf / Maghreb
    "united arab emirates": "ae", "emirats arabes unis": "ae", "uae": "ae", "dubai": "ae",
    "abu dhabi": "ae", "sharjah": "ae", "saudi arabia": "sa", "arabie saoudite": "sa",
    "ksa": "sa", "riyadh": "sa", "jeddah": "sa", "qatar": "qa", "doha": "qa", "kuwait": "kw",
    "koweit": "kw", "bahrain": "bh", "bahrein": "bh", "oman": "om", "muscat": "om",
    "jordan": "jo", "jordanie": "jo", "amman": "jo", "lebanon": "lb", "liban": "lb",
    "beirut": "lb", "egypt": "eg", "egypte": "eg", "cairo": "eg", "le caire": "eg",
    "iraq": "iq", "irak": "iq", "turkey": "tr", "turquie": "tr", "istanbul": "tr",
    "morocco": "ma", "maroc": "ma", "casablanca": "ma", "rabat": "ma", "algeria": "dz",
    "algerie": "dz", "alger": "dz", "libya": "ly", "libye": "ly",
    # Europe
    "france": "fr", "paris": "fr", "lyon": "fr", "marseille": "fr", "toulouse": "fr",
    "lille": "fr", "nantes": "fr", "bordeaux": "fr", "ile-de-france": "fr",
    "germany": "de", "allemagne": "de", "deutschland": "de", "berlin": "de", "munich": "de",
    "munchen": "de", "hamburg": "de", "frankfurt": "de", "cologne": "de",
    "united kingdom": "gb", "royaume-uni": "gb", "england": "gb", "london": "gb",
    "londres": "gb", "manchester": "gb", "scotland": "gb", "netherlands": "nl",
    "pays-bas": "nl", "amsterdam": "nl", "rotterdam": "nl", "belgium": "be",
    "belgique": "be", "brussels": "be", "bruxelles": "be", "switzerland": "ch",
    "suisse": "ch", "zurich": "ch", "geneva": "ch", "geneve": "ch", "spain": "es",
    "espagne": "es", "madrid": "es", "barcelona": "es", "italy": "it", "italie": "it",
    "milan": "it", "rome": "it", "portugal": "pt", "lisbon": "pt", "lisbonne": "pt",
    "ireland": "ie", "irlande": "ie", "dublin": "ie", "luxembourg": "lu", "austria": "at",
    "autriche": "at", "vienna": "at", "sweden": "se", "suede": "se", "stockholm": "se",
    "norway": "no", "norvege": "no", "denmark": "dk", "danemark": "dk", "copenhagen": "dk",
    "finland": "fi", "finlande": "fi", "poland": "pl", "pologne": "pl", "warsaw": "pl",
    "czech republic": "cz", "czechia": "cz", "prague": "cz", "romania": "ro",
    "roumanie": "ro", "greece": "gr", "grece": "gr", "hungary": "hu", "estonia": "ee",
    "latvia": "lv", "lithuania": "lt", "bulgaria": "bg", "croatia": "hr", "slovakia": "sk",
    "slovenia": "si", "malta": "mt", "cyprus": "cy", "ukraine": "ua", "serbia": "rs",
    # North America
    "united states": "us", "etats-unis": "us", "usa": "us", "u.s.": "us", "us only": "us",
    "new york": "us", "san francisco": "us", "california": "us", "texas": "us",
    "seattle": "us", "boston": "us", "chicago": "us", "austin": "us", "canada": "ca",
    "toronto": "ca", "montreal": "ca", "vancouver": "ca", "quebec": "ca", "ottawa": "ca",
}  # fmt: skip
_NAME_PATTERNS = [
    (re.compile(rf"(?<![a-z]){re.escape(name)}(?![a-z])"), code)
    for name, code in sorted(_NAMES.items(), key=lambda kv: -len(kv[0]))
]

# whole-region phrasings ("Remote - Europe", "EMEA", "Worldwide")
_REGION_WORDS: list[tuple[re.Pattern[str], Region]] = [
    (
        re.compile(r"\b(worldwide|anywhere|global|monde entier|international|any location)\b"),
        Region.WORLDWIDE,
    ),
    (re.compile(r"\b(europe|european union|eu|emea|eea|cet)\b"), Region.EUROPE),
    (
        re.compile(r"\b(middle east|moyen-orient|gcc|gulf|golfe|mena)\b"),
        Region.MIDDLE_EAST,
    ),
    (re.compile(r"\b(north america|americas|amerique du nord)\b"), Region.USA),
]


def locate(
    text: str | None, *, country_hint: str | None = None
) -> tuple[str | None, Region | None]:
    """Return (ISO alpha-2 country or None, region or None)."""
    if country_hint:
        code = country_hint.strip().lower()[:2]
        if code in COUNTRY_REGION:
            return code, COUNTRY_REGION[code]
    if not text:
        return None, None
    folded = fold(text)
    for pattern, code in _NAME_PATTERNS:
        if pattern.search(folded):
            return code, COUNTRY_REGION.get(code, Region.OTHER)
    for pattern, region in _REGION_WORDS:
        if pattern.search(folded):
            return None, region
    return None, None
