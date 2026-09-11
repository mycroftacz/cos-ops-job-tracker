#!/usr/bin/env python3
"""
Company discovery from VC portfolio job boards.

These boards aggregate openings across a fund's portfolio - exactly the
sub-100-person startups this tracker is aimed at, and precisely the companies
the original SimplifyJobs datasets miss (those only list companies that post
software internships).

This does NOT poll the VC boards for jobs. It harvests *companies* from them
and resolves each to its own ATS board, so the existing checker.py engine does
the actual watching. A VC board is a directory that goes stale; a company's own
Greenhouse/Lever/Ashby feed is the source of truth.

Boards running Getro embed their full result set in the page's __NEXT_DATA__
blob, including each posting's real apply URL - which is usually the company's
ATS link, the same shape refresh_companies.py already parses. No API key, no
scraping of rendered HTML, stdlib only.
"""
import json
import re
import urllib.parse
import urllib.request
from urllib.parse import urlparse

# Verified Getro-powered boards. The first nine were found by hand; the rest come
# from probing every fund in an OpenVC export of NYC/California investors for a
# job board at jobs.<domain> or careers.<domain>. Only ~9% of funds had one -
# solo angels, family offices and small seed funds generally don't - but the ones
# that do skew early-stage and local, which is exactly the target profile.
#
# Others (First Round, Sequoia, Bessemer, Lightspeed) render client-side with no
# embedded payload, and a16z runs a bespoke Next.js app - both would need their
# own parser, so they're out for now.
GETRO_BOARDS = [
    "careers.blumbergcapital.com",
    "careers.canaan.com",
    "careers.greymattercapital.com",
    "careers.h-l.vc",
    "careers.playground.global",
    "careers.quiet.com",
    "careers.sierraventures.com",
    "jobs.645ventures.com",
    "jobs.alphapartners.com",
    "jobs.bbgventures.com",
    "jobs.blackjays.vc",
    "jobs.bonfirevc.com",
    "jobs.breakout.vc",
    "jobs.btv.vc",
    "jobs.cervinventures.com",
    "jobs.correlationvc.com",
    "jobs.dcvc.com",
    "jobs.energyimpactpartners.com",
    "jobs.framework.ventures",
    "jobs.g2vp.com",
    "jobs.greycroft.com",
    "jobs.gritventures.com",
    "jobs.group11.vc",
    "jobs.headline.com",
    "jobs.hivemind.capital",
    "jobs.joulevc.com",
    "jobs.moneta.vc",
    "jobs.nfx.com",
    "jobs.ngpcap.com",
    "jobs.outsidersfund.com",
    "jobs.praxislabs.org",
    "jobs.reachcapital.com",
    "jobs.riverparkvc.com",
    "jobs.sapphireventures.com",
    "jobs.scalevp.com",
    "jobs.signalfire.com",
    "jobs.sogalventures.com",
    "jobs.spacecapital.com",
    "jobs.thehelm.co",
    "jobs.uluventures.com",
    "jobs.upfront.com",
    "jobs.vmgpartners.com",
    "jobs.westlygroup.com",
]

# Getro reports company size as a bucket index, not a headcount. Observed
# values run 1-6, ascending with size. We keep everything at or below 4 -
# deliberately loose, because this only decides whether a company is worth
# *watching*; checker.py still applies max_open_roles per run.
MAX_HEADCOUNT_BUCKET = 4

# A handful of broad queries rather than every keyword - these boards do fuzzy
# matching, so a few well-chosen terms surface most of the relevant companies
# without 18 requests per board.
QUERIES = [
    "chief of staff", "business operations", "revenue operations",
    "special projects", "generalist", "strategy and operations",
]

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"


def fetch_getro(host, query):
    """Return the job list embedded in a Getro board's Next.js payload."""
    url = f"https://{host}/jobs?q={urllib.parse.quote(query)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return []
    data = json.loads(m.group(1))
    return data["props"]["pageProps"]["initialState"]["jobs"]["found"]


def harvest(boards=None, queries=None, verbose=True):
    """Collect {company_name: apply_url} across every board and query."""
    boards = boards or GETRO_BOARDS
    queries = queries or QUERIES
    found = {}
    for host in boards:
        for q in queries:
            try:
                jobs = fetch_getro(host, q)
            except Exception as e:  # noqa: BLE001 - one dead board mustn't stop the rest
                if verbose:
                    print(f"  skip {host} '{q}': {e}")
                continue
            for j in jobs:
                org = j.get("organization") or {}
                name = (org.get("name") or "").strip()
                url = j.get("url") or ""
                if not name or not url:
                    continue
                bucket = org.get("headCount")
                if bucket is not None and bucket > MAX_HEADCOUNT_BUCKET:
                    continue
                found.setdefault(name, {"url": url, "stage": org.get("stage"),
                                        "bucket": bucket, "board": host})
        if verbose:
            print(f"  {host}: running total {len(found)} companies")
    return found


if __name__ == "__main__":

    got = harvest()
    print(f"\n{len(got)} distinct companies harvested")
    for n, meta in sorted(got.items())[:25]:
        print(f"  {n[:32]:34} {urlparse(meta['url']).netloc:28} stage={meta['stage']}")
