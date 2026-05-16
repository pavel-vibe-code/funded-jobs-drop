"""S1-S9 deterministic prefilter pipeline.

Each stage filters jobs against profile rules. Stages that need LLM judgment
(S5/S6 title-keyword matching, S8b industry inference from JD) are deferred
to Evaluation. Survivors of this pipeline go to LLM screening.

Stage mapping:
  S1: variant region — already enforced at API call (locations param)
  S2: work mode acceptance
  S3: country + relocation logic
  S4: seniority range
  S7: company blacklist (exact-match)
  S8a: industry exclusion (clean-tag match)
  S9: salary floor (when disclosed; FX-converted)
"""
from __future__ import annotations

import re
from typing import Optional

from discovery.sources.base import DiscoveredJob
from state.profile import Profile


# Static FX-to-USD rates. Update periodically. When a currency is missing here,
# S9 cannot compare and skips the salary filter for that job.
FX_TO_USD = {
    "USD": 1.00, "EUR": 1.08, "GBP": 1.27, "CHF": 1.10,
    "CAD": 0.73, "AUD": 0.66,
    "PLN": 0.25, "CZK": 0.043, "SEK": 0.095,
}

# Country recognition list for S3. Longer / multi-word names first so that
# matching prefers the more specific match (United Kingdom before UK, etc.).
_KNOWN_COUNTRIES = [
    "United Kingdom", "United States", "South Korea", "Saudi Arabia",
    "United Arab Emirates", "Hong Kong",
    "Germany", "France", "Spain", "Netherlands", "Italy", "Poland", "Portugal",
    "Sweden", "Denmark", "Finland", "Norway", "Austria", "Switzerland",
    "Belgium", "Czechia", "Czech Republic", "Lithuania", "Estonia", "Latvia",
    "Romania", "Bulgaria", "Hungary", "Slovakia", "Slovenia", "Croatia",
    "Greece", "Ireland", "Iceland", "Luxembourg", "Malta", "Cyprus",
    "Canada", "Mexico", "USA", "UK", "US",
    "India", "China", "Japan", "Singapore", "Australia",
    "Brazil", "Argentina", "Chile", "Colombia",
    "Israel", "IL", "IE",
]

_COUNTRY_ALIASES = {
    "usa": "united states",      "united states": "usa",
    "us":  "united states",
    "uk":  "united kingdom",     "united kingdom": "uk",
    "ie":  "ireland",
    "il":  "israel",
    "czechia": "czech republic", "czech republic": "czechia",
}

