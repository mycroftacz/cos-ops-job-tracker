#!/usr/bin/env python3
"""
Chief of Staff / Ops job tracker - checker script.

Polls every company's ATS job board directly (Greenhouse, Lever, Ashby, Workable),
diffs against previously-seen job ids, and writes any brand-new postings whose
title matches the configured keywords to results/latest.json. Designed to run
on a cron (hourly, via GitHub Actions) - it's plain concurrent HTTP + JSON
parsing, no AI model in the loop. Measured throughput is roughly 200 companies
per 35s at MAX_WORKERS=40, i.e. ~6 minutes for the full ~2,180-company list.

State (data/state.json) is committed back to the repo each run so nothing gets
re-reported. results/latest.json always reflects the most recent run's findings
(read by anything downstream that wants to relay a notification).
"""
import html as html_lib
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from functools import lru_cache

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
RESULTS_DIR = os.path.join(ROOT, "results")
COMPANIES_PATH = os.path.join(DATA_DIR, "companies.json")
STATE_PATH = os.path.join(DATA_DIR, "state.json")
LATEST_PATH = os.path.join(RESULTS_DIR, "latest.json")
HISTORY_PATH = os.path.join(RESULTS_DIR, "history.jsonl")

MAX_WORKERS = 40
TIMEOUT = 15
USER_AGENT = "Mozilla/5.0 (compatible; job-tracker-bot/1.0; +https://github.com/)"


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r") as f:
        return json.load(f)


def fetch(url, as_json=True):
    accept = "application/json" if as_json else "text/html"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return json.loads(body) if as_json else body


def fetch_with_retry(url, retries=1, as_json=True):
    last_err = None
    for attempt in range(retries + 1):
        try:
            return fetch(url, as_json=as_json), None
        except Exception as e:  # noqa: BLE001 - we want to catch and report, not crash the run
            last_err = e
            time.sleep(0.5)
    return None, str(last_err)


def parse_greenhouse(data, slug):
    out = []
    for j in data.get("jobs", []) or []:
        jid = str(j.get("id", ""))
        title = j.get("title", "") or ""
        url = j.get("absolute_url") or f"https://job-boards.greenhouse.io/{slug}/jobs/{jid}"
        loc = ((j.get("location") or {}).get("name") or "") if isinstance(j.get("location"), dict) else ""
        if jid:
            out.append((jid, title, url, loc))
    return out


def parse_lever(data, slug):
    out = []
    items = data if isinstance(data, list) else data.get("data", []) or []
    for j in items or []:
        jid = str(j.get("id", ""))
        title = j.get("text", "") or j.get("title", "") or ""
        url = j.get("hostedUrl") or f"https://jobs.lever.co/{slug}/{jid}"
        cats = j.get("categories") or {}
        parts = [cats.get("location") or ""] + list(cats.get("allLocations") or [])
        parts.append(j.get("workplaceType") or "")
        loc = ", ".join(p for p in parts if p)
        if jid:
            out.append((jid, title, url, loc))
    return out


def parse_ashby(data, slug):
    out = []
    jobs = data.get("jobs") if isinstance(data, dict) else data
    for j in jobs or []:
        jid = str(j.get("id", ""))
        title = j.get("title", "") or ""
        url = j.get("jobUrl") or j.get("applyUrl") or f"https://jobs.ashbyhq.com/{slug}/{jid}"
        parts = [j.get("location") or ""] + [
            (x.get("location") if isinstance(x, dict) else str(x)) or ""
            for x in (j.get("secondaryLocations") or [])
        ]
        if j.get("isRemote"):
            parts.append("Remote")
        loc = ", ".join(p for p in parts if p)
        if jid:
            out.append((jid, title, url, loc))
    return out


def parse_workable(data, slug):
    out = []
    jobs = data.get("jobs") if isinstance(data, dict) else data
    for j in jobs or []:
        jid = str(j.get("shortcode") or j.get("id") or "")
        title = j.get("title", "") or ""
        url = j.get("url") or f"https://apply.workable.com/{slug}/j/{jid}/"
        parts = [j.get("city") or "", j.get("state") or "", j.get("country") or ""]
        if j.get("telecommuting"):
            parts.append("Remote")
        loc = ", ".join(p for p in parts if p)
        if jid:
            out.append((jid, title, url, loc))
    return out


