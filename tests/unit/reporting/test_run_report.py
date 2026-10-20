"""The static HTML run report: one story, three languages, two variants, no JavaScript."""

from __future__ import annotations

import base64
import importlib
import importlib.util
import json
import re
from collections import Counter
from hashlib import sha256
from html.parser import HTMLParser
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from adlife.reporting.run_story_model import RunStoryV1

# --- a coherent synthetic story ------------------------------------------------------------

LABELLED = ("answered", "reused", "practice", "unsure", "warning")
TERMINAL = ("answered", "reused", "practice", "unsure", "warning", "failed", "over_limit")
SENT = ("answered", "reused", "unsure", "warning", "failed")
AFFECT = ("annoyed", "skeptical", "neutral", "curious", "positive", "uncertain")
RELEVANCE = ("none", "low", "medium", "high", "uncertain")
CREDIBILITY = ("low", "medium", "high", "uncertain")
SALIENCE = ("none", "low", "medium", "high", "uncertain")
FLAGS = ("stereotype-risk", "unsupported-inference", "unsafe-content")
LABELS = {
    "answered": ("skeptical", "none", "low", "low"),
    "reused": ("curious", "high", "medium", "medium"),
    "practice": ("neutral", "medium", "medium", "low"),
    "unsure": ("uncertain", "low", "medium", "low"),
    "warning": ("annoyed", "low", "low", "high"),
}
APPLIED = {"answered", "reused", "practice"}
# Remote plan: every status, a person never shown, one never noticed, and one person with
# more ad moments than the report lists (plus moments the builder already omitted).
REMOTE_PLAN: dict[str, list[str]] = {
    "person-001": ["answered", "not_noticed", "reused", "over_limit"],
    "person-002": ["not_noticed", "answered"],
    "person-003": [],
    "person-004": ["not_noticed", "not_noticed"],
    "person-005": [
        "answered",
        "failed",
        "unsure",
        "warning",
        "not_chosen",
        "not_noticed",
        "not_chosen",
        "not_noticed",
        "not_noticed",
        "not_noticed",
    ],
    "person-006": ["answered"],
    "person-007": ["over_limit", "not_noticed"],
    "person-008": ["reused", "answered"],
}
OMITTED = {"person-005": 3}
ACTIVITIES = ("home", "commute", "leisure", "home", "work")


