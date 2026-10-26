"""The static HTML run report: one saved V10 run as a short, translated briefing.

``render_run_report_html`` turns one revalidated :class:`RunStoryV1` into a complete
page with no JavaScript. Its single packaged stylesheet is authorised by its exact
sha256 CSP hash. Every sentence is a guide catalog message or one of the story's
message references, so the report says what the story page says, in English, German or
Persian. The view variant links back to the guide; the standalone download variant holds
only in-page anchors and works offline from a file. Nothing is written to disk, and the
output depends only on the story and the language (no clock, no locale, no paths).
"""

from __future__ import annotations

import base64
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from importlib import resources
from typing import Final, Literal

from jinja2 import Environment, StrictUndefined
from markupsafe import Markup

from adlife.i18n import LANGUAGES, Catalog, Language, MessageRef, load_catalog, template_functions
from adlife.i18n.refs import message_ref, person_param
from adlife.reporting.run_story_model import (
    RunStoryV1,
    StoryLabels,
    StoryMoment,
    StoryPerson,
    StoryPersona,
    story_ref_context,
)

MAX_RUN_REPORT_BYTES: Final = 4_194_304
REPORT_MOMENTS_PER_PERSON: Final = 8

Tone = Literal["rules", "ai", "practice", "none", "aside"]

# Same-origin absolute paths only: no scheme, no host, no quotes, spaces or backslashes.
_LINK: Final = re.compile(r"^/(?![/\\])[A-Za-z0-9._~/?=&%-]*$")
_TERMINAL: Final = frozenset(
    {"answered", "reused", "practice", "unsure", "warning", "failed", "over_limit"}
)
_SET_ASIDE: Final = frozenset({"unsure", "warning"})
_PROVIDER_MODES: Final = frozenset({"local", "remote"})
_DIMENSIONS: Final = ("affect", "relevance", "credibility", "salience")
_ORDERS: Final[dict[str, tuple[str, ...]]] = {
    "affect": ("annoyed", "skeptical", "neutral", "curious", "positive", "uncertain"),
    "relevance": ("none", "low", "medium", "high", "uncertain"),
    "credibility": ("low", "medium", "high", "uncertain"),
    "salience": ("none", "low", "medium", "high", "uncertain"),
}
_FLAGS: Final = ("stereotype-risk", "unsupported-inference", "unsafe-content")
_AI_HEADLINES: Final = frozenset(
    {"story.headline.ai", "story.headline.ai_as_person", "story.headline.practice"}
)
# Prompt contract v3 asked the AI to answer as the person who noticed the ad; v4 also asked
# for their inner thoughts.
_PERSONA_PROMPTS: Final = frozenset(
    {"city-cognition-persona-prompt-v3", "city-cognition-persona-thoughts-prompt-v4"}
)
_THOUGHT_LANGUAGES: Final = ("en", "de", "fa")


@dataclass(frozen=True, slots=True)
class RunReportLinks:
    """Same-origin links of the view variant; ``None`` renders the standalone file."""

    story: str
    download: str
    languages: tuple[tuple[Language, str], ...]


@dataclass(frozen=True, slots=True)
class RenderedRunReport:
    """Exact report bytes (UTF-8, LF only) and the CSP its ``<meta>`` element carries."""

    content: bytes
    content_security_policy: str


# --- the template's view of one story -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Chip:
    dimension: str
    key: str
    # Further values tied with ``key`` for the most common label ("skeptical / annoyed").
    also: tuple[str, ...] = ()
    once: bool = False


@dataclass(frozen=True, slots=True)
class _Thought:
    """One language of a moment's inner thoughts: saved AI text, escaped like user text."""

    language: str
    direction: str
    label_key: str
    text: str


@dataclass(frozen=True, slots=True)
class _Moment:
    status: str
    day: int
    status_key: str
    sentence: MessageRef
    ai_clause: MessageRef | None
    labels: tuple[_Chip, ...]
    label_tone: Tone
    set_aside: bool
    rules_line: MessageRef | None
    with_ai_line: MessageRef | None
    ai_tone: Tone
    thoughts_title: MessageRef | None
    thoughts: tuple[_Thought, ...]