def parse_rippling(data, slug):
    """Rippling's own board pages sit behind a Cloudflare challenge and render
    client-side, so they can't be fetched directly. This public board API
    returns the same postings as plain JSON with no challenge."""
    out = []
    jobs = data if isinstance(data, list) else (data.get("jobs") or [])
    for j in jobs or []:
        jid = str(j.get("uuid") or j.get("id") or "")
        title = j.get("name") or j.get("title") or ""
        url = j.get("url") or f"https://ats.rippling.com/{slug}/jobs/{jid}"
        wl = j.get("workLocation") or {}
        loc = (wl.get("label") or wl.get("id") or "") if isinstance(wl, dict) else str(wl)
        if jid:
            out.append((jid, title, url, loc))
    return out


# Career Group Companies is a staffing agency, not an ATS: one Webflow page
# lists every open role across its divisions. Unlike the ATS feeds, the hiring
# employer is deliberately anonymous ("our client, a luxury home organization
# company"), so a match here tells you a role exists and who to talk to, not
# where you'd be working. All postings are in the initial HTML - the infinite
# scroll is client-side only - so one request gets the whole list.
_CG_ITEM_RE = re.compile(
    r'href="/job-posting/(?P<id>\d+)".*?'
    r'fs-cmsfilter-field="title"[^>]*>(?P<title>[^<]+)<.*?'
    r'fs-cmsfilter-field="division"[^>]*>(?P<division>[^<]*)<.*?'
    r'fs-cmsfilter-field="location"[^>]*>(?P<location>[^<]*)<',
    re.S)


def parse_careergroup(html, slug):
    out = []
    for m in _CG_ITEM_RE.finditer(html if isinstance(html, str) else ""):
        jid = m.group("id")
        title = m.group("title").strip()
        div = m.group("division").strip()
        loc = m.group("location").strip()
        url = f"https://www.careergroupcompanies.com/job-posting/{jid}"
        if jid and title:
            out.append((jid, f"{title} ({div})" if div else title, url, loc))
    return out


def parse_yc(html, slug):
    """YC's own company page (Work at a Startup).

    Many YC companies never set up a Greenhouse/Ashby/Lever board - their
    careers page is a Notion doc or a custom site - but nearly all of them list
    roles here, because it's YC's default hiring channel. The page embeds its
    data as an Inertia `data-page` JSON attribute with a stable id per posting.

    It's keyed by YC's own company slug, so unlike every other YC resolution
    path there is no identity to verify: this page belongs to that company by
    construction.
    """
    m = re.search(r'data-page="([^"]+)"', html if isinstance(html, str) else "")
    if not m:
        return []
    data = json.loads(html_lib.unescape(m.group(1)))
    out = []
    for j in (data.get("props") or {}).get("jobPostings") or []:
        jid = str(j.get("id") or "")
        title = j.get("title") or ""
        url = j.get("url") or f"/companies/{slug}/jobs"
        if url.startswith("/"):
            url = "https://www.ycombinator.com" + url
        if jid:
            out.append((jid, title, url, j.get("location") or ""))
    return out


PARSERS = {
    "greenhouse": parse_greenhouse,
    "lever": parse_lever,
    "ashby": parse_ashby,
    "workable": parse_workable,
    "rippling": parse_rippling,
    "careergroup": parse_careergroup,
    "yc": parse_yc,
}

# Platforms served as HTML rather than JSON.
HTML_PLATFORMS = {"careergroup", "yc"}


def check_company(company, url_templates):
    name = company["name"]
    platform = company["platform"]
    slug = company["slug"]
    template = url_templates.get(platform)
    if not template:
        return name, platform, slug, [], f"no url template for platform {platform}"
    url = template.format(slug=slug)
    data, err = fetch_with_retry(url, as_json=platform not in HTML_PLATFORMS)
    if err:
        return name, platform, slug, [], err
    parser = PARSERS.get(platform)
    try:
        jobs = parser(data, slug)
    except Exception as e:  # noqa: BLE001
        return name, platform, slug, [], f"parse error: {e}"
    return name, platform, slug, jobs, None


