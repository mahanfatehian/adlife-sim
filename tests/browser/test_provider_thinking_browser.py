"""Browser operation of the opt-in model-thinking control in the provider-profile form.

A Qwen model on a vLLM server may spend its whole output budget thinking unless the request
carries ``chat_template_kwargs`` ``{"enable_thinking": false}``; OpenAI refuses that member.
The form therefore offers it as an explicit checkbox that defaults on only for a remote
profile whose model ID names Qwen, saves it as part of the immutable revision, and shows it.
"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import Page, expect

from adlife.city.provider_profiles import ProviderProfileRegistry, ProviderProfileRevisionV1
from adlife.city.provider_runtime import build_provider_runtime_policy
from adlife.city.workbench_workspace import prepare_workbench_workspace
from tests.browser.test_bounded_cognition_workbench_browser import (
    BOUNDED,
    REMOTE_REVISION,
    _choose,
    _profile,
    _provider_document,
    _providers,
    _session,
    _to_run_step,
)

LABEL = "Turn off model thinking (Qwen on vLLM servers)"
GAPGPT = build_provider_runtime_policy(provider_origins=("gapgpt=https://api.gapgpt.app/v1",))


def _create_and_capture(page: Page) -> dict[str, object]:
    with page.expect_request(
        lambda request: request.method == "POST" and request.url.endswith("/api/providers")
    ) as created:
        page.locator("#provider-create-submit").click()
    return json.loads(created.value.post_data or "{}")


def _stored(root: Path) -> list[ProviderProfileRevisionV1]:
    return list(ProviderProfileRegistry(prepare_workbench_workspace(root)).list())


def test_thinking_control_defaults_on_for_a_remote_qwen_model_and_is_saved(
    tmp_path: Path,
) -> None:
    with _session(tmp_path, provider_runtime=GAPGPT) as (page, base_url, root):
        _to_run_step(page, base_url)
        _choose(page, BOUNDED, "remote")
        expect(page.locator("#provider-create-details")).to_have_attribute("open", "")
        checkbox = page.get_by_label(LABEL)
        expect(checkbox).to_be_visible()
        expect(checkbox).not_to_be_checked()
        expect(checkbox).to_have_attribute("aria-describedby", "provider-disable-thinking-help")
        help_text = page.locator("#provider-disable-thinking-help")
        expect(help_text).to_contain_text('"enable_thinking": false')
        expect(help_text).to_contain_text("OpenAI")

        model = page.locator("#provider-model")
        for model_id, default_on in (
            ("gapgpt-qwen-3.6", True),
            ("gpt-4o-mini", False),
            ("Qwen/Qwen3.6-35B-A3B-FP8", True),
            ("llama-3.3-70b", False),
            ("gapgpt-qwen-3.6", True),
        ):
            model.fill(model_id)
            if default_on:
                expect(checkbox).to_be_checked()
            else:
                expect(checkbox).not_to_be_checked()

        body = _create_and_capture(page)
        assert body["model_id"] == "gapgpt-qwen-3.6"
        assert body["mode"] == "remote"
        assert body["disable_thinking"] is True
        expect(page.locator("#provider-status")).to_have_text(
            "Profile saved as an immutable revision.", timeout=10_000
        )
        expect(page.locator("#provider-model-receipt")).to_have_text(
            "gapgpt-qwen-3.6 · thinking off"
        )
        expect(page.locator("#provider-profile option:checked")).to_contain_text("thinking off")
        (stored,) = _stored(root)
        assert stored.model_id == "gapgpt-qwen-3.6"
        assert stored.disable_thinking is True


def test_an_explicit_choice_overrides_the_default_and_local_models_default_off(
    tmp_path: Path,
) -> None:
    with _session(tmp_path, provider_runtime=GAPGPT) as (page, base_url, root):
        _to_run_step(page, base_url)
        _choose(page, BOUNDED, "local")
        expect(page.locator("#provider-model")).to_have_value("qwen2.5:7b-instruct")
        checkbox = page.get_by_label(LABEL)
        # A local Qwen model on Ollama keeps today's request unless the operator opts in.
        expect(checkbox).not_to_be_checked()

        page.locator("#cognition-mode").select_option("remote")
        page.locator("#provider-model").fill("gapgpt-qwen-3.6")
        expect(checkbox).to_be_checked()
        checkbox.focus()
        page.keyboard.press("Space")
        expect(checkbox).not_to_be_checked()
        # Once chosen, a later model edit no longer moves the box.
        page.locator("#provider-model").fill("qwen-2.5-72b-instruct")
        expect(checkbox).not_to_be_checked()

        body = _create_and_capture(page)
        assert "disable_thinking" not in body
        expect(page.locator("#provider-status")).to_have_text(
            "Profile saved as an immutable revision.", timeout=10_000
        )
        expect(page.locator("#provider-model-receipt")).to_have_text("qwen-2.5-72b-instruct")
        (stored,) = _stored(root)
        assert stored.disable_thinking is False


def test_listed_profiles_state_whether_thinking_is_turned_off(tmp_path: Path) -> None:
    thinking_off = _profile(
        "gapgpt", "remote", REMOTE_REVISION, label="GapGPT", model="gapgpt-qwen-3.6"
    ) | {"disable_thinking": True}
    plain = _profile("remote-plain", "remote", "e" * 64, label="Plain", model="gpt-4o-mini")
    with _session(tmp_path) as (page, base_url, _root):
        _providers(page, _provider_document(thinking_off, plain))
        _to_run_step(page, base_url)
        _choose(page, BOUNDED, "remote")
        options = page.locator("#provider-profile option")
        expect(options).to_have_count(2)
        expect(options.nth(0)).to_have_text(
            f"GapGPT · gapgpt-qwen-3.6 · thinking off · {REMOTE_REVISION[:10]}"
        )
        expect(options.nth(1)).to_have_text(f"Plain · gpt-4o-mini · {'e' * 10}")
        page.locator("#provider-profile").select_option(index=0)
        expect(page.locator("#provider-model-receipt")).to_have_text(
            "gapgpt-qwen-3.6 · thinking off"
        )
        page.locator("#provider-profile").select_option(index=1)
        expect(page.locator("#provider-model-receipt")).to_have_text("gpt-4o-mini")


def test_a_profile_document_with_a_malformed_thinking_member_is_refused(tmp_path: Path) -> None:
    malformed = _profile("gapgpt", "remote", REMOTE_REVISION, model="gapgpt-qwen-3.6") | {
        "disable_thinking": "yes"
    }
    with _session(tmp_path) as (page, base_url, _root):
        _providers(page, _provider_document(malformed))
        page.goto(base_url, wait_until="networkidle")
        expect(page.locator("#error-banner")).to_be_visible()
        expect(page.locator("#error-banner-message")).to_have_text(
            "The provider profile response is incompatible."
        )


def test_thinking_control_fits_a_phone_width_form_without_overflow(tmp_path: Path) -> None:
    with _session(tmp_path, width=390, provider_runtime=GAPGPT) as (page, base_url, _root):
        _to_run_step(page, base_url)
        _choose(page, BOUNDED, "remote")
        page.locator("#provider-model").fill("gapgpt-qwen-3.6")
        checkbox = page.get_by_label(LABEL)
        expect(checkbox).to_be_checked()
        geometry = page.evaluate(
            """() => {
              const field = document.getElementById('provider-thinking-field');
              const label = field.querySelector('label').getBoundingClientRect();
              return {
                overflow: document.documentElement.scrollWidth - window.innerWidth,
                right: field.getBoundingClientRect().right <= window.innerWidth + 0.5,
                target: label.height >= 40,
              };
            }"""
        )
        assert geometry == {"overflow": geometry["overflow"], "right": True, "target": True}
        assert geometry["overflow"] <= 0
