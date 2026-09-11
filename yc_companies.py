#!/usr/bin/env python3
"""
Company discovery from Y Combinator's directory.

YC is the largest single source of the companies this tracker is aimed at:
small, private, NYC/SF, currently hiring. The public datasets the original list
came from cover it badly - only ~8% of YC companies matching those criteria were
being watched before this was added.

It also carries the one field no other source does: `team_size`, a real integer
headcount. Everywhere else this tool has to infer company size from how many
roles are open at once, which is a weak proxy (a 2,000-person firm in a hiring
freeze posts three jobs). Here the size filter can be exact.

Data comes from yc-oss.github.io/api, a community-maintained mirror of YC's own
directory that refreshes daily. No API key, stdlib only.

The hard part is that YC publishes a company's website, not its ATS board, so
each one has to be resolved. Guessing a slug from the company name is not safe
on its own - "Handle", "Glimpse" and "Sola" are all real YC companies whose
names collide with unrelated boards - so every candidate is verified before it
is accepted. See resolve_ats.
"""
import json
import re
import urllib.error
import urllib.request

YC_API = "https://yc-oss.github.io/api/companies/all.json"

ATS_TEMPLATES = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    "rippling": "https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs",
}

ATS_LINK_RE = re.compile(
    r"https?://(?:www\.)?("
    r"job-boards(?:\.eu)?\.greenhouse\.io|boards(?:\.eu)?\.greenhouse\.io|"
    r"jobs\.lever\.co|jobs\.ashbyhq\.com|apply\.workable\.com|ats\.rippling\.com"
    r")/([A-Za-z0-9._%-]+)", re.I)

HOST_PLATFORM = {
    "job-boards.greenhouse.io": "greenhouse", "boards.greenhouse.io": "greenhouse",
    "job-boards.eu.greenhouse.io": "greenhouse", "boards.eu.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever", "jobs.ashbyhq.com": "ashby",
    "apply.workable.com": "workable", "ats.rippling.com": "rippling",
}

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"

NYC_SF = ["new york", "brooklyn", "nyc", "manhattan", "san francisco", "bay area",
          "oakland", "palo alto", "mountain view", "menlo park", "berkeley"]


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def fetch_json(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def fetch_text(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(600000).decode("utf-8", errors="replace")


def load_yc(max_team_size=100, metros=None):
    """Active, currently-hiring YC companies in the target metros and size band."""
    metros = metros or NYC_SF
    out = []
    for c in fetch_json(YC_API):
        if c.get("status") != "Active" or not c.get("isHiring"):
            continue
        loc = (c.get("all_locations") or "").lower()
        if not any(m in loc for m in metros):
            continue
        ts = c.get("team_size") or 0
        if not ts or ts > max_team_size:
            continue
        out.append(c)
    return out


def board_jobs(platform, slug):
    """Return the board's postings, or None if it doesn't resolve."""
    try:
        data = fetch_json(ATS_TEMPLATES[platform].format(slug=slug), timeout=12)
    except Exception:  # noqa: BLE001
        return None
    jobs = data if isinstance(data, list) else (data.get("jobs") or [])
    return jobs or None


def _domain_label(website):
    m = re.search(r"https?://(?:www\.)?([^./]+)", website or "")
    return m.group(1).lower() if m else ""


def ats_links_on_site(website):
    """Look for an ATS link on the company's own site.

    This is the authoritative path: a board linked from the company's careers
    page is theirs by definition, no name-matching guesswork involved.
    """
    if not website:
        return []
    base = website.rstrip("/")
    found = []
    for path in ("/careers", "/jobs", "", "/company/careers", "/about/careers"):
        try:
            html = fetch_text(base + path)
        except Exception:  # noqa: BLE001
            continue
        for host, slug in ATS_LINK_RE.findall(html):
            platform = HOST_PLATFORM.get(host.lower())
            if platform and slug.lower() not in ("jobs", "embed", "search"):
                found.append((platform, slug))
        if found:
            break
    return list(dict.fromkeys(found))


def resolve_ats(company):
    """Resolve one YC company to a verified ATS board, or None.

    Two paths, in order of trustworthiness:

    1. An ATS link on the company's own website. Authoritative - no guessing.
    2. A slug guessed from the domain or name, but only if it can be *verified*:
       Greenhouse returns `company_name` on each posting, which is checked
       against the YC name; for the other platforms, which expose no identity
       field, a guess is accepted only when the slug matches the company's own
       domain label. A bare name-derived guess is never accepted on those,
       because "Handle" and "Glimpse" resolve to boards belonging to entirely
       different companies.
    """
    name, website = company["name"], company.get("website") or ""
    for platform, slug in ats_links_on_site(website):
        if board_jobs(platform, slug):
            return platform, slug, "site-link"

    domain = _domain_label(website)
    n = norm(name)
    for slug, kind in ([(domain, "domain")] if domain else []) + [(n, "name"),
                       (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"), "name")]:
        if not slug or len(slug) < 3:
            continue
        for platform in ATS_TEMPLATES:
            jobs = board_jobs(platform, slug)
            if not jobs:
                continue
            if platform == "greenhouse":
                got = norm(jobs[0].get("company_name"))
                if got and (got == n or got.startswith(n) or n.startswith(got)):
                    return platform, slug, f"{kind}+company_name"
                continue
            if kind == "domain":
                return platform, slug, "domain-match"
    return None
