# Chief of Staff / Ops job tracker

Checks ~2,180 companies' own job boards (Greenhouse, Lever, Ashby, Workable) directly,
once an hour, for new postings matching Chief of Staff / Ops-style keywords -
no LinkedIn lag, no AI model in the polling loop, so it can get through the whole
list in well under a minute per run.

## How it works

- `data/companies.json` - the company list, keywords, and API URL templates.
- `data/state.json` - job ids already seen per company (created automatically on first run).
- `checker.py` - does the actual work: fetches every company's board, diffs against
  `state.json`, writes new matches to `results/latest.json`, and (optionally) pushes
  a phone notification via [ntfy.sh](https://ntfy.sh).
- `.github/workflows/check-jobs.yml` - runs `checker.py` once an hour via GitHub
  Actions and commits the updated state back to the repo.
- `refresh_companies.py` / `.github/workflows/refresh-companies.yml` - twice a month,
  re-pulls the same source datasets and adds any newly-appeared company. Never
  removes anything, never touches `data/state.json` for existing companies.

## One-time setup

1. **Create a repo.** On github.com, create a new repository (public is simplest -
   see "Public vs. private" below). Add every file in this folder to it (drag-and-drop
   on github.com works fine, or `git init && git add . && git commit -m "init" && git push`
   if you're comfortable with git).

2. **(Optional but recommended) Get instant push notifications.**
   - Install the [ntfy app](https://ntfy.sh/) (iOS/Android) or just use ntfy.sh in a browser tab.
   - Pick a random, hard-to-guess topic name, e.g. `cos-job-alerts-x7k2p9` (anyone who
     knows the exact topic name could see your alerts, so don't use something guessable
     like your name).
   - Subscribe to that topic in the app.
   - In your GitHub repo: Settings -> Secrets and variables -> Actions -> New repository
     secret. Name it `NTFY_TOPIC`, value = the topic name you picked.
   - If you skip this, the tracker still works - you'd just check `results/latest.json`
     in the repo manually instead of getting a push.
   - **What the notification actually contains:** company name, job title, and the
     direct link to the posting (Greenhouse/Lever/Ashby/Workable's own URL for that
     job - not a generic search link). If exactly one new match was found, the
     notification itself is tappable and opens straight to the JD. If several came
     in at once (most likely right after the company-list refresh adds a company
     you weren't tracking before - see below), each one is listed with its own link
     in the notification body instead of just one tap-through.

3. **Enable Actions.** Go to the repo's "Actions" tab and enable workflows if prompted
   (GitHub sometimes asks for confirmation on a repo's first workflow).

4. **Run it once manually to seed the baseline.** Actions tab -> "Check job boards" ->
   "Run workflow". This first run has no `state.json` yet, so it treats everything
   currently open as the starting point rather than "new" - it will NOT send a
   notification on this run, but `results/latest.json` will show you a snapshot of
   every Chief-of-Staff/Ops role currently open across the whole list, which is a
   nice bonus. After this, it's on the 15-minute schedule and will only alert on
   postings that appear after this point.

## Public vs. private repo

GitHub Actions is **unlimited and free on public repos**. Private repos get 2,000
free minutes/month on the free plan. At the current hourly schedule (~720 runs/month,
each maybe 1-2 minutes end to end) this comfortably fits inside that budget, so
private is a real option now, not just public. There's nothing sensitive in this
repo either way (just job titles and companies), so pick whichever you're more
comfortable with.

## Keeping the company list current

Three months is long enough that new companies will appear that weren't in the
original snapshot. `refresh_companies.py` re-downloads the same source datasets
(they're updated continuously by their own maintainers) and appends anything not
already tracked - it runs automatically on the 1st and 15th of each month, or you
can trigger it manually any time from the Actions tab ("Refresh company list" ->
Run workflow).

One thing to expect: when a new company gets added this way, the checker has no
history for it yet, so its next run will report *all* of that company's currently
open matching roles as "new" - not because they were just posted, but because
that's the first time it's being watched. Treat that as a one-time snapshot of a
newly-tracked company, not a sign something's wrong.

This only ever adds companies, and only from the same handful of recruiting
datasets - it won't catch a company that never posts an internship/new-grad role
(the structural gap described when this was first built still applies). If you
come across a specific company you want covered, tell me and I'll add it directly
to `data/companies.json` rather than waiting for a refresh to maybe catch it.

## Maintaining the company list

- To add a company: find its Greenhouse/Lever/Ashby/Workable board URL (usually
  linked from their careers page) and add `{"name": "...", "platform": "...", "slug": "..."}`
  to `data/companies.json`. The slug is the first path segment after the domain,
  e.g. `jobs.lever.co/acme` -> slug `acme`, platform `lever`.
- To remove a company (e.g. you spot one that's actually large/public and not a fit):
  just delete its entry from `data/companies.json`.
- To adjust what counts as a match, edit `data/companies.json`:
  - `keywords` - fixed phrases, matched as a literal substring (case-insensitive).
    Use this for phrases whose word order doesn't vary in practice, like
    "chief of staff".
  - `keyword_word_groups` - lists of words that must *all* appear in the title,
    in any order. Use this for titles where word order genuinely varies, e.g.
    `["operations", "director"]` matches both "Director of Operations" and
    "Operations Director." A plain substring keyword would only ever catch one
    ordering.
  - `exclude_title_keywords` - a title containing any of these is rejected
    outright, before either check above runs. This is what keeps junior titles
    (Associate, Coordinator, Assistant, Intern) out of the results - tune it
    based on your own seniority level and what you're seeing come through.

## Known limitations

- The company list was built from public tech-recruiting datasets (companies that
  post software internship/new-grad roles on these ATS platforms), not individually
  verified for funding stage. Using a startup-friendly ATS correlates with being a
  smaller/private company but isn't a verified check - expect some noise, and prune
  as you spot it.
- Some ATS endpoints occasionally rate-limit or reject scripted requests. The script
  retries once and logs unreachable companies in `results/latest.json` rather than
  failing the whole run; expect ~80-90% effective coverage per run, not 100%.
- GitHub's scheduled workflows are "best effort" timing - a `*/15` cron can slip by
  a few minutes under platform load. This is still far faster than LinkedIn's
  multi-day lag, just not to-the-second.
