"""The report's resources must survive being packaged.

A report rendered on a user's machine reads its template, stylesheet, and demo assets
through :mod:`importlib.resources` - never through paths relative to ``__file__``, so
the same code path works from a source checkout, an installed wheel, or a frozen
binary. The wheel checks run only when a wheel has been built into ``dist/`` (the
verify step does that); the resource checks always run.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from adlife.reporting.resources import report_css, report_template


def test_report_template_loads_through_importlib() -> None:
    template = report_template()
    assert "{{" in template  # it is a Jinja2 template
    assert "synthetic and exploratory" in template.lower()


def test_report_css_loads_through_importlib() -> None:
    css = report_css()
    assert "http" not in css  # no remote fonts or imports
    assert "{" in css


def test_demo_resources_include_campaigns() -> None:
    from importlib import resources

    campaigns = resources.files("adlife.resources.demo") / "campaigns"
    names = {entry.name for entry in campaigns.iterdir()}
    assert "demo-phone.yaml" in names
    assert "demo-billboard.yaml" in names


def test_wheel_contains_report_resources() -> None:
    dist = Path(__file__).resolve().parents[2] / "dist"
    wheels = sorted(dist.glob("*.whl"))
    if not wheels:  # built by the verify step; skip when absent so CI stays fast
        import pytest

        pytest.skip("no wheel in dist/ - run `uv build --no-sources` to exercise this")
        return

    with zipfile.ZipFile(wheels[-1]) as wheel:
        names = wheel.namelist()
    assert any("reporting/templates/report.html.j2" in name for name in names)
    assert any("reporting/static/report.css" in name for name in names)
    assert any("resources/demo/adlife.yaml" in name for name in names)
