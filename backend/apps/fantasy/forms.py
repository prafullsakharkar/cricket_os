"""Forms for the fantasy admin surface (Task 6).

``DryRunVersionForm`` drives the dry-run admin action: pick a completed match
(and optionally a second version to diff against) without writing anything.
"""

from __future__ import annotations

from django import forms

from apps.core.enums import MatchStatus
from apps.fantasy.models import FantasyRulesetVersion, RulesetVersionStatus
from apps.matches.models import Match


class DryRunVersionForm(forms.Form):
    """Select a completed match to dry-run a version against (+ optional diff)."""

    match = forms.ModelChoiceField(
        queryset=Match.objects.filter(status=MatchStatus.COMPLETED).order_by(
            "start_time_utc", "id"
        ),
        label="Match",
        empty_label="Select a completed match",
    )
    against = forms.ModelChoiceField(
        queryset=FantasyRulesetVersion.objects.filter(status=RulesetVersionStatus.FROZEN).order_by(
            "ruleset_id", "version"
        ),
        label="Compare against (frozen version)",
        required=False,
        empty_label="(no comparison)",
    )
