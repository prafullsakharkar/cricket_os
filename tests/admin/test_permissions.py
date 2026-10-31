"""Role Groups (viewer/operator/data_steward/admin) permission matrix."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth.models import Group, User
from django.test import Client

from apps.players.models import Player, PlayerDuplicateCandidate, PlayerMerge

pytestmark = pytest.mark.django_db

ROLES = ["viewer", "operator", "data_steward", "admin"]

#: (permission, viewer, operator, data_steward, admin)
MATRIX = [
    ("venues.view_country", True, True, True, True),
    ("venues.change_country", False, True, True, True),
    ("venues.add_country", False, False, True, True),
    ("venues.delete_country", False, False, True, True),
    ("venues.view_ground", True, True, True, True),
    ("teams.change_team", False, True, True, True),
    ("players.view_player", True, True, True, True),
    ("players.change_player", False, True, True, True),
    ("players.delete_player", False, False, True, True),
    ("players.merge_players", False, False, True, True),
    ("competitions.view_series", True, True, True, True),
    ("competitions.add_series", False, False, True, True),
    ("administration.view_auditlog", False, False, True, True),
    ("providers.view_dataprovider", False, False, False, True),
    ("providers.change_providerentitymap", False, False, False, True),
]


def make_user(role: str, *, staff: bool = False) -> User:
    user = User.objects.create_user(username=role, password="pw", is_staff=staff)
    user.groups.add(Group.objects.get(name=role))
    return user


class TestGroups:
    def test_all_four_roles_exist(self) -> None:
        names = set(Group.objects.values_list("name", flat=True))
        assert set(ROLES) <= names


class TestPermissionMatrix:
    def test_exact_matrix(self) -> None:
        users = {role: make_user(role) for role in ROLES}
        for perm, viewer, operator, steward, admin in MATRIX:
            actual = {
                "viewer": users["viewer"].has_perm(perm),
                "operator": users["operator"].has_perm(perm),
                "data_steward": users["data_steward"].has_perm(perm),
                "admin": users["admin"].has_perm(perm),
            }
            expected = {
                "viewer": viewer,
                "operator": operator,
                "data_steward": steward,
                "admin": admin,
            }
            assert actual == expected, f"{perm}: {actual} != {expected}"

    def test_admin_has_every_permission(self) -> None:
        admin = make_user("admin")
        from django.contrib.auth.models import Permission

        # The admin group is synced at migration time; the test-only
        # dummy_app (installed only in test settings) adds permissions after
        # that and is outside the role scope by design. ``has_perm`` expects
        # the "app_label.codename" string form, not Permission instances.
        for perm in Permission.objects.exclude(content_type__app_label="dummy_app"):
            perm_str = f"{perm.content_type.app_label}.{perm.codename}"
            assert admin.has_perm(perm_str), perm_str


class TestAdminMergeGate:
    def test_viewer_cannot_merge_via_admin(self) -> None:
        viewer = make_user("viewer", staff=True)
        client = Client()
        client.force_login(viewer)

        winner = Player.objects.create(full_name="Virat Kohli")
        loser = Player.objects.create(full_name="V Kohli")
        candidate = PlayerDuplicateCandidate.objects.create(
            candidate_player=loser,
            canonical_player=winner,
            score=Decimal("0.950"),
            feature_breakdown={},
        )
        response = client.post(
            "/admin/players/playerduplicatecandidate/",
            {
                "action": "confirm_merge",
                "_selected_action": [str(candidate.pk)],
                "index": "0",
                "confirm": "yes",
            },
        )
        # Denied: the action is invisible to the viewer, so the POST is a
        # no-op ("no action selected") and nothing is merged.
        assert PlayerMerge.objects.count() == 0
        candidate.refresh_from_db()
        assert candidate.status == "open"
        assert response.status_code in (200, 302)

    def test_data_steward_can_merge_via_admin(self) -> None:
        steward = make_user("data_steward", staff=True)
        client = Client()
        client.force_login(steward)

        winner = Player.objects.create(full_name="Virat Kohli")
        loser = Player.objects.create(full_name="V Kohli")
        candidate = PlayerDuplicateCandidate.objects.create(
            candidate_player=loser,
            canonical_player=winner,
            score=Decimal("0.950"),
            feature_breakdown={},
        )
        client.post(
            "/admin/players/playerduplicatecandidate/",
            {
                "action": "confirm_merge",
                "_selected_action": [str(candidate.pk)],
                "index": "0",
                "confirm": "yes",
            },
        )
        assert PlayerMerge.objects.count() == 1
        candidate.refresh_from_db()
        assert candidate.status == "merged"

    def test_operator_cannot_delete(self) -> None:
        operator = make_user("operator", staff=True)
        assert operator.has_perm("venues.delete_country") is False
        client = Client()
        client.force_login(operator)
        from apps.venues.models import Country

        country = Country.objects.create(name="Atlantis")
        response = client.post(
            f"/admin/venues/country/{country.pk}/delete/",
            {"post": "yes"},
        )
        # Without delete permission the delete view denies the request.
        assert response.status_code == 403
        assert Country.objects.filter(pk=country.pk).exists()
