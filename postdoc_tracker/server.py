"""
Postdoc Tracker – Flask backend
"""

import json
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request, send_from_directory, Response

try:
    import yaml
    def _load_yaml(path):
        with open(path) as f:
            return yaml.safe_load(f) or {}
except ImportError:
    import json as _json
    def _load_yaml(path):
        p = Path(str(path).replace(".yaml", ".json"))
        return _json.loads(p.read_text()) if p.exists() else {}

from .sources import SOURCES as FEEDS, build_url as build_feed_url

# ── Paths ─────────────────────────────────────────────────────────────────────
PACKAGE_DIR = Path(__file__).parent
PUBLIC      = PACKAGE_DIR / "public"

# User data dir: prefer cwd (dev / run-in-place), else ~/.postdoc-tracker/
_cwd = Path.cwd()
if (_cwd / "config.yaml").exists():
    USER_DIR = _cwd
else:
    USER_DIR = Path.home() / ".postdoc-tracker"
    USER_DIR.mkdir(exist_ok=True)
    if not (USER_DIR / "config.yaml").exists():
        shutil.copy(PACKAGE_DIR / "config_default.yaml", USER_DIR / "config.yaml")

DATA_DIR = USER_DIR / "data"
DB_PATH  = USER_DIR / "data" / "jobs.json"

# ── Config ────────────────────────────────────────────────────────────────────
CONFIG       = _load_yaml(USER_DIR / "config.yaml")
PORT         = CONFIG.get("app", {}).get("port", 3742)
APP_TITLE    = CONFIG.get("app", {}).get("title", "Postdoc Tracker")
FILTER_OUT   = [kw.lower() for kw in CONFIG.get("filter_out", [])]
DOMAIN_RULES = {k: [kw.lower() for kw in v] for k, v in CONFIG.get("domain_rules", {}).items()}

DATA_DIR.mkdir(exist_ok=True)
if not DB_PATH.exists():
    DB_PATH.write_text(json.dumps({"jobs": [], "lastUpdated": datetime.now(timezone.utc).isoformat()}, indent=2))

app = Flask(__name__, static_folder=str(PUBLIC), static_url_path="")

# ── DB with write lock ────────────────────────────────────────────────────────
_db_lock = threading.Lock()

def read_db():
    with _db_lock:
        return json.loads(DB_PATH.read_text())

def write_db(data):
    data["lastUpdated"] = datetime.now(timezone.utc).isoformat()
    with _db_lock:
        DB_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))

# ── Position type detector ────────────────────────────────────────────────────
_POSTDOC_KW = re.compile(
    r'post.?doc|post.?doctoral|postdoctorant|chercheur\b|research fellow|research associate|'
    r'research visit|visiting researcher',
    re.I
)
_PHD_KW = re.compile(
    r'\bphd\b|th[eè]se\b|th[eé]sard|doctorant\b|doctoral student|'
    r'doctorate\b|phd position\b|phd student',
    re.I
)

def detect_position_type(text: str, url: str = "") -> str:
    """Returns 'postdoc', 'phd', or 'other'."""
    # URL-based hint (CNRS)
    if "/Offres/Doctorant/" in url:
        return "phd"
    if "/Offres/CDD/" in url or "/Offres/CDI/" in url:
        # CDD can be postdoc or engineer — refine by text
        pass
    t = (text or "").lower()
    if _POSTDOC_KW.search(t):
        return "postdoc"
    if _PHD_KW.search(t):
        return "phd"
    return "other"

# ── Domain auto-tagger ────────────────────────────────────────────────────────
def auto_tag_domains(text: str) -> list[str]:
    lower = (text or "").lower()
    return [d for d, kws in DOMAIN_RULES.items() if any(kw in lower for kw in kws)]


# ── Deadline extractor ────────────────────────────────────────────────────────
MONTH_MAP = {
    "january":"01","february":"02","march":"03","april":"04","may":"05","june":"06",
    "july":"07","august":"08","september":"09","october":"10","november":"11","december":"12",
    "jan":"01","feb":"02","mar":"03","apr":"04","jun":"06","jul":"07","aug":"08",
    "sep":"09","oct":"10","nov":"11","dec":"12",
    "janvier":"01","février":"02","fevrier":"02","mars":"03","avril":"04","mai":"05","juin":"06",
    "juillet":"07","août":"08","aout":"08","septembre":"09","octobre":"10","novembre":"11","décembre":"12","decembre":"12",
}