def _hex(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _plan_for_mode(mode: str) -> dict[str, list[str]]:
    plan = {agent: list(statuses) for agent, statuses in REMOTE_PLAN.items()}
    for agent, statuses in plan.items():
        if mode == "mock":
            plan[agent] = ["practice" if status in TERMINAL else status for status in statuses]
        elif mode == "rules":
            plan[agent] = ["rules" if status in TERMINAL else status for status in statuses]
    return plan


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _r6(value: float) -> float:
    return round(value, 6) + 0.0


def _effect(applied: float, rule: float) -> str:
    difference = applied - rule
    if abs(difference) < 0.005:
        return "same"
    return "higher" if difference > 0 else "lower"


def _compare(with_ai: float, rules: float) -> str:
    difference = with_ai - rules
    if abs(difference) < 0.005:
        return "about_same"
    if abs(difference) < 0.03:
        return "slightly_higher" if difference > 0 else "slightly_lower"
    return "higher" if difference > 0 else "lower"


def _num(value: float) -> dict[str, Any]:
    return {"num": value, "decimals": 2}


def _state_line(key: str, before: tuple[float, float], after: tuple[float, float]):
    return {
        "key": key,
        "params": {
            "liking_before": _num(before[0]),
            "liking_after": _num(after[0]),
            "memory_before": _num(before[1]),
            "memory_after": _num(after[1]),
        },
    }


def _ai_clause(status: str, limit: int) -> dict[str, Any]:
    if status == "warning":
        return {
            "key": "story.ai.warning",
            "params": {"flags": {"list": [{"key": "flag.stereotype-risk"}]}},
        }
    if status == "failed":
        return {"key": "story.ai.failed", "params": {"reason": {"key": "reason.timeout-exhausted"}}}
    if status == "over_limit":
        return {"key": "story.ai.over_limit", "params": {"limit": {"int": limit}}}
    if status == "not_chosen":
        return {
            "key": "story.ai.not_chosen",
            "params": {"per_person": {"int": 4}, "per_run": {"int": 60}},
        }
    return {"key": f"story.ai.{status}"}


def _model_param(model_id: str) -> dict[str, str]:
    if len(model_id) <= 120 and all(" " <= character <= "~" for character in model_id):
        return {"id": model_id}
    return {"text": model_id[:240]}


def _mode_value(values: list[str], order: tuple[str, ...]) -> str:
    counts = Counter(values)
    return max(order, key=lambda value: (counts[value], -order.index(value)))


def story_document(
    *,
    mode: str = "remote",
    model_id: str = "gapgpt-qwen-3.6",
    custom_name: str | None = None,
    scenario_name: str = "Guided demo study - Lumen Pocket",
    campaign_name: str = "Lumen Pocket campaign",
    product: str = "Lumen Pocket",
) -> dict[str, Any]:
    """A complete, internally coherent ``RunStoryV1`` document (spec 8.3 shapes)."""
    provider_mode = mode in {"local", "remote"}
    limit = 10 if provider_mode else 60
    plan = _plan_for_mode(mode)
    owners = {f"person-00{n}": "persona-001" for n in range(1, 5)}
    owners.update({f"person-00{n}": "persona-002" for n in range(5, 9)})
    if custom_name is not None:
        plan["person-009"] = ["answered" if provider_mode else plan["person-006"][0], "not_noticed"]
        owners["person-009"] = "persona-005"
    people = []
    key_candidates = []
    all_moments = []
    for index, (agent_id, statuses) in enumerate(sorted(plan.items())):
        number = int(agent_id[-3:])
        veyra = owners[agent_id] == "persona-002"
        rules_state = [-0.2 if veyra else 0.05, 0.1, 0.05]
        ai_state = [rules_state[0], rules_state[1]]
        start = {
            "rules": {"liking": rules_state[0], "memory": 0.1, "purchase_interest": 0.05},
            "with_ai": {"liking": rules_state[0], "memory": 0.1},
        }
        moments = []
        labelled: list[tuple[str, str, str, str]] = []
        for position, status in enumerate(statuses):
            minute = 420 + 37 * position + 3 * index
            moment_id = _hex(f"{agent_id}-{position}")[:16]
            noticed = status != "not_noticed"
            activity = ACTIVITIES[position % len(ACTIVITIES)]
            sentence = {
                "key": "story.moment.noticed" if noticed else "story.moment.missed",
                "params": {
                    "time": {"clock": minute},
                    "name": {"person": agent_id},
                    "product": {"text": product},
                    "where": {"key": f"story.where.mobile-feed.{activity}"},
                },
            }
            moment: dict[str, Any] = {
                "moment_id": moment_id,
                "minute": minute,
                "millisecond": 0,
                "day_index": 0,
                "channel": "mobile-feed",
                "activity": activity,
                "position": [0.01 * position, 0.03],
                "noticed": noticed,
                "status": status,
                "labels": None,
                "safety_flags": [],
                "reason": "timeout-exhausted" if status == "failed" else None,
                "before": None,
                "after": None,
                "rule_delta": None,
                "applied_delta": None,
                "effect": None,
                "math": None,
                "sentence": sentence,
                "ai_clause": None,
                "rules_line": None,
                "with_ai_line": None,
            }
            if noticed:
                rule_delta = (-0.0213 if veyra else 0.0312, 0.1702)
                applied = (
                    (rule_delta[0] + 0.006, round(rule_delta[1] * 0.19, 6))
                    if status in APPLIED
                    else rule_delta
                )
                before = {
                    "rules": {
                        "liking": _r6(rules_state[0]),
                        "memory": _r6(rules_state[1]),
                        "purchase_interest": _r6(rules_state[2]),
                    },
                    "with_ai": {"liking": _r6(ai_state[0]), "memory": _r6(ai_state[1])},
                }
                rules_state[0] = _clamp(rules_state[0] + rule_delta[0], -1, 1)
                rules_state[1] = 1 - (1 - rules_state[1]) * (1 - rule_delta[1])
                rules_state[2] = _clamp(rules_state[2] + 0.04, 0, 1)
                ai_state[0] = _clamp(ai_state[0] + applied[0], -1, 1)
                ai_state[1] = 1 - (1 - ai_state[1]) * (1 - applied[1])
                after = {
                    "rules": {
                        "liking": _r6(rules_state[0]),
                        "memory": _r6(rules_state[1]),
                        "purchase_interest": _r6(rules_state[2]),
                    },
                    "with_ai": {"liking": _r6(ai_state[0]), "memory": _r6(ai_state[1])},
                }
                effect = {
                    "liking": _effect(applied[0], rule_delta[0]),
                    "memory": _effect(applied[1], rule_delta[1]),
                }
                same = effect["liking"] == "same" and effect["memory"] == "same"
                moment.update(
                    before=before,
                    after=after,
                    rule_delta={"liking": rule_delta[0], "memory": rule_delta[1]},
                    applied_delta={"liking": _r6(applied[0]), "memory": _r6(applied[1])},
                    effect=effect,
                    ai_clause=_ai_clause(status, limit),
                    rules_line=_state_line(
                        "story.moment.rules_line",
                        (before["rules"]["liking"], before["rules"]["memory"]),
                        (after["rules"]["liking"], after["rules"]["memory"]),
                    ),
                    with_ai_line=(
                        {"key": "story.moment.with_ai_same"}
                        if same
                        else _state_line(
                            "story.moment.with_ai_line",
                            (before["with_ai"]["liking"], before["with_ai"]["memory"]),
                            (after["with_ai"]["liking"], after["with_ai"]["memory"]),
                        )
                    ),
                )
            if status in LABELLED:
                values = LABELS[status]
                labelled.append(values)
                moment["labels"] = dict(
                    zip(("affect", "relevance", "credibility", "salience"), values, strict=True)
                )
                if status == "warning":
                    moment["safety_flags"] = ["stereotype-risk"]
            moments.append(moment)
            all_moments.append((minute, 0, agent_id, moment_id, status))
        counts = Counter(statuses)
        noticed_count = sum(status != "not_noticed" for status in statuses)
        chosen = sum(status in TERMINAL or status == "rules" for status in statuses)
        sent = sum(status in SENT for status in statuses) if provider_mode else 0
        omitted = OMITTED.get(agent_id, 0)
        shown = len(statuses) + omitted
        if shown == 0:
            summary: dict[str, Any] = {"key": "story.person.never_shown"}
        elif noticed_count == 0:
            summary = {"key": "story.person.never_noticed", "count": shown}
        else:
            summary = {
                "key": "story.person.noticed",
                "count": shown,
                "params": {"noticed": {"int": noticed_count}, "sent": {"int": sent}},
            }
        end_rules = {
            "liking": _r6(rules_state[0]),
            "memory": _r6(rules_state[1]),
            "purchase_interest": _r6(rules_state[2]),
        }
        end_ai = {"liking": _r6(ai_state[0]), "memory": _r6(ai_state[1])}
        label_mode = None
        if labelled:
            label_mode = {
                name: _mode_value([values[slot] for values in labelled], order)
                for slot, (name, order) in enumerate(
                    (
                        ("affect", AFFECT),
                        ("relevance", RELEVANCE),
                        ("credibility", CREDIBILITY),
                        ("salience", SALIENCE),
                    )
                )
            }
        people.append(
            {
                "agent_id": agent_id,
                "number": number,
                "persona_id": owners[agent_id],
                "anchors": {"home": "west-center", "work": "center-north", "leisure": "east-park"},
                "days": [
                    {
                        "day_index": 0,
                        "destination": "work",
                        "outbound": [[480.0, 0.0, 0.03], [498.0, 0.04, 0.06]],
                        "inbound": [[1020.0, 0.04, 0.06], [1038.0, 0.0, 0.03]],
                    }
                ],
                "totals": {
                    "shown": shown,
                    "noticed": noticed_count,
                    "chosen": chosen,
                    "sent": sent,
                    "used": counts["answered"] + counts["reused"] + counts["practice"],
                    "set_aside": counts["unsure"] + counts["warning"],
                    "failed": counts["failed"],
                    "over_limit": counts["over_limit"],
                    "not_chosen": counts["not_chosen"],
                },
                "start": start,
                "end": {"rules": end_rules, "with_ai": end_ai},
                "compare": {
                    "liking": _compare(end_ai["liking"], end_rules["liking"]),
                    "memory": _compare(end_ai["memory"], end_rules["memory"]),
                },
                "label_mode": label_mode,
                "label_answers": len(labelled),
                "summary": summary,
                "moments": moments,
                "moments_omitted": omitted,
            }
        )
        key_candidates.extend(
            (minute, 0, agent_id, moment_id, status)
            for minute, _ms, owner, moment_id, status in all_moments
            if owner == agent_id
        )
    statuses_all = Counter(status for *_rest, status in all_moments if status != "not_noticed")
    noticed_total = sum(statuses_all.values())
    not_chosen = statuses_all["not_chosen"]
    chosen_total = noticed_total - not_chosen
    sent_total = sum(statuses_all[status] for status in SENT) if provider_mode else 0
    answered_records = (
        statuses_all["answered"]
        + statuses_all["reused"]
        + statuses_all["unsure"]
        + statuses_all["warning"]
        if provider_mode
        else 0
    )
    labelled_all = [LABELS[status] for *_rest, status in all_moments if status in LABELLED]
    labels = None
    if answered_records + statuses_all["practice"]:
        labels = {
            "group": "practice" if mode == "mock" else "ai",
            "answers": len(labelled_all),
            **{
                name: {value: sum(item[slot] == value for item in labelled_all) for value in order}
                for slot, (name, order) in enumerate(
                    (
                        ("affect", AFFECT),
                        ("relevance", RELEVANCE),
                        ("credibility", CREDIBILITY),
                        ("salience", SALIENCE),
                    )
                )
            },
            "safety_flags": {
                flag: statuses_all["warning"] if flag == "stereotype-risk" else 0 for flag in FLAGS
            },
        }
    ordered = sorted(key_candidates)
    if mode == "rules":
        selected = [item for item in ordered if item[4] != "not_noticed"][:20]
        firsts: tuple[str, ...] = ("not_noticed",)
    else:
        selected = [
            item
            for item in ordered
            if item[4] in {"answered", "reused", "practice", "unsure", "warning", "failed"}
        ]
        firsts = ("over_limit", "not_chosen", "not_noticed")
    for status in firsts:
        first = next((item for item in ordered if item[4] == status), None)
        if first is not None and first not in selected:
            selected.append(first)
    key_moments = [
        {"agent_id": agent_id, "moment_id": moment_id}
        for _minute, _ms, agent_id, moment_id, _status in sorted(selected)
    ]
    shown_total = sum(person["totals"]["shown"] for person in people)
    rules_liking = (-0.075, -0.088)
    ai_liking = (-0.075, -0.083 if mode != "rules" else -0.088)
    averages = {
        "rules": {
            "liking": {"start": rules_liking[0], "end": rules_liking[1]},
            "memory": {"start": 0.1, "end": 0.274},
            "purchase_interest": {"start": 0.05, "end": 0.32},
        },
        "with_ai": {
            "liking": {"start": ai_liking[0], "end": ai_liking[1]},
            "memory": {"start": 0.1, "end": 0.19 if mode != "rules" else 0.274},
        },
        "compare": {
            "liking": _compare(ai_liking[1], rules_liking[1]),
            "memory": _compare(0.19 if mode != "rules" else 0.274, 0.274),
        },
    }
    headline: list[dict[str, Any]] = [
        {
            "key": "story.headline.people",
            "params": {
                "people": {"key": "common.people", "count": len(people)},
                "days": {"key": "common.days", "count": 1},
                "city": {"key": "city.fictional-grid-v2"},
            },
        },
        {
            "key": "story.headline.shown",
            "count": shown_total,
            "params": {"product": {"text": product}},
        },
        {"key": "story.headline.noticed", "count": noticed_total},
    ]
    if mode == "rules":
        headline.append({"key": "story.headline.rules"})
    else:
        if mode == "mock":
            headline.append({"key": "story.headline.practice", "count": statuses_all["practice"]})
        else:
            headline.append(
                {
                    "key": "story.headline.ai",
                    "count": answered_records,
                    "params": {"model": _model_param(model_id)},
                }
            )
        compare = averages["compare"]
        headline.append({"key": f"story.headline.compare.liking.{compare['liking']}"})
        if compare["memory"] != "about_same":
            headline.append({"key": f"story.headline.compare.memory.{compare['memory']}"})
    personas = [
        {
            "persona_id": "persona-001",
            "template_key": "lumo",
            "display_name": "Fictional Lumo",
            "count": 4,
            "interests": ["commuting", "technology"],
            "trait_chips": [
                {"trait": "novelty_seeking", "level": "high"},
                {"trait": "mobile_recall_encoding", "level": "high"},
                {"trait": "advertising_skepticism", "level": "low"},
            ],
            "color_index": 0,
            "agent_ids": [f"person-00{n}" for n in range(1, 5)],
        },
        {
            "persona_id": "persona-002",
            "template_key": "veyra",
            "display_name": "Fictional Veyra",
            "count": 4,
            "interests": ["food", "cooking"],
            "trait_chips": [
                {"trait": "impulsivity", "level": "low"},
                {"trait": "price_sensitivity", "level": "high"},
                {"trait": "advertising_skepticism", "level": "high"},
            ],
            "color_index": 1,
            "agent_ids": [f"person-00{n}" for n in range(5, 9)],
        },
    ]
    if custom_name is not None:
        personas.append(
            {
                "persona_id": "persona-005",
                "template_key": None,
                "display_name": custom_name,
                "count": 1,
                "interests": ["books", "stargazing"],
                "trait_chips": [],
                "color_index": 4,
                "agent_ids": ["person-009"],
            }
        )
    statuses_doc = {
        status: statuses_all[status]
        for status in (
            "answered",
            "reused",
            "practice",
            "rules",
            "unsure",
            "warning",
            "failed",
            "over_limit",
            "not_chosen",
        )
    }
    return {
        "schema_version": 1,
        "format": "adlife-run-story-v1",
        "run": {
            "run_id": "guided-device-1020-1405",
            "seed": "42",
            "days": 1,
            "people_count": len(people),
            "city": {
                "city_id": "fictional-grid-v2",
                "name": "Fictional Grid City V2",
                "catalog_key": "fictional-grid-v2",
            },
            "scenario": {"scenario_id": "guided-device", "name": scenario_name},
            "campaign": {
                "campaign_id": "fictional-launch",
                "name": campaign_name,
                "target_interests": ["commuting", "technology"],
                "relative_price": 1.25,
            },
            "creative": {
                "template_id": "fictional-device-launch-v1",
                "catalog_key": "fictional-device-launch-v1",
                "product_name": product,
                "product_category": "fictional consumer device",
                "message": "A fictional compact device concept for a synthetic commute.",
                "call_to_action": "Explore the fictional concept",
            },
            "channels": ["mobile-feed"],
            "phone": {
                "windows": [{"day": 1, "start_minute": 420, "end_minute": 1260}],
                "cap": 4,
                "eligible_activities": ["commute", "home", "leisure"],
                "chance_per_minute": 0.01,
            },
            "billboard": None,
            "hashes": {
                "workbench_input_sha256": _hex("input"),
                "cognition_input_sha256": _hex("cognition"),
                "causal_contract_sha256": _hex("contract"),
            },
        },
        "ai": {
            "mode": mode,
            "model_id": model_id if provider_mode else None,
            "provider_profile_id": "guide-gapgpt" if provider_mode else None,
            "provider_revision_sha256": _hex("revision") if provider_mode else None,
            "thinking_off": provider_mode,
            "funded_limit": limit,
            "per_person": 4,
            "per_run": 60,
            "coverage": {
                "noticed": noticed_total,
                "chosen": chosen_total,
                "sent": sent_total,
                "over_limit": statuses_all["over_limit"],
                "not_chosen": not_chosen,
            },
            "statuses": statuses_doc,
            "fallback_reasons": (
                {"timeout-exhausted": statuses_all["failed"]} if statuses_all["failed"] else {}
            ),
            "usage_this_run": {
                "attempts": sent_total + 2 if provider_mode else 0,
                "prompt_tokens": 1480 * sent_total,
                "completion_tokens": 130 * sent_total,
            },
            "reused_answers": statuses_all["reused"],
            "contract": {
                "prompt_contract_id": "city-cognition-signal-prompt-v2",
                "prompt_contract_sha256": _hex("prompt"),
                "result_schema_id": "city-cognition-signal-result-v2",
                "influence_policy_id": "city-cognition-influence-v1",
                "influence_policy_sha256": _hex("policy"),
            },
        },
        "funnel": {
            "opportunities": shown_total,
            "shown": shown_total,
            "noticed": noticed_total,
            "chosen": chosen_total,
            "sent": sent_total,
            "answered": answered_records,
            "used": statuses_all["answered"] + statuses_all["reused"] + statuses_all["practice"],
        },
        "labels": labels,
        "averages": averages,
        "headline": headline,
        "map": {
            "bounds": {"west": 0.0, "south": 0.0, "east": 0.05, "north": 0.06},
            "nodes": [
                {"node_id": "west-center", "longitude": 0.0, "latitude": 0.03},
                {"node_id": "center-north", "longitude": 0.04, "latitude": 0.06},
            ],
            "roads": [
                {"road_id": "middle-west", "kind": "primary", "points": [[0.0, 0.03], [0.04, 0.06]]}
            ],
        },
        "personas": personas,
        "people": people,
        "key_moments": key_moments,
        "limits": {"moments_per_person": 40},
    }


def make_story(**options: Any) -> RunStoryV1:
    # Strict models take JSON arrays as tuples, exactly as the story API serves them.
    return RunStoryV1.model_validate_json(json.dumps(story_document(**options)))


# --- helpers -------------------------------------------------------------------------------


def report_module():
    assert importlib.util.find_spec("adlife.reporting.run_report") is not None, (
        "the run report renderer is missing"
    )
    return importlib.import_module("adlife.reporting.run_report")


def links_for(run_id: str, language: str):
    module = report_module()
    return module.RunReportLinks(
        story=f"/guide/runs/{run_id}?lang={language}",
        technical=f"/runs/{run_id}",
        download=f"/guide/runs/{run_id}/report/download?lang={language}",
        languages=tuple(
            (code, f"/guide/runs/{run_id}/report?lang={code}") for code in ("en", "de", "fa")
        ),
    )


def render(story: RunStoryV1, language: str = "en", *, view: bool = False):
    links = links_for(story.run.run_id, language) if view else None
    return report_module().render_run_report_html(story, language=language, links=links)


class Page(HTMLParser):
    """Elements, text and the id-scoped HTML of a rendered report."""

    def __init__(self, content: bytes):
        super().__init__(convert_charrefs=True)
        self.elements: list[tuple[str, dict[str, str | None]]] = []
        self.text: list[str] = []
        self.feed(content.decode("utf-8"))

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)

    def ids(self) -> list[str]:
        return [attrs["id"] for _, attrs in self.elements if attrs.get("id")]

    def all_text(self) -> str:
        return " ".join("".join(self.text).split())


