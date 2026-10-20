"""Hostile saved text stays inert in the run report: escaped, isolated, never markup."""

from __future__ import annotations

import pytest
from markupsafe import escape

from tests.unit.reporting.test_run_report import Page, links_for, make_story, report_module

PAYLOADS = [
    "<script>alert(1)</script>",
    '</style><img src="https://invalid.test/x" onerror="alert(1)">',
    '<svg onload="alert(1)"><use href="https://invalid.test/x"></use></svg>',
    '<iframe srcdoc="<script>alert(1)</script>"></iframe>',
    '<a href="javascript:alert(1)">click</a><base href="https://invalid.test/">',
    '<form action="https://invalid.test/"><input autofocus onfocus="alert(1)"></form>',
    '{{ cycler.__init__.__globals__ }} & "quotes" < >',
    "\u0634\u0647\u0631 \u0622\u0632\u0645\u0627\u06cc\u0634\u06cc \u062a\u0647\u0631\u0627\u0646",
]
ALLOWED_TAGS = {
    "html",
    "head",
    "meta",
    "title",
    "style",
    "link",
    "body",
    "a",
    "header",
    "nav",
    "main",
    "section",
    "footer",
    "div",
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "span",
    "bdi",
    "code",
    "ol",
    "ul",
    "li",
    "dl",
    "dt",
    "dd",
    "table",
    "caption",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
}
FORBIDDEN_ATTRIBUTES = {
    "style",
    "src",
    "srcset",
    "srcdoc",
    "action",
    "formaction",
    "poster",
    "background",
    "data",
    "ping",
    "xlink:href",
}
ICON = "/assets/workbench-icon.svg"


def hostile_story(payload: str):
    return make_story(
        custom_name=payload,
        scenario_name=payload,
        campaign_name=payload,
        model_id=payload,
        product=payload if len(payload) <= 80 else "Lumen Pocket",
    )


@pytest.mark.parametrize("language", ["en", "fa"])
@pytest.mark.parametrize("view", [False, True])
@pytest.mark.parametrize("payload", PAYLOADS)
def test_hostile_saved_text_remains_inert_escaped_bdi_text(payload, view, language):
    story = hostile_story(payload)
    links = links_for(story.run.run_id, language) if view else None
    rendered = report_module().render_run_report_html(story, language=language, links=links)
    content = rendered.content
    parsed = Page(content)
    text = "".join(parsed.text)
    # The persona name, scenario and campaign names and the model ID each appear as data.
    assert text.count(payload) >= 4
    tags = {tag for tag, _ in parsed.elements}
    assert tags <= ALLOWED_TAGS, tags - ALLOWED_TAGS
    allowed_links = (
        set()
        if links is None
        else {links.story, links.technical, links.download, *(h for _, h in links.languages)}
    )
    for tag, attrs in parsed.elements:
        assert not any(name.startswith("on") for name in attrs), (tag, attrs)
        assert not (FORBIDDEN_ATTRIBUTES & attrs.keys()), (tag, attrs)
        href = attrs.get("href")
        if href is None:
            continue
        if tag == "link":
            assert view and href == ICON and attrs.get("rel") == "icon"
        else:
            assert tag == "a"
            assert href.startswith("#") or href in allowed_links, href
    assert content.count(b"<style>") == 1
    assert content.count(b"</style>") == 1
    assert b"<script" not in content
    assert b"<img" not in content and b"<iframe" not in content and b"<form" not in content
    assert any(tag == "bdi" and attrs.get("dir") == "auto" for tag, attrs in parsed.elements)


@pytest.mark.parametrize("payload", PAYLOADS)
def test_hostile_text_is_escaped_wherever_it_is_rendered(payload):
    # The model ID is also a headline reference parameter and the product name a text
    # parameter of every moment sentence; each renders escaped inside <bdi>.
    story = hostile_story(payload)
    decoded = (
        report_module()
        .render_run_report_html(story, language="en", links=None)
        .content.decode("utf-8")
    )
    escaped = str(escape(payload))
    assert decoded.count(escaped) >= 4
    if escaped != payload:
        assert payload not in decoded


def test_report_never_names_addresses_paths_or_secrets():
    story = make_story(custom_name="Kiro")
    for language in ("en", "de", "fa"):
        for links in (None, links_for(story.run.run_id, language)):
            content = (
                report_module()
                .render_run_report_html(story, language=language, links=links)
                .content.decode("utf-8")
            )
            for forbidden in (
                "://",
                "credential",
                "api_key",
                "11434",
                "C:\\",
                "\\\\",
                "base_url",
                "display_label",
            ):
                assert forbidden not in content, (language, forbidden)
