from __future__ import annotations

import pytest

from adlife.core.experiments._spatial_study_validation import document_sha256
from adlife.core.experiments.spatial_study import SpatialStudyResult
from tests.integration.test_city_spatial_study import analyze, definition, make_runs
from tests.unit.reporting.test_spatial_html import Document, report_module


def public_city_result(result, text):
    # Detached public provenance can change coherently; authenticity still rests
    # on verified loading. This exercises the actual renderer validation boundary.
    city = result.city.model_copy(update={"name": text, "attribution": text})
    document = result.model_dump(mode="python")
    document.update(city=city, city_provenance_sha256=document_sha256(city))
    return SpatialStudyResult.model_validate(document)


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    root = tmp_path_factory.mktemp("hostile-report")
    make_runs(root, response=True)
    return analyze(root, definition(response=True))


@pytest.mark.parametrize(
    "payload",
    [
        "<script>alert(1)</script>",
        '</style><img src="https://invalid.test/x" onerror="alert(1)">',
        '<svg onload="alert(1)"><use href="https://invalid.test/x"></use></svg>',
        '<iframe srcdoc="<script>alert(1)</script>"></iframe>',
        '<a href="javascript:alert(1)">click</a><base href="https://invalid.test/">',
        '<form action="https://invalid.test/"><input autofocus onfocus="alert(1)"></form>',
        '{{ cycler.__init__.__globals__ }} & "quotes" < >',
        "شهر آزمایشی تهران",
    ],
)
def test_hostile_public_unicode_remains_inert_escaped_bdi_text(result, payload):
    content = report_module().render_spatial_study_html(public_city_result(result, payload))
    parsed = Document(content)
    assert payload in "".join(parsed.text)
    forbidden = {
        "script",
        "svg",
        "form",
        "iframe",
        "frame",
        "img",
        "object",
        "embed",
        "link",
        "base",
        "input",
        "video",
        "audio",
        "canvas",
        "math",
    }
    assert not ({tag for tag, _ in parsed.elements} & forbidden)
    links = []
    for tag, attrs in parsed.elements:
        assert not any(key.startswith("on") for key in attrs)
        assert "style" not in attrs
        assert not (
            {
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
            & attrs.keys()
        )
        if "href" in attrs:
            links.append((tag, attrs["href"]))
    assert links == [("a", "#main")]
    assert any(tag == "bdi" and attrs.get("dir") == "auto" for tag, attrs in parsed.elements)
    assert content.count(b"<style>") == 1


def test_renderer_never_receives_or_emits_private_source_payloads(result):
    content = report_module().render_spatial_study_html(result).decode("utf-8")
    for forbidden in (
        "target_interests",
        '"price_sensitivity":',
        "person-001",
        "agent_profiles",
        "BEGIN_SIMULATION_DATA",
        "ADLIFE_API_KEY",
        "file://",
        "C:\\",
    ):
        assert forbidden not in content
