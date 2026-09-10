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


if __name__ == "__main__":
    test_greenhouse()
    test_lever()
    test_ashby()
    test_workable()
    test_matches_keywords()
    test_shipped_config_allows_associate_titles()
    print("ALL PARSER TESTS PASSED")
