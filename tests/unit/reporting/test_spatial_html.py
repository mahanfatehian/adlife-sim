from __future__ import annotations

import base64
import importlib
import importlib.util
import re
from hashlib import sha256
from html.parser import HTMLParser
from importlib import resources

import pytest
from pydantic import BaseModel

from tests.integration.test_city_spatial_study import _analyze_v7, analyze, definition, make_runs


def report_module():
    assert importlib.util.find_spec("adlife.reporting.spatial_html") is not None, (
        "the spatial study renderer and publisher are missing"
    )
    return importlib.import_module("adlife.reporting.spatial_html")


class Document(HTMLParser):
    def __init__(self, content: bytes):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self.text = []
        self.rows = []
        self.row = []
        self.cell = None
        self.feed(content.decode("utf-8"))

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))
        if tag == "tr":
            self.row = []
        elif tag in {"td", "th"}:
            self.cell = []
        elif tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr":
            self.rows.append(self.row)

    def handle_data(self, data):
        self.text.append(data)
        if self.cell is not None:
            self.cell.append(data)


@pytest.fixture(scope="module", params=[False, True])
def study_result(request, tmp_path_factory):
    root = tmp_path_factory.mktemp("report-evidence")
    make_runs(root, response=request.param, contrast=True, same=False)
    return analyze(root, definition(response=request.param, contrast=True, same=False))


def test_report_is_deterministic_utf8_lf_with_exact_statistics_and_provenance(study_result):
    renderer = report_module().render_spatial_study_html
    content = renderer(study_result)
    assert type(content) is bytes
    assert content == renderer(study_result)
    assert b"\r" not in content
    assert content.endswith(b"\n")
    assert len(content) < 8 * 1024 * 1024
    parsed = Document(content)
    text = " ".join(parsed.text)
    assert sum(tag == "h1" for tag, _ in parsed.elements) == 1
    assert sum(tag == "main" for tag, _ in parsed.elements) == 1
    assert len(parsed.rows) == len(study_result.statistics) + 1
    for statistic, row in zip(study_result.statistics, parsed.rows[1:], strict=True):
        assert statistic.metric_key in text
        for value in statistic.model_dump().values():
            if isinstance(value, (float, int)) or value is None:
                assert ("null" if value is None else str(value)) in text
        for source in statistic.source_artifacts:
            assert source in text
        assert row[0] == statistic.metric_key + "".join(statistic.source_artifacts)
        assert row[1:] == [
            str(statistic.n_seeds),
            f"Mean: {statistic.mean_paired_difference} "
            f"Median: {statistic.median_paired_difference}",
            f"Sample SD: {statistic.sample_standard_deviation} Standardized difference: "
            + (
                "null"
                if statistic.paired_standardized_difference is None
                else str(statistic.paired_standardized_difference)
            ),
            f"Low: {statistic.bootstrap_ci_low} High: {statistic.bootstrap_ci_high}",
            f"Positive: {statistic.positive_count} Negative: {statistic.negative_count} "
            f"Zero: {statistic.zero_count}",
            f"{statistic.direction} Agreement: {statistic.agreement_fraction}",
        ]
    for pair in study_result.pairs:
        for arm in (pair.control, pair.treatment):
            assert arm.run_id in text
            for field, value in arm.model_dump().items():
                if field != "response":
                    assert str(value) in text
            if arm.response is not None:
                for value in arm.response.model_dump().values():
                    if isinstance(value, str):
                        assert value in text
        assert pair.agents_sha256 in text
        assert pair.trace_sha256 in text
    assert "NaN" not in text and "Infinity" not in text
    assert "control.value" not in text  # Per-seed scalar triples are deliberately omitted.


def test_report_claims_and_attention_response_stages_are_explicit(study_result):
    content = report_module().render_spatial_study_html(study_result)
    text = " ".join(Document(content).text)
    for disclosure in (
        "Synthetic",
        "exploratory",
        "unobserved",
        "non-causal",
        "not sales",
        "simulator seed variation",
        "not population confidence intervals",
        "Assumptions",
        "Reproduction",
        "Limitations",
        "Opportunity",
        "Impression",
        "Notice",
        "Rule response",
        "Campaign state proxy",
        "0.5",
    ):
        assert disclosure in text
    if study_result.definition.analysis_scope == "attention":
        assert text.count("Not analyzed") >= 2
    else:
        for disclosure in (
            "Every notice mechanically produces one rule response",
            "not engagement",
            "planned rule deltas",
            "committed bounded state changes",
            "uncalibrated internal proxy",
            "not a clean creative effect",
            "not attributed to channels",
            "0.55",
            "0.18",
            "math.prod",
            "no daily decay",
        ):
            assert disclosure in text


