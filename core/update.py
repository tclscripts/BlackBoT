# update.py

# ───────────────────────────────────────────────
# Core Bot AutoUpdate module
# ───────────────────────────────────────────────

import os
import shutil
import zipfile
import tempfile
import requests
import sys
import re

GITHUB_REPO = "https://github.com/tclscripts/BlackBoT"
BRANCH = "main"
VERSION_FILE = "VERSION"
CONFIG_FILE = "settings.py"

def read_local_version():
    if os.path.exists(VERSION_FILE):
        with open(VERSION_FILE, "r") as f:
            return f.read().strip()
    return "0.0.0"

def fetch_remote_version():
    url = f"{GITHUB_REPO}/raw/{BRANCH}/{VERSION_FILE}"
    r = requests.get(url)
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
    lines = new_settings_content.splitlines()
    merged = []
    for line in lines:
        match = re.match(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(.+)", line)
        if match:
            var = match.group(1)
            if var in old_settings:
                line = f"{var} = {old_settings[var]}"
        merged.append(line)
    return "\n".join(merged)


def update_from_github(self, feedback):
    local_version = read_local_version()
    remote_version = fetch_remote_version()

    if not remote_version or remote_version <= local_version:
        self.send_message(feedback, f"✅ Already up to date (version {local_version})")
        return

    self.send_message(feedback, f"🔄 Update available: {remote_version} (current: {local_version})")

    zip_url = f"{GITHUB_REPO}/archive/refs/heads/{BRANCH}.zip"

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = os.path.join(tmpdir, "update.zip")

            r = requests.get(zip_url)
            if r.status_code != 200:
                self.send_message(feedback, "❌ Failed to download update.")
                return

            with open(zip_path, "wb") as f:
                f.write(r.content)

            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(tmpdir)

            extracted_dir = os.path.join(tmpdir, os.listdir(tmpdir)[0])

            # Backup settings
            if os.path.exists(CONFIG_FILE):
                old_settings = parse_settings(CONFIG_FILE)
            else:
                old_settings = {}

            for root, _, files in os.walk(extracted_dir):
                rel_path = os.path.relpath(root, extracted_dir)
                dest_dir = os.path.join(os.getcwd(), rel_path)
                os.makedirs(dest_dir, exist_ok=True)

                for file in files:
                    src = os.path.join(root, file)
                    dst = os.path.join(dest_dir, file)

                    if file == CONFIG_FILE:
                        with open(src, "r") as f:
                            new_content = f.read()
                        merged = merge_settings(old_settings, new_content)
                        with open(dst, "w") as f:
                            f.write(merged)
                        self.send_message(feedback, f"🛠️ Merged settings preserved in {CONFIG_FILE}")
                    else:
                        shutil.copy2(src, dst)

            # Save new version
            with open(VERSION_FILE, "w") as f:
                f.write(remote_version)

            self.send_message(feedback, "✅ Update complete. Restarting...")
            python = sys.executable
            os.execl(python, python, *sys.argv)

    except Exception as e:
        self.send_message(feedback, f"❌ Update failed: {e}")