@dataclass(frozen=True, slots=True)
class _Measure:
    rules: float
    with_ai: float | None
    rules_bar: str
    with_ai_bar: str
    compare_key: str | None


@dataclass(frozen=True, slots=True)
class _End:
    liking: _Measure
    memory: _Measure
    purchase: _Measure


@dataclass(frozen=True, slots=True)
class _Person:
    agent_id: str
    number: int
    summary: MessageRef
    label_answers: int
    label_mode: tuple[_Chip, ...]
    moments: tuple[_Moment, ...]
    more: int
    end: _End | None


@dataclass(frozen=True, slots=True)
class _Interest:
    key: str | None
    text: str


@dataclass(frozen=True, slots=True)
class _Persona:
    persona_id: str
    mark: str
    name_key: str | None
    display_name: str
    tagline_key: str | None
    count: int
    trait_keys: tuple[str, ...]
    interests: tuple[_Interest, ...]
    people: tuple[_Person, ...]


@dataclass(frozen=True, slots=True)
class _Cell:
    name: str
    value: int
    tone: Tone
    width: str


@dataclass(frozen=True, slots=True)
class _Average:
    measure: str
    source: Tone
    start: float
    end: float
    diverging: bool
    width: str


@dataclass(frozen=True, slots=True)
class _Entry:
    key: str
    count: int
    width: str


@dataclass(frozen=True, slots=True)
class _Spread:
    dimension: str
    entries: tuple[_Entry, ...]


@dataclass(frozen=True, slots=True)
class _Headline:
    ref: MessageRef
    tone: Tone


@dataclass(frozen=True, slots=True)
class _LanguageLink:
    code: str
    key: str
    href: str
    current: bool


@dataclass(frozen=True, slots=True)
class _Coverage:
    noticed: int
    chosen: int
    sent: int
    answered: int
    reused: int
    failed: int
    set_aside: int
    over_limit: int
    not_chosen: int


@dataclass(frozen=True, slots=True)
class _Day:
    number: int
    shown: int
    noticed: int
    chosen: int
    used: int


@dataclass(frozen=True, slots=True)
class _View:
    product: str
    city_key: str | None
    city_name: str
    category_key: str | None
    message_key: str | None
    category: str
    message: str
    mode_key: str
    ai_line_key: str
    provider: bool
    practice: bool
    rules: bool
    # A real AI answered as the person (prompt v3), so the in-character wording applies.
    in_character: bool
    tone: Tone
    label_tone: Tone
    headline: tuple[_Headline, ...]
    funnel: tuple[_Cell, ...]
    averages: tuple[_Average, ...]
    compare_liking_key: str
    compare_memory_key: str
    spread: tuple[_Spread, ...]
    flags: tuple[_Entry, ...]
    coverage: _Coverage
    # Day-by-day totals; a one-day run has nothing to compare, so it shows none.
    days: tuple[_Day, ...]
    personas: tuple[_Persona, ...]
    languages: tuple[_LanguageLink, ...]
    revision_short: str | None
    contract_short: str
    replay_command: str


def _lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _template_source() -> str:
    packaged = resources.files("adlife.reporting") / "templates" / "run-report.html.j2"
    return _lf(packaged.read_text(encoding="utf-8"))


def _stylesheet() -> str:
    packaged = resources.files("adlife.reporting") / "static" / "run-report.css"
    return _lf(packaged.read_text(encoding="utf-8"))


def _bucket(ratio: float) -> int:
    """Round a 0..1 magnitude to a width class in steps of five percent."""
    if not math.isfinite(ratio):
        raise ValueError("bar values must be finite")
    return max(0, min(100, math.floor(abs(ratio) * 100 / 5 + 0.5) * 5))


def _width_class(ratio: float) -> str:
    return f"w-{_bucket(ratio)}"


def _diverging_class(value: float) -> str:
    """A liking bar around the centre axis; -1..+1 maps onto each half of the track."""
    side = "neg" if value < 0 else "pos"
    return f"dv-{side}-{_bucket(value)}"


