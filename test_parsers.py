"""Quick unit tests for checker.py's parse_* functions against documented API
shapes for each ATS. Run with: python3 test_parsers.py
These don't hit the network - they're synthetic fixtures matching each
platform's published response schema, just to catch typos/field-name bugs
before this runs unattended in CI.
"""
from checker import (parse_greenhouse, parse_lever, parse_ashby, parse_workable,
                     parse_rippling, parse_careergroup,
                     matches_keywords, location_matches, is_remote,
                     is_remote_anywhere, company_is_local)


def test_greenhouse():
    data = {
        "jobs": [
            {"id": 123, "title": "Chief of Staff", "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/123"},
            {"id": 456, "title": "Software Engineer"},
        ]
    }
    out = parse_greenhouse(data, "acme")
    assert out[0][:3] == ("123", "Chief of Staff", "https://job-boards.greenhouse.io/acme/jobs/123"), out[0]
    assert out[1][2] == "https://job-boards.greenhouse.io/acme/jobs/456"
    print("greenhouse OK")


def test_lever():
    data = [
        {"id": "abc-123", "text": "Business Operations Lead", "hostedUrl": "https://jobs.lever.co/acme/abc-123"},
        {"id": "def-456", "text": "Recruiter"},
    ]
    out = parse_lever(data, "acme")
    assert out[0][:3] == ("abc-123", "Business Operations Lead", "https://jobs.lever.co/acme/abc-123"), out[0]
    assert out[1][2] == "https://jobs.lever.co/acme/def-456"
    print("lever OK")


def test_ashby():
    data = {
        "jobs": [
            {"id": "xyz-1", "title": "Founder's Office Associate", "jobUrl": "https://jobs.ashbyhq.com/acme/xyz-1"},
            {"id": "xyz-2", "title": "Designer"},
        ]
    }
    out = parse_ashby(data, "acme")
    assert out[0][:3] == ("xyz-1", "Founder's Office Associate", "https://jobs.ashbyhq.com/acme/xyz-1"), out[0]
    assert out[1][2] == "https://jobs.ashbyhq.com/acme/xyz-2"
    print("ashby OK")


def test_workable():
    data = {
        "jobs": [
            {"shortcode": "ABCD1234", "title": "Operations Manager", "url": "https://apply.workable.com/acme/j/ABCD1234/"},
            {"shortcode": "EFGH5678", "title": "Support Engineer"},
        ]
    }
    out = parse_workable(data, "acme")
    assert out[0][:3] == ("ABCD1234", "Operations Manager", "https://apply.workable.com/acme/j/ABCD1234/"), out[0]
    assert out[1][2] == "https://apply.workable.com/acme/j/EFGH5678/"
    print("workable OK")


def test_matches_keywords():
    keywords = ["chief of staff", "founder's office"]
    groups = [["operations", "director"], ["operations", "manager"]]
    exclude = ["intern", "associate", "coordinator", "assistant"]

    # fixed-phrase matches
    assert matches_keywords("Chief of Staff to the CEO", keywords, groups, exclude)
    assert matches_keywords("Founder's Office Lead", keywords, groups, exclude)

    # word-order-independent matches - both orders must work
    assert matches_keywords("Director of Operations", keywords, groups, exclude)
    assert matches_keywords("Operations Director", keywords, groups, exclude)
    assert matches_keywords("Operations Manager, Fulfillment", keywords, groups, exclude)
    assert matches_keywords("Manager, Operations", keywords, groups, exclude)

    # junior titles must be rejected even if they'd otherwise match
    assert not matches_keywords("Operations Associate", keywords, groups, exclude)
    assert not matches_keywords("Operations Coordinator", keywords, groups, exclude)
    assert not matches_keywords("Summer Intern, Operations", keywords, groups, exclude)
    assert not matches_keywords("Executive Assistant, Operations", keywords, groups, exclude)

    # unrelated titles must not match
    assert not matches_keywords("Software Engineer", keywords, groups, exclude)

    print("matches_keywords OK")


