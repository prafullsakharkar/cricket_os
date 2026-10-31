"""Cricsheet register importer: pure core, Django sink, command, idempotency."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pytest
from django.core.management import call_command
from django.test import override_settings

from apps.players.import_register import ImportReport, import_register
from apps.players.models import Player
from apps.providers.models import ProviderEntityMap
from apps.venues.models import Ground, Venue

pytestmark = pytest.mark.django_db

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PEOPLE_CSV = FIXTURES / "people.csv"
NAMES_CSV = FIXTURES / "names.csv"
VENUES_CSV = FIXTURES / "venues.csv"


def _csv_rows(path: Path) -> list[dict[str, str]]:
    """Rows of a register CSV, skipping blank and ``#`` comment lines."""
    with path.open(newline="", encoding="utf-8") as handle:
        lines = [line for line in handle if line.strip() and not line.lstrip().startswith("#")]
        reader = csv.DictReader(lines)
        return [
            {k: (v or "").strip() for k, v in raw.items()}
            for raw in reader
            if any((v or "").strip() for v in raw.values())
        ]


def _digest() -> tuple[int, int]:
    """(player rows, provider_entity_map rows) — enough to prove idempotency."""
    return Player.objects.count(), ProviderEntityMap.objects.count()


# --------------------------------------------------------------------------- #
# Pure core (fake sink, no DB)
# --------------------------------------------------------------------------- #
class FakeSink:
    """Records sink calls; returns a configurable status per method."""

    def __init__(self, status: str = "created") -> None:
        self.status = status
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def player(self, identifier: str, name: str, unique_name: str | None, keys: Any) -> str:
        self.calls.append(("player", (identifier, name, unique_name, dict(keys))))
        return self.status

    def player_name_variant(self, identifier: str, name: str) -> str:
        self.calls.append(("names", (identifier, name)))
        return self.status

    def venue(self, identifier: str, name: str, unique_name: str | None, keys: Any) -> str:
        self.calls.append(("venue", (identifier, name, unique_name, dict(keys))))
        return self.status


class TestPureCore:
    def test_people_counts_and_keys(self) -> None:
        rows = _csv_rows(PEOPLE_CSV)
        sink = FakeSink()
        report = import_register(rows, sink, kind="people")

        assert report.read == len(rows)
        assert report.created == len(rows)
        assert report.errors == []
        assert len(sink.calls) == len(rows)
        # key_opta/key_pulse pass through; key_cricbuzz/key_cricinfo too — the
        # allowlist is the SINK's concern, not the pure core's.
        identifier, name, unique_name, keys = sink.calls[0][1]
        assert identifier == "p-0001"
        assert name == "Virat Kohli"
        assert unique_name == "V Kohli"
        assert keys == {
            "key_bcci": "bcci-001",
            "key_opta": "opt-1001",
            "key_pulse": "kp-9001",
            "key_cricbuzz": "cbz-7001",
            "key_cricinfo": "ci-5001",
        }

    def test_skipped_status_counts_as_skipped(self) -> None:
        sink = FakeSink(status="skipped")
        report = import_register([{"identifier": "x", "name": "X"}], sink, kind="people")
        assert report.skipped == 1
        assert report.created == 0

    def test_bad_rows_are_errors_never_raised(self) -> None:
        sink = FakeSink()
        rows = [
            {"identifier": "ok", "name": "Good"},
            {"identifier": "missing-name"},  # no name
            {"name": "missing-identifier"},  # no identifier
            {"identifier": "  ", "name": "blank-id"},  # blank identifier
        ]
        report = import_register(rows, sink, kind="people")

        assert report.read == 4
        assert report.created == 1
        assert report.failed == 3
        assert report.errors == [
            {"row": 2, "reason": "missing identifier or name"},
            {"row": 3, "reason": "missing identifier or name"},
            {"row": 4, "reason": "missing identifier or name"},
        ]
        assert len(sink.calls) == 1

    def test_sink_rejection_is_an_error(self) -> None:
        class RejectingSink(FakeSink):
            def player(self, *args: Any, **kwargs: Any) -> str:
                raise ValueError("unknown player")

        report = import_register([{"identifier": "x", "name": "X"}], RejectingSink(), kind="people")
        assert report.failed == 1
        assert report.errors == [{"row": 1, "reason": "unknown player"}]

    def test_names_kind_routes_to_player_name_variant(self) -> None:
        sink = FakeSink()
        report = import_register([{"identifier": "p-0001", "name": "V Kohli"}], sink, kind="names")
        assert report.created == 1
        assert sink.calls == [("names", ("p-0001", "V Kohli"))]

    def test_venues_kind_routes_to_venue(self) -> None:
        sink = FakeSink()
        report = import_register([{"identifier": "v-1", "name": "Lord's"}], sink, kind="venues")
        assert report.created == 1
        assert sink.calls == [("venue", ("v-1", "Lord's", None, {}))]

    def test_unknown_kind_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown register kind"):
            import_register([], FakeSink(), kind="scores")  # type: ignore[arg-type]

    def test_keys_only_non_empty_key_columns(self) -> None:
        sink = FakeSink()
        import_register(
            [
                {
                    "identifier": "x",
                    "name": "X",
                    "key_opta": "a",
                    "key_pulse": "",
                    "other": "ignored",
                }
            ],
            sink,
            kind="people",
        )
        _, _, _, keys = sink.calls[0][1]
        assert keys == {"key_opta": "a"}


