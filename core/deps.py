# core/deps.py
from __future__ import annotations

import os
import sys
import subprocess
from typing import Iterable, List

def _run(cmd: List[str], *, env: dict | None = None, cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
    )

def ensure_packages(packages: Iterable[str], *, python_exec: str | None = None, env: dict | None = None, cwd: str | None = None) -> bool:
    """
    Verifică pachetele și instalează ce lipsește folosind:
      <python_exec> -m pip show/install ...

    Returnează True dacă totul e OK, False dacă instalarea a eșuat.
    """
    python_exec = python_exec or sys.executable
    packages = [p.strip() for p in packages if p and p.strip()]

    missing = []
    for pkg in packages:
        try:
            r = _run([python_exec, "-m", "pip", "show", pkg], env=env, cwd=cwd)
            if r.returncode != 0:
                missing.append(pkg)
        except Exception:
            missing.append(pkg)

    if not missing:
        return True

    # upgrade pip util când apar dependențe noi
    try:
        _run([python_exec, "-m", "pip", "install", "--upgrade", "pip"], env=env, cwd=cwd)
    except Exception:
        pass

    # instalează tot într-un singur apel (mai rapid)
    r = _run([python_exec, "-m", "pip", "install", *missing], env=env, cwd=cwd)
    return r.returncode == 0