def person_blocks(content: bytes) -> dict[str, str]:
    html = content.decode("utf-8")
    blocks = re.findall(
        r'<section class="person" id="(person-\d{3})"(.*?)</section>', html, flags=re.S
    )
    return dict(blocks)


PERSIAN_DIGITS = str.maketrans("0123456789", "".join(chr(0x06F0 + i) for i in range(10)))


def english_messages() -> dict[str, Any]:
    source = resources.files("adlife.i18n").joinpath("catalogs", "en.json")
    return json.loads(source.read_text(encoding="utf-8"))["messages"]


@pytest.fixture(scope="module")
def remote_story() -> RunStoryV1:
    return make_story()


# --- tests ---------------------------------------------------------------------------------


def test_synthetic_story_factory_is_a_valid_run_story_in_every_mode():
    for mode in ("remote", "local", "mock", "rules"):
        story = make_story(mode=mode)
        assert story.ai.mode == mode
    assert make_story(custom_name="Kiro").personas[-1].template_key is None


@pytest.mark.parametrize("language", ["en", "de", "fa"])
@pytest.mark.parametrize("view", [False, True])
def test_report_bytes_are_deterministic_utf8_lf(remote_story, language, view):
    first = render(remote_story, language, view=view)
    second = render(remote_story, language, view=view)
    assert type(first.content) is bytes
    assert first.content == second.content
    assert first.content_security_policy == second.content_security_policy
    assert b"\r" not in first.content
    assert first.content.endswith(b"\n")
    first.content.decode("utf-8")
    assert first.content.startswith(b"<!doctype html>\n")