def test_shipped_config_allows_associate_titles():
    """Regression test tied to the actual data/companies.json, not a synthetic
    fixture - confirms 'associate' titles the user explicitly asked to keep
    aren't being silently killed by the exclude list."""
    import json
    import os
    config = json.load(open(os.path.join(os.path.dirname(__file__), "data", "companies.json")))
    keywords = [k.lower() for k in config["keywords"]]
    groups = [[w.lower() for w in g] for g in config.get("keyword_word_groups", [])]
    exclude = [k.lower() for k in config["exclude_title_keywords"]]

    for title in ["Founder's Associate", "Operations Associate", "Founder's Office Lead"]:
        assert matches_keywords(title, keywords, groups, exclude), f"expected match: {title}"

    # still-junior titles that were never asked to be kept should stay excluded
    for title in ["Marketing Intern", "Operations Coordinator", "Executive Assistant to the CEO"]:
        assert not matches_keywords(title, keywords, groups, exclude), f"expected no match: {title}"

    print("shipped config OK")


def test_exclude_terms_match_on_word_boundaries():
    """Regression: the exclude list used to be a bare substring test, so
    "intern" matched "International"/"Internal" and "co-op" matched
    "Cooperative" - silently rejecting the exact senior roles this tracker
    exists to find. Excludes must match whole words only."""
    keywords = ["chief of staff", "business operations"]
    groups = [["operations", "manager"]]
    exclude = ["intern", "internship", "co-op", "coordinator", "assistant"]

    # these must NOT be swallowed by the exclude list
    assert matches_keywords("Chief of Staff, International", keywords, groups, exclude)
    assert matches_keywords("Head of Internal Operations Manager", keywords, groups, exclude)
    assert matches_keywords("International Business Operations", keywords, groups, exclude)
    assert matches_keywords("Cooperative Operations Manager", keywords, groups, exclude)

    # ...while the terms themselves still work as whole words
    assert not matches_keywords("Chief of Staff Intern", keywords, groups, exclude)
    assert not matches_keywords("Business Operations Internship", keywords, groups, exclude)
    assert not matches_keywords("Business Operations Co-op", keywords, groups, exclude)
    print("word-boundary excludes OK")


def test_shipped_config_rejects_unrelated_ops_domains():
    """The ['operations', <seniority>] word groups match any title containing
    both words, which dragged in a lot of specialist ops roles unrelated to a
    Chief-of-Staff/BizOps search. The domain terms in exclude_title_keywords
    are what keep those out."""
    import json
    import os
    config = json.load(open(os.path.join(os.path.dirname(__file__), "data", "companies.json")))
    keywords = [k.lower() for k in config["keywords"]]
    groups = [[w.lower() for w in g] for g in config.get("keyword_word_groups", [])]
    exclude = [k.lower() for k in config["exclude_title_keywords"]]

    for title in ["Clinical Operations Manager", "Cleanroom Operations Manager",
                  "Senior Operations Manager, Infrastructure", "Manager, BSA/AML Operations",
                  "Warehouse Operations Manager", "Laboratory Operations Director",
                  "Loan Operations Manager", "Recruiting Operations Lead"]:
        assert not matches_keywords(title, keywords, groups, exclude), f"expected no match: {title}"

    for title in ["Chief of Staff", "Chief of Staff, International",
                  "Director of Strategy & Business Operations", "Revenue Operations Lead",
                  "Business Operations Manager (International)", "Head of Business Operations"]:
        assert matches_keywords(title, keywords, groups, exclude), f"expected match: {title}"
    print("domain excludes OK")


def test_empty_response_preserves_prior_state():
    """Regression: a 200 response that parses to zero jobs used to overwrite a
    company's seen-id list with [], so the next run reported every one of its
    postings as brand-new. An empty parse must leave prior state alone."""
    assert parse_greenhouse({"jobs": []}, "acme") == []
    assert parse_lever([], "acme") == []
    assert parse_ashby({"jobs": []}, "acme") == []
    assert parse_workable({"jobs": []}, "acme") == []

    # mirrors the guard in checker.main(): empty parse -> keep what we had
    seen = {"Acme": ["1", "2", "3"]}
    jobs = []
    if jobs:
        seen["Acme"] = sorted({j[0] for j in jobs})
    assert seen["Acme"] == ["1", "2", "3"], "empty response must not wipe state"
    print("empty-response state guard OK")


