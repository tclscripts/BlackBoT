# update.py

# ───────────────────────────────────────────────
# Core Bot AutoUpdate module
# ───────────────────────────────────────────────

import os
import shutil
import zipfile
import tempfile
import re
import logging
from pathlib import Path
from twisted.internet import reactor

# configurare logger local pentru update
logger = logging.getLogger("BlackBoT.update")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

GITHUB_REPO = "https://github.com/tclscripts/BlackBoT"
BRANCH = "main"
VERSION_FILE = "VERSION"
CONFIG_FILE = "settings.py"

def read_local_version():
    path = Path(__file__).resolve().parent.parent / VERSION_FILE
    if path.exists():
        with open(path, "r") as f:
            v = f.read().strip()
            logger.info(f"Local version found: {v}")
            return v
    logger.info("No local VERSION file, defaulting to 0.0.0")
    return "0.0.0"

def fetch_remote_version():
    import requests
    url = f"{GITHUB_REPO}/raw/{BRANCH}/{VERSION_FILE}"
    logger.info(f"Fetching remote VERSION from {url}")
    r = requests.get(url, timeout=15)
    if r.status_code == 200:
        v = r.text.strip()
        logger.info(f"Remote VERSION = {v}")
        return v
    logger.warning(f"Failed to fetch remote VERSION (status={r.status_code})")
    return None

def parse_settings(file_path):
    settings = {}
    if not os.path.exists(file_path):
        return settings
    with open(file_path, "r") as f:
        for line in f:
            match = re.match(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(.+)", line)
            if match:
                var, value = match.groups()
                settings[var] = value.strip()
    return settings

def merge_settings(old_settings, new_settings_content):
    merged = []
    seen_vars = set()

    for line in new_settings_content.splitlines():
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            merged.append(line)
            continue

        match = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(.+)", line)
        if match:
            var, new_value = match.groups()
            seen_vars.add(var)

            if var in old_settings:
                comment_split = line.split("#", 1)
                comment = f" # {comment_split[1].strip()}" if len(comment_split) > 1 else ""
                merged.append(f"{var} = {old_settings[var]}{comment}")
            else:
                merged.append(line)
        else:
            merged.append(line)

    for var in old_settings:
        if var not in seen_vars:
            merged.append(f"{var} = {old_settings[var]}")

    return "\n".join(merged)

def _choose_extracted_root(tmpdir: Path) -> Path:
    for entry in tmpdir.iterdir():
        if entry.is_dir():
            return entry
    raise RuntimeError("Zip extracted but no directory found.")

def _replace_dir(src_dir: Path, dst_dir: Path):
    if dst_dir.exists():
        logger.info(f"Removing old directory: {dst_dir}")
        shutil.rmtree(dst_dir)
    logger.info(f"Copying new directory {src_dir} -> {dst_dir}")
    shutil.copytree(src_dir, dst_dir)

def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent

def _copy_file_atomic(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".updtmp")
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)
    logger.debug(f"Copied {src} -> {dst}")

def update_from_github(self, feedback):
    import requests

    local_version = read_local_version()
    remote_version = fetch_remote_version()

    if not remote_version:
        logger.warning("Remote VERSION not available, aborting update.")
        return
    if remote_version == local_version:
        logger.info(f"Already up to date: {local_version}")
        return

    logger.info(f"Update available: {remote_version} (current {local_version})")
    zip_url = f"{GITHUB_REPO}/archive/refs/heads/{BRANCH}.zip"

    try:
        with tempfile.TemporaryDirectory() as tmpdir_str:
            tmpdir = Path(tmpdir_str)
            zip_path = tmpdir / "update.zip"

            logger.info(f"Downloading archive {zip_url}")
            r = requests.get(zip_url, timeout=60)
            r.raise_for_status()
            with open(zip_path, "wb") as f:
                f.write(r.content)

            logger.info(f"Extracting {zip_path}")
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(tmpdir)

            extracted_dir = _choose_extracted_root(tmpdir)
            project_root = _project_root()
            logger.info(f"Extracted to {extracted_dir}, project root is {project_root}")

            # replace complet core/
            src_core = extracted_dir / "core"
            dst_core = project_root / "core"
            if src_core.exists():
                py_files = list(src_core.rglob("*.py"))
                logger.info(f"core/ in archive has {len(py_files)} files")
                for f in py_files[:10]:
                    logger.debug(f"   {f.relative_to(extracted_dir)}")
                _replace_dir(src_core, dst_core)

            local_settings = project_root / CONFIG_FILE
            old_settings = parse_settings(local_settings) if local_settings.exists() else {}

            for root, _, files in os.walk(extracted_dir):
                root = Path(root)
                rel_path = root.relative_to(extracted_dir)

                if rel_path.parts and rel_path.parts[0] == "core":
                    continue

                dest_dir = project_root / rel_path
                for file in files:
                    src = root / file
                    dst = dest_dir / file

                    if file == CONFIG_FILE:
                        logger.info("Merging settings.py")
                        with open(src, "r", encoding="utf-8") as f:
                            new_content = f.read()
                        merged = merge_settings(old_settings, new_content)
                        with open(local_settings, "w", encoding="utf-8") as f:
                            f.write(merged)
                    elif file == "update.zip":
                        continue
                    else:
                        logger.debug(f"Copying {src.relative_to(extracted_dir)}")
                        _copy_file_atomic(src, dst)

            with open(project_root / VERSION_FILE, "w", encoding="utf-8") as f:
                f.write(remote_version)
            logger.info("Update complete, restarting bot in 2s…")
            reactor.callLater(2, self.restart, "✨ Updating... Be right back with fresh powers!")

    except Exception as e:
        import traceback
        logger.error(f"Update failed: {e}\n{traceback.format_exc()}")
