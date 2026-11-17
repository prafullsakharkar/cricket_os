"""The fantasy engine must import and run with Django (and every Django-carrying
first-party package) blocked.

``apps.fantasy.engine`` is stdlib-only — the same purity contract as the
``scoring`` engine. In particular it must not import ``apps.core`` (whose
``enums`` imports Django), the ORM, DRF, Celery, or channels. The engine is a
pure function of (ruleset, snapshot): no database, no clock, no network.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / ".git").exists():
            return parent
    for parent in current.parents:
        if (parent / "apps" / "fantasy" / "engine" / "__init__.py").exists():
            return parent
    raise RuntimeError("could not locate repository root")


ROOT = _repo_root()

CODE = """
import sys

class Block:
    def find_spec(self, fullname, path=None, target=None):
        root = fullname.split('.')[0]
        if root in {'django', 'rest_framework', 'celery', 'channels', 'environ'}:
            raise ImportError('blocked third-party import: ' + fullname)
        # apps.core imports Django; the engine must not touch it either.
        if root == 'apps' and fullname.startswith('apps.core'):
            raise ImportError('blocked first-party import: ' + fullname)
        return None

sys.meta_path.insert(0, Block())

from apps.fantasy.engine import (FantasyMatchSnapshot, MatchContext, PlayerLine,
    RulesetSpec, SnapshotCoverage, compute, content_hash, point_rule)
from decimal import Decimal

full = SnapshotCoverage(has_ball_data=True, has_dismissal_kinds=True, has_fielder_credit_types=True)
ruleset = RulesetSpec(
    ruleset_id='r', version=1,
    rules=(point_rule('runs', 'per_unit', '1.00', 'batting'),),
)
snapshot = FantasyMatchSnapshot(
    match=MatchContext(match_id='m1', result_type='NORMAL'),
    coverage=full,
    player_lines=(PlayerLine(player_id='p1', is_playing_xi=True, runs=5),),
)
result = compute(ruleset, snapshot)
assert result.players[0].total == Decimal('5.00'), result
assert len(content_hash(ruleset)) == 64
bad = [name for name in sys.modules
       if name.split('.')[0] in {'django', 'rest_framework', 'celery', 'channels', 'environ'}
       or name.startswith('apps.core')]
assert not bad, bad
print('pure-import-ok')
"""


def test_engine_imports_and_computes_without_django() -> None:
    # Force the subprocess onto the real repository: mutmut runs a copy of the
    # tests from ``mutants/`` whose PYTHONPATH would shadow ``apps`` with an
    # incomplete copy of the package.
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    completed = subprocess.run(
        [sys.executable, "-c", CODE],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert "pure-import-ok" in completed.stdout