def test_duplicate_postings_collapse():
    """Companies re-post the same role under several job ids; only one should
    reach the notification."""
    matches = [
        {"company": "Acme", "title": "Operations Manager", "url": "u1"},
        {"company": "Acme", "title": "operations manager ", "url": "u2"},
        {"company": "Acme", "title": "Chief of Staff", "url": "u3"},
        {"company": "Beta", "title": "Operations Manager", "url": "u4"},
    ]
    out, seen_pairs = [], set()
    for m in matches:
        key = (m["company"], m["title"].strip().lower())
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        out.append(m)
    assert len(out) == 3, out
    assert [m["url"] for m in out] == ["u1", "u3", "u4"]
    print("duplicate collapse OK")


def test_oversized_company_is_skipped_but_state_still_recorded():
    """Companies with hundreds of open reqs are enterprises, not startups
    hiring a Chief of Staff. They're skipped for matching - but their job ids
    must still be recorded, or shrinking back under the threshold would dump
    the whole back catalogue as "new"."""
    max_open_roles = 150
    seen = {}
    jobs = [(str(i), "Chief of Staff", "u") for i in range(400)]
    name = "BigCo"
    matched = []
    if len(jobs) > max_open_roles:
        seen[name] = sorted({j[0] for j in jobs})
    else:
        for jid, title, url in jobs:
            matched.append(title)
    assert matched == [], "oversized company must produce no matches"
    assert len(seen[name]) == 400, "ids must still be memorized"
    print("oversized-company skip OK")


def test_shipped_config_has_size_limit_and_prune_list():
    import json
    import os
    cfg = json.load(open(os.path.join(os.path.dirname(__file__), "data", "companies.json")))
    assert isinstance(cfg.get("max_open_roles"), int), "max_open_roles must be set"
    assert cfg["max_open_roles"] > 0
    pruned = {n.lower() for n in cfg.get("excluded_companies", [])}
    assert pruned, "excluded_companies must not be empty"
    # the prune must actually have been applied to the tracked list
    tracked = {c["name"].lower() for c in cfg["companies"]}
    assert not (tracked & pruned), "pruned companies are still being tracked"
    for name in ["alo yoga", "accenture"]:
        assert name in pruned, f"{name} should be pruned"
    print("shipped size-limit config OK")


def test_parsers_extract_location():
    """Each ATS reports location in its own shape; all four must surface it."""
    gh = parse_greenhouse({"jobs": [{"id": 1, "title": "T",
                                     "location": {"name": "New York, NY (Hybrid)"}}]}, "acme")
    assert gh[0][3] == "New York, NY (Hybrid)", gh

    lv = parse_lever([{"id": "a", "text": "T",
                       "categories": {"location": "San Francisco, CA",
                                      "allLocations": ["San Francisco, CA"]},
                       "workplaceType": "hybrid"}], "acme")
    assert "San Francisco, CA" in lv[0][3] and "hybrid" in lv[0][3], lv

    ab = parse_ashby({"jobs": [{"id": "x", "title": "T", "location": "Brooklyn, NY",
                                "secondaryLocations": [], "isRemote": True}]}, "acme")
    assert "Brooklyn, NY" in ab[0][3] and "Remote" in ab[0][3], ab

    wk = parse_workable({"jobs": [{"shortcode": "S", "title": "T", "city": "San Francisco",
                                   "state": "CA", "country": "United States",
                                   "telecommuting": False}]}, "acme")
    assert "San Francisco" in wk[0][3], wk
    print("parser location extraction OK")