_OPS_RE = re.compile(r"\bops\b")


def normalize_title(text):
    """Lowercase, and expand the standalone abbreviation "ops" to "operations".

    Startups write "Head of Ops", "Strategy & Ops" and "Founding Ops" far more
    often than the spelled-out form, and none of those matched any keyword or
    word group before this. Keywords and excludes go through the same function,
    so "biz ops" and "it ops" keep working. Word boundaries leave "DevOps",
    "MLOps" and "BizOps" untouched.
    """
    return _OPS_RE.sub("operations", (text or "").lower())


@lru_cache(maxsize=16)
def _normalized(terms):
    return tuple(normalize_title(t) for t in terms)


@lru_cache(maxsize=8)
def _exclude_regex(exclude_terms):
    """Compile the exclude list into one word-boundary alternation.

    Word boundaries matter: a plain substring test made "intern" reject
    "Chief of Staff, International" and "Head of Internal Operations", and
    made "co-op" reject "Cooperative Operations Manager" - i.e. it silently
    threw away the exact roles this tracker exists to find. Cached because
    main() calls this once per job title across ~110k titles per run."""
    if not exclude_terms:
        return None
    return re.compile(r"\b(?:" + "|".join(re.escape(t) for t in exclude_terms) + r")\b")


def matches_keywords(title, keywords, keyword_word_groups, exclude_keywords):
    """A title matches if either:
    - it contains one of `keywords` verbatim as a substring (for fixed phrases
      like "chief of staff", where word order doesn't vary in practice), or
    - it contains every word in one of `keyword_word_groups`, in any order
      (for titles like "operations director" vs "director of operations" -
      real postings use both orders, and a plain substring check would only
      ever catch one of them).
    Either way, a title containing any `exclude_keywords` term is rejected
    first - this is what keeps entry-level titles (Coordinator, Assistant,
    Intern) and unrelated operational domains (facilities, warehouse,
    clinical, ...) out of the results. Exclude terms are matched on word
    boundaries, not as bare substrings - see _exclude_regex.
    """
    t = normalize_title(title)
    rx = _exclude_regex(_normalized(tuple(exclude_keywords)))
    if rx is not None and rx.search(t):
        return False
    if any(k in t for k in _normalized(tuple(keywords))):
        return True
    for group in keyword_word_groups:
        if all(normalize_title(word) in t for word in group):
            return True
    return False


@lru_cache(maxsize=8)
def _location_regex(location_terms):
    """Word-boundary alternation over the wanted-location terms.

    Word boundaries matter as much as they do for the title excludes: a bare
    substring test for "ny" matches "Albany, NY" and "Germany", and "sf"
    matches "SFO". Terms are city/metro names ("new york", "san francisco"),
    never bare state codes, for exactly that reason."""
    if not location_terms:
        return None
    return re.compile(r"\b(?:" + "|".join(re.escape(t) for t in location_terms) + r")\b")


_REMOTE_RE = re.compile(r"\b(?:remote|anywhere|distributed|work from home|wfh)\b")

# Words that carry no geographic meaning of their own, so their presence
# alongside "Remote" doesn't mean the posting is tied to somewhere else.
_LOC_NOISE_RE = re.compile(
    r"\b(?:remote|anywhere|distributed|work from home|wfh|hybrid|onsite|on-site|"
    r"in-office|office|hq|headquarters|flexible|optional|preferred|based|or|and|"
    r"us|usa|u\.s\.|united states|canada|north america|global|worldwide|"
    r"full|part|time|any|all|multiple|locations?|various)\b"
)


