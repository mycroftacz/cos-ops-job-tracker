# Chief of Staff / Ops job tracker

Checks ~2,180 companies' own job boards (Greenhouse, Lever, Ashby, Workable) directly,
once an hour, for new postings matching Chief of Staff / Ops-style keywords -
no LinkedIn lag, no AI model in the polling loop. On a GitHub Actions runner a
full pass over the whole list takes **about 30 seconds** (45 seconds including
checkout and the state commit) - measured on a real run, not estimated. It's
noticeably slower from a home connection, where per-request latency dominates.

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

1. **Create a repo and push with git - not drag-and-drop.** On github.com, create a
   new repository (public is strongly preferred - see "Public vs. private" below).

   **Do not use github.com's drag-and-drop upload for this repo.** Two things about
   it will silently break Actions:
   - The browser upload **skips dot-directories**, so `.github/` and `.gitignore`
     never make it up - and a workflow that isn't in `.github/workflows/` simply
     does not exist as far as GitHub is concerned.
   - Dragging the *folder* nests everything one level deep (`cos-ops-job-tracker/...`).
     Workflows are only picked up from `.github/workflows/` at the **repository
     root**, so a nested copy is ignored even if it does upload.

   Push from this directory instead, so the layout is right:

   ```
   .github/workflows/check-jobs.yml   <- must be exactly here, at the repo root
   .github/workflows/refresh-companies.yml
   checker.py
   data/companies.json
   ```

   One more gotcha: pushing a commit that adds or edits anything under
   `.github/workflows/` requires a token with the **`workflow` scope**. If you
   authenticated with `gh` and get `refusing to allow an OAuth App to create or
   update workflow`, run `gh auth refresh -h github.com -s workflow` and push again.

   To confirm the workflows actually registered after pushing:

   ```
   gh api repos/<you>/cos-ops-job-tracker/actions/workflows --jq '.total_count'
   ```

   That must print `2`. If it prints `0`, the files aren't where GitHub expects
   them - re-check the layout above.

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
   nice bonus. Expect that snapshot to be large - on the order of **1,000 roles**,
   since it's every open match across ~2,180 companies at once. Ongoing hourly runs
   report only what's genuinely new, which is a handful a day at most. After this, it's on the hourly schedule and will only alert on
   postings that appear after this point.

## Finding a match after the fact

- `results/latest.json` - only the *most recent* run's findings. It gets fully
  overwritten every hour, so it's good for "what just happened" but not a
  permanent record. Alongside the matches it records `companies_unreachable`
  (boards that failed to fetch), `companies_empty_state_preserved` (boards that
  returned zero jobs, where the previous state was deliberately kept rather than
  wiped), and `duplicates_collapsed` (repeat postings of the same role that were
  folded into one).
