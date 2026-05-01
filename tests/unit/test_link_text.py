"""Unit test for link_text rule's generic-phrase heuristic."""

from a11yfix.rules.link_text import _is_generic


def test_click_here_is_generic():
    assert _is_generic("Click here", url=None)


def test_url_only_is_generic():
    assert _is_generic("https://example.com", url=None)


def test_descriptive_text_not_generic():
    assert not _is_generic("Quarterly Earnings Report", url=None)