def test_schema_v7_report_discloses_response_enabled_schema_scope(tmp_path) -> None:
    result, _ = _analyze_v7(tmp_path)

    text = " ".join(Document(report_module().render_spatial_study_html(result)).text)

    assert "schema v6 or v7" in text
    assert "workbench_input_sha256" in text
    assert result.pairs[0].control.workbench_input_sha256 in text


def test_csp_authorizes_only_exact_packaged_lf_css_before_style(study_result):
    content = report_module().render_spatial_study_html(study_result).decode("utf-8")
    styles = re.findall(r"<style>(.*?)</style>", content, re.S)
    assert len(styles) == 1
    css = (
        (resources.files("adlife.reporting") / "static" / "spatial-report.css")
        .read_text(encoding="utf-8")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )
    assert styles[0] == css
    encoded = base64.b64encode(sha256(css.encode("utf-8")).digest()).decode("ascii")
    meta = next(
        attrs
        for tag, attrs in Document(content.encode()).elements
        if tag == "meta" and attrs.get("http-equiv") == "Content-Security-Policy"
    )
    assert meta["content"] == (
        "default-src 'none'; script-src 'none'; style-src 'sha256-" + encoded + "'; "
        "img-src 'none'; font-src 'none'; connect-src 'none'; media-src 'none'; "
        "object-src 'none'; frame-src 'none'; child-src 'none'; worker-src 'none'; "
        "base-uri 'none'; form-action 'none'"
    )
    assert content.index("Content-Security-Policy") < content.index("<style>")
    assert not re.search(r"url\s*\(|@import|@font-face", css, re.I)
    assert "@media print" in css and "forced-colors: active" in css


@pytest.mark.parametrize("mutation", ["count", "statistic", "nan", "definition"])
def test_renderer_revalidates_bypass_copies_before_emission(study_result, mutation):
    module = report_module()
    updates = {"opportunity_matched_pair_count": 100}
    if mutation in {"statistic", "nan"}:
        statistic = BaseModel.model_copy(
            study_result.statistics[0],
            update={
                "mean_paired_difference": float("nan") if mutation == "nan" else 100.0,
            },
        )
        updates = {"statistics": (statistic, *study_result.statistics[1:])}
    elif mutation == "definition":
        updates = {
            "definition": BaseModel.model_copy(
                study_result.definition, update={"study_id": "../escape"}
            )
        }
    bad = BaseModel.model_copy(study_result, update=updates)
    with pytest.raises(ValueError):
        module.render_spatial_study_html(bad)


def test_report_byte_ceiling_refuses_instead_of_truncating(study_result, monkeypatch):
    module = report_module()
    content = module.render_spatial_study_html(study_result)
    monkeypatch.setattr(module, "MAX_SPATIAL_REPORT_BYTES", len(content))
    assert module.render_spatial_study_html(study_result) == content
    monkeypatch.setattr(module, "MAX_SPATIAL_REPORT_BYTES", len(content) - 1)
    with pytest.raises(ValueError, match="size limit"):
        module.render_spatial_study_html(study_result)


def test_dedicated_template_refuses_missing_context(study_result, monkeypatch):
    module = report_module()
    real_files = module.resources.files

    class MissingTemplate:
        def __truediv__(self, item):
            return self

        def read_text(self, **kwargs):
            return "{{ absent_field }}"

    monkeypatch.setattr(module.resources, "files", lambda package: MissingTemplate())
    from jinja2 import UndefinedError

    with pytest.raises(UndefinedError):
        module.render_spatial_study_html(study_result)
    monkeypatch.setattr(module.resources, "files", real_files)


def test_maximum_100_pair_328_statistic_report_keeps_every_provenance_receipt(
    tmp_path, monkeypatch
):
    from tests.unit.reporting.spatial_report_fixtures import maximum_report_result

    result = maximum_report_result(tmp_path, monkeypatch)
    assert len(result.pairs) == 100 and len(result.statistics) == 328
    assert all(len(pair.scalars) == 328 for pair in result.pairs)
    renderer = report_module().render_spatial_study_html
    first, second = renderer(result), renderer(result)
    print(f"maximum spatial HTML: {len(first)} bytes; SHA-256 {sha256(first).hexdigest()}")
    assert first == second and len(first) < 8_388_608
    parsed = Document(first)
    text = " ".join(parsed.text)
    assert sum(tag == "article" for tag, _ in parsed.elements) == 100
    assert sum(tag == "th" and attrs.get("scope") == "row" for tag, attrs in parsed.elements) == 328
    for pair in result.pairs:
        assert pair.control.run_id in text and pair.control.manifest_sha256 in text
        assert pair.control.response.response_metrics_sha256 in text
    for statistic in result.statistics:
        assert statistic.metric_key in text