@pytest.mark.parametrize("view", [False, True])
def test_csp_hash_authorises_exactly_the_packaged_stylesheet(remote_story, view):
    rendered = render(remote_story, "en", view=view)
    html = rendered.content.decode("utf-8")
    styles = re.findall(r"<style>(.*?)</style>", html, flags=re.S)
    assert len(styles) == 1
    css = (
        resources.files("adlife.reporting")
        .joinpath("static", "run-report.css")
        .read_text(encoding="utf-8")
        .replace("\r\n", "\n")
    )
    assert styles[0] == css
    digest = base64.b64encode(sha256(css.encode("utf-8")).digest()).decode("ascii")
    csp = rendered.content_security_policy
    assert f"style-src 'sha256-{digest}'" in csp
    assert "script-src 'none'" in csp
    assert "default-src 'none'" in csp
    assert "frame-ancestors" not in csp
    metas = [
        attrs
        for tag, attrs in Page(rendered.content).elements
        if tag == "meta" and attrs.get("http-equiv") == "Content-Security-Policy"
    ]
    # The parsed attribute (what the browser enforces) is exactly the returned policy.
    assert [attrs.get("content") for attrs in metas] == [csp]
    assert ("img-src 'self'" in csp) is view
    assert ("img-src 'none'" in csp) is not view
    for directive in (
        "font-src 'none'",
        "connect-src 'none'",
        "media-src 'none'",
        "object-src 'none'",
        "frame-src 'none'",
        "child-src 'none'",
        "worker-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
    ):
        assert directive in csp