# City → country, for location strings like "San Francisco" or "Berlin" that
# have no country code attached. ATS adapters (esp. Ashby) commonly emit
# city-only values; without this map S3 falls through and non-EU on-site
# jobs slip into Pass B at meaningful cost. Cover the top US + EU tech hubs;
# expand as data demands.
_CITY_TO_COUNTRY = {
    # US tech hubs
    "san francisco":  "United States", "sf":             "United States",
    "bay area":       "United States", "palo alto":      "United States",
    "menlo park":     "United States", "mountain view":  "United States",
    "sunnyvale":      "United States", "redwood city":   "United States",
    "new york":       "United States", "new york city":  "United States",
    "nyc":            "United States", "brooklyn":       "United States",
    "manhattan":      "United States", "los angeles":    "United States",
    "la":             "United States", "seattle":        "United States",
    "bellevue":       "United States", "redmond":        "United States",
    "boston":         "United States", "cambridge ma":   "United States",
    "austin":         "United States", "dallas":         "United States",
    "houston":        "United States", "plano":          "United States",
    "chicago":        "United States", "denver":         "United States",
    "boulder":        "United States", "arvada":         "United States",
    "miami":          "United States", "washington dc":  "United States",
    "atlanta":        "United States", "san diego":      "United States",
    "portland":       "United States", "phoenix":        "United States",
    "tulsa":          "United States", "abilene":        "United States",
    "amarillo":       "United States", "warrenton":      "United States",
    "childress":      "United States", "brighton":       "United States",
    "springfield":    "United States", "salt lake city": "United States",
    # UK
    "london":         "United Kingdom", "manchester":    "United Kingdom",
    "edinburgh":      "United Kingdom", "oxford":        "United Kingdom",
    "cambridge uk":   "United Kingdom",
    # Ireland
    "dublin":         "Ireland",        "cork":          "Ireland",
    # EU continental
    "berlin":         "Germany",        "munich":        "Germany",
    "hamburg":        "Germany",        "frankfurt":     "Germany",
    "cologne":        "Germany",        "dresden":       "Germany",
    "paris":          "France",         "lyon":          "France",
    "marseille":      "France",         "toulouse":      "France",
    "amsterdam":      "Netherlands",    "rotterdam":     "Netherlands",
    "the hague":      "Netherlands",    "utrecht":       "Netherlands",
    "barcelona":      "Spain",          "madrid":        "Spain",
    "milan":          "Italy",          "rome":          "Italy",
    "stockholm":      "Sweden",         "copenhagen":    "Denmark",
    "helsinki":       "Finland",        "oslo":          "Norway",
    "warsaw":         "Poland",         "krakow":        "Poland",
    "lisbon":         "Portugal",       "brussels":      "Belgium",
    "zurich":         "Switzerland",    "geneva":        "Switzerland",
    "vienna":         "Austria",        "athens":        "Greece",
    "budapest":       "Hungary",        "bucharest":     "Romania",
    "prague":         "Czechia",        "brno":          "Czechia",
    # Outside EU/US tech hubs
    "tel aviv":       "Israel",         "tokyo":         "Japan",
    "singapore":      "Singapore",      "hong kong":     "Hong Kong",
    "sydney":         "Australia",      "melbourne":     "Australia",
    "toronto":        "Canada",         "vancouver":     "Canada",
    "montreal":       "Canada",         "mexico city":   "Mexico",
    "bangalore":      "India",          "mumbai":        "India",
    "shanghai":       "China",          "beijing":       "China",
    "são paulo":      "Brazil",         "sao paulo":     "Brazil",
    # Additional hubs seen in Workday enterprise tenants (MSD/Nvidia/Adobe).
    "san jose":       "United States",  "lehi":          "United States",
    "mclean":         "United States",  "ottawa":        "Canada",
    "noida":          "India",          "hyderabad":     "India",
    "pune":           "India",          "chennai":       "India",
    "gurugram":       "India",          "gurgaon":       "India",
    "seoul":          "South Korea",    "riyadh":        "Saudi Arabia",
    "bangkok":        "Thailand",       "jakarta":       "Indonesia",
    "manila":         "Philippines",    "kuala lumpur":  "Malaysia",
    "dubai":          "United Arab Emirates",
}

# ISO 3166-1 alpha-3 → country, for the "DEU - Berlin - …" / "MYS - Selangor"
# location format Workday tenants emit (MSD et al.). Matched only as a leading
# "XXX - " token (see _country_from_text) so English-word codes can't misfire.
# EU-region codes must map to names present in _EU_REGION / _EU_UK_IE; the rest
# need only be accurate (they classify as out-of-region). Missing codes degrade
# safely to "ambiguous" — never a wrong drop.
_ISO3_TO_COUNTRY = {
    # EU continental + EEA/EFTA
    "deu": "Germany", "fra": "France", "esp": "Spain", "nld": "Netherlands",
    "ita": "Italy", "pol": "Poland", "prt": "Portugal", "swe": "Sweden",
    "dnk": "Denmark", "fin": "Finland", "nor": "Norway", "aut": "Austria",
    "che": "Switzerland", "bel": "Belgium", "cze": "Czechia",
    "ltu": "Lithuania", "est": "Estonia", "lva": "Latvia", "rou": "Romania",
    "bgr": "Bulgaria", "hun": "Hungary", "svk": "Slovakia", "svn": "Slovenia",
    "hrv": "Croatia", "grc": "Greece", "isl": "Iceland", "lux": "Luxembourg",
    "mlt": "Malta", "cyp": "Cyprus",
    # UK + Ireland
    "gbr": "United Kingdom", "irl": "Ireland",
    # Americas
    "usa": "United States", "can": "Canada", "mex": "Mexico", "bra": "Brazil",
    "arg": "Argentina", "chl": "Chile", "col": "Colombia", "per": "Peru",
    "ecu": "Ecuador", "cri": "Costa Rica", "pan": "Panama",
    "pri": "Puerto Rico", "ury": "Uruguay", "ven": "Venezuela",
    "dom": "Dominican Republic", "gtm": "Guatemala",
    # Asia
    "chn": "China", "ind": "India", "jpn": "Japan", "kor": "South Korea",
    "twn": "Taiwan", "hkg": "Hong Kong", "sgp": "Singapore", "mys": "Malaysia",
    "idn": "Indonesia", "tha": "Thailand", "vnm": "Vietnam",
    "phl": "Philippines", "pak": "Pakistan", "bgd": "Bangladesh",
    # Oceania
    "aus": "Australia", "nzl": "New Zealand",
    # Africa
    "zaf": "South Africa", "egy": "Egypt", "nga": "Nigeria", "ken": "Kenya",
    "mar": "Morocco",
    # Middle East + other
    "tur": "Turkey", "isr": "Israel", "sau": "Saudi Arabia",
    "are": "United Arab Emirates", "qat": "Qatar", "lbn": "Lebanon",
    "jor": "Jordan", "kwt": "Kuwait", "ukr": "Ukraine", "rus": "Russia",
    "srb": "Serbia",
}

