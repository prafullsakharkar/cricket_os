"""The scoring package must import with Django (and web frameworks) blocked."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    """Locate the repository root even when mutmut runs a copy under ``mutants/``.

    ``mutants/`` also carries a ``pyproject.toml`` and a full ``apps`` package, so
    the authoritative marker is the ``.git`` directory; ``apps/scoring/__init__.py``
    is only a fallback for non-git checkouts.
    """

    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / ".git").exists():
            return parent
    for parent in current.parents:
        if (parent / "apps" / "scoring" / "__init__.py").exists():
            return parent
    raise RuntimeError("could not locate repository root")


ROOT = _repo_root()

CODE = """
import sys

class Block:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'django', 'rest_framework', 'celery', 'channels', 'environ'}:
            raise ImportError('blocked third-party import: ' + fullname)
        return None

sys.meta_path.insert(0, Block())
import apps.scoring as scoring
assert scoring.rebuild_innings
bad = [name for name in sys.modules if name.split('.')[0] in {'django', 'rest_framework', 'celery'}]
assert not bad, bad
print('pure-import-ok')
"""


def test_import_without_django() -> None:
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
