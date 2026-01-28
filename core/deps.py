# core/deps.py
from __future__ import annotations

import os
import sys
import subprocess
from typing import Iterable, List, Optional
from pathlib import Path


def _run(cmd: List[str], *, env: dict | None = None, cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
    )


def parse_requirements_file(requirements_path: str | Path) -> List[str]:
    """
    Citește un fișier requirements.txt și extrage numele pachetelor.

    Suportă:
    - Comentarii (#)
    - Linii goale
    - Version specifiers (==, ~=, >=, etc.)
    - Extras ([standard], [dev], etc.)

    Returns:
        List[str]: Lista de nume pachete (fără versiuni)

    Examples:
        >>> parse_requirements_file('requirements.txt')
        ['fastapi', 'uvicorn', 'pydantic', ...]
    """
    requirements_path = Path(requirements_path)

    if not requirements_path.exists():
        return []

    packages = []

    with open(requirements_path, 'r', encoding='utf-8') as f:
        for line in f:
            # Strip whitespace
            line = line.strip()

            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue

            # Skip -r/-e lines (recursive requirements)
            if line.startswith('-r') or line.startswith('-e'):
                continue

            # Remove comments at end of line
            if '#' in line:
                line = line.split('#')[0].strip()

            # Extract package name (before any version specifier or extras)
            # Examples:
            #   fastapi==0.115.0 -> fastapi
            #   uvicorn[standard]~=0.32.0 -> uvicorn
            #   pydantic -> pydantic
            package = line.split('[')[0]  # Remove extras like [standard]
            for sep in ['==', '~=', '>=', '<=', '!=', '<', '>', '@']:
                if sep in package:
                    package = package.split(sep)[0]
                    break

            package = package.strip()
            if package:
                packages.append(package)

    return packages


def ensure_packages(
        packages: Optional[Iterable[str]] = None,
        *,
        requirements_file: Optional[str | Path] = None,
        python_exec: str | None = None,
        env: dict | None = None,
        cwd: str | None = None
) -> tuple[bool, dict]:
    """
    Verifică pachetele și instalează ce lipsește folosind:
      <python_exec> -m pip show/install ...

    Args:
        packages: Lista de pachete (opțional dacă requirements_file e specificat)
        requirements_file: Path la requirements.txt (opțional)
        python_exec: Python executable
        env: Environment variables
        cwd: Working directory

    Returns:
        tuple[bool, dict]: (success, details)
        - success: True dacă toate pachetele sunt OK sau au fost instalate
        - details: {
            'total': int,
            'already_installed': list[str],
            'newly_installed': list[str],
            'failed': list[str],
            'error_message': str | None,
            'source': str  # 'packages' sau 'requirements.txt'
          }

    Examples:
        # Folosind listă directă
        ok, details = ensure_packages(['fastapi', 'uvicorn'])

        # Folosind requirements.txt
        ok, details = ensure_packages(requirements_file='requirements.txt')

        # Combinație (merge packages + requirements.txt)
        ok, details = ensure_packages(
            packages=['extra-package'],
            requirements_file='requirements.txt'
        )
    """
    python_exec = python_exec or sys.executable

    # Colectează toate pachetele
    all_packages = []
    source = 'packages'

    # Din requirements.txt
    if requirements_file:
        req_path = Path(requirements_file)
        if cwd:
            req_path = Path(cwd) / req_path

        req_packages = parse_requirements_file(req_path)
        all_packages.extend(req_packages)
        source = 'requirements.txt'

    # Din packages parameter
    if packages:
        package_list = [p.strip() for p in packages if p and p.strip()]
        all_packages.extend(package_list)
        if requirements_file:
            source = 'packages + requirements.txt'
        else:
            source = 'packages'

    # Remove duplicates, păstrând ordinea
    seen = set()
    unique_packages = []
    for pkg in all_packages:
        if pkg not in seen:
            seen.add(pkg)
            unique_packages.append(pkg)

    all_packages = unique_packages

    details = {
        'total': len(all_packages),
        'already_installed': [],
        'newly_installed': [],
        'failed': [],
        'error_message': None,
        'source': source
    }

    missing = []
    for pkg in all_packages:
        try:
            r = _run([python_exec, "-m", "pip", "show", pkg], env=env, cwd=cwd)
            if r.returncode != 0:
                missing.append(pkg)
            else:
                details['already_installed'].append(pkg)
        except Exception as e:
            missing.append(pkg)
            if not details['error_message']:
                details['error_message'] = f"Error checking {pkg}: {e}"

    if not missing:
        return True, details

    # upgrade pip util când apar dependențe noi
    try:
        _run([python_exec, "-m", "pip", "install", "--upgrade", "pip"], env=env, cwd=cwd)
    except Exception:
        pass

    # instalează tot într-un singur apel (mai rapid)
    r = _run([python_exec, "-m", "pip", "install", *missing], env=env, cwd=cwd)

    if r.returncode == 0:
        details['newly_installed'] = missing
        return True, details
    else:
        # Verifică individual ce s-a instalat și ce a eșuat
        for pkg in missing:
            check = _run([python_exec, "-m", "pip", "show", pkg], env=env, cwd=cwd)
            if check.returncode == 0:
                details['newly_installed'].append(pkg)
            else:
                details['failed'].append(pkg)

        # Salvează mesajul de eroare
        if r.stderr:
            details['error_message'] = r.stderr.strip()[:500]  # Primele 500 caractere

        return False, details