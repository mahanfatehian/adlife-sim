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


def test_fictional_persona_templates_load_through_importlib() -> None:
    from importlib import resources

    resource = resources.files("adlife.city").joinpath("persona_templates.json")
    assert resource.is_file(), "packaged persona templates are missing"
    from adlife.city.workbench_personas import load_persona_template_catalog

    catalog = load_persona_template_catalog()
    assert [item.persona_id for item in catalog.templates] == [
        "persona-001",
        "persona-002",
        "persona-003",
        "persona-004",
    ]
    assert all(item.fictional is True for item in catalog.templates)


def test_all_fictional_city_and_creative_choices_load_through_importlib() -> None:
    from importlib import resources

    city_root = resources.files("adlife.city")
    for resource_name in (
        "catalog/fictional-grid-v2.json",
        "catalog/fictional-harbor-loop-v2.json",
        "catalog/fictional-river-junction-v2.json",
        "catalog.json",
        "creative_templates.json",
    ):
        assert city_root.joinpath(resource_name).is_file(), resource_name

    from adlife.city.catalog import load_verified_catalog_cities
    from adlife.city.workbench_creatives import load_creative_template_catalog

    assert [city.entry.city_id for city in load_verified_catalog_cities()] == [
        "fictional-grid-v2",
        "fictional-harbor-loop-v2",
        "fictional-river-junction-v2",
    ]
    assert [item.template_id for item in load_creative_template_catalog().templates] == [
        "fictional-coffee-pass-v1",
        "fictional-crispy-meal-v1",
        "fictional-device-launch-v1",
        "fictional-home-delivery-v1",
    ]


def test_packaged_city_static_assets_include_the_bounded_cognition_inspector() -> None:
    """A saved manifest-V10 run fails closed without its inspector helper and stylesheet."""
    import fnmatch
    import tomllib
    from importlib import resources

    static = resources.files("adlife.city").joinpath("static")
    names = {entry.name for entry in static.iterdir()}
    for asset in ("app-cognition-v2.js", "app-cognition-v2.css"):
        assert asset in names, asset
        assert static.joinpath(asset).read_text(encoding="utf-8").strip(), asset

    # The shared saved-run page loads the helper styles after app.css and the helper
    # script before app.js, so every V10 inspector page can verify its evidence.
    index = static.joinpath("index.html").read_text(encoding="utf-8")
    assert index.index("/static/app.css") < index.index("/static/app-cognition-v2.css")
    assert index.index("/static/app-cognition-v2.js") < index.index("/static/app.js")

    # Without a built wheel, the build configuration must still select both assets.
    pyproject = tomllib.loads(
        (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    )
    patterns = pyproject["tool"]["hatch"]["build"]["include"]
    for asset in ("app-cognition-v2.js", "app-cognition-v2.css"):
        packaged_path = f"src/adlife/city/static/{asset}"
        assert any(fnmatch.fnmatch(packaged_path, pattern) for pattern in patterns), asset


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


def test_spatial_resources_are_lf_pinned_for_identical_cross_platform_csp():
    import subprocess

    result = subprocess.run(
        [
            "git",
            "check-attr",
            "eol",
            "--",
            "src/adlife/reporting/templates/spatial-report.html.j2",
            "src/adlife/reporting/static/spatial-report.css",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert all(line.endswith(": eol: lf") for line in result.stdout.splitlines())
    assert len(result.stdout.splitlines()) == 2