def test_view_variant_links_languages_story_download_and_technical_view(remote_story):
    rendered = render(remote_story, "fa", view=True)
    page = Page(rendered.content)
    hrefs = [attrs.get("href") for tag, attrs in page.elements if tag == "a"]
    run_id = remote_story.run.run_id
    for expected in (
        f"/guide/runs/{run_id}?lang=fa",
        f"/runs/{run_id}",
        f"/guide/runs/{run_id}/report/download?lang=fa",
        f"/guide/runs/{run_id}/report?lang=en",
        f"/guide/runs/{run_id}/report?lang=de",
        f"/guide/runs/{run_id}/report?lang=fa",
    ):
        assert expected in hrefs, expected
    icons = [attrs for tag, attrs in page.elements if tag == "link"]
    assert icons == [{"rel": "icon", "href": "/assets/workbench-icon.svg", "type": "image/svg+xml"}]
    languages = {
        attrs.get("hreflang"): attrs
        for tag, attrs in page.elements
        if tag == "a" and attrs.get("hreflang")
    }
    assert set(languages) == {"en", "de", "fa"}
    assert languages["fa"].get("aria-current") == "true"
    assert all(languages[code].get("lang") == code for code in languages)
    technical = [
        attrs
        for tag, attrs in page.elements
        if tag == "a" and attrs.get("href") == f"/runs/{run_id}"
    ]
    assert technical and all(
        attrs.get("target") == "_blank" and attrs.get("rel") == "noopener noreferrer"
        for attrs in technical
    )


