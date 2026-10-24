"""Static security scan of every guide asset and template (spec 9.3, 5.6, 5.7, D16).

The guide pages run under ``script-src 'self'`` with no inline code, so these scans keep
the sources free of the APIs that would build markup from strings, persist data, change
history, reach the network beyond this server, or bypass the CSP; and they keep the CSS
right-to-left safe by refusing physical properties. Comments are scanned too.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

import pytest

import adlife
from adlife.core.domain.city_cognition_v2 import CityCognitionSelectionPolicyV2

PACKAGE = Path(adlife.__file__).resolve().parent
GUIDE_STATIC = PACKAGE / "city" / "static" / "guide"
GUIDE_TEMPLATES = PACKAGE / "city" / "templates" / "guide"
RUN_REPORT_CSS = PACKAGE / "reporting" / "static" / "run-report.css"
ASSET_ALLOWLIST = frozenset(
    {
        "guide.css",
        "guide-setup.css",
        "guide-story.css",
        "guide-core.js",
        "guide-i18n.js",
        "guide-connection.js",
        "guide-start.js",
        "guide-setup.js",
        "guide-progress.js",
        "guide-story.js",
        "guide-map.js",
    }
)
MAX_ASSET_BYTES = 262_144
MAX_TEMPLATE_BYTES = 65_536

FORBIDDEN_JS_TOKENS = (
    "innerHTML",
    "outerHTML",
    "insertAdjacentHTML",
    "document.write",
    "eval(",
    "new Function",
    "localStorage",
    "sessionStorage",
    "indexedDB",
    "serviceWorker",
    "Worker(",
    "SharedWorker(",
    "caches.",
    "WebSocket(",
    "EventSource(",
    "importScripts(",
    "document.cookie",
    "pushState",
    "replaceState",
    "location.hash",
    "location.search",
    "URLSearchParams",
    'setAttribute("style"',
    "cssText",
    "http://",
    "https://",
    # The rest of the innerHTML family: every API that parses a string as markup.
    "createContextualFragment",
    "DOMParser",
    "parseFromString",
    "setHTMLUnsafe",
    "execCommand",
    # Requests leave only through guide-core.js (fetch with a checked local path).
    "XMLHttpRequest",
    "sendBeacon",
    "window.open",
    "postMessage",
)
FORBIDDEN_CSS_TOKENS = ("@import", "@font-face", "url(", "expression(", "http://", "https://")
PHYSICAL_CSS = (
    re.compile(r"(?<![\w-])(?:margin|padding|border)-(?:left|right)(?:-[a-z]+)*\s*:"),
    re.compile(r"(?<![\w-])(?:left|right)\s*:"),
    re.compile(r"(?<![\w-])float\s*:\s*(?:left|right)\b"),
    re.compile(r"(?<![\w-])text-align\s*:\s*(?:left|right)\b"),
    re.compile(r"(?<![\w-])text-transform\s*:\s*uppercase\b"),
    re.compile(r"(?<![\w-])letter-spacing\s*:"),
)

JS_SIGNATURES = {
    "guide-i18n.js": (
        "export async function initI18n()",
        "export function lang()",
        "export function dir()",
        "export function href(path)",
        "export function t(key, params = {})",
        "export function tn(key, count, params = {})",
        "export const fmt = ",
        "export function makeRefContext({ days, people })",
        "export function resolveNodes(ref, ctx)",
        "export function resolveText(ref, ctx)",
        "export function createCatalog(language, messages)",
    ),
    "guide-core.js": (
        "export function byId(id)",
        "export function meta(name)",
        "export function clear(node)",
        "export function el(tag, { className, text, attrs, dataset } = {}, ...children)",
        'export function bdi(text, dir = "auto")',
        "export class ApiError extends Error",
        "export async function getJson(path)",
        "export async function postJson(path, body)",
        "export function announce(text, { force = false } = {})",
        "export function prefersReducedMotion()",
    ),
}


def _guide_files(suffix: str) -> list[Path]:
    return sorted(path for path in GUIDE_STATIC.glob(f"*{suffix}") if path.is_file())


def _text(path: Path) -> str:
    data = path.read_bytes()
    # A Windows checkout with autocrlf may store CRLF; the template loader normalizes it.
    return data.decode("utf-8", errors="strict").replace("\r\n", "\n")


def _template_files() -> list[Path]:
    return sorted(path for path in GUIDE_TEMPLATES.glob("*.j2") if path.is_file())


def test_foundation_assets_exist_and_every_guide_asset_is_allowlisted() -> None:
    assert (GUIDE_STATIC / "guide-core.js").is_file()
    assert (GUIDE_STATIC / "guide-i18n.js").is_file()
    assert (GUIDE_TEMPLATES / "base.html.j2").is_file()
    assert (GUIDE_TEMPLATES / "error.html.j2").is_file()
    present = {path.name for path in GUIDE_STATIC.iterdir() if path.is_file()}
    assert present <= ASSET_ALLOWLIST, sorted(present - ASSET_ALLOWLIST)
    for path in GUIDE_STATIC.iterdir():
        if path.is_file():
            assert len(path.read_bytes()) <= MAX_ASSET_BYTES, path.name
            _text(path)


@pytest.mark.parametrize("path", _guide_files(".js"), ids=lambda path: path.name)
def test_guide_javascript_avoids_forbidden_apis(path: Path) -> None:
    source = _text(path)
    found = [token for token in FORBIDDEN_JS_TOKENS if token in source]
    assert found == []
    assert re.search(r"setAttribute\(\s*[\"'](?:on|style|srcdoc)", source) is None
    assert re.search(r"\bimport\s*\(", source) is None, "no dynamic imports"
    assert re.search(r"^\s*import\s[^;]*from\s+[\"'](?!\./)", source, re.MULTILINE) is None, (
        "guide modules import only sibling modules"
    )
    assert "javascript:" not in source.lower()


def test_only_the_core_module_sends_requests() -> None:
    # getJson/postJson check every path (/api/ or /guide/assets/, no "..", no other
    # origin) and add the CSRF header, so no other module may call fetch() itself.
    callers = [path.name for path in _guide_files(".js") if re.search(r"\bfetch\s*\(", _text(path))]
    assert callers == ["guide-core.js"]


@pytest.mark.parametrize("path", _guide_files(".css"), ids=lambda path: path.name)
def test_guide_css_is_local_and_uses_logical_properties(path: Path) -> None:
    _assert_safe_css(_text(path))


def test_run_report_css_uses_logical_properties_when_present() -> None:
    if not RUN_REPORT_CSS.is_file():
        pytest.skip("run-report.css is not present yet")
    _assert_safe_css(_text(RUN_REPORT_CSS))


def _assert_safe_css(source: str) -> None:
    lowered = source.lower()
    assert [token for token in FORBIDDEN_CSS_TOKENS if token in lowered] == []
    violations = [match.group(0) for rule in PHYSICAL_CSS for match in rule.finditer(lowered)]
    assert violations == [], "use logical properties (inline-start/end) instead"


@pytest.mark.parametrize("path", _template_files(), ids=lambda path: path.name)
def test_guide_templates_have_no_inline_code_styles_or_handlers(path: Path) -> None:
    data = path.read_bytes()
    assert len(data) <= MAX_TEMPLATE_BYTES
    source = data.decode("utf-8", errors="strict")
    lowered = source.lower()
    assert re.search(r"\sstyle\s*=", lowered) is None
    assert "<style" not in lowered
    assert re.search(r"\son[a-z]+\s*=", lowered) is None
    assert "javascript:" not in lowered
    assert "http://" not in lowered and "https://" not in lowered
    assert "|safe" not in lowered.replace(" ", "")
    assert "autoescape" not in lowered
    assert "markup(" not in lowered
    scripts = re.findall(r"<script\b[^>]*>", lowered)
    if path.name == "base.html.j2":
        assert scripts == ['<script type="module" src="/guide/assets/{{ page_script }}">']
        assert "</script>" in lowered
    else:
        assert scripts == [], "page templates load code only through the base template"


def test_base_template_is_the_csp_safe_page_shell() -> None:
    base = _text(GUIDE_TEMPLATES / "base.html.j2")
    assert base.startswith("<!doctype html>\n")
    assert '<html lang="{{ lang }}" dir="{{ dir }}">' in base
    assert '<meta name="adlife-csrf-token" content="{{ csrf_token }}">' in base
    assert '<link rel="icon" href="/assets/workbench-icon.svg" type="image/svg+xml">' in base
    assert '<link rel="stylesheet" href="/guide/assets/guide.css">' in base
    assert '<a class="g-skip" href="#main">' in base
    assert '<main id="main" class="g-main" tabindex="-1">' in base
    assert (
        '<div id="g-live" class="g-visually-hidden" role="status" aria-live="polite"'
        ' aria-atomic="true"></div>'
    ) in base
    # The guide is the only browser interface: no link leaves it for a removed page.
    assert "technical" not in base
    assert 'target="_blank"' not in base
    assert 'href="/runs/' not in base and 'href="/"' not in base
    assert "{% block main %}{% endblock %}" in base


@pytest.mark.parametrize(
    "path",
    sorted([*GUIDE_TEMPLATES.glob("*.j2"), *GUIDE_STATIC.glob("*.js")]),
    ids=lambda path: path.name,
)
def test_guide_sources_offer_no_technical_view_or_inspector_link(path: Path) -> None:
    # The removed workbench page (/) and saved-run inspector (/runs/...) are gone; the
    # run list still names an inspector_url, which the guide never turns into a link.
    source = _text(path)
    assert "technical" not in source.lower()
    assert "inspector" not in source.lower()
    assert "common.new_tab" not in source and "_blank" not in source
    assert re.search(r'href="/(?:runs/|static/|assets/workbench\.|")', source) is None
    assert "`/runs/" not in source and '"/runs/' not in source


@pytest.mark.parametrize("name", sorted(JS_SIGNATURES))
def test_foundation_modules_export_the_frozen_interface(name: str) -> None:
    source = _text(GUIDE_STATIC / name)
    missing = [signature for signature in JS_SIGNATURES[name] if signature not in source]
    assert missing == []


def _js_constant(path: Path, name: str) -> int:
    match = re.search(rf"\b{name}\s*=\s*(\d+)\s*;", _text(path))
    assert match is not None, f"{path.name} must define {name}"
    return int(match.group(1))


def test_guide_pages_read_the_selection_caps_the_server_publishes() -> None:
    # The AI limit options, defaults, caps and concurrency differ per selection policy, so
    # the pages read them from the capabilities and the job view instead of fixing them.
    setup = _text(GUIDE_STATIC / "guide-setup.js")
    for name in (
        "funded_response_limits",
        "all_noticed_funded_response_limit",
        "default_provider_funded_response_limit",
        "default_local_funded_response_limit",
        "offline_funded_response_limit",
        "selected_requests_per_agent",
        "selected_requests_per_run",
        "selection_strategy",
    ):
        assert f'"{name}"' in setup, name
    assert re.search(r"\b(?:PER_PERSON|CONCURRENCY)\s*=", setup) is None
    progress = _text(GUIDE_STATIC / "guide-progress.js")
    assert "progress.concurrency_limit" in progress
    assert re.search(r"\bCONCURRENCY\s*=", progress) is None
    # The fallbacks are selection policy 2's values, for a server that publishes none.
    (per_agent,) = get_args(
        CityCognitionSelectionPolicyV2.model_fields["selected_requests_per_agent"].annotation
    )
    (per_run,) = get_args(
        CityCognitionSelectionPolicyV2.model_fields["selected_requests_per_run"].annotation
    )
    assert re.search(rf"\bperPerson:\s*{per_agent},", setup)
    assert re.search(rf"\bperRun:\s*{per_run},", setup)
    # Selection policy 2 froze four questions at a time.
    assert _js_constant(GUIDE_STATIC / "guide-progress.js", "FALLBACK_CONCURRENCY") == 4