def test_location_matching_uses_word_boundaries():
    """Same trap as the title excludes: bare state codes produce false hits."""
    terms = ["new york", "nyc", "san francisco", "bay area", "brooklyn"]
    assert location_matches("New York, NY", terms)
    assert location_matches("NYC (Hybrid)", terms)
    assert location_matches("San Francisco, CA", terms)
    assert location_matches("Brooklyn, NY", terms)
    # must NOT match
    assert not location_matches("Albany, NY", terms)
    assert not location_matches("Austin, TX", terms)
    assert not location_matches("London, UK", terms)
    assert not location_matches("Germany", terms)
    print("location word boundaries OK")


def test_remote_only_counts_at_companies_with_local_presence():
    """A remote role is a fit if the company is actually in NYC/SF; the same
    role at a company with no local footprint is not."""
    terms = ["new york", "san francisco"]
    assert is_remote("Remote - US")
    assert not is_remote("Austin, TX")

    sf_company = [("1", "T", "u", "San Francisco, CA"), ("2", "T", "u", "Remote")]
    tx_company = [("1", "T", "u", "Austin, TX"), ("2", "T", "u", "Remote")]
    assert company_is_local(sf_company, terms)
    assert not company_is_local(tx_company, terms)
    print("remote-at-local-company OK")


def test_remote_must_not_be_pinned_to_another_city():
    """Regression: a posting listing several non-target cities plus "Remote"
    was slipping through the remote allowance at locally-based companies,
    smuggling in e.g. Central Valley roles via an SF company."""
    terms = ["new york", "san francisco", "bay area"]
    for loc in ["Remote - US", "Remote, Remote", "United States (Remote)",
                "San Francisco Bay Area, Remote", "Bay Area or Remote"]:
        assert is_remote_anywhere(loc, terms), loc
    for loc in ["Fresno, Modesto, Bakersfield, Stockton, Remote",
                "Austin, TX (Remote)", "Remote (Europe)", "London, Remote"]:
        assert not is_remote_anywhere(loc, terms), loc
    print("remote-anywhere strictness OK")


def test_nyc_boroughs_and_lookalike_cities():
    """All five boroughs count as New York; identically-named cities in other
    states do not. There is a Brooklyn in Ohio, a Brooklyn Park in Minnesota,
    and a Queens Park in London."""
    import json
    import os
    cfg = json.load(open(os.path.join(os.path.dirname(__file__), "data", "companies.json")))
    terms = [t.lower() for t in cfg["location_keywords"]]

    for loc in ["Brooklyn, NY", "Queens, NY", "Bronx, NY", "Staten Island, NY",
                "Long Island City, NY", "Astoria, Queens", "Williamsburg, Brooklyn",
                "DUMBO, Brooklyn, NY", "Manhattan, New York", "New York, NY",
                "Jersey City, NJ", "Hoboken, NJ",
                "New York, NY / Austin, TX"]:
        assert location_matches(loc, terms), f"should match: {loc}"

    for loc in ["Brooklyn Park, MN", "Brooklyn, OH", "Queens Park, London",
                "Berkeley, MO", "Austin, TX", "London, UK", "Boston, MA"]:
        assert not location_matches(loc, terms), f"should NOT match: {loc}"
    print("NYC boroughs + lookalikes OK")


def test_project_roles_match_but_not_engineering_pm():
    """"Project Manager" is a generic title dominated by construction, civil
    engineering and IT. The business-side ones should match; the domain ones
    should not - and adding those domain excludes must not break the existing
    ops keywords."""
    import json
    import os
    cfg = json.load(open(os.path.join(os.path.dirname(__file__), "data", "companies.json")))
    kw = [k.lower() for k in cfg["keywords"]]
    gr = [[w.lower() for w in g] for g in cfg["keyword_word_groups"]]
    ex = [k.lower() for k in cfg["exclude_title_keywords"]]

    for title in ["Project Manager", "Project Lead", "Senior Project Manager",
                  "Project Manager (Remote)", "Enterprise Project Manager",
                  "Project Lead, Digital Marketing Analytics"]:
        assert matches_keywords(title, kw, gr, ex), f"expected match: {title}"

    for title in ["Civil Engineering Project Manager", "Technical Project Manager",
                  "Commercial Project Manager - General Contractor", "Survey Project Manager",
                  "Sprinkler Project Manager", "Junior Project Manager",
                  "Project Coordinator", "Project Management Intern",
                  "Senior Transportation Project Manager", "Cloud Native Technical Project Manager"]:
        assert not matches_keywords(title, kw, gr, ex), f"expected NO match: {title}"

    # the PM domain excludes must not have collateral damage on the ops keywords
    for title in ["Chief of Staff", "Business Operations Lead", "Revenue Operations Manager",
                  "Director of Strategy & Business Operations", "Founder's Associate"]:
        assert matches_keywords(title, kw, gr, ex), f"regression - should still match: {title}"
    print("project-role matching OK")


