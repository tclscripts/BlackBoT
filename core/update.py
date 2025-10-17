# update.py

# ───────────────────────────────────────────────
# Core Bot AutoUpdate module
# ───────────────────────────────────────────────

import os
import shutil
import zipfile
import tempfile
import re
from pathlib import Path

GITHUB_REPO = "https://github.com/tclscripts/BlackBoT"
BRANCH = "main"
VERSION_FILE = "VERSION"
CONFIG_FILE = "settings.py"

def read_local_version():
    if os.path.exists(Path(__file__).resolve().parent.parent / VERSION_FILE):
        with open(Path(__file__).resolve().parent.parent / VERSION_FILE, "r") as f:
            return f.read().strip()
    return "0.0.0"

def fetch_remote_version():
    import requests
    url = f"{GITHUB_REPO}/raw/{BRANCH}/{VERSION_FILE}"
    r = requests.get(url, timeout=15)
    if r.status_code == 200:
        return r.text.strip()
    return None

def parse_settings(file_path):
    settings = {}
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
    # Caută primul director rezultat din unzip (de obicei "BlackBoT-main")
    for entry in tmpdir.iterdir():
        if entry.is_dir():
            return entry
    raise RuntimeError("Zip extracted but no directory found.")

def _project_root() -> Path:
    # core/update.py -> core/ -> project root
    return Path(__file__).resolve().parent.parent

def _copy_file_atomic(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Copiere într-un tmp și apoi replace atomic (unde OS permite)
    tmp = dst.with_suffix(dst.suffix + ".updtmp")
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)

def update_from_github(self, feedback):
    import requests

    local_version = read_local_version()
    remote_version = fetch_remote_version()

    if not remote_version or remote_version <= local_version:
        self.send_message(feedback, f"✅ Already up to date (version {local_version})")
        return

    self.send_message(feedback, f"🔄 Update available: {remote_version} (current: {local_version})")

    zip_url = f"{GITHUB_REPO}/archive/refs/heads/{BRANCH}.zip"

    try:
        with tempfile.TemporaryDirectory() as tmpdir_str:
            tmpdir = Path(tmpdir_str)
            zip_path = tmpdir / "update.zip"

            r = requests.get(zip_url, timeout=60)
            r.raise_for_status()
            with open(zip_path, "wb") as f:
                f.write(r.content)

            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(tmpdir)

            extracted_dir = _choose_extracted_root(tmpdir)
            project_root = _project_root()

            self.send_message(feedback, f"📦 Extracted: {extracted_dir.name}")
            self.send_message(feedback, f"📁 Project root: {project_root}")

            # Backup & merge settings
            local_settings = project_root / CONFIG_FILE
            old_settings = parse_settings(local_settings) if local_settings.exists() else {}

            # Copiază TOT (mai puțin settings.py) în proiect
            for root, _, files in os.walk(extracted_dir):
                root = Path(root)
                rel_path = root.relative_to(extracted_dir)
                dest_dir = project_root / rel_path

                for file in files:
                    src = root / file
                    dst = dest_dir / file

                    if file == CONFIG_FILE:
                        with open(src, "r", encoding="utf-8") as f:
                            new_content = f.read()
                        merged = merge_settings(old_settings, new_content)
                        with open(local_settings, "w", encoding="utf-8") as f:
                            f.write(merged)
                        self.send_message(feedback, f"🛠️ Merged settings preserved in {CONFIG_FILE}")
                    elif file == "update.zip":
                        continue
                    else:
                        _copy_file_atomic(src, dst)

            # Scrie versiunea nouă în rădăcina proiectului
            with open(project_root / VERSION_FILE, "w", encoding="utf-8") as f:
                f.write(remote_version)

            self.send_message(feedback, "✅ Update complete. Restarting...")
            self.restart("✨ Updating... Be right back with fresh powers!")

    except Exception as e:
        self.send_message(feedback, f"❌ Update failed: {e}")
