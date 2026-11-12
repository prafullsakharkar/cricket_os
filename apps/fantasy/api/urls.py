"""Fantasy public read API routes (Task 7).

Registered from ``config/urls.py`` only when ``FANTASY_PUBLIC_API_ENABLED`` is
on (the routes 404 by URL when the flag is off); each view also re-checks the
flag so a stale URLconf still answers 404.
"""

from __future__ import annotations

from django.urls import path

from apps.fantasy.api import views

app_name = "fantasy"

urlpatterns = [
    path("fantasy/rulesets", views.FantasyRulesetListView.as_view(), name="rulesets"),
    path(
        "fantasy/rulesets/<uuid:ruleset_id>/versions/<int:version>",
        views.FantasyRulesetVersionDetailView.as_view(),
        name="ruleset-version-detail",
    ),
    path("fantasy/points", views.FantasyPointsView.as_view(), name="points"),
    path(
        "fantasy/matches/<uuid:pk>",
        views.FantasyMatchPointsView.as_view(),
        name="match-points",
    ),
]
