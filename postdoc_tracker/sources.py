"""
sources.py – Job source definitions for Postdoc Tracker
========================================================
Add, remove, or edit sources here without touching server.py.

Each entry in SOURCES is keyed by a short identifier and must contain:
  name             str   Display name shown in the UI
  type             str   Scraper to use: "inria" | "cnrs" | "linkedin" | "wtj" | "rss"
  base_url         str   URL of the source (no query string)
  supports_location bool  Whether to show the Location field in the UI

Optional fields depending on type:
  postdoc_only     bool  (cnrs) filter to postdoc positions only
  default_keywords str   Pre-filled keywords if the user leaves the field empty
  default_location str   Fallback location if the user leaves the field empty

---- How keywords and location are injected per type -------------------------

  inria     → ?keyword=<kw>  (no type filter — postdoc + research engineer both included)
              location filtered post-scrape on the Ville field

  cnrs      → URL is unchanged; keywords are applied as a post-scrape text
              filter because CNRS does not expose useful URL search params.
              location is ignored.

  linkedin  → ?keywords=<kw>&location=<loc>&f_TPR=r2592000&sortBy=DD
              f_TPR controls recency (r86400=24h, r604800=week, r2592000=month).

  wtj       → ?query=<kw>&aroundQuery=<loc>

  academicpositions → positions[0]=post-doc&positions[1]=research-engineer
                      fields[0]=<kw> (discipline, e.g. "mathematics", "computer science")
                      locations[0]=<loc> (e.g. "france", "europe") — space/comma separated
                      for multiple locations.

  rss       → URL returned as-is (no params injected). Keyword/location fields
              have no effect; filter post-scrape if needed.

For a plain RSS feed that does not take query params, just paste the full
URL as base_url — the keyword/location fields will have no effect.
"""

from urllib.parse import urlencode

SOURCES = {
    # ── Academic ──────────────────────────────────────────────────────────────
    "inria": {
        "name": "INRIA",
        "type": "inria",
        "base_url": "https://jobs.inria.fr/public/classic/fr/offres",
        "supports_location": True,
    },
    "cnrs": {
        "name": "CNRS",
        "type": "cnrs",
        "base_url": "https://emploi.cnrs.fr/Offres/Recherche.aspx",
        "supports_location": True,
    },

    # ── Industrial / mixed ────────────────────────────────────────────────────
    "linkedin": {
        "name": "LinkedIn",
        "type": "linkedin",
        "base_url": "https://www.linkedin.com/jobs/search/",
        "supports_location": True,
    },
    "wtj": {
        "name": "Welcome to the Jungle",
        "type": "wtj",
        "base_url": "https://www.welcometothejungle.com/fr/jobs",
        "supports_location": True,
    },

}


def build_url(source_id: str, keywords: str = "", location: str = "") -> str:
    """
    Build the final fetch URL for a given source, injecting user-supplied
    keywords and location. Falls back to the source's defaults if empty.
    """
    cfg   = SOURCES[source_id]
    stype = cfg["type"]
    base  = cfg["base_url"]
    kw    = keywords.strip()
    loc   = location.strip()

    if stype == "inria":
        # INRIA ignores URL search params server-side — keyword and location
        # are filtered post-scrape in server.py
        return base

    if stype == "cnrs":
        return base  # keyword filtering happens post-scrape; location not supported

    if stype == "linkedin":
        params = {
            "keywords": kw,
            "location": loc,
            "f_TPR":    "r2592000",
            "sortBy":   "DD",
        }
        return base + "?" + urlencode(params)

    if stype == "wtj":
        params = {"aroundQuery": loc or "France"}
        if kw:
            params["query"] = kw
        return base + "?" + urlencode(params)

    if stype == "rss":
        # Plain RSS feeds — just return the base URL as-is
        return base

    return base