# --------------------------------------------------------------------------- #
# Django sink + command (real DB)
# --------------------------------------------------------------------------- #
class TestDjangoSink:
    def test_people_import_creates_stubs_and_map_rows(self) -> None:
        call_command(
            "import_cricsheet_register",
            f"--people={PEOPLE_CSV}",
        )
        assert Player.objects.count() == 10
        maps = ProviderEntityMap.objects.filter(provider__code="cricsheet", entity_type="player")
        # One primary map row per person (cricsheet identifier p-*).
        assert maps.filter(provider_entity_id__startswith="p-").count() == 10
        # Plus one extra map row per allowlisted key per row (key_opta + key_pulse).
        assert maps.count() == 30
        assert maps.filter(provider_entity_id__startswith="opt-").count() == 10
        assert maps.filter(provider_entity_id__startswith="kp-").count() == 10
        # key_cricbuzz / key_cricinfo are NOT imported.
        assert not maps.filter(provider_entity_id__startswith=("cbz-", "ci-")).exists()
        # unique_name lands on the stub.
        assert Player.objects.get(full_name="Virat Kohli").unique_name == "V Kohli"

    def test_allowlist_respected_and_extendable(self) -> None:
        call_command("import_cricsheet_register", f"--people={PEOPLE_CSV}")
        assert not ProviderEntityMap.objects.filter(provider_entity_id__startswith="cbz-").exists()

        with override_settings(CRICSHEET_KEY_ALLOWLIST=["key_opta", "key_pulse", "key_cricbuzz"]):
            call_command("import_cricsheet_register", f"--people={PEOPLE_CSV}")
        # Enabling key_cricbuzz imports those rows on the re-run.
        assert ProviderEntityMap.objects.filter(provider_entity_id__startswith="cbz-").count() == 10

    def test_run_twice_is_byte_identical(self) -> None:
        call_command("import_cricsheet_register", f"--people={PEOPLE_CSV}")
        first = _digest()
        call_command("import_cricsheet_register", f"--people={PEOPLE_CSV}")
        second = _digest()
        assert second == first
        assert second[0] == 10

    def test_dry_run_writes_nothing(self) -> None:
        call_command("import_cricsheet_register", f"--people={PEOPLE_CSV}", "--dry-run")
        assert _digest() == (0, 0)

    def test_names_variant_sets_known_as_once(self) -> None:
        call_command("import_cricsheet_register", f"--people={PEOPLE_CSV}")
        call_command("import_cricsheet_register", f"--names={NAMES_CSV}")

        player = Player.objects.get(full_name="Virat Kohli")
        # First variant (p-0001 -> "V Kohli") populates known_as; the second
        # (p-0001 -> "Virat") is skipped (D7).
        assert player.known_as == "V Kohli"
        assert Player.objects.get(full_name="Rohit Sharma").known_as == "Rohit Gurunath Sharma"
        assert Player.objects.get(full_name="Ben Stokes").known_as == "Benjamin Andrew Stokes"
        assert Player.objects.get(full_name="Kane Williamson").known_as == "KS Williamson"
        assert Player.objects.get(full_name="Babar Azam").known_as == "Babar Azam Khan"

    def test_venues_register_imports_venues_no_keys(self) -> None:
        call_command("import_cricsheet_register", f"--venues={VENUES_CSV}")
        assert Venue.objects.count() == 3
        assert Venue.objects.filter(name="Lord's").exists()
        # No venue keys imported (key_cricinfo never imported by default).
        assert not ProviderEntityMap.objects.filter(
            provider_entity_id__startswith="ci-ground-"
        ).exists()
        # No Grounds seeded from the register file (that is curated-only).
        assert Ground.objects.count() == 0

    def test_curated_venues_csv_seeds_venues_and_grounds(self) -> None:
        call_command("import_cricsheet_register", f"--people={PEOPLE_CSV}")
        # No --venues: the curated seed CSV is used and also seeds grounds.
        assert Venue.objects.count() == 9
        assert Ground.objects.count() == 9
        assert Venue.objects.filter(name="Lord's").exists()
        lord = Venue.objects.get(name="Lord's")
        assert lord.country is not None and lord.country.iso_code == "GB"
        assert Ground.objects.filter(venue=lord, name="Lord's").exists()
        # Idempotent: re-running adds nothing.
        call_command("import_cricsheet_register", f"--people={PEOPLE_CSV}")
        assert Venue.objects.count() == 9
        assert Ground.objects.count() == 9

    def test_bad_rows_write_errors_file_without_crash(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad_people.csv"
        bad.write_text(
            "identifier,name\nok,Good\n,NoId\nOnlyName,\n",
            encoding="utf-8",
        )
        call_command("import_cricsheet_register", f"--people={bad}")
        errors_path = Path(str(bad) + ".errors.csv")
        assert errors_path.exists()
        with errors_path.open(encoding="utf-8") as handle:
            errors = list(csv.DictReader(handle))
        assert {e["row"] for e in errors} == {"2", "3"}
        # The good row still imported.
        assert Player.objects.filter(full_name="Good").exists()

    def test_dry_run_does_not_write_errors_file(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad_people.csv"
        bad.write_text("identifier,name\n,NoId\n", encoding="utf-8")
        call_command("import_cricsheet_register", f"--people={bad}", "--dry-run")
        assert not Path(str(bad) + ".errors.csv").exists()
        assert _digest() == (0, 0)


class TestReportShape:
    def test_report_aggregates(self) -> None:
        sink = FakeSink()
        report = import_register(
            [
                {"identifier": "a", "name": "A"},
                {"identifier": "b", "name": "B"},
                {"name": "no-id"},
            ],
            sink,
            kind="people",
        )
        assert report.read == 3
        assert report.created == 2
        assert report.failed == 1
        assert isinstance(report, ImportReport)
