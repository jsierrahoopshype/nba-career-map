"""Lightweight geography helpers for location discovery.

Wikipedia lead sentences often read "... based in <City>, <Region>" where
<Region> is a sub-national region (Lazio, Subcarpathian Voivodeship) rather
than a country. resolve_location() maps a known region to its country (keeping
the region as `state`), accepts a recognized country as-is, and otherwise
leaves the country blank so the team is flagged for manual review.
"""
from __future__ import annotations

# Recognized country tokens relevant to basketball careers (not exhaustive, but
# covers the leagues that appear in this dataset). Aliases normalize to a
# canonical name.
COUNTRY_ALIASES = {
    "usa": "USA", "u.s.": "USA", "u.s.a.": "USA", "united states": "USA",
    "uk": "United Kingdom", "u.k.": "United Kingdom",
    "united kingdom": "United Kingdom", "england": "United Kingdom",
    "scotland": "United Kingdom", "wales": "United Kingdom",
    "great britain": "United Kingdom",
}
COUNTRIES = {
    "USA", "Canada", "Mexico", "United Kingdom", "Ireland", "Spain", "France",
    "Italy", "Germany", "Greece", "Turkey", "Russia", "Serbia", "Croatia",
    "Slovenia", "Lithuania", "Latvia", "Estonia", "Poland", "Hungary",
    "Czech Republic", "Slovakia", "Belgium", "Netherlands", "Israel",
    "Portugal", "Switzerland", "Austria", "Finland", "Sweden", "Norway",
    "Denmark", "Ukraine", "Georgia", "Montenegro", "Bosnia and Herzegovina",
    "North Macedonia", "Bulgaria", "Romania", "China", "Japan", "South Korea",
    "Philippines", "Australia", "New Zealand", "Argentina", "Brazil", "Uruguay",
    "Venezuela", "Puerto Rico", "Dominican Republic", "Iran", "Lebanon",
}

# Sub-national region -> country. Covers the regions observed in the data plus
# the common Italian regions, Polish voivodeships and Spanish communities that
# turn up in European basketball club descriptions.
REGION_TO_COUNTRY = {
    # Italy
    "Lazio": "Italy", "Lombardy": "Italy", "Lombardia": "Italy",
    "Tuscany": "Italy", "Toscana": "Italy", "Veneto": "Italy",
    "Piedmont": "Italy", "Piemonte": "Italy", "Sicily": "Italy",
    "Sardinia": "Italy", "Emilia-Romagna": "Italy", "Campania": "Italy",
    "Apulia": "Italy", "Puglia": "Italy", "Marche": "Italy",
    "Liguria": "Italy", "Calabria": "Italy",
    # Poland (voivodeships)
    "Subcarpathian Voivodeship": "Poland", "Masovian Voivodeship": "Poland",
    "Silesian Voivodeship": "Poland", "Lesser Poland Voivodeship": "Poland",
    "Greater Poland Voivodeship": "Poland", "Pomeranian Voivodeship": "Poland",
    "Lower Silesian Voivodeship": "Poland",
    # Spain (autonomous communities)
    "Catalonia": "Spain", "Basque Country": "Spain", "Andalusia": "Spain",
    "Galicia": "Spain", "Valencian Community": "Spain",
    "Community of Madrid": "Spain", "Castile and León": "Spain",
    "Asturias": "Spain", "Canary Islands": "Spain",
    # Greece
    "Crete": "Greece", "Attica": "Greece", "Macedonia": "Greece",
}


# US states + DC (all map to USA). Used to catch American cities whose lead
# sentence reads "based in <City>, <State>" (e.g. "College Park, Georgia").
US_STATES = {
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
    "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine",
    "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi",
    "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey",
    "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio",
    "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina",
    "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia",
    "Washington", "West Virginia", "Wisconsin", "Wyoming",
    "District of Columbia", "D.C.", "Washington, D.C.",
}

# Tokens that name both a US state and a sovereign country. Disambiguated by
# whether the city is a known city of the *country*.
_AMBIGUOUS = {"Georgia"}
_GEORGIA_COUNTRY_CITIES = {"Tbilisi", "Batumi", "Kutaisi", "Rustavi", "Zugdidi"}


def resolve_location(tail: str, city: str = "") -> tuple[str, str]:
    """Given the token after the city in "based in City, X", return (state, country).

    - a US state -> (state, "USA"); "Georgia" defaults to the US state unless the
      city is a known city of the country Georgia
    - a recognized country (or alias) -> ("", country)
    - a known sub-national region -> (region, its country)
    - anything else -> (tail, "")   [country blank so the team is flagged]
    """
    tail = (tail or "").strip()
    city = (city or "").strip()
    if not tail:
        return "", ""
    if tail in _AMBIGUOUS:  # "Georgia": US state unless the city is Georgian
        if city in _GEORGIA_COUNTRY_CITIES:
            return "", "Georgia"
        return tail, "USA"
    if tail in US_STATES:
        return tail, "USA"
    alias = COUNTRY_ALIASES.get(tail.lower())
    if alias:
        return "", alias
    if tail in COUNTRIES:
        return "", tail
    if tail in REGION_TO_COUNTRY:
        return tail, REGION_TO_COUNTRY[tail]
    return tail, ""