def _share(count: int, total: int) -> str:
    return _width_class(count / total if total else 0.0)


def _content_security_policy(css: str, *, view: bool) -> str:
    digest = base64.b64encode(sha256(css.encode("utf-8")).digest()).decode("ascii")
    images = "'self'" if view else "'none'"
    return (
        "default-src 'none'; script-src 'none'; "
        f"style-src 'sha256-{digest}'; img-src {images}; font-src 'none'; "
        "connect-src 'none'; media-src 'none'; object-src 'none'; frame-src 'none'; "
        "child-src 'none'; worker-src 'none'; base-uri 'none'; form-action 'none'"
    )


def _checked_language(language: object) -> Language:
    for supported in LANGUAGES:
        if type(language) is str and language == supported:
            return supported
    raise ValueError("unsupported report language")


def _checked_links(links: object) -> RunReportLinks | None:
    if links is None:
        return None
    if not isinstance(links, RunReportLinks):
        raise TypeError("report links must be RunReportLinks or None")
    codes = [code for code, _ in links.languages]
    if any(code not in LANGUAGES for code in codes) or len(set(codes)) != len(codes):
        raise ValueError("report language links are invalid")
    for href in (links.story, links.download, *(h for _, h in links.languages)):
        if type(href) is not str or _LINK.fullmatch(href) is None:
            raise ValueError("report links must be same-origin paths")
    return links


def _revalidated(story: object) -> RunStoryV1:
    if not isinstance(story, RunStoryV1):
        raise TypeError("the run report needs a RunStoryV1")
    # Message references serialise with their wire names, so a dump round-trips.
    return RunStoryV1.model_validate(story.model_dump(mode="python"))


def _priority(moment: StoryMoment) -> int:
    if moment.status in _TERMINAL:
        return 0
    return 1 if moment.noticed else 2


def _time(moment: StoryMoment) -> tuple[int, int, str]:
    return moment.minute, moment.millisecond, moment.moment_id


def _listed_moments(person: StoryPerson, days: int) -> tuple[tuple[StoryMoment, ...], int]:
    """Pick at most ``max(8, 2 x days)`` noticed moments, read in time.

    AI-selected moments come first, then the other noticed ones; within each, the days
    take turns (each giving its earliest moment not yet listed), so a long run lists
    every day instead of only its first ones. Moments the person never noticed changed
    nothing; the summary sentence already counts them, so the report leaves them out.
    """
    limit = max(REPORT_MOMENTS_PER_PERSON, 2 * days)
    noticed = sorted((moment for moment in person.moments if moment.noticed), key=_time)
    picked: list[StoryMoment] = []
    for priority in (0, 1):
        per_day: dict[int, list[StoryMoment]] = {}
        for moment in noticed:
            if _priority(moment) == priority:
                per_day.setdefault(moment.day_index, []).append(moment)
        queues = [per_day[day] for day in sorted(per_day)]
        turn = 0
        while len(picked) < limit and any(turn < len(queue) for queue in queues):
            for queue in queues:
                if turn < len(queue) and len(picked) < limit:
                    picked.append(queue[turn])
            turn += 1
    listed = sorted(picked, key=_time)
    return tuple(listed), len(noticed) - len(listed)


def _chips(labels: StoryLabels | None) -> tuple[_Chip, ...]:
    if labels is None:
        return ()
    return tuple(
        _Chip(dimension=name, key=f"{name}.{getattr(labels, name)}") for name in _DIMENSIONS
    )


def _mode_chips(person: StoryPerson) -> tuple[_Chip, ...]:
    """The most common label per dimension over the moments listed in the story; a tie
    names every tied value, so two different answers are never reported as one."""
    labelled = [
        moment.labels
        for moment in person.moments
        if moment.labels is not None and moment.status in {"answered", "reused", "practice"}
    ]
    if not labelled:
        return _chips(person.label_mode)
    chips: list[_Chip] = []
    for name in _DIMENSIONS:
        counts = Counter(getattr(labels, name) for labels in labelled)
        top = max(counts.values())
        tied = [value for value in _ORDERS[name] if counts[value] == top]
        chips.append(
            _Chip(
                dimension=name,
                key=f"{name}.{tied[0]}",
                also=tuple(f"{name}.{value}" for value in tied[1:]),
                # When every answer differed, no value is "most common": each came once.
                once=top == 1 and len(tied) > 1,
            )
        )
    return tuple(chips)