def test_generalist_matches_business_roles_not_hr():
    """"Generalist" in the wild is overwhelmingly "HR Generalist" and AI-trainer
    gig listings. The startup sense of the word should match; those should not."""
    import json
    import os
    cfg = json.load(open(os.path.join(os.path.dirname(__file__), "data", "companies.json")))
    kw = [k.lower() for k in cfg["keywords"]]
    gr = [[w.lower() for w in g] for g in cfg["keyword_word_groups"]]
    ex = [k.lower() for k in cfg["exclude_title_keywords"]]

    for title in ["Generalist", "Founding Generalist", "Business Generalist",
                  "Operations Generalist", "GTM Generalist",
                  "Business Operations Generalist"]:
        assert matches_keywords(title, kw, gr, ex), f"expected match: {title}"

    for title in ["HR Generalist", "Senior HR Generalist - EMEA",
                  "Human Resources Generalist", "Graduate HR Generalist - Americas",
                  "AI Generalist (No Experience Required)",
                  "AI Training Generalist - Freelance AI Trainer Project",
                  "SEO Generalist", "Senior Generalist Programmer",
                  "Generalist/Builder - New Grad", "Underwriter II, USDA Generalist"]:
        assert not matches_keywords(title, kw, gr, ex), f"expected NO match: {title}"

    # no collateral damage on the rest of the keyword set
    for title in ["Chief of Staff", "Business Operations Lead", "Project Manager",
                  "Revenue Operations Manager", "Founder's Associate"]:
        assert matches_keywords(title, kw, gr, ex), f"regression: {title}"
    print("generalist matching OK")


def test_ats_slug_parsing_preserves_encoding():
    """Regression: decoding the slug looked like a cleanup but breaks real
    boards - some Ashby slugs contain spaces, and a decoded slug raises
    InvalidURL when interpolated into a request URL."""
    from refresh_companies import parse_ats
    assert parse_ats("https://jobs.ashbyhq.com/Superhuman%20Platform%20Inc/abc") == \
        ("ashby", "Superhuman%20Platform%20Inc")
    assert parse_ats("https://boards.greenhouse.io/verve/jobs/123") == ("greenhouse", "verve")
    assert parse_ats("https://jobs.lever.co/acme/xyz") == ("lever", "acme")
    assert parse_ats("https://ats.rippling.com/sanas/jobs/abc") == ("rippling", "sanas")
    assert parse_ats("https://www.linkedin.com/jobs/view/123") is None
    assert parse_ats("https://jobs.smartrecruiters.com/foo") is None
    print("ats slug parsing OK")


def test_company_list_has_no_duplicate_boards():
    """Acquired companies' apply links point at the acquirer's board, so
    name-only dedupe would poll the same board under several names and report
    every match once per alias."""
    import json
    import os
    cfg = json.load(open(os.path.join(os.path.dirname(__file__), "data", "companies.json")))
    seen = {}
    dupes = []
    for c in cfg["companies"]:
        key = (c["platform"], c["slug"])
        if key in seen:
            dupes.append((seen[key], c["name"], key))
        seen[key] = c["name"]
    assert not dupes, f"duplicate boards: {dupes[:5]}"
    print(f"no duplicate boards across {len(cfg['companies'])} companies OK")


