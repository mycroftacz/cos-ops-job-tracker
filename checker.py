#!/usr/bin/env python3
"""
Chief of Staff / Ops job tracker - checker script.

Polls every company's ATS job board directly (Greenhouse, Lever, Ashby, Workable),
diffs against previously-seen job ids, and writes any brand-new postings whose
title matches the configured keywords to results/latest.json. Designed to run
on a tight cron (e.g. every 10-15 minutes) via GitHub Actions - it's plain
concurrent HTTP + JSON parsing, no AI model in the loop, so it can get through
thousands of companies in well under a minute.

State (data/state.json) is committed back to the repo each run so nothing gets
re-reported. results/latest.json always reflects the most recent run's findings
(read by anything downstream that wants to relay a notification).
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

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


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def fetch_with_retry(url, retries=1):
    last_err = None
    for attempt in range(retries + 1):
        try:
            return fetch(url), None
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
        if jid:
            out.append((jid, title, url))
    return out


def parse_lever(data, slug):
    out = []
    items = data if isinstance(data, list) else data.get("data", []) or []
    for j in items or []:
        jid = str(j.get("id", ""))
        title = j.get("text", "") or j.get("title", "") or ""
        url = j.get("hostedUrl") or f"https://jobs.lever.co/{slug}/{jid}"
        if jid:
            out.append((jid, title, url))
    return out


def parse_ashby(data, slug):
    out = []
    jobs = data.get("jobs") if isinstance(data, dict) else data
    for j in jobs or []:
        jid = str(j.get("id", ""))
        title = j.get("title", "") or ""
        url = j.get("jobUrl") or j.get("applyUrl") or f"https://jobs.ashbyhq.com/{slug}/{jid}"
        if jid:
            out.append((jid, title, url))
    return out


def parse_workable(data, slug):
    out = []
    jobs = data.get("jobs") if isinstance(data, dict) else data
    for j in jobs or []:
        jid = str(j.get("shortcode") or j.get("id") or "")
        title = j.get("title", "") or ""
        url = j.get("url") or f"https://apply.workable.com/{slug}/j/{jid}/"
        if jid:
            out.append((jid, title, url))
    return out


PARSERS = {
    "greenhouse": parse_greenhouse,
    "lever": parse_lever,
    "ashby": parse_ashby,
    "workable": parse_workable,
}


def check_company(company, url_templates):
    name = company["name"]
    platform = company["platform"]
    slug = company["slug"]
    template = url_templates.get(platform)
    if not template:
        return name, platform, slug, [], f"no url template for platform {platform}"
    url = template.format(slug=slug)
    data, err = fetch_with_retry(url)
    if err:
        return name, platform, slug, [], err
    parser = PARSERS.get(platform)
    try:
        jobs = parser(data, slug)
    except Exception as e:  # noqa: BLE001
        return name, platform, slug, [], f"parse error: {e}"
    return name, platform, slug, jobs, None


def matches_keywords(title, keywords, keyword_word_groups, exclude_keywords):
    """A title matches if either:
    - it contains one of `keywords` verbatim as a substring (for fixed phrases
      like "chief of staff", where word order doesn't vary in practice), or
    - it contains every word in one of `keyword_word_groups`, in any order
      (for titles like "operations director" vs "director of operations" -
      real postings use both orders, and a plain substring check would only
      ever catch one of them).
    Either way, a title containing any `exclude_keywords` term is rejected
    first - this is what keeps entry-level titles (Associate, Coordinator,
    Assistant, Intern) out of the results.
    """
    t = title.lower()
    if any(x in t for x in exclude_keywords):
        return False
    if any(k in t for k in keywords):
        return True
    for group in keyword_word_groups:
        if all(word in t for word in group):
            return True
    return False


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
    lines = [f"{m['company']}: {m['title']}\n{m['url']}" for m in matches[:10]]
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

    new_matches = []
    unreachable = []
    checked = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(check_company, c, url_templates): c for c in companies}
        for fut in as_completed(futures):
            name, platform, slug, jobs, err = fut.result()
            checked += 1
            if err:
                unreachable.append({"name": name, "error": err})
                continue
            company_seen = set(seen.get(name, []))
            new_ids_this_run = set()
            for jid, title, url in jobs:
                new_ids_this_run.add(jid)
                if jid not in company_seen:
                    if matches_keywords(title, keywords, keyword_word_groups, exclude_keywords):
                        new_matches.append({
                            "company": name,
                            "platform": platform,
                            "title": title,
                            "url": url,
                        })
            seen[name] = sorted(new_ids_this_run)

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
            "new_match_count": len(new_matches),
            "new_matches": new_matches,
        }) + "\n")

    print(f"Checked {checked} companies, {len(unreachable)} unreachable, {len(new_matches)} matches.")
    for m in new_matches:
        print(f"  MATCH: {m['company']} - {m['title']} - {m['url']}")

    if is_seed_run:
        print("This was the seed run (no prior state.json) - matches above are today's snapshot, "
              "not genuinely new postings. No notification sent. Future runs will only report "
              "postings that appear after this point.")
        return

    if new_matches:
        topic = os.environ.get("NTFY_TOPIC", "").strip()
        count_label = "1 new Chief of Staff / Ops posting" if len(new_matches) == 1 \
            else f"{len(new_matches)} new Chief of Staff / Ops postings"
        send_ntfy(topic, count_label, new_matches)


if __name__ == "__main__":
    main()