def test_standalone_variant_has_only_in_page_anchors_and_no_images(remote_story):
    rendered = render(remote_story, "en")
    page = Page(rendered.content)
    hrefs = [attrs.get("href") for _tag, attrs in page.elements if "href" in attrs]
    assert hrefs and all(href is not None and href.startswith("#") for href in hrefs)
    targets = {href[1:] for href in hrefs if href}
    assert targets <= set(page.ids())
    assert not any(tag in {"link", "img", "script"} for tag, _ in page.elements)
    assert "img-src 'none'" in rendered.content_security_policy
    # Only the download variant says that it is self-contained.
    standalone = english_messages()["report.standalone"]
    assert standalone in page.all_text()
    assert standalone not in Page(render(remote_story, "en", view=True).content).all_text()


@pytest.mark.parametrize("language", ["en", "de", "fa"])
def test_all_ten_sections_are_present_in_order(remote_story, language):
    page = Page(render(remote_story, language).content)
    tags = [tag for tag, _ in page.elements]
    assert tags.count("h1") == 1
    assert tags.count("main") == 1
    assert tags.count("header") >= 1 and tags.count("footer") == 1
    ids = page.ids()
    sections = ["contents", "minute", "how", "run", "people", "ai", "limits", "technical"]
    positions = [ids.index(section) for section in sections]
    assert positions == sorted(positions)
    header = next(attrs for tag, attrs in page.elements if tag == "header")
    assert header.get("class") == "masthead"
    footer = next(attrs for tag, attrs in page.elements if tag == "footer")
    assert footer.get("class") == "report-footer"


def test_people_get_at_most_eight_noticed_moment_lines_in_builder_priority(remote_story):
    content = render(remote_story, "en").content
    blocks = person_blocks(content)
    assert sorted(blocks) == [person.agent_id for person in remote_story.people]
    for person in remote_story.people:
        block = blocks[person.agent_id]
        lines = re.findall(r'<li class="moment" data-status="([a-z_]+)"', block)
        noticed = [moment for moment in person.moments if moment.noticed]
        # Moments a person never noticed changed nothing; the summary sentence counts them.
        assert "not_noticed" not in lines
        assert len(lines) == min(8, len(noticed))
        assert ('class="more"' in block) is (len(noticed) > len(lines))
    busy = next(person for person in remote_story.people if person.agent_id == "person-005")
    shown = re.findall(r'<li class="moment" data-status="([a-z_]+)"', blocks["person-005"])
    # Every terminal answer is listed, and the kept lines read in time order.
    assert {"answered", "failed", "unsure", "warning"} <= set(shown)
    assert shown == [moment.status for moment in busy.moments if moment.noticed][:8]
    assert Counter(shown)["not_chosen"] == 2


def test_moment_lines_carry_sentence_ai_clause_labels_and_state_lines(remote_story):
    html = render(remote_story, "en").content.decode("utf-8")
    text = " ".join(Page(html.encode()).all_text().split())
    assert "Veyra #6 noticed the" in text
    # An answered moment says so in its badge and chips; no clause repeats it.
    assert "The AI judged this moment." not in text
    assert english_messages()["status.answered"] in text
    assert "Reaction: skeptical" in text
    assert "Rules only: liking" in text and "With AI: liking" in text
    assert "Over the AI limit of 10 questions" in text
    assert "The AI did not answer (no answer in time). No AI effect." in text
    assert "Set aside" in text
    assert "stereotype risk" in text
    assert "The ad never reached this person." in text
    assert "Was shown the ad 2 times but never noticed it" in text


def test_people_are_grouped_by_persona_in_agent_order(remote_story):
    html = render(remote_story, "en").content.decode("utf-8")
    order = re.findall(r'<section class="person" id="(person-\d{3})"', html)
    assert order == sorted(order)
    personas = re.findall(r'<section class="persona" id="(persona-\d{3})"', html)
    assert personas == ["persona-001", "persona-002"]
    text = Page(html.encode()).all_text()
    assert "Lumo" in text and "4 people like this" in text
    assert "Loves new things" in text  # a translated trait chip
    assert "Likes commuting, technology" in text


