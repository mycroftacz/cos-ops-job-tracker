#!/usr/bin/env python3
"""
Periodic company-list refresh.

Re-downloads the same public tech-recruiting datasets used to build the
original list, extracts any company -> ATS-slug mapping not already present
in data/companies.json, and appends the new ones. Existing entries (and
their tracking history in data/state.json, which this script never touches)
are left alone - this only ever adds, never removes or reorders.

Run manually (Actions tab -> "Refresh company list" -> Run workflow) or let
the scheduled workflow do it twice a month.
"""
import json
import os
import urllib.request
from urllib.parse import urlparse

from vc_boards import harvest as harvest_vc_boards
from yc_companies import load_yc, resolve_ats

ROOT = os.path.dirname(os.path.abspath(__file__))
COMPANIES_PATH = os.path.join(ROOT, "data", "companies.json")

# Same community-maintained recruiting datasets used for the initial build.
# These get updated continuously by their own maintainers, so re-fetching
# the same URL later naturally picks up newly-added companies. Add more
# entries here any time to pull from additional sources.
DATASET_URLS = [
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/vanshb03/Summer2026-Internships/dev/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/sharunkumar/Summer-Internships/dev/.github/scripts/listings.json",
]

# Same exclusion list as the original build - obvious large/public/late-stage
# names that shouldn't be re-added even if they reappear in a refreshed dataset.
# Extend this any time you spot noise getting pulled in.
EXCLUDE_NAMES = {
    "Stripe", "Airbnb", "DoorDash", "Robinhood", "Snowflake", "Databricks", "Coinbase",
    "Palantir", "Palantir Technologies", "Palantir Technologies Inc", "Reddit", "Pinterest",
    "Affirm", "Instacart", "Duolingo", "Roblox", "Unity", "Cloudflare", "HubSpot", "Twilio",
    "Okta", "Datadog", "MongoDB", "Confluent", "Gitlab", "GitLab", "Elastic", "Asana",
    "Dropbox", "Squarespace", "Wix", "Peloton", "Chime", "Plaid", "Brex", "Ramp", "Toast",
    "Samsara", "Rippling", "Deel", "Notion", "Figma", "Canva", "Anthropic", "OpenAI", "xAI",
    "Perplexity", "Scale AI", "Discord", "Spotify", "Netflix", "Nvidia", "NVIDIA", "Tesla",
    "SpaceX", "ByteDance", "TikTok", "10x Genomics", "AXS", "1Password", "Anduril",
    "Anduril Industries", "Applied Intuition", "CLEAR", "Assured Guaranty",
    "Atlas Energy Solutions", "BlueRock Therapeutics", "Cambridge Mobile Telematics",
    "Chicago Trading Company", "Businessolver", "Palo Alto Networks", "ServiceNow",
    "Salesforce", "Adobe", "Oracle", "IBM", "Intel", "Cisco", "VMware", "Workday", "SAP",
    "Shopify", "Block", "Square", "PayPal", "Intuit", "Zoom", "Slack", "Atlassian", "Splunk",
    "Snap", "Lyft", "Uber", "Robinhood Markets", "Coupang", "Grab", "Klarna", "Revolut",
    "Wise", "N26", "Epic Games", "Riot Games", "Electronic Arts", "Activision Blizzard",
    "Take-Two Interactive", "Zillow", "Redfin", "Compass", "Opendoor", "Carvana", "Wayfair",
    "Chewy", "Etsy", "Wayve", "Waymo", "Cruise", "Aurora Innovation", "Rivian",
    "Lucid Motors", "Nikola", "Faraday Future", "Two Sigma", "Citadel", "Jane Street",
    "DE Shaw", "Point72", "Bridgewater Associates", "Renaissance Technologies",
    "Millennium Management", "Susquehanna", "Jump Trading", "Hudson River Trading", "DRW",
    "IMC Trading", "Optiver", "Akuna Capital", "Goldman Sachs", "Morgan Stanley",
    "JPMorgan Chase", "JPMorgan", "Bank of America", "Citigroup", "Wells Fargo",
    "Capital One", "American Express", "Visa", "Mastercard", "Fidelity Investments",
    "BlackRock", "Vanguard", "State Street", "T. Rowe Price", "Charles Schwab", "Nasdaq",
    "DeepMind", "Deliveroo", "Google", "Microsoft", "Meta", "Amazon", "Apple", "Alphabet",
    "X", "Twitter", "LinkedIn",
}

ATS_HOSTS = {
    "job-boards.greenhouse.io": "greenhouse",
    "boards.greenhouse.io": "greenhouse",
    "job-boards.eu.greenhouse.io": "greenhouse",
    "boards.eu.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "jobs.ashbyhq.com": "ashby",
    "apply.workable.com": "workable",
    "ats.rippling.com": "rippling",
}


API_TEMPLATES = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    "workable": "https://www.workable.com/api/accounts/{slug}?details=true",
    "rippling": "https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs",
}


def parse_ats(url):
    try:
        netloc = urlparse(url).netloc
        path = urlparse(url).path.strip("/").split("/")
    except Exception:
        return None
    platform = ATS_HOSTS.get(netloc)
    if platform and path and path[0]:
        # Keep the path segment exactly as it appears, percent-encoding and
        # all. Some Ashby boards genuinely have spaces in their slug
        # ("Superhuman%20Platform%20Inc"); decoding it produces a slug that
        # raises InvalidURL when checker.py interpolates it into a request.
        return platform, path[0]
    return None