def _thoughts(moment: StoryMoment) -> tuple[_Thought, ...]:
    """Every language the moment's inner thoughts were kept in, in the stored order."""
    if moment.thoughts is None:
        return ()
    return tuple(
        _Thought(
            language=language,
            direction="rtl" if language == "fa" else "ltr",
            label_key=f"lang.{language}",
            text=text,
        )
        for language in _THOUGHT_LANGUAGES
        if (text := getattr(moment.thoughts, language)) is not None
    )


def _moment(moment: StoryMoment, *, practice: bool, agent_id: str) -> _Moment:
    status = moment.status
    set_aside = status in _SET_ASIDE
    label_tone: Tone = "aside" if set_aside else ("practice" if practice else "ai")
    if status in {"answered", "reused"}:
        ai_tone: Tone = "ai"
    elif status == "practice":
        ai_tone = "practice"
    else:
        ai_tone = "none"
    return _Moment(
        status=status,
        day=moment.day_index + 1,
        status_key=f"status.{status}",
        sentence=moment.sentence,
        # An answered or practice moment already says so in its badge and chips; the clause
        # stays only where it explains something the badge does not (limits, set-asides).
        ai_clause=None if status in {"answered", "reused", "practice"} else moment.ai_clause,
        labels=_chips(moment.labels),
        label_tone=label_tone,
        set_aside=set_aside,
        rules_line=moment.rules_line,
        with_ai_line=moment.with_ai_line,
        ai_tone=ai_tone,
        thoughts_title=(
            None
            if moment.thoughts is None
            else message_ref("story.thoughts.title", name=person_param(agent_id))
        ),
        thoughts=_thoughts(moment),
    )


def _end(person: StoryPerson) -> _End | None:
    if person.totals.noticed == 0:
        return None
    rules, with_ai = person.end.rules, person.end.with_ai
    return _End(
        liking=_Measure(
            rules=rules.liking,
            with_ai=with_ai.liking,
            rules_bar=_diverging_class(rules.liking),
            with_ai_bar=_diverging_class(with_ai.liking),
            compare_key=f"compare.{person.compare.liking}",
        ),
        memory=_Measure(
            rules=rules.memory,
            with_ai=with_ai.memory,
            rules_bar=_width_class(rules.memory),
            with_ai_bar=_width_class(with_ai.memory),
            compare_key=f"compare.{person.compare.memory}",
        ),
        purchase=_Measure(
            rules=rules.purchase_interest,
            with_ai=None,
            rules_bar=_width_class(rules.purchase_interest),
            with_ai_bar="w-0",
            compare_key=None,
        ),
    )


def _person(person: StoryPerson, *, practice: bool, days: int) -> _Person:
    listed, rest = _listed_moments(person, days)
    return _Person(
        agent_id=person.agent_id,
        number=person.number,
        summary=person.summary,
        label_answers=person.label_answers,
        # One answer's labels already show on its moment; a summary only helps from two on.
        label_mode=_mode_chips(person) if person.label_answers >= 2 else (),
        moments=tuple(
            _moment(moment, practice=practice, agent_id=person.agent_id) for moment in listed
        ),
        more=rest,
        end=_end(person),
    )


def _mark(color_index: int) -> str:
    """Colour slot 0-9 and shape 0-3: the ten templates differ in colour, and another
    persona (10 onward) reuses a template colour only with a different shape."""
    return f"c{color_index % 10} s{color_index % 4}"