@pytest.mark.parametrize("language", ["en", "de", "fa"])
def test_language_direction_and_localised_digits(remote_story, language):
    content = render(remote_story, language).content
    html = content.decode("utf-8")
    direction = "rtl" if language == "fa" else "ltr"
    assert html.startswith(f'<!doctype html>\n<html lang="{language}" dir="{direction}">')
    funnel = re.search(r'<ol class="funnel"[^>]*>(.*?)</ol>', html, flags=re.S)
    assert funnel is not None
    values = re.findall(r'<span class="funnel-value">(.*?)</span>', funnel.group(1))
    expected = [
        remote_story.funnel.opportunities,
        remote_story.funnel.noticed,
        remote_story.funnel.chosen,
        remote_story.funnel.sent,
        remote_story.funnel.used,
    ]
    rendered_values = [re.sub(r"<[^>]+>", "", value) for value in values]
    if language == "fa":
        assert rendered_values == [str(value).translate(PERSIAN_DIGITS) for value in expected]
        assert not re.search(r"[0-9]", "".join(rendered_values))
    else:
        assert rendered_values == [str(value) for value in expected]
    # Identifiers and hashes keep ASCII digits in every language.
    assert remote_story.run.hashes.workbench_input_sha256 in html
    assert f'<bdi dir="ltr">{remote_story.run.run_id}</bdi>' in html


def test_persian_report_uses_persian_digits_in_moment_times(remote_story):
    html = render(remote_story, "fa").content.decode("utf-8")
    # 07:00 is the first ad moment of person-001; the clock is an LTR island.
    assert '<bdi dir="ltr">' + "07:00".translate(PERSIAN_DIGITS) + "</bdi>" in html


@pytest.mark.parametrize("mode", ["mock", "rules", "local"])
def test_offline_and_local_modes_render_their_own_wording(mode):
    story = make_story(mode=mode)
    text = Page(render(story, "en").content).all_text()
    english = english_messages()
    if mode == "mock":
        assert english["report.ai.mock"] in text
        assert english["story.summary.funnel.practice"] in text
        assert english["story.summary.labels.title.practice"] in text
        assert english["common.not_an_ai"] in text
    elif mode == "rules":
        assert english["report.ai.rules"] in text
        assert english["story.summary.labels.none"] in text
        assert english["story.summary.funnel.chosen"] not in text
    else:
        assert "Labels from an AI on this computer" in text
        assert english["limits.vendor"] in text


def test_about_the_ai_and_technical_details_show_provenance(remote_story):
    html = render(remote_story, "en").content.decode("utf-8")
    text = Page(html.encode()).all_text()
    revision = remote_story.ai.provider_revision_sha256
    assert revision is not None
    assert revision[:12] in text and revision in text
    assert "city-cognition-influence-v1" in text
    assert remote_story.ai.contract.prompt_contract_sha256[:12] in text
    assert "Turned off" in text  # model thinking for the Qwen profile
    assert "12 requests" in text
    assert "14,800 input tokens" in text and "1,300 output tokens" in text
    for value in (
        remote_story.run.hashes.workbench_input_sha256,
        remote_story.run.hashes.cognition_input_sha256,
        remote_story.run.hashes.causal_contract_sha256,
        "adlife-run-story-v1",
        "guided-device",
        "fictional-launch",
        "fictional-device-launch-v1",
    ):
        assert value in text
    assert (
        "<code>uv run adlife city-replay &lt;workspace&gt; guided-device-1020-1405</code>" in html
    )


def test_width_class_bars_bucket_to_steps_of_five_and_print_numbers(remote_story):
    html = render(remote_story, "en").content.decode("utf-8")
    widths = re.findall(r'class="fill (w-\d+|dv-(?:neg|pos)-\d+)', html)
    assert widths
    for name in widths:
        value = int(name.rsplit("-", 1)[1])
        assert value % 5 == 0 and 0 <= value <= 100
    module = report_module()
    assert module._bucket(0.0) == 0
    assert module._bucket(0.024) == 0
    assert module._bucket(0.025) == 5
    assert module._bucket(-0.144) == 15
    assert module._bucket(1.0) == 100
    assert module._bucket(1.7) == 100
    assert module._diverging_class(-0.144) == "dv-neg-15"
    assert module._diverging_class(0.4) == "dv-pos-40"


def test_template_failures_are_strict_and_the_size_cap_raises(remote_story, monkeypatch):
    from jinja2 import UndefinedError

    module = report_module()
    monkeypatch.setattr(module, "_template_source", lambda: "{{ missing_value }}\n")
    with pytest.raises(UndefinedError):
        render(remote_story, "en")
    monkeypatch.undo()
    monkeypatch.setattr(module, "MAX_RUN_REPORT_BYTES", 1024)
    with pytest.raises(ValueError, match="size"):
        render(remote_story, "en")


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_renderer_revalidates_the_story_and_checks_its_arguments(remote_story):
    module = report_module()
    with pytest.raises(TypeError):
        module.render_run_report_html(story_document(), language="en", links=None)
    bypassed = RunStoryV1.model_construct(**{**remote_story.__dict__, "funnel": None})
    with pytest.raises(ValueError):
        module.render_run_report_html(bypassed, language="en", links=None)
    with pytest.raises(ValueError):
        module.render_run_report_html(remote_story, language="xx", links=None)
    hostile_links = module.RunReportLinks(
        story="https://invalid.test/",
        technical="/runs/x",
        download="/guide/runs/x/report/download?lang=en",
        languages=(("en", "/guide/runs/x/report?lang=en"),),
    )
    with pytest.raises(ValueError):
        module.render_run_report_html(remote_story, language="en", links=hostile_links)
    for bad in ("//invalid.test/x", '/runs/x" onclick="y', "javascript:alert(1)", "/a b"):
        with pytest.raises(ValueError):
            module.render_run_report_html(
                remote_story,
                language="en",
                links=module.RunReportLinks(
                    story="/guide/runs/x?lang=en",
                    technical=bad,
                    download="/guide/runs/x/report/download?lang=en",
                    languages=(("en", "/guide/runs/x/report?lang=en"),),
                ),
            )