# US state names → United States. Workday enterprise tenants post location as
# "Remote Illinois", "Austin, Texas" etc.; without this such jobs go undetected
# by the country/city passes and slip through the variant-region filter.
_US_STATES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "north carolina", "north dakota", "ohio", "oklahoma",
    "oregon", "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming",
}

# Combined country-name matcher: the curated S3 list + every ISO-3 map value +
# "Korea" (Workday writes the short form). Sorted longest-first so multi-word
# names win over their abbreviations ("United Kingdom" before "UK").
_COUNTRY_NAMES = sorted(
    set(_KNOWN_COUNTRIES) | set(_ISO3_TO_COUNTRY.values()) | {"Korea"},
    key=len, reverse=True,
)

# Variant region sets — S1a hard-drops jobs in countries outside the user's
# variant region. EU continental + EEA/EFTA; UK + Ireland are conditional on
# Profile.eu_include_uk_ie. US is US-only (no Canada/Mexico — keeps semantics
# clean; users wanting cross-border can override per-favorite).
_EU_REGION = {c.lower() for c in (
    "Germany", "France", "Spain", "Netherlands", "Italy", "Poland", "Portugal",
    "Sweden", "Denmark", "Finland", "Norway", "Austria", "Switzerland",
    "Belgium", "Czechia", "Czech Republic", "Lithuania", "Estonia", "Latvia",
    "Romania", "Bulgaria", "Hungary", "Slovakia", "Slovenia", "Croatia",
    "Greece", "Iceland", "Luxembourg", "Malta", "Cyprus",
)}
_EU_UK_IE = {c.lower() for c in ("United Kingdom", "UK", "Ireland", "IE")}
_US_REGION = {c.lower() for c in ("United States", "USA", "US")}

# Region hints — multi-country location markers that don't map to one country
# but still affirmatively place the job in a variant region. Used so that a
# job posted as "Remote, Europe" or "Remote, EMEA" survives the variant gate
# even when no specific country is detectable.
_EU_REGION_HINTS = {"europe", "eu", "emea", "european union", "eea"}
_US_REGION_HINTS = {"united states", "north america", "us-based", "us only"}


def _allowed_countries_for(profile: Profile) -> Optional[set[str]]:
    """Return the lower-case set of countries allowed by the variant region.

    None means no S1a filter applies (unknown variant — let everything through).
    """
    if profile.variant == "EU":
        allowed = set(_EU_REGION)
        if profile.eu_include_uk_ie:
            allowed |= _EU_UK_IE
        return allowed
    if profile.variant == "US":
        return set(_US_REGION)
    return None


def _region_hints_for(variant: str) -> set[str]:
    """Multi-country hint phrases that affirmatively place a job in-region."""
    if variant == "EU":
        return _EU_REGION_HINTS
    if variant == "US":
        return _US_REGION_HINTS
    return set()


