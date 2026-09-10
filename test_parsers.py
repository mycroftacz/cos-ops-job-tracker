"""Quick unit tests for checker.py's parse_* functions against documented API
shapes for each ATS. Run with: python3 test_parsers.py
These don't hit the network - they're synthetic fixtures matching each
platform's published response schema, just to catch typos/field-name bugs
before this runs unattended in CI.
"""
from checker import parse_greenhouse, parse_lever, parse_ashby, parse_workable, matches_keywords


def test_greenhouse():
    data = {
        "jobs": [
            {"id": 123, "title": "Chief of Staff", "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/123"},
            {"id": 456, "title": "Software Engineer"},
        ]
    }
    out = parse_greenhouse(data, "acme")
    assert out[0] == ("123", "Chief of Staff", "https://job-boards.greenhouse.io/acme/jobs/123"), out[0]
    assert out[1][2] == "https://job-boards.greenhouse.io/acme/jobs/456"
    print("greenhouse OK")


def test_lever():
    data = [
        {"id": "abc-123", "text": "Business Operations Lead", "hostedUrl": "https://jobs.lever.co/acme/abc-123"},
        {"id": "def-456", "text": "Recruiter"},
    ]
    out = parse_lever(data, "acme")
    assert out[0] == ("abc-123", "Business Operations Lead", "https://jobs.lever.co/acme/abc-123"), out[0]
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
    assert out[0] == ("xyz-1", "Founder's Office Associate", "https://jobs.ashbyhq.com/acme/xyz-1"), out[0]
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
    assert out[0] == ("ABCD1234", "Operations Manager", "https://apply.workable.com/acme/j/ABCD1234/"), out[0]
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


if __name__ == "__main__":
    test_greenhouse()
    test_lever()
    test_ashby()
    test_workable()
    test_matches_keywords()
    test_shipped_config_allows_associate_titles()
    test_exclude_terms_match_on_word_boundaries()
    test_shipped_config_rejects_unrelated_ops_domains()
    test_empty_response_preserves_prior_state()
    test_duplicate_postings_collapse()
    print("ALL PARSER TESTS PASSED")
