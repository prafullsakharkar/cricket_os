"""Extras, legal-ball and charge attribution rules (E4-E8, E25)."""

from __future__ import annotations

from collections.abc import Iterable

from apps.scoring.config import RuleConfig
from apps.scoring.types import DeliveryRecord, ExtrasBreakdown


def batter_faces_ball(delivery: DeliveryRecord, config: RuleConfig) -> bool:
    """Wides never count; a no-ball counts only per ``counts_ball_on_noball``."""

    if delivery.is_wide:
        return False
    if delivery.is_noball:
        return config.counts_ball_on_noball
    return True


def bowler_runs_charged(delivery: DeliveryRecord) -> int:
    """Byes, leg-byes and penalties are never charged to the bowler."""

    return delivery.runs_off_bat + delivery.wides + delivery.noballs


def is_batter_dot(delivery: DeliveryRecord) -> bool:
    return delivery.runs_off_bat == 0 and delivery.total_runs == 0


def is_bowling_dot(delivery: DeliveryRecord) -> bool:
    return delivery.total_runs == 0


def extras_breakdown(deliveries: Iterable[DeliveryRecord], rule_version: str) -> ExtrasBreakdown:
    wides = noballs = byes = leg_byes = penalty = 0
    for delivery in deliveries:
        wides += delivery.wides
        noballs += delivery.noballs
        byes += delivery.byes
        leg_byes += delivery.leg_byes
        penalty += delivery.penalty_runs
    return ExtrasBreakdown(
        rule_version=rule_version,
        wides=wides,
        noballs=noballs,
        byes=byes,
        leg_byes=leg_byes,
        penalty=penalty,
        total=wides + noballs + byes + leg_byes + penalty,
    )