def _matches_region_hint(job: DiscoveredJob, hints: set[str]) -> bool:
    """Check raw_location for any region hint phrase ('Europe', 'EMEA', etc.)."""
    if not hints:
        return False
    text = " ".join(list(job.normalized_locations) + list(job.raw_location)).lower()
    return any(re.search(rf"\b{re.escape(h)}\b", text) for h in hints)


def _accepted_modes(profile: Profile) -> set[str]:
    """Convert profile.work_modes labels to canonical {remote, hybrid, on_site}."""
    accepted: set[str] = set()
    for m in profile.work_modes:
        if m == "Remote":
            accepted.add("remote")
        elif m == "Hybrid":
            accepted.add("hybrid")
        elif m == "Onsite (includes Hybrid)":
            accepted.add("on_site")
            accepted.add("hybrid")  # onsite implies hybrid OK
    return accepted


def _country_from_text(text: str) -> Optional[str]:
    """Country name from a lower-cased location string.

    Search order:
      1. Word-boundary match against the country list (longer names first).
      2. Word-boundary match against the city-to-country map.

    Word boundaries avoid false positives — substring matching treated
    "houston" as containing "us", which mis-classified countries.
    """
    if not text.strip():
        return None
    # Leading ISO-3166 alpha-3 code in Workday's "XXX - region - city" format.
    # Anchored to the start AND requiring the " - " separator, so English-word
    # codes (AND, ARE, CAN) can't false-match a city/place name.
    iso = re.match(r"([a-z]{3})\s+-\s+", text)
    if iso and iso.group(1) in _ISO3_TO_COUNTRY:
        return _ISO3_TO_COUNTRY[iso.group(1)]
    for country in _COUNTRY_NAMES:
        if re.search(rf"\b{re.escape(country.lower())}\b", text):
            return country
    for state in _US_STATES:
        if re.search(rf"\b{re.escape(state)}\b", text):
            return "United States"
    for city, country in _CITY_TO_COUNTRY.items():
        if re.search(rf"\b{re.escape(city)}\b", text):
            return country
    return None


def _extract_country(job: DiscoveredJob) -> Optional[str]:
    """Best-effort country extraction from a job's location fields."""
    haystacks = list(job.normalized_locations) + list(job.raw_location)
    return _country_from_text(" ".join(haystacks).lower())


def location_in_variant_region(location_text: str,
                               profile: Profile) -> Optional[bool]:
    """Variant-region membership for a raw location string.

    Returns True (in-region), False (out-of-region), or None — None meaning
    no country was detectable, or no variant filter applies to this profile;
    the caller should keep the job and defer to post-JD screening.

    Mirrors the S1a check in `apply`, but works on a bare string so the
    Favorites source can drop obviously-out-of-region jobs *before* the
    per-job JD fetch, for ATSes whose listing response carries a location
    (Workday). Ambiguous (None) is deliberately kept — same lax policy as S1a.
    """
    allowed = _allowed_countries_for(profile)
    if allowed is None:
        return None
    text = (location_text or "").lower().strip()
    if not text:
        return None
    hints = _region_hints_for(profile.variant)
    if hints and any(re.search(rf"\b{re.escape(h)}\b", text) for h in hints):
        return True
    country = _country_from_text(text)
    if country is None:
        return None
    return country.lower() in allowed


def _same_country(extracted: str, home: str) -> bool:
    """Check two country strings refer to the same place, handling aliases."""
    e, h = extracted.lower(), home.lower()
    if e == h:
        return True
    return _COUNTRY_ALIASES.get(e) == h


def _to_usd(amount: float, currency: Optional[str]) -> Optional[float]:
    """FX-convert to USD. Returns None if currency unknown."""
    if not currency:
        return None
    rate = FX_TO_USD.get(currency)
    return amount * rate if rate is not None else None