- `results/history.jsonl` - one line per run, forever appended, never
  overwritten. Each line includes that run's full `new_matches` list (company,
  title, link), so this is the actual answer to "where did that batch of
  matches go" if you didn't check `latest.json` in time. Open it on github.com,
  or download it and search/grep locally - each line is a standalone JSON
  object (that's what the `.jsonl` extension means), so a plain text search
  for a company name or `"new_match_count":` values greater than 1 will find
  bursts quickly even as the file grows over the 3 months you're planning to
  run this.
- When a single run finds more than 10 matches, the push notification only
  lists the first 10 inline and links directly to `results/history.jsonl` for
  the rest, rather than truncating with no way to recover what got cut.

## Public vs. private repo

GitHub Actions is **unlimited and free on public repos**. Private repos get 2,000
free minutes/month on the free plan.

**Cost is not the deciding factor.** A run bills at 1 minute (45s, rounded up), so
the hourly schedule costs ~720 minutes/month - comfortably inside the private
allowance. Either choice works on the free plan.

**Schedule reliability is the deciding factor.** GitHub treats `schedule:` triggers
as best-effort and drops them under load, and it deprioritizes low-activity private
repos hardest. In practice this repo saw only 1 of 8 expected hourly runs fire on
its first day as a private repo, with that one delayed 31 minutes. Public repos get
noticeably better scheduler treatment. There's nothing sensitive here - just public
job titles and company names - so **public is recommended**, for reliability rather
than cost.

## Filtering by location (NYC / SF)

`location_keywords` in `data/companies.json` lists the metros you'll consider.
A posting has to name one of them to be reported. All four ATS platforms expose
a location per job, in four different shapes, and `checker.py` normalises them
into one string before matching:

| Platform | Field |
|---|---|
| Greenhouse | `location.name` -> `"New York, NY (Hybrid)"` |
| Ashby | `location` + `secondaryLocations` + `isRemote` |
| Lever | `categories.location` + `categories.allLocations` + `workplaceType` |
| Workable | `city` + `state` + `country` + `telecommuting` |

Matching is on **whole words**, and the list deliberately contains city and
metro names, never bare state codes - `"ny"` as a substring matches "Albany, NY"
and "Germany", and `"sf"` matches "SFO".

All five NYC boroughs are covered (`brooklyn`, `queens`, `bronx`,
`staten island`, plus `manhattan` and `long island city`), along with Jersey
City and Hoboken. Because those names aren't unique - there's a Brooklyn in
Ohio, a Brooklyn Park in Minnesota, an Oakland in New Jersey, a Queens Park in
London - a match is rejected when the location also names a region that rules
it out. `"Williamsburg, Brooklyn"` still matches, `"Brooklyn Park, MN"` does
not, and `"New York, NY / Austin, TX"` still counts as a New York role. The one
known gap is `"Oakland, NJ"` (pop. 13,000), which passes because NJ has to stay
valid for Jersey City and Hoboken.

**Remote roles.** ATS APIs report a location per *posting*, not a company HQ, so
"is this company in NYC or SF" is inferred: a company counts as locally based if
**any** of its open roles names one of your metros. Its remote postings are then
included too, since a remote Chief of Staff job at an SF startup is a fit while
the same job at a company with no local footprint isn't. Set
`include_remote_at_local_companies` to `false` to require an explicit
NYC/SF location on every match.

A posting only counts as remote if it isn't *also* pinned somewhere else -
`"Remote - US"` qualifies, `"Fresno, Modesto, Bakersfield, Remote"` does not.

**The default list covers the whole Bay Area**, down to San Jose and Sunnyvale.
If you mean San Francisco proper and a short commute, delete `"san jose"`,
`"sunnyvale"`, `"santa clara"`, and `"mountain view"` from `location_keywords`.
To drop the location filter entirely, set it to `[]`.

Each run reports `rejected_wrong_location` in `results/latest.json` so you can
see how much the filter is removing, and every match now carries its location
and the company's open-role count - in the results file, the run log, and the
push notification itself.

## Keeping large employers out

The company list came from public tech-recruiting datasets, which carry no funding
or headcount field - so large employers (Accenture, Alo Yoga, Mercedes-Benz) came
along with the startups. A hand-maintained blocklist of names can't keep up with
that, so the filter uses the one reliable size signal actually available: **how many
roles a company has open at once.**

`max_open_roles` in `data/companies.json` (default **40**) is the threshold. A
board with more simultaneous openings than that is very unlikely to be a small
company. It's enforced two ways:

- `checker.py` skips matching for any company over the limit at runtime, so a
  company that grows past it drops out on its own - no list surgery needed.
- `excluded_companies` records the 95 companies pruned from the original list, and
  `refresh_companies.py` honours it, so the twice-monthly refresh can't quietly
  re-add them.

Each run reports `companies_skipped_too_large` and a `largest_skipped` sample in
`results/latest.json`, so you can see what the threshold is doing.

**Open reqs are not headcount, and the gap is real.** DRW Holdings (~2,000 people)
posts 1-5 roles at a time; BioAgilytix (~1,500) posts 6-10. A company in a hiring
freeze looks tiny by this measure whatever its size. The threshold reliably excludes
the *obviously* huge and gets noisier the tighter you set it - so 40 is a
deliberate compromise rather than a precise "under 100 people" rule, and every
notification carries the company's open-role count so you can judge for yourself.
Raise or lower it as one number; runtime enforcement means it takes effect on the
next run, and companies drift back in on their own if they shrink.

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
    outright, before either check above runs. Matched on **whole words**, so
    `intern` rejects "Operations Intern" but leaves "Chief of Staff,
    International" and "Head of Internal Operations" alone.

    This list does two jobs. The first few terms (`intern`, `internship`,
    `co-op`, `coordinator`, `assistant`) filter out junior titles. Everything
    after that filters out **unrelated operational domains** - `clinical`,
    `warehouse`, `infrastructure`, `aml`, `recruiting`, and so on. Those are
    there because the `["operations", <seniority>]` word groups match any title
    containing both words, which otherwise drags in things like "Cleanroom
    Operations Manager" and "Manager, BSA/AML Operations - Cases". Removing the
    domain terms roughly doubles the volume you get.

    `"generalist"` needs the same treatment for a different reason: in the wild
    the word almost always means "HR Generalist", plus a long tail of
    AI-trainer gig listings. `hr`, `human resources`, `ai training`, `freelance`
    and `no experience` are what leave the startup sense of the word intact.
    Note that `people operations` is deliberately *not* excluded, so
    "People Operations Generalist" still comes through - add it if you don't
    want HR-adjacent roles at all.

    A large share of this list exists to make `"project manager"` usable. That
    title is generic and dominated by construction, civil engineering and IT -
    on a sample of 13,115 live postings, adding it raw brought in 75 roles, of
    which only ~18 were business-side. Terms like `civil`, `contractor`,
    `sprinkler`, `technical` and `cloud` are what keep "Project Manager" meaning
    the kind you want. The cost is small but real: `Technical Operations
    Associate` no longer matches. Drop `technical` from the list if you'd rather
    have it back and tolerate engineering PM roles.

    **This is the main dial to turn.** If you're seeing a category of role you
    don't care about, add its distinguishing word here. If you think you're
    missing roles, remove terms. `data/companies.json` is the only file you need
    to edit, and `python3 test_parsers.py` will tell you if you've broken
    anything.

## Known limitations

- The company list was built from public tech-recruiting datasets (companies that
  post software internship/new-grad roles on these ATS platforms), not individually
  verified for funding stage. Using a startup-friendly ATS correlates with being a
  smaller/private company but isn't a verified check - expect some noise, and prune
  as you spot it.
- Some ATS endpoints occasionally rate-limit or reject scripted requests. The script
  retries once and logs unreachable companies in `results/latest.json` rather than
  failing the whole run; expect ~80-90% effective coverage per run, not 100%.
- GitHub's scheduled workflows are "best effort" timing - the hourly cron can slip
  by several minutes under platform load, and GitHub drops runs entirely if the
  queue is congested. This is still far faster than LinkedIn's multi-day lag, just
  not to-the-minute.
- Scheduled workflows are auto-disabled after 60 days of repository inactivity.
  This one commits state every hour, so that clock never runs down - but if you
  ever pause the schedule for a couple of months, re-enable it from the Actions tab.