def _persona(
    persona: StoryPersona,
    people: Sequence[StoryPerson],
    catalog: Catalog,
    *,
    practice: bool,
    days: int,
) -> _Persona:
    key = persona.template_key
    interests = tuple(
        _Interest(key=f"interest.{value}" if catalog.has(f"interest.{value}") else None, text=value)
        for value in persona.interests
    )
    members = sorted(
        (person for person in people if person.persona_id == persona.persona_id),
        key=lambda person: person.agent_id,
    )
    return _Persona(
        persona_id=persona.persona_id,
        mark=_mark(persona.color_index),
        name_key=None if key is None else f"persona.{key}.name",
        display_name=persona.display_name,
        tagline_key=None if key is None else f"persona.{key}.tagline",
        count=persona.count,
        trait_keys=tuple(f"trait.{chip.trait}.{chip.level}" for chip in persona.trait_chips),
        interests=interests,
        people=tuple(_person(person, practice=practice, days=days) for person in members),
    )


def _headline(story: RunStoryV1, ai_tone: Tone) -> tuple[_Headline, ...]:
    lines = []
    for ref in story.headline:
        tone: Tone = (
            ai_tone
            if ref.key in _AI_HEADLINES or ref.key.startswith("story.headline.compare.")
            else "rules"
        )
        lines.append(_Headline(ref=ref, tone=tone))
    return tuple(lines)


def _funnel(story: RunStoryV1, ai_tone: Tone) -> tuple[_Cell, ...]:
    funnel = story.funnel
    total = funnel.opportunities
    cells = [
        _Cell("opportunities", total, "rules", _share(total, total)),
        _Cell("noticed", funnel.noticed, "rules", _share(funnel.noticed, total)),
    ]
    if story.ai.mode != "rules":
        cells.extend(
            (
                _Cell("chosen", funnel.chosen, ai_tone, _share(funnel.chosen, total)),
                _Cell("sent", funnel.sent, ai_tone, _share(funnel.sent, total)),
                _Cell("used", funnel.used, ai_tone, _share(funnel.used, total)),
            )
        )
    return tuple(cells)


def _averages(story: RunStoryV1, ai_tone: Tone) -> tuple[_Average, ...]:
    rules, with_ai = story.averages.rules, story.averages.with_ai
    rows = [
        _Average(
            "liking",
            "rules",
            rules.liking.start,
            rules.liking.end,
            True,
            _diverging_class(rules.liking.end),
        ),
    ]
    if story.ai.mode != "rules":
        rows.append(
            _Average(
                "liking",
                ai_tone,
                with_ai.liking.start,
                with_ai.liking.end,
                True,
                _diverging_class(with_ai.liking.end),
            )
        )
    rows.append(
        _Average(
            "memory",
            "rules",
            rules.memory.start,
            rules.memory.end,
            False,
            _width_class(rules.memory.end),
        )
    )
    if story.ai.mode != "rules":
        rows.append(
            _Average(
                "memory",
                ai_tone,
                with_ai.memory.start,
                with_ai.memory.end,
                False,
                _width_class(with_ai.memory.end),
            )
        )
    rows.append(
        _Average(
            "purchase",
            "rules",
            rules.purchase_interest.start,
            rules.purchase_interest.end,
            False,
            _width_class(rules.purchase_interest.end),
        )
    )
    return tuple(rows)


def _spread(story: RunStoryV1) -> tuple[tuple[_Spread, ...], tuple[_Entry, ...]]:
    labels = story.labels
    if labels is None:
        return (), ()
    answers = labels.answers
    spread = []
    for name in _DIMENSIONS:
        counts: dict[str, int] = getattr(labels, name)
        entries = tuple(
            _Entry(key=f"{name}.{value}", count=counts[value], width=_share(counts[value], answers))
            for value in _ORDERS[name]
            if counts.get(value, 0) > 0
        )
        spread.append(_Spread(dimension=name, entries=entries))
    flag_counts: dict[str, int] = {str(flag): count for flag, count in labels.safety_flags.items()}
    flags = tuple(
        _Entry(
            key=f"flag.{flag}", count=flag_counts[flag], width=_share(flag_counts[flag], answers)
        )
        for flag in _FLAGS
        if flag_counts.get(flag, 0) > 0
    )
    return tuple(spread), flags