def apply(jobs: list[DiscoveredJob],
          profile: Profile) -> tuple[list[DiscoveredJob], dict[str, int]]:
    """Run S1a + S2-S9 sequentially. Returns (survivors, per-stage drop counts).

    S1a (variant region) hard-drops jobs in countries outside the user's
    variant region — EU continental (+ UK/IE if opted in) for EU variant,
    US-only for US variant. This is stricter than S3, which only enforces
    home-country/relocation logic. S1a runs first so we don't waste cycles
    on jobs that can't qualify regardless of work-mode/relocation.
    """
    counts: dict[str, int] = {
        "input": len(jobs),
        "s1a_variant_region": 0,
        "s2_work_mode": 0,
        "s3_country_relocation": 0,
        "s4_seniority": 0,
        "s7_company_blacklist": 0,
        "s8a_industry_blacklist": 0,
        "s9_salary_floor": 0,
        "output": 0,
    }

    accepted_modes = _accepted_modes(profile)
    excluded_companies = {c.lower() for c in profile.excluded_companies}
    excluded_industries = {i.lower() for i in profile.excluded_industries}
    allowed_region = _allowed_countries_for(profile)
    region_hints = _region_hints_for(profile.variant)

    survivors: list[DiscoveredJob] = []
    for j in jobs:
        # S1a: variant region.
        #
        # Policy differs by source:
        #   VC sources (Consider/Getro): variant is enforced at fetch time
        #     via API region params. S1a is defense-in-depth — drops only
        #     when detected country is unambiguously out-of-region.
        #     Unknown locations get the benefit of the doubt.
        #   Favorites: no fetch-time gate (per-company, not per-region).
        #     Require a POSITIVE in-region signal — detected country in
        #     variant set OR explicit region hint ("Europe", "EMEA",
        #     "EU"). Unknown/ambiguous → drop.
        if allowed_region is not None:
            job_country = _extract_country(j)
            in_region = bool(
                (job_country and job_country.lower() in allowed_region)
                or _matches_region_hint(j, region_hints)
            )
            # Both VC and Favorites get the same lax deterministic check:
            # drop only when the detected country is unambiguously
            # out-of-region. Ambiguous cases (no detectable country, or
            # "Remote/Anywhere") fall through. For Favorites post-JD,
            # the /fd-run skill follows up with a Haiku Pass A screener
            # that judges those ambiguous cases using title + location +
            # JD excerpt — see `postjd_screen_apply` in orchestrator.py.
            if job_country and not in_region:
                counts["s1a_variant_region"] += 1
                continue

        # S2: work mode acceptance
        if accepted_modes and j.work_mode not in accepted_modes:
            counts["s2_work_mode"] += 1
            continue

        # S3: country + relocation
        if profile.home_country:
            job_country = _extract_country(j)
            if job_country and not _same_country(job_country, profile.home_country):
                # Job is in a different country than home
                if not profile.search_outside_home:
                    counts["s3_country_relocation"] += 1
                    continue
                if not profile.willing_to_relocate and j.work_mode != "remote":
                    # Not relocating + not remote → can't take this job
                    counts["s3_country_relocation"] += 1
                    continue
                # Remote in another country (still within variant region):
                # keep, residency verified at Pass B
            # job_country == home OR job_country unknown: continue to next stage

        # S4: seniority
        if profile.accepted_seniority and j.seniority:
            if j.seniority not in profile.accepted_seniority:
                counts["s4_seniority"] += 1
                continue
        # j.seniority None: keep, LLM will figure it out

        # S7: company blacklist (exact-match on normalized name)
        if j.company_name.lower() in excluded_companies:
            counts["s7_company_blacklist"] += 1
            continue

        # S8a: industry exclusion via clean tag match
        if excluded_industries:
            job_tags_lower = {t.lower() for t in j.industry_tags}
            if job_tags_lower & excluded_industries:
                counts["s8a_industry_blacklist"] += 1
                continue

        # S9: salary floor (when disclosed AND both currencies known to FX table)
        if (
            j.salary_disclosed
            and j.salary_max_yearly
            and profile.salary_floor_amount
            and profile.salary_floor_currency
        ):
            job_max_usd = _to_usd(j.salary_max_yearly, j.salary_currency)
            floor_usd = _to_usd(profile.salary_floor_amount, profile.salary_floor_currency)
            if job_max_usd is not None and floor_usd is not None and job_max_usd < floor_usd:
                counts["s9_salary_floor"] += 1
                continue

        survivors.append(j)

    counts["output"] = len(survivors)
    return survivors, counts
