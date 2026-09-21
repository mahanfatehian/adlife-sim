"""Report resources, loaded through :mod:`importlib.resources` only.

A report must render identically from a source checkout, an installed wheel, or a
frozen binary, so nothing here may resolve a path relative to ``__file__``. The
stylesheet ships with the package; the Plotly runtime comes from the installed
library and is inlined into the report exactly once.
"""

from __future__ import annotations

from importlib import resources


def report_template() -> str:
    """The Jinja2 template source for the report page."""
    return (resources.files("adlife.reporting") / "templates" / "report.html.j2").read_text(
        encoding="utf-8"
    )


def report_css() -> str:
    """The report stylesheet, inlined into the generated page."""
    return (resources.files("adlife.reporting") / "static" / "report.css").read_text(
        encoding="utf-8"
    )


def plotly_runtime() -> str:
    """The Plotly offline JavaScript runtime, inlined once into the report."""
    from plotly.offline import get_plotlyjs  # type: ignore[import-untyped]

    return str(get_plotlyjs())


__all__ = ["plotly_runtime", "report_css", "report_template"]