def slug_is_live(platform, slug):
    """Confirm a slug actually resolves to a board before we commit to watching it.

    Harvested links go stale in specific ways: an acquired company's board
    redirects to the acquirer, and some apply URLs carry a job path rather than
    a board slug. Adding an unverified slug means a company that silently 404s
    on every run forever, so each one is checked once here instead.
    """
    url = API_TEMPLATES[platform].format(slug=slug)
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return False
    jobs = data if isinstance(data, list) else (data.get("jobs") or [])
    return bool(jobs)


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (job-tracker-refresh)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def main():
    with open(COMPANIES_PATH) as f:
        config = json.load(f)

    existing_names = {c["name"].lower() for c in config["companies"]}
    # Also dedupe on the board itself. Acquired companies' apply links point at
    # the acquirer's board (Codecov -> sentry, Athelas -> Commure), so matching
    # on name alone would poll the same board several times under different
    # names and report every match once per alias.
    existing_boards = {(c["platform"], c["slug"]) for c in config["companies"]}
    # Companies pruned for being too large (see max_open_roles in
    # data/companies.json). Without this, every refresh would cheerfully
    # re-add Accenture and Alo Yoga, and the prune would undo itself twice
    # a month. checker.py also enforces the size limit at runtime, so this
    # is belt-and-braces - it just avoids the wasted fetches.
    excluded_names = {n.lower() for n in config.get("excluded_companies", [])}
    added = []
    skipped_excluded = 0

    for url in DATASET_URLS:
        try:
            data = fetch_json(url)
        except Exception as e:  # noqa: BLE001
            print(f"skip {url}: {e}")
            continue
        for entry in data:
            name = (entry.get("company_name") or "").strip()
            job_url = entry.get("url", "")
            if not name or not job_url:
                continue
            if name in EXCLUDE_NAMES or name.lower() in existing_names:
                continue
            if name.lower() in excluded_names:
                skipped_excluded += 1
                continue
            parsed = parse_ats(job_url)
            if not parsed:
                continue
            platform, slug = parsed
            if (platform, slug) in existing_boards:
                continue
            config["companies"].append({"name": name, "platform": platform, "slug": slug, "source": "bulk_refresh"})
            existing_names.add(name.lower())
            existing_boards.add((platform, slug))
            added.append(name)

    # --- second source: VC portfolio job boards ---------------------------
    # The SimplifyJobs datasets only contain companies that post software
    # internships, which is why a company like Verve - with an open NYC Chief
    # of Staff role on its own Greenhouse board - was never being watched.
    # VC portfolio boards cover exactly the gap: small, private, well-funded.
    vc_added = 0
    vc_rejected = 0
    try:
        harvested = harvest_vc_boards(verbose=False)
    except Exception as e:  # noqa: BLE001 - never let this break the primary refresh
        print(f"VC board harvest failed, continuing with dataset sources only: {e}")
        harvested = {}

    for name, meta in sorted(harvested.items()):
        if name in EXCLUDE_NAMES or name.lower() in existing_names:
            continue
        if name.lower() in excluded_names:
            skipped_excluded += 1
            continue
        parsed = parse_ats(meta.get("url", ""))
        if not parsed:
            continue
        platform, slug = parsed
        if (platform, slug) in existing_boards:
            continue
        if not slug_is_live(platform, slug):
            vc_rejected += 1
            continue
        config["companies"].append({
            "name": name, "platform": platform, "slug": slug,
            "source": "vc_board", "stage": meta.get("stage"),
        })
        existing_names.add(name.lower())
        existing_boards.add((platform, slug))
        added.append(name)
        vc_added += 1

    # --- third source: Y Combinator ---------------------------------------
    # The largest single pool of the companies this tracker targets, and the
    # only source with a real integer headcount, so the size filter here is
    # exact rather than inferred from open-req count.
    yc_added = 0
    yc_unresolved = 0
    try:
        yc = load_yc(max_team_size=config.get("max_team_size", 100))
    except Exception as e:  # noqa: BLE001
        print(f"YC fetch failed, continuing without it: {e}")
        yc = []

    for c in yc:
        name = c["name"].strip()
        if not name or name in EXCLUDE_NAMES or name.lower() in existing_names:
            continue
        if name.lower() in excluded_names:
            skipped_excluded += 1
            continue
        resolved = resolve_ats(c)
        if not resolved:
            yc_unresolved += 1
            continue
        platform, slug, via = resolved
        if (platform, slug) in existing_boards:
            continue
        config["companies"].append({
            "name": name, "platform": platform, "slug": slug,
            "source": "yc", "team_size": c.get("team_size"),
            "batch": c.get("batch"), "verified_via": via,
        })
        existing_names.add(name.lower())
        existing_boards.add((platform, slug))
        added.append(name)
        yc_added += 1

    config["companies"].sort(key=lambda c: c["name"].lower())

    with open(COMPANIES_PATH, "w") as f:
        json.dump(config, f, indent=1)

    print(f"Added {len(added)} new companies "
          f"({vc_added} from VC portfolio boards, {vc_rejected} VC candidates "
          f"dropped as dead/unverifiable slugs, "
          f"{yc_added} from Y Combinator ({yc_unresolved} YC companies had no "
          f"resolvable ATS board), "
          f"{skipped_excluded} skipped as previously-pruned large employers).")
    for n in added[:50]:
        print(f"  + {n}")
    if len(added) > 50:
        print(f"  ...and {len(added) - 50} more")


if __name__ == "__main__":
    main()