def extract_deadline(text: str) -> str | None:
    if not text:
        return None
    # ISO date: 2026-05-15
    m = re.search(r'\b(202[6-9]|203\d)[-/](0[1-9]|1[0-2])[-/]([0-2]\d|3[01])\b', text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    months = "|".join(MONTH_MAP.keys())
    # "15 May 2026" or "15 mai 2026"
    m = re.search(rf'\b(\d{{1,2}})\s+({months})\s+(202[6-9]|203\d)\b', text, re.I)
    if m:
        return f"{m.group(3)}-{MONTH_MAP[m.group(2).lower()]}-{m.group(1).zfill(2)}"
    # "May 15, 2026"
    m = re.search(rf'\b({months})\s+(\d{{1,2}})[,\s]+(202[6-9]|203\d)\b', text, re.I)
    if m:
        return f"{m.group(3)}-{MONTH_MAP[m.group(1).lower()]}-{m.group(2).zfill(2)}"
    return None

# ── HTML job extractor (single URL) ──────────────────────────────────────────
def extract_job_from_html(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    # Try JSON-LD JobPosting
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") == "JobPosting":
                    org = item.get("hiringOrganization") or {}
                    loc = item.get("jobLocation") or {}
                    addr = loc.get("address") or {} if isinstance(loc, dict) else {}
                    location = addr.get("addressLocality") or addr.get("addressCountry") or ""
                    salary_info = item.get("baseSalary") or {}
                    salary = str(salary_info.get("value", {}).get("value", "")) if isinstance(salary_info, dict) else ""
                    desc = BeautifulSoup(item.get("description", ""), "html.parser").get_text(" ")[:800]
                    deadline_raw = item.get("validThrough", "")
                    title = item.get("title", "")
                    return {
                        "title": title,
                        "institution": org.get("name", "") if isinstance(org, dict) else "",
                        "location": location,
                        "deadline": deadline_raw[:10] if deadline_raw else None,
                        "description": desc.strip(),
                        "salary": salary,
                        "positionType": detect_position_type(title + " " + desc, url),
                    }
        except Exception:
            pass

    og = lambda name: (soup.find("meta", property=f"og:{name}") or {}).get("content", "")
    title = og("title") or (soup.find("h1") and soup.find("h1").get_text(strip=True)) or \
            (soup.find("title") and soup.find("title").get_text(strip=True)) or ""
    description = og("description") or ""
    body_text = soup.get_text(" ")
    deadline = extract_deadline(body_text)
    hostname = urlparse(url).hostname or ""
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return {
        "title": title.strip(),
        "institution": hostname,
        "location": "",
        "deadline": deadline,
        "description": description[:800],
        "salary": "",
        "positionType": detect_position_type(title + " " + description + " " + body_text[:500], url),
    }

# ── RSS parser ────────────────────────────────────────────────────────────────
_INVALID_XML_CHARS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]')

def _clean_xml(text: str) -> str:
    return _INVALID_XML_CHARS.sub('', text)

def parse_rss(xml_text: str) -> tuple[str, list[dict]]:
    xml_text = _clean_xml(xml_text)
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        items_raw = re.findall(r'<item[\s>].*?</item>', xml_text, re.DOTALL)
        if not items_raw:
            raise
        xml_text = '<rss><channel><title/>' + ''.join(items_raw) + '</channel></rss>'
        root = ET.fromstring(xml_text)

    channel = root.find("channel")
    feed_title = channel.findtext("title", "") if channel else ""
    items = []
    for item in (channel or root).iter("item"):
        title = item.findtext("title", "").strip()
        link  = item.findtext("link", "").strip()
        desc  = BeautifulSoup(item.findtext("description", ""), "html.parser").get_text(" ")[:500]
        pub   = item.findtext("pubDate", "")
        creator = item.findtext("{http://purl.org/dc/elements/1.1/}creator", "")
        text  = title + " " + desc
        deadline = extract_deadline(pub) or extract_deadline(text)
        items.append({
            "title": title,
            "institution": creator,
            "location": "",
            "url": link,
            "source": "feed",
            "deadline": deadline,
            "description": desc,
            "salary": "",
            "domains": auto_tag_domains(text),
            "positionType": detect_position_type(text, link),
        })
    return feed_title, items

# ── Shared browser-like headers ───────────────────────────────────────────────
_BR_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

# ── INRIA scraper ─────────────────────────────────────────────────────────────
def scrape_inria(url: str, source_id: str) -> tuple[str, list[dict]]:
    resp = requests.get(url, headers=_BR_HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    feed_title = soup.title.get_text(strip=True) if soup.title else "INRIA Jobs"
    existing = {j["url"] for j in read_db()["jobs"] if j["url"]}
    items = []
    for card in soup.find_all("div", class_="job-card"):
        h = card.find(["h2", "h3"])
        title = h.get_text(strip=True) if h else ""
        if not title:
            continue
        link_el = card.find("a", href=True)
        href = link_el["href"] if link_el else ""
        job_url = ("https://jobs.inria.fr" + href) if href.startswith("/") else href
        meta = [li.get_text(" ", strip=True) for li in card.find_all("li")]
        location = ""
        deadline = None
        for m in meta:
            if re.search(r'ville\s*:', m, re.I):
                location = re.split(r'ville\s*:', m, flags=re.I, maxsplit=1)[-1].strip()
            if re.search(r'date limite|deadline', m, re.I):
                raw = m.split(":", 1)[-1].strip()
                deadline = extract_deadline(raw) or (raw[:10] if raw else None)
        items.append({
            "title": title,
            "institution": "INRIA",
            "location": f"{location}, France" if location else "France",
            "url": job_url,
            "source": source_id,
            "deadline": deadline,
            "description": "",
            "salary": "",
            "domains": auto_tag_domains(title),
            "positionType": detect_position_type(title, job_url),
            "alreadyAdded": job_url in existing,
        })
    return feed_title, items

# ── LinkedIn public job search scraper ───────────────────────────────────────
def scrape_linkedin(cfg: dict) -> tuple[str, list[dict]]:
    """
    Scrapes the LinkedIn public job search page (no login required).
    LinkedIn renders SSR HTML for SEO, so BeautifulSoup can read job cards.
    """
    headers = {
        **_BR_HEADERS,
        "Accept-Language":  "en-US,en;q=0.9",  # override fr-FR: LinkedIn uses this to pick regional index
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
    }
    resp = requests.get(cfg["url"], headers=headers, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    existing = {j["url"] for j in read_db()["jobs"] if j["url"]}
    items = []
    seen = set()

    for card in soup.find_all(class_=re.compile(r'base-card|job-search-card')):
        title_el   = card.find(class_=re.compile(r'base-search-card__title|job-search-card__title'))
        company_el = card.find(class_=re.compile(r'base-search-card__subtitle|job-search-card__company'))
        loc_el     = card.find(class_=re.compile(r'job-search-card__location'))
        link_el    = card.find("a", href=re.compile(r'linkedin\.com/jobs/(view|collections)/'))

        if not title_el or not link_el:
            continue
        title    = title_el.get_text(strip=True)
        company  = company_el.get_text(strip=True) if company_el else ""
        location = loc_el.get_text(strip=True) if loc_el else ""
        # Strip tracking params from LinkedIn job URLs
        job_url  = link_el["href"].split("?")[0]
        if job_url in seen:
            continue
        seen.add(job_url)

        text = title + " " + company
        items.append({
            "title":        title,
            "institution":  company,
            "location":     location,
            "url":          job_url,
            "source":       cfg.get("id", "linkedin"),
            "deadline":     None,
            "description":  "",
            "salary":       "",
            "domains":      auto_tag_domains(text),
            "positionType": detect_position_type(text, job_url),
            "alreadyAdded": job_url in existing,
        })

    return "LinkedIn Jobs", items


# ── Welcome to the Jungle scraper ─────────────────────────────────────────────
_WTJ_COUNTRY_CODES = {
    "france":"FR","germany":"DE","netherlands":"NL","belgium":"BE","switzerland":"CH",
    "spain":"ES","italy":"IT","uk":"GB","united kingdom":"GB","sweden":"SE",
    "denmark":"DK","norway":"NO","finland":"FI","austria":"AT","portugal":"PT",
    "luxembourg":"LU","ireland":"IE","poland":"PL","czechia":"CZ","czech republic":"CZ",
    "europe":"EU",
}

_WTJ_ENV_CACHE: dict = {}

def _get_wtj_env() -> dict:
    """Fetch window.env from the WTJ homepage (Algolia credentials live there)."""
    if _WTJ_ENV_CACHE:
        return _WTJ_ENV_CACHE
    resp = requests.get("https://www.welcometothejungle.com/fr/jobs",
                        headers=_BR_HEADERS, timeout=15)
    resp.raise_for_status()
    m = re.search(r'window\.env\s*=\s*(\{[^;]+\})', resp.text)
    if m:
        _WTJ_ENV_CACHE.update(json.loads(m.group(1)))
    return _WTJ_ENV_CACHE


def _wtj_location_filter(location: str) -> str:
    """
    Convert a free-text location into an Algolia filter string.
    Supports country names (→ country_code) and city names (→ city).
    Comma-separated parts are OR-ed together.
    """
    parts = [p.strip() for p in location.replace("/", ",").split(",") if p.strip()]
    clauses = []
    for part in parts:
        key = part.lower()
        code = _WTJ_COUNTRY_CODES.get(key)
        if code:
            clauses.append(f"offices.country_code:{code}")
        else:
            # Treat as city name — capitalise to match stored values
            city = part.strip().title()
            clauses.append(f"offices.city:{city}")
    return " OR ".join(clauses)


def scrape_wtj(cfg: dict) -> tuple[str, list[dict]]:
    """
    Query WTJ via their Algolia search index.
    Credentials are extracted from window.env on the WTJ homepage.
    """
    from urllib.parse import urlparse, parse_qs
    parsed   = urlparse(cfg["url"])
    qs       = parse_qs(parsed.query)
    query    = " ".join(qs.get("query", []))
    location = " ".join(qs.get("aroundQuery", [""]))

    env = _get_wtj_env()
    app_id  = env.get("ALGOLIA_APPLICATION_ID", "CSEKHVMS53")
    api_key = env.get("ALGOLIA_API_KEY_CLIENT", "4bd8f6215d0cc52b26430765769e65a0")
    index   = env.get("ALGOLIA_JOBS_INDEX_PREFIX", "wttj_jobs_production") + "_fr"

    algolia_url = f"https://{app_id}-dsn.algolia.net/1/indexes/{index}/query"
    payload: dict = {
        "query":       query,
        "hitsPerPage": 50,
        "page":        0,
    }
    if location:
        payload["filters"] = _wtj_location_filter(location)
    resp = requests.post(
        algolia_url,
        headers={
            "X-Algolia-Application-Id": app_id,
            "X-Algolia-API-Key":        api_key,
            "Content-Type":             "application/json",
            "Referer":                  "https://www.welcometothejungle.com/",
        },
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    existing  = {j["url"] for j in read_db()["jobs"] if j["url"]}
    source_id = cfg.get("id", "wtj")
    items     = []
    seen      = set()

    for j in data.get("hits", []):
        title        = j.get("name", "")
        org          = j.get("organization") or {}
        company      = org.get("name", "")
        org_slug     = org.get("slug", "")
        slug         = j.get("slug", "")
        offices      = j.get("offices") or []
        city         = offices[0].get("city", "") if offices else ""
        country_name = offices[0].get("country", "") if offices else ""
        loc          = ", ".join(filter(None, [city, country_name])) or location
        job_url      = (f"https://www.welcometothejungle.com/fr/companies/{org_slug}/jobs/{slug}"
                        if org_slug and slug else "")
        if not title or job_url in seen:
            continue
        seen.add(job_url)
        text = title + " " + company + " " + j.get("summary", "")
        items.append({
            "title":        title,
            "institution":  company,
            "location":     loc,
            "url":          job_url,
            "source":       source_id,
            "deadline":     None,
            "description":  BeautifulSoup(j.get("summary", ""), "html.parser").get_text(" ")[:400],
            "salary":       "",
            "domains":      auto_tag_domains(text),
            "positionType": detect_position_type(text, job_url),
            "alreadyAdded": job_url in existing,
        })

    return "Welcome to the Jungle", items


# ── Generic heuristic scraper (company pages) ────────────────────────────────
def scrape_heuristic(cfg: dict) -> tuple[str, list[dict]]:
    """
    Best-effort scraper for company job pages.
    Finds all links whose URL or surrounding text looks like a job posting,
    deduplicates, and returns up to 50 items.
    """
    url = cfg["url"]
    resp = requests.get(url, headers=_BR_HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    feed_title = cfg.get("name", soup.title.get_text(strip=True) if soup.title else "Jobs")
    existing   = {j["url"] for j in read_db()["jobs"] if j["url"]}
    parsed     = urlparse(url)
    base       = f"{parsed.scheme}://{parsed.netloc}"
    source_id  = cfg.get("id", "heuristic")
    institution = cfg.get("institution", feed_title)
    default_loc = cfg.get("default_location", "France")

    items = []
    seen  = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        job_url = href if href.startswith("http") else base + href
        if job_url in seen:
            continue

        title = a.get_text(strip=True)
        # A job title should be a real sentence, not a nav link
        if not (10 <= len(title) <= 200):
            continue
        if any(w in title.lower() for w in [
            "home", "accueil", "contact", "news", "actualité",
            "about", "à propos", "login", "search", "menu",
        ]):
            continue

        # The URL or surrounding text should hint at a job/offer
        parent = a.find_parent(["article", "li", "div", "tr", "section"])
        context = parent.get_text(" ", strip=True)[:600] if parent else ""
        combined = job_url + " " + context
        if not re.search(
            r'offre|emploi|job|poste|position|career|recrutement|vacancy|recruit|cdd|cdi|postdoc',
            combined, re.I
        ):
            continue

        seen.add(job_url)

        # Try to extract a city from the surrounding text
        location = default_loc
        loc_m = re.search(r'\b([A-ZÀ-Ü][a-zà-ü]+(?:[- ][A-ZÀ-Ü][a-zà-ü]+)*),?\s*(?:France|FR)\b', context)
        if loc_m:
            location = loc_m.group(0).strip()

        text = title + " " + context
        items.append({
            "title":        title,
            "institution":  institution,
            "location":     location,
            "url":          job_url,
            "source":       source_id,
            "deadline":     extract_deadline(context),
            "description":  context[:400],
            "salary":       "",
            "domains":      auto_tag_domains(text),
            "positionType": detect_position_type(text, job_url),
            "alreadyAdded": job_url in existing,
        })
        if len(items) >= 50:
            break

    return feed_title, items

# ── CNRS scraper ──────────────────────────────────────────────────────────────
def scrape_cnrs(cfg: dict) -> tuple[str, list[dict]]:
    """
    Scrapes emploi.cnrs.fr. Always fetches the default listing (newest first).
    Uses URL path to distinguish PhD (/Offres/Doctorant/) from CDD (/Offres/CDD/).
    """
    resp = requests.get(cfg.get("url") or cfg["base_url"], headers=_BR_HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    feed_title = "CNRS Emploi"
    existing = {j["url"] for j in read_db()["jobs"] if j["url"]}
    items = []
    for card in soup.find_all("div", class_=lambda c: c and "card" in c and "shadow" in c):
        link_el = card.find("a", href=True)
        href = link_el["href"] if link_el else ""
        if not href:
            continue
        job_url = ("https://emploi.cnrs.fr" + href) if href.startswith("/") else href

        # Always skip PhD and permanent positions
        if "/Offres/Doctorant/" in href or "/Offres/CDI/" in href:
            continue

        # Extract title (first meaningful heading or link text)
        title_el = card.find(["h2","h3","h4","h5"]) or link_el
        title = title_el.get_text(strip=True) if title_el else ""
        # Remove leading "H/F " prefix common on CNRS listings
        title = re.sub(r'^H/F\s+', '', title).strip()
        if not title:
            continue

        full_text = card.get_text(" ", strip=True)
        pos_type = detect_position_type(title + " " + full_text, href)

        # Extract institution and city from card text
        # Pattern: "Title Nouveau? Lab CITY • Dept ContractType …"
        institution = ""
        location = ""
        # City is the word(s) just before " • "
        city_match = re.search(r'([A-ZÀÂÉÈÊÎÏÔÙÛÜ][A-ZÀÂÉÈÊÎÏÔÙÛÜ\s\-]+)\s*•', full_text)
        if city_match:
            city = city_match.group(1).strip().title()
            location = f"{city}, France"
        # Institution: first multi-word segment that's not the title or a keyword
        title_lower = title.lower()
        for seg in re.split(r'(?:Nouveau\b|\d+\s*mois|CDD|CDI|BAC|Doctorat|Chercheur|Publiée|Ingénieur)', full_text, flags=re.I):
            seg = seg.strip()
            if (len(seg) > 8 and seg.lower() != title_lower and
                    not re.match(r'^[A-Z\s\-]+$', seg) and  # skip all-caps city tokens
                    not any(w in seg.lower() for w in ["heure","jour","mois","h/f","f/h"])):
                institution = seg[:80]
                break

        items.append({
            "title": title,
            "institution": institution or "CNRS",
            "location": location or "France",
            "url": job_url,
            "source": "cnrs",
            "deadline": None,  # CNRS listing page doesn't show deadlines
            "description": full_text[:400],
            "salary": "",
            "domains": auto_tag_domains(title + " " + full_text),
            "positionType": pos_type,
            "alreadyAdded": job_url in existing,
        })
    return feed_title, items

# ── Job factory ───────────────────────────────────────────────────────────────
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PostdocTracker/1.0)"}

def make_job(**kw) -> dict:
    text = (kw.get("title","") + " " + kw.get("description",""))
    return {
        "id": str(uuid.uuid4()),
        "title": kw.get("title",""),
        "institution": kw.get("institution",""),
        "location": kw.get("location",""),
        "url": kw.get("url",""),
        "source": kw.get("source","manual"),
        "domains": kw.get("domains") or auto_tag_domains(text),
        "positionType": kw.get("positionType") or detect_position_type(text, kw.get("url","")),
        "deadline": kw.get("deadline") or None,
        "salary": kw.get("salary",""),
        "description": kw.get("description",""),
        "addedAt": datetime.now(timezone.utc).isoformat(),
        "affinity": kw.get("affinity",0),
        "notes": kw.get("notes",""),
        "applied": False,
        "appliedAt": None,
    }

# ── Static files ──────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(str(PUBLIC), "index.html")

# ── API: List / filter jobs ───────────────────────────────────────────────────
@app.route("/api/jobs", methods=["GET"])
def get_jobs():
    db = read_db()
    jobs = list(db["jobs"])

    search       = request.args.get("search","").lower()
    location     = request.args.get("location","").lower()
    domain       = request.args.get("domain","all")
    sort_by      = request.args.get("sort","deadline")
    hide_applied = request.args.get("hideApplied","false") == "true"
    pos_type     = request.args.get("positionType","all")  # all | postdoc | phd | other

    if search:
        jobs = [j for j in jobs if search in (j["title"]+" "+j["institution"]+" "+j["location"]+" "+j.get("notes","")).lower()]
    if location:
        jobs = [j for j in jobs if location in j["location"].lower()]
    if domain != "all":
        jobs = [j for j in jobs if domain in j.get("domains", [])]
    if hide_applied:
        jobs = [j for j in jobs if not j["applied"]]
    if pos_type != "all":
        jobs = [j for j in jobs if j.get("positionType","other") == pos_type]

    far = "9999-12-31"
    if sort_by == "deadline":
        jobs.sort(key=lambda j: j.get("deadline") or far)
    elif sort_by == "affinity":
        jobs.sort(key=lambda j: j.get("affinity",0), reverse=True)
    elif sort_by == "added":
        jobs.sort(key=lambda j: j.get("addedAt",""), reverse=True)

    return jsonify({"jobs": jobs, "lastUpdated": db["lastUpdated"]})

# ── API: Add job ──────────────────────────────────────────────────────────────
@app.route("/api/jobs", methods=["POST"])
def add_job():
    db = read_db()
    job = make_job(**request.get_json(force=True))
    db["jobs"].append(job)
    write_db(db)
    return jsonify(job), 201

# ── API: Bulk add ─────────────────────────────────────────────────────────────
@app.route("/api/jobs/bulk", methods=["POST"])
def bulk_add():
    body = request.get_json(force=True)
    db = read_db()
    existing = {j["url"] for j in db["jobs"] if j["url"]}
    added = []
    for item in body.get("jobs", []):
        if item.get("url") and item["url"] in existing:
            continue
        job = make_job(**item)
        db["jobs"].append(job)
        if job["url"]:
            existing.add(job["url"])
        added.append(job)
    write_db(db)
    return jsonify({"added": len(added), "jobs": added})

# ── API: Bulk delete ──────────────────────────────────────────────────────────
@app.route("/api/jobs/bulk-delete", methods=["POST"])
def bulk_delete():
    ids = set(request.get_json(force=True).get("ids", []))
    if not ids:
        return jsonify({"error": "No ids provided"}), 400
    db = read_db()
    before = len(db["jobs"])
    db["jobs"] = [j for j in db["jobs"] if j["id"] not in ids]
    deleted = before - len(db["jobs"])
    write_db(db)
    return jsonify({"deleted": deleted})

# ── API: Update job ───────────────────────────────────────────────────────────
ALLOWED_PATCH = {"title","institution","location","url","domains","positionType","deadline",
                 "salary","description","affinity","notes","applied","appliedAt"}

@app.route("/api/jobs/<job_id>", methods=["PATCH"])
def update_job(job_id):
    db = read_db()
    job = next((j for j in db["jobs"] if j["id"] == job_id), None)
    if not job:
        return jsonify({"error": "Not found"}), 404
    body = request.get_json(force=True)
    for key in ALLOWED_PATCH:
        if key in body:
            job[key] = body[key]
    if body.get("applied") and not job.get("appliedAt"):
        job["appliedAt"] = datetime.now(timezone.utc).isoformat()
    write_db(db)
    return jsonify(job)

# ── API: Delete job ───────────────────────────────────────────────────────────
@app.route("/api/jobs/<job_id>", methods=["DELETE"])
def delete_job(job_id):
    db = read_db()
    before = len(db["jobs"])
    db["jobs"] = [j for j in db["jobs"] if j["id"] != job_id]
    if len(db["jobs"]) == before:
        return jsonify({"error": "Not found"}), 404
    write_db(db)
    return jsonify({"ok": True})

# ── API: Fetch feed ───────────────────────────────────────────────────────────
@app.route("/api/fetch/feed", methods=["POST"])
def fetch_feed():
    body       = request.get_json(force=True)
    source     = body.get("source", "")
    custom_url = body.get("customUrl", "").strip()
    keywords   = body.get("keywords", "").strip()
    location   = body.get("location", "").strip()

    if custom_url:
        feed_url  = custom_url
        feed_cfg  = {}
        feed_type = "rss"
    else:
        feed_cfg = FEEDS.get(source, {})
        if not feed_cfg:
            return jsonify({"error": "Unknown source"}), 400
        feed_type = feed_cfg["type"]
        s = CONFIG.get("search", {})
        kw  = keywords  or s.get("default_keywords", "")
        loc = location  or s.get("default_location", "")
        feed_url  = build_feed_url(source, kw, loc)

    try:
        if feed_type == "inria":
            feed_title, items = scrape_inria(feed_url, source)
            # INRIA ignores URL params server-side — filter both keyword and location post-scrape
            if keywords:
                kw_lower = keywords.lower()
                items = [i for i in items if kw_lower in (i["title"] + " " + i.get("description","")).lower()]
            if location:
                loc_lower = location.lower()
                items = [i for i in items if loc_lower in (i["location"] + " " + i.get("description","")).lower()]
        elif feed_type == "cnrs":
            feed_cfg["id"] = source
            feed_title, items = scrape_cnrs(feed_cfg)
            # CNRS has no URL search params — filter both keyword and location post-scrape
            if keywords:
                kw_lower = keywords.lower()
                items = [i for i in items if kw_lower in (i["title"] + " " + i["description"]).lower()]
            if location:
                loc_lower = location.lower()
                items = [i for i in items if loc_lower in (i["location"] + " " + i.get("description","")).lower()]
        elif feed_type == "linkedin":
            feed_cfg["id"] = source
            feed_cfg["url"] = feed_url
            feed_title, items = scrape_linkedin(feed_cfg)
        elif feed_type == "wtj":
            feed_cfg["id"] = source
            feed_cfg["url"] = feed_url
            feed_title, items = scrape_wtj(feed_cfg)
        else:  # rss (euraxess + custom)
            rss_headers = {
                **_BR_HEADERS,
                "Accept": "application/rss+xml, application/xml, text/xml, */*;q=0.8",
            }
            resp = requests.get(feed_url, headers=rss_headers, timeout=12)
            resp.raise_for_status()
            feed_title, items = parse_rss(resp.text)
            existing = {j["url"] for j in read_db()["jobs"] if j["url"]}
            for item in items:
                item["alreadyAdded"] = item["url"] in existing
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    if FILTER_OUT:
        def _blocked(item):
            haystack = (item.get("title","") + " " + item.get("description","")).lower()
            return any(kw in haystack for kw in FILTER_OUT)
        items = [i for i in items if not _blocked(i)]

    return jsonify({"items": items[:80], "feedTitle": feed_title})

# ── API: Fetch single URL ─────────────────────────────────────────────────────
@app.route("/api/fetch/url", methods=["POST"])
def fetch_url():
    body = request.get_json(force=True)
    url = body.get("url","").strip()
    if not url:
        return jsonify({"error": "url required"}), 400
    try:
        resp = requests.get(url, headers=_BR_HEADERS, timeout=12)
        resp.raise_for_status()
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    job = extract_job_from_html(resp.text, url)
    job["url"] = url
    job["domains"] = auto_tag_domains((job.get("title","")) + " " + (job.get("description","")))
    return jsonify(job)

# ── API: List feeds ───────────────────────────────────────────────────────────
@app.route("/api/feeds", methods=["GET"])
def list_feeds():
    return jsonify([
        {"id": k, "name": v["name"], "supportsLocation": v.get("supports_location", True)}
        for k, v in FEEDS.items()
    ])

# ── API: App config (search defaults etc.) ────────────────────────────────────
@app.route("/api/config", methods=["GET"])
def get_config():
    s = CONFIG.get("search", {})
    return jsonify({
        "title":                APP_TITLE,
        "defaultKeywords":      s.get("default_keywords", ""),
        "defaultLocation":      s.get("default_location", ""),
        "linkedinTimeWindow":   s.get("linkedin_time_window", "r2592000"),
        "domains":              list(DOMAIN_RULES.keys()),
    })

# ── Style vars CSS (generated from config.yaml) ───────────────────────────────
@app.route("/api/style-vars.css", methods=["GET"])
def style_vars():
    st = CONFIG.get("style", {})
    font = st.get("font", "Crimson Pro").replace(" ", "+")
    css = f"""@import url('https://fonts.bunny.net/css?family={font.lower().replace("+", "-")}:400,600');

:root {{
  --font:      '{st.get("font", "Crimson Pro")}', serif;
  --bg:        {st.get("bg",       "#F5F0E7")};
  --surface:   {st.get("surface",  "#FFFFFF")};
  --surface2:  {st.get("surface2", "#F5F0E7")};
  --border:    {st.get("border",   "#D4C9B5")};
  --accent:    {st.get("accent",   "#607D3B")};
  --secondary: {st.get("secondary","#8B5E3C")};
  --text:      {st.get("text",     "#2E2E2E")};
  --muted:     {st.get("muted",    "#8A7D6B")};
  --green:     {st.get("green",    "#607D3B")};
  --red:       {st.get("red",      "#c0392b")};
  --orange:    {st.get("orange",   "#d35400")};
  --yellow:    {st.get("yellow",   "#c9a227")};
  --shadow:    {st.get("shadow",   "0 2px 12px rgba(0,0,0,.08)")};
  --radius:    10px;
}}
"""
    return Response(css, mimetype="text/css")

# ── Run ───────────────────────────────────────────────────────────────────────
def run(port: int = PORT, open_browser: bool = True, force_http: bool = False):
    cert = USER_DIR / "cert.crt"
    key  = USER_DIR / "cert.key"
    ssl_context = (str(cert), str(key)) if (cert.exists() and key.exists() and not force_http) else None
    scheme = "https" if ssl_context else "http"
    url = f"{scheme}://localhost:{port}"
    print(f"{APP_TITLE} → {url}", flush=True)
    if open_browser:
        import threading, webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, debug=False,
            **({"ssl_context": ssl_context} if ssl_context else {}))
