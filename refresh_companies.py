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
}


def parse_ats(url):
    try:
        netloc = urlparse(url).netloc
        path = urlparse(url).path.strip("/").split("/")
    except Exception:
        return None
    platform = ATS_HOSTS.get(netloc)
    if platform and path and path[0]:
        return platform, path[0]
    return None


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (job-tracker-refresh)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def main():
    with open(COMPANIES_PATH) as f:
        config = json.load(f)

    existing_names = {c["name"].lower() for c in config["companies"]}
    added = []

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
            parsed = parse_ats(job_url)
            if not parsed:
                continue
            platform, slug = parsed
            config["companies"].append({"name": name, "platform": platform, "slug": slug, "source": "bulk_refresh"})
            existing_names.add(name.lower())
            added.append(name)

    config["companies"].sort(key=lambda c: c["name"].lower())

    with open(COMPANIES_PATH, "w") as f:
        json.dump(config, f, indent=1)

    print(f"Added {len(added)} new companies.")
    for n in added[:50]:
        print(f"  + {n}")
    if len(added) > 50:
        print(f"  ...and {len(added) - 50} more")


if __name__ == "__main__":
    main()