def test_template_and_stylesheet_follow_the_guide_contract():
    template = (
        resources.files("adlife.reporting")
        .joinpath("templates", "run-report.html.j2")
        .read_text(encoding="utf-8")
    )
    css = (
        resources.files("adlife.reporting")
        .joinpath("static", "run-report.css")
        .read_text(encoding="utf-8")
    )
    english = english_messages()
    literal_keys = set(re.findall(r'\btr(?:_text)?\("([a-z0-9_.-]+)"', template))
    assert literal_keys, "the template translates through tr() and tr_text()"
    assert literal_keys <= set(english), sorted(literal_keys - set(english))
    assert "<script" not in template and " style=" not in template
    assert not re.search(r"\son[a-z]+=", template)
    for forbidden in (
        "margin-left",
        "margin-right",
        "padding-left",
        "padding-right",
        "border-left",
        "border-right",
        "@import",
        "@font-face",
        "url(",
        "expression(",
        "letter-spacing",
        "uppercase",
        "http",
    ):
        assert forbidden not in css, forbidden
    assert not re.search(r"(?<![-\w])(left|right)\s*:", css)
    assert not re.search(r"float\s*:\s*(left|right)", css)
    assert not re.search(r"text-align\s*:\s*(left|right)", css)
    assert not re.search(r"\b(rgb|rgba|hsl|hsla|hwb|lab|lch|oklch|oklab|color-mix)\(", css)
    for rule in ("@media print", "@page", "forced-colors: active", "max-width: 640px"):
        assert rule in css, rule
    for step in range(0, 101, 5):
        assert f".w-{step} " in css or f".w-{step}," in css
        assert f".dv-neg-{step} " in css or f".dv-neg-{step}," in css
        assert f".dv-pos-{step} " in css or f".dv-pos-{step}," in css


@pytest.fixture(scope="module")
def demo_story(tmp_path_factory) -> RunStoryV1:
    """The measured default study (Appendix C) run through the real workbench, mock answers."""
    from fastapi.testclient import TestClient

    from adlife.city.run_store import CityRunStore
    from adlife.city.run_story import build_run_story
    from adlife.city.workbench_web import create_city_workbench_app
    from adlife.city.workbench_workspace import prepare_workbench_workspace

    root = tmp_path_factory.mktemp("run-report-demo")
    token = "run-report-demo-csrf-token-abcdefghij"
    host = "127.0.0.1:8765"
    app = create_city_workbench_app(prepare_workbench_workspace(root), csrf_token=token)
    with TestClient(app, base_url=f"http://{host}") as client:
        templates = {
            item["persona_id"]: item
            for item in client.get("/api/persona-templates").json()["templates"]
        }
        draft = default_study_draft(templates, run_id="guided-device-1020-1405", mode="mock")
        response = client.post(
            "/api/jobs",
            content=json.dumps(draft).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Origin": f"http://{host}",
                "X-AdLife-CSRF": token,
            },
        )
        assert response.status_code == 202, response.text
        job_id = response.json()["job"]["job_id"]
        job = wait_for_job(client, job_id)
        assert job["phase"] == "completed", job
    return build_run_story(CityRunStore(Path(root)).load("guided-device-1020-1405"))


def default_study_draft(templates: dict[str, Any], *, run_id: str, mode: str) -> dict[str, Any]:
    """Spec 2.4: the guided default study (Lumo x4 and Veyra x4, Lumen Pocket, phone feed)."""
    return {
        "schema_version": 4,
        "city_id": "fictional-grid-v2",
        "scenario": {
            "scenario_id": "guided-device",
            "name": "Guided demo study - Lumen Pocket",
            "campaign": {
                "campaign_id": "fictional-launch",
                "name": "Lumen Pocket campaign",
                "creative_template_id": "fictional-device-launch-v1",
                "target_interests": ["commuting", "technology"],
                "relative_price": 1.25,
            },
            "phone": {
                "active_windows": [{"day": 1, "start_minute": 420, "end_minute": 1260}],
                "frequency_cap_per_agent_per_day": 4,
                "eligible_activities": ["commute", "home", "leisure"],
                "opportunity_probability_per_minute": 0.01,
            },
            "roadside": None,
        },
        "population": {
            "personas": [
                {**templates["persona-001"], "count": 4},
                {**templates["persona-002"], "count": 4},
            ]
        },
        "settings": {
            "run_id": run_id,
            "agent_count": 8,
            "days": 1,
            "seed": "42",
            "response_mode": "deterministic-rules",
        },
        "cognition": {
            "effect_scope": "bounded-signals-v1",
            "mode": mode,
            "provider_profile_id": None,
            "provider_revision_sha256": None,
            "funded_response_limit": 60,
        },
    }


def wait_for_job(client: Any, job_id: str, *, timeout: float = 120.0) -> dict[str, Any]:
    import time

    deadline = time.monotonic() + timeout
    while True:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["phase"] in {"completed", "failed", "cancelled"} or time.monotonic() > deadline:
            return job
        time.sleep(0.05)


@pytest.mark.parametrize("language", ["en", "de", "fa"])
def test_the_measured_demo_study_report_fits_its_budget(demo_story, language):
    rendered = render(demo_story, language, view=True)
    assert len(rendered.content) <= 150_000
    standalone = render(demo_story, language)
    assert len(standalone.content) <= 150_000
    page = Page(standalone.content)
    assert (
        sum(tag == "section" and attrs.get("class") == "person" for tag, attrs in page.elements)
        == 8
    )
    assert demo_story.ai.mode == "mock"
