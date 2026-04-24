from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Snapshot:
    day: int
    timestamp: int
    product: str
    bid_price_1: float | None
    ask_price_1: float | None
    bid_volume_1: int | None
    ask_volume_1: int | None
    mid_price: float | None

    @property
    def tick(self) -> int:
        return self.timestamp // 100

    @property
    def spread(self) -> float | None:
        if self.bid_price_1 is None or self.ask_price_1 is None:
            return None
        return self.ask_price_1 - self.bid_price_1


@dataclass(frozen=True)
class BoundaryBand:
    lower: float
    upper: float

    def to_dict(self) -> dict[str, float]:
        return {
            "lower": round(float(self.lower), 4),
            "upper": round(float(self.upper), 4),
        }


@dataclass
class ProductProfile:
    round_num: int | None
    product: str
    sample_count: int
    day_count: int
    type_key: str
    type_label: str
    description: str
    confidence: float
    metrics: dict[str, float]
    boundaries: dict[str, BoundaryBand]
    type_scores: dict[str, float]
    generator_template: dict[str, Any]
    chart_points: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self, include_series: bool = True) -> dict[str, Any]:
        payload = {
            "round": self.round_num,
            "product": self.product,
            "sample_count": self.sample_count,
            "day_count": self.day_count,
            "type_key": self.type_key,
            "type_label": self.type_label,
            "description": self.description,
            "confidence": round(float(self.confidence), 4),
            "metrics": {
                key: round(float(value), 4) for key, value in self.metrics.items()
            },
            "boundaries": {
                key: value.to_dict() for key, value in self.boundaries.items()
            },
            "type_scores": {
                key: round(float(value), 4) for key, value in self.type_scores.items()
            },
            "generator_template": dict(self.generator_template),
        }
        if include_series:
            payload["chart_points"] = list(self.chart_points)
        return payload