def test_rippling():
    """Rippling's board pages are Cloudflare-challenged and client-rendered;
    the public board API returns the same postings as plain JSON."""
    data = [
        {"uuid": "d9e1cbed", "name": "Revenue Operations Manager",
         "url": "https://ats.rippling.com/sanas/jobs/d9e1cbed",
         "workLocation": {"label": "Palo Alto, CA", "id": "Palo Alto, CA"}},
        {"uuid": "abc123", "name": "Software Engineer", "workLocation": {}},
    ]
    out = parse_rippling(data, "sanas")
    assert out[0] == ("d9e1cbed", "Revenue Operations Manager",
                      "https://ats.rippling.com/sanas/jobs/d9e1cbed", "Palo Alto, CA"), out[0]
    # falls back to a constructed URL and tolerates a missing location
    assert out[1][2] == "https://ats.rippling.com/sanas/jobs/abc123"
    assert out[1][3] == ""
    print("rippling OK")


def test_careergroup():
    """Career Group is a staffing agency on Webflow, not an ATS - the whole
    board is one HTML page. The division is appended to the title because the
    hiring employer is deliberately anonymous."""
    html = """
    <div role="listitem"><a href="/job-posting/185798" class="jobs-card">
      <h4 fs-cmsfilter-field="title" class="h4-serif">Chief of Staff</h4>
      <p fs-cmsfilter-field="division" class="chip-text">Career Group</p>
      <p fs-cmsfilter-field="location">New York, NY</p>
    </a></div>
    <div role="listitem"><a href="/job-posting/185799" class="jobs-card">
      <h4 fs-cmsfilter-field="title" class="h4-serif">Packaging Designer</h4>
      <p fs-cmsfilter-field="division" class="chip-text">Syndicatebleu</p>
      <p fs-cmsfilter-field="location">Los Angeles, CA</p>
    </a></div>
    """
    out = parse_careergroup(html, "find-work")
    assert len(out) == 2, out
    assert out[0] == ("185798", "Chief of Staff (Career Group)",
                      "https://www.careergroupcompanies.com/job-posting/185798",
                      "New York, NY"), out[0]
    assert out[1][3] == "Los Angeles, CA"
    assert parse_careergroup("", "find-work") == []
    print("careergroup OK")


def test_every_platform_has_a_parser_and_template():
    """A company whose platform has no template is silently skipped every run."""
    import json
    import os
    from checker import PARSERS
    cfg = json.load(open(os.path.join(os.path.dirname(__file__), "data", "companies.json")))
    templates = cfg["api_url_templates"]
    used = {c["platform"] for c in cfg["companies"]}
    for p in sorted(used):
        assert p in PARSERS, f"no parser for platform {p}"
        assert p in templates, f"no api_url_template for platform {p}"
    print(f"all {len(used)} platforms wired: {', '.join(sorted(used))}")


if __name__ == "__main__":
    test_greenhouse()
    test_lever()
    test_ashby()
    test_workable()
    test_rippling()
    test_careergroup()
    test_matches_keywords()
    test_shipped_config_allows_associate_titles()
    test_exclude_terms_match_on_word_boundaries()
    test_shipped_config_rejects_unrelated_ops_domains()
    test_empty_response_preserves_prior_state()
    test_duplicate_postings_collapse()
    test_oversized_company_is_skipped_but_state_still_recorded()
    test_shipped_config_has_size_limit_and_prune_list()
    test_parsers_extract_location()
    test_location_matching_uses_word_boundaries()
    test_remote_only_counts_at_companies_with_local_presence()
    test_remote_must_not_be_pinned_to_another_city()
    test_nyc_boroughs_and_lookalike_cities()
    test_project_roles_match_but_not_engineering_pm()
    test_generalist_matches_business_roles_not_hr()
    test_ats_slug_parsing_preserves_encoding()
    test_company_list_has_no_duplicate_boards()
    test_every_platform_has_a_parser_and_template()
    print("ALL PARSER TESTS PASSED")
