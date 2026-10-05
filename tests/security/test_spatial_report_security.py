from __future__ import annotations

from hashlib import sha256

import pytest
from pydantic import BaseModel

from adlife.core.domain.serialization import canonical_json
from adlife.core.experiments._spatial_study_validation import document_sha256, reconstruct_manifest
from adlife.core.experiments.spatial_study import SpatialStudyPairReceipt, SpatialStudyResult
from tests.integration.test_city_spatial_study import analyze, definition, make_runs
from tests.unit.reporting.test_spatial_html import Document, report_module


def public_city_result(result, text):
    # Detached public provenance can change coherently; authenticity still rests
    # on verified loading. This exercises the actual renderer validation boundary.
    city = result.city.model_copy(update={"name": text, "attribution": text})
    document = result.model_dump(mode="python")
    document.update(city=city, city_provenance_sha256=document_sha256(city))
    return SpatialStudyResult.model_validate(document)


def package_version_document(result, version):
    # Regenerate every manifest digest so rejection cannot be mistaken for a
    # stale opaque receipt. Source scalar evidence remains unchanged.
    document = result.model_dump(mode="python")
    document["package_version"] = version
    for pair, fields in zip(result.pairs, document["pairs"], strict=True):
        for name in ("control", "treatment"):
            manifest = reconstruct_manifest(result, pair, getattr(pair, name)).model_dump()
            manifest["package_version"] = version
            fields[name]["manifest_sha256"] = sha256(
                (canonical_json(manifest) + "\n").encode("utf-8")
            ).hexdigest()
    return document


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    root = tmp_path_factory.mktemp("hostile-report")
    make_runs(root, response=True)
    return analyze(root, definition(response=True))


@pytest.mark.parametrize("bypass", [False, True])
@pytest.mark.parametrize(
    "version",
    [
        "0.1.0\rX",
        "0.1.0\nX",
        r"C:\Users\Example\private",
        "ADLIFE_API_KEY=fixture-secret",
        "0.1.0+sk-ant-" + "a" * 20,
    ],
)
def test_report_refuses_unsafe_coherent_package_versions(result, version, bypass):
    document = package_version_document(result, version)
    with pytest.raises(ValueError):
        if bypass:
            copied = BaseModel.model_copy(
                result,
                update={
                    "package_version": version,
                    "pairs": tuple(
                        SpatialStudyPairReceipt.model_validate(pair) for pair in document["pairs"]
                    ),
                },
            )
            report_module().render_spatial_study_html(copied)
        else:
            report_module().render_spatial_study_html(SpatialStudyResult.model_validate(document))


@pytest.mark.parametrize("version", ["0.1.0rc1", "2!1.0.dev2+local.3", "v1.2.post3"])
def test_report_preserves_normal_coherent_pep440_package_versions(result, version):
    checked = SpatialStudyResult.model_validate(package_version_document(result, version))
    content = report_module().render_spatial_study_html(checked)
    assert version in " ".join(Document(content).text)
    assert b"\r" not in content


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
