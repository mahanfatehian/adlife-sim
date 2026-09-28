"""Streaming canonical evidence for every minute of a synthetic city run."""

from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256

from adlife.core.domain.city_run import CityTraceSummary
from adlife.core.domain.serialization import canonical_json
from adlife.core.simulation.city_mobility import CityMobility


def summarize_city_trace(mobility: CityMobility) -> CityTraceSummary:
    """Hash every core minute frame in order, without retaining the trace in memory."""
    frame_count = mobility.days * 1_440
    digest = sha256()
    for minute in range(frame_count):
        line = canonical_json(mobility.frame_document(minute)) + "\n"
        digest.update(line.encode("utf-8"))
    agents_document: dict[str, object] = {"agents": [asdict(agent) for agent in mobility.agents]}
    return CityTraceSummary(
        trace_sha256=digest.hexdigest(),
        agents_sha256=sha256(canonical_json(agents_document).encode("utf-8")).hexdigest(),
        frame_count=frame_count,
        position_count=frame_count * len(mobility.agents),
    )


__all__ = ["summarize_city_trace"]