def is_remote_anywhere(location, location_terms):
    """True only for postings that are remote AND not pinned to some other city.

    "Remote - US" qualifies. "Fresno, Modesto, Bakersfield, Remote" does not:
    it names specific places, none of them the metros we want, so treating it
    as remote would smuggle in a Central Valley role via an SF-based company.
    """
    low = (location or "").lower()
    if not _REMOTE_RE.search(low):
        return False
    rx = _location_regex(tuple(location_terms))
    stripped = _LOC_NOISE_RE.sub(" ", low)
    if rx is not None:
        stripped = rx.sub(" ", stripped)
    # Anything alphabetic still standing is another place name.
    return not re.search(r"[a-z]{2,}", stripped)


# Borough and city names are not unique across the US: there is a Brooklyn in
# Ohio, a Brooklyn Park in Minnesota, an Oakland in New Jersey, and a Queens
# Park in London. When a location names one of our metros AND a region that
# rules it out, the region wins. Deliberately narrow - only regions that
# actually conflict, so a bare "Williamsburg, Brooklyn" still matches.
_CONFLICTING_REGION_RE = re.compile(
    r"\b(?:al|ak|az|ar|co|de|fl|ga|hi|id|il|in|ia|ks|ky|la|me|md|ma|mi|mn|ms|"
    r"mo|mt|ne|nv|nh|nm|nc|nd|oh|ok|or|pa|ri|sc|sd|tn|tx|ut|vt|va|wa|wv|wi|wy|"
    r"alabama|alaska|arizona|arkansas|colorado|florida|georgia|illinois|indiana|"
    r"iowa|kansas|kentucky|louisiana|maryland|massachusetts|michigan|minnesota|"
    r"missouri|nevada|ohio|oklahoma|oregon|pennsylvania|tennessee|texas|utah|"
    r"virginia|washington|wisconsin|"
    r"london|uk|united kingdom|england|scotland|ireland|dublin|berlin|germany|"
    r"france|paris|india|bangalore|bengaluru|singapore|australia|sydney|"
    r"toronto|vancouver|mexico|brazil|japan|tokyo)\b"
)

# Signals that the posting really is in our target regions, which override a
# conflicting-region hit (e.g. "New York, NY / Austin, TX" is still a NY role).
_HOME_REGION_RE = re.compile(r"\b(?:ny|nyc|new york|nj|new jersey|ca|california)\b")


def location_matches(location, location_terms):
    """True if this posting's location names one of the wanted metros."""
    rx = _location_regex(tuple(location_terms))
    if rx is None:
        return True  # no location filter configured
    low = (location or "").lower()
    if not rx.search(low):
        return False
    if _CONFLICTING_REGION_RE.search(low) and not _HOME_REGION_RE.search(low):
        return False
    return True


def is_remote(location):
    return bool(_REMOTE_RE.search((location or "").lower()))


def company_is_local(jobs, location_terms):
    """Is this company *based* in one of the wanted metros?

    The user's ask is "companies in NYC or SF", but ATS APIs only expose a
    location per posting, not a company HQ. A company that lists any of its
    open roles in the target metros is treated as having a presence there.
    That's what makes it sensible to surface its remote roles too: a remote
    Chief of Staff job at an SF-based startup is a fit, while the same job at
    a company with no NYC/SF footprint is not.
    """
    rx = _location_regex(tuple(location_terms))
    if rx is None:
        return True
    return any(rx.search((loc or "").lower()) for _, _, _, loc in jobs)


def repo_file_url(relative_path):
    """Build a direct link to a file in this repo, using the env vars GitHub
    Actions sets automatically. Returns None outside of Actions (e.g. a local
    test run), where there's no repo/ref to link to."""
    server = os.environ.get("GITHUB_SERVER_URL")
    repo = os.environ.get("GITHUB_REPOSITORY")
    ref = os.environ.get("GITHUB_REF_NAME")
    if server and repo and ref:
        return f"{server}/{repo}/blob/{ref}/{relative_path}"
    return None


