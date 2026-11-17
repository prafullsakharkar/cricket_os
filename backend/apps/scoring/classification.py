"""Official vs derived/estimated labelling for every output metric.

SCORING_RULES §4 splits metrics into normative **official** values and
convenience **derived/estimated** ones. Output dataclasses declare the class of
their derived fields via ``dataclass`` field metadata; everything else is
official by default. This keeps the label next to the field definition and
available at runtime through :class:`Classified`.
"""

from __future__ import annotations

from dataclasses import fields
from enum import StrEnum
from typing import Any


class MetricClass(StrEnum):
    """How a metric may be presented."""

    OFFICIAL = "official"
    DERIVED = "derived"


def derived() -> dict[str, Any]:
    """Field metadata marking a metric as derived/estimated."""

    return {"metric_class": MetricClass.DERIVED}


class Classified:
    """Mixin exposing per-field metric classification for a dataclass."""

    @classmethod
    def classification_of(cls, field_name: str) -> MetricClass:
        for item in fields(cls):  # type: ignore[arg-type]
            if item.name == field_name:
                value = item.metadata.get("metric_class", MetricClass.OFFICIAL)
                assert isinstance(value, MetricClass)
                return value
        raise KeyError(f"{cls.__name__} has no field {field_name!r}")

    @classmethod
    def classifications(cls) -> dict[str, MetricClass]:
        return {
            item.name: cls.classification_of(item.name)
            for item in fields(cls)  # type: ignore[arg-type]
            if item.name != "rule_version"
        }

    @classmethod
    def derived_fields(cls) -> frozenset[str]:
        return frozenset(
            name
            for name, metric_class in cls.classifications().items()
            if metric_class is MetricClass.DERIVED
        )