def _view(story: RunStoryV1, catalog: Catalog, links: RunReportLinks | None) -> _View:
    mode = story.ai.mode
    practice = mode == "mock"
    ai_tone: Tone = "practice" if practice else ("rules" if mode == "rules" else "ai")
    run = story.run
    creative_key = run.creative.catalog_key
    statuses = story.ai.statuses
    coverage = story.ai.coverage
    spread, flags = _spread(story)
    language_links = (
        ()
        if links is None
        else tuple(
            _LanguageLink(
                code=code, key=f"lang.{code}", href=href, current=code == catalog.language
            )
            for code, href in links.languages
        )
    )
    revision = story.ai.provider_revision_sha256
    return _View(
        product=run.creative.product_name,
        city_key=None if run.city.catalog_key is None else f"city.{run.city.catalog_key}",
        city_name=run.city.name,
        category_key=None if creative_key is None else f"creative.{creative_key}.category",
        message_key=None if creative_key is None else f"creative.{creative_key}.message",
        category=run.creative.product_category,
        message=run.creative.message,
        mode_key=f"mode.{mode}",
        ai_line_key=f"report.ai.{mode}",
        provider=mode in _PROVIDER_MODES,
        practice=practice,
        rules=mode == "rules",
        in_character=(
            mode in _PROVIDER_MODES and story.ai.contract.prompt_contract_id in _PERSONA_PROMPTS
        ),
        tone=ai_tone,
        label_tone="practice" if practice else "ai",
        headline=_headline(story, ai_tone),
        funnel=_funnel(story, ai_tone),
        averages=_averages(story, ai_tone),
        compare_liking_key=f"compare.{story.averages.compare.liking}",
        compare_memory_key=f"compare.{story.averages.compare.memory}",
        spread=spread,
        flags=flags,
        coverage=_Coverage(
            noticed=coverage.noticed,
            chosen=coverage.chosen,
            sent=coverage.sent,
            answered=statuses.answered,
            reused=statuses.reused,
            failed=statuses.failed,
            set_aside=statuses.unsure + statuses.warning,
            over_limit=statuses.over_limit,
            not_chosen=statuses.not_chosen,
        ),
        days=(
            ()
            if run.days < 2
            else tuple(
                _Day(
                    number=day.day_index + 1,
                    shown=day.shown,
                    noticed=day.noticed,
                    chosen=day.chosen,
                    used=day.used,
                )
                for day in story.day_totals
            )
        ),
        personas=tuple(
            _persona(persona, story.people, catalog, practice=practice, days=run.days)
            for persona in story.personas
        ),
        languages=language_links,
        revision_short=None if revision is None else revision[:12],
        contract_short=story.ai.contract.prompt_contract_sha256[:12],
        replay_command=f"uv run adlife city-replay <workspace> {run.run_id}",
    )


def render_run_report_html(
    story: RunStoryV1,
    *,
    language: Language,
    links: RunReportLinks | None,
) -> RenderedRunReport:
    """Render one story as a deterministic report; ``links=None`` is the download file."""
    checked = _revalidated(story)
    checked_language = _checked_language(language)
    checked_links = _checked_links(links)
    catalog = load_catalog(checked_language)
    css = _stylesheet()
    source = _template_source()
    csp = _content_security_policy(css, view=checked_links is not None)
    environment = Environment(
        autoescape=True,
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        newline_sequence="\n",
    )
    environment.globals.update(template_functions(catalog, story_ref_context(checked)))
    content = (
        environment.from_string(source)
        .render(
            lang=checked_language,
            dir=catalog.direction,
            csp=csp,
            css=Markup(css),
            links=checked_links,
            story=checked,
            view=_view(checked, catalog, checked_links),
        )
        .encode("utf-8")
    )
    if len(content) > MAX_RUN_REPORT_BYTES:
        raise ValueError("the run report exceeds its size limit")
    return RenderedRunReport(content=content, content_security_policy=csp)


__all__ = [
    "MAX_RUN_REPORT_BYTES",
    "REPORT_MOMENTS_PER_PERSON",
    "RenderedRunReport",
    "RunReportLinks",
    "render_run_report_html",
]