def send_ntfy(topic, notification_title, matches):
    """Send a push notification for one or more new matches. Each match is
    {"company", "platform", "title", "url"}. If there's exactly one match,
    the notification is made directly tappable to the job posting via ntfy's
    "Click" header - tapping the phone notification opens the JD in one step.
    With multiple matches, each one's link is listed in the body instead
    (most ntfy clients auto-linkify URLs in the expanded notification). Past
    10 matches in one run, the rest are summarized with a direct link to
    results/history.jsonl (append-only - never overwritten by a later run,
    unlike results/latest.json) rather than listed inline."""
    if not topic:
        return
    lines = [
        f"{m['company']} - {m.get('location') or 'location n/a'} "
        f"({m.get('company_open_roles', '?')} open roles)\n{m['title']}\n{m['url']}"
        for m in matches[:10]
    ]
    if len(matches) > 10:
        history_url = repo_file_url("results/history.jsonl")
        if history_url:
            lines.append(f"...and {len(matches) - 10} more - full list: {history_url}")
        else:
            lines.append(f"...and {len(matches) - 10} more - see results/history.jsonl in the repo")
    body = "\n\n".join(lines)
    headers = {"Title": notification_title, "Priority": "default", "Tags": "briefcase"}
    if len(matches) == 1:
        headers["Click"] = matches[0]["url"]
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{topic}",
            data=body.encode("utf-8"),
            headers=headers,
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:  # noqa: BLE001
        print(f"ntfy notification failed: {e}", file=sys.stderr)


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    config = load_json(COMPANIES_PATH, {})
    is_seed_run = not os.path.exists(STATE_PATH)
    state = load_json(STATE_PATH, {"seen_job_ids": {}})
    seen = state.get("seen_job_ids", {})

    companies = config.get("companies", [])
    keywords = [k.lower() for k in config.get("keywords", [])]
    keyword_word_groups = [[w.lower() for w in group] for group in config.get("keyword_word_groups", [])]
    exclude_keywords = [k.lower() for k in config.get("exclude_title_keywords", [])]
    url_templates = config.get("api_url_templates", {})
    # Open-req count is the most reliable size proxy available here: the source
    # datasets carry no funding/headcount field, and a hand-maintained name
    # blocklist can never keep up. A board with hundreds of simultaneous
    # openings is an enterprise, not a startup hiring a Chief of Staff. Enforced
    # at runtime (not just by pruning the list) so a company that grows past the
    # threshold drops out on its own, and so anything the twice-monthly refresh
    # re-adds is ignored without needing list surgery.
    max_open_roles = config.get("max_open_roles") or float("inf")
    location_terms = [t.lower() for t in config.get("location_keywords", [])]
    include_remote = config.get("include_remote_at_local_companies", True)

    new_matches = []
    unreachable = []
    emptied = []
    too_large = []
    wrong_location = 0
    checked = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(check_company, c, url_templates): c for c in companies}
        for fut in as_completed(futures):
            name, platform, slug, jobs, err = fut.result()
            company = futures[fut]
            checked += 1
            if err:
                unreachable.append({"name": name, "error": err})
                continue
            company_seen = set(seen.get(name, []))
            if not jobs:
                # A 200 response that parses to zero jobs is ambiguous: the board
                # really is empty, OR the ATS changed its response shape / served
                # a soft-error page. Overwriting state with [] on the second case
                # would make every one of this company's roles look brand-new on
                # the next run, firing a burst of false alerts. Keep the previous
                # state instead - a genuinely-emptied board costs us nothing, since
                # there are no jobs to report either way.
                if company_seen:
                    emptied.append(name)
                continue
            # A staffing agency's board is meant to be large - it lists roles
            # across every client - so the size heuristic, which exists to spot
            # big *employers*, doesn't apply to it.
            if len(jobs) > max_open_roles and not company.get("ignore_size_limit"):
                # Still record the ids, so that if this company later shrinks
                # below the threshold we don't dump its entire back catalogue
                # as "new" the first time it becomes eligible again.
                seen[name] = sorted({j[0] for j in jobs})
                too_large.append({"name": name, "open_roles": len(jobs)})
                continue
            # Does this company have any presence in the target metros? Decided
            # once per company, from the whole board, so a remote posting can be
            # judged by where the company actually is.
            local_company = company_is_local(jobs, location_terms)
            new_ids_this_run = set()
            for jid, title, url, loc in jobs:
                new_ids_this_run.add(jid)
                if jid not in company_seen:
                    if matches_keywords(title, keywords, keyword_word_groups, exclude_keywords):
                        if not (location_matches(loc, location_terms)
                                or (include_remote
                                    and is_remote_anywhere(loc, location_terms)
                                    and local_company)):
                            wrong_location += 1
                            continue
                        new_matches.append({
                            "company": name,
                            "platform": platform,
                            "title": title,
                            "url": url,
                            "location": loc,
                            # How many roles this company has open right now.
                            # It's a rough size signal, not headcount - a big
                            # company in a hiring freeze looks small here - but
                            # it's the only size hint these ATS APIs expose, and
                            # seeing it inline beats guessing from the name.
                            "company_open_roles": len(jobs),
                        })
            seen[name] = sorted(new_ids_this_run)

    # Companies routinely post the same role several times under different job
    # ids (one company in testing had the identical freelance listing open 6x).
    # State above already recorded every id, so none of these will re-report on
    # a later run - this only collapses what gets shown/pushed for THIS run.
    deduped = []
    seen_pairs = set()
    for m in new_matches:
        key = (m["company"], m["title"].strip().lower())
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        deduped.append(m)
    duplicates_collapsed = len(new_matches) - len(deduped)
    new_matches = deduped

    state["seen_job_ids"] = seen
    state["_last_run_utc"] = datetime.now(timezone.utc).isoformat()
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=1)

    result = {
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "is_seed_run": is_seed_run,
        "companies_checked": checked,
        "companies_unreachable": len(unreachable),
        "unreachable_sample": unreachable[:20],
        "companies_empty_state_preserved": len(emptied),
        "companies_skipped_too_large": len(too_large),
        "rejected_wrong_location": wrong_location,
        "largest_skipped": sorted(too_large, key=lambda c: -c["open_roles"])[:10],
        "duplicates_collapsed": duplicates_collapsed,
        "new_matches": new_matches,
    }
    with open(LATEST_PATH, "w") as f:
        json.dump(result, f, indent=1)

    # Append-only, unlike latest.json which is overwritten every run - this is
    # the permanent record of every match ever found, so a burst of matches is
    # never lost even if nobody checks latest.json before the next run replaces it.
    with open(HISTORY_PATH, "a") as f:
        f.write(json.dumps({
            "run_utc": result["run_utc"],
            "is_seed_run": is_seed_run,
            "companies_checked": checked,
            "companies_unreachable": len(unreachable),
            "duplicates_collapsed": duplicates_collapsed,
            "new_match_count": len(new_matches),
            "new_matches": new_matches,
        }) + "\n")

    print(f"Checked {checked} companies, {len(unreachable)} unreachable, "
          f"{len(emptied)} returned empty (prior state kept), "
          f"{len(too_large)} skipped as too large (>{max_open_roles} open roles), "
          f"{duplicates_collapsed} duplicate postings collapsed, "
          f"{wrong_location} rejected on location, {len(new_matches)} matches.")
    for m in new_matches:
        print(f"  MATCH: {m['company']} [{m.get('location') or 'n/a'}] "
              f"({m.get('company_open_roles','?')} open roles) - {m['title']} - {m['url']}")

    if is_seed_run:
        print("This was the seed run (no prior state.json) - matches above are today's snapshot, "
              "not genuinely new postings. No notification sent. Future runs will only report "
              "postings that appear after this point.")
        return

    if new_matches:
        topic = os.environ.get("NTFY_TOPIC", "").strip()
        if not topic:
            print("NTFY_TOPIC is not set - matches above were NOT pushed to your phone. "
                  "Set it under Settings -> Secrets and variables -> Actions to get "
                  "notifications.", file=sys.stderr)
        count_label = "1 new Chief of Staff / Ops posting" if len(new_matches) == 1 \
            else f"{len(new_matches)} new Chief of Staff / Ops postings"
        send_ntfy(topic, count_label, new_matches)


if __name__ == "__main__":
    main()
