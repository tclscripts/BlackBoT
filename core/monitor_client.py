# core/monitor_client.py
import os, json, time, stat, platform, hmac, hashlib, requests, socket, uuid


# Unde se salvează credențialele botului
CRED_PATH = os.getenv("BLACKBOT_CRED", "./files/cred.json")

def _read_creds():
    try:
        with open(CRED_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not data.get("bot_id") or not data.get("hmac_secret"):
            return None
        return data
    except Exception as e:
        return None

def _write_creds(data: dict):
    d = os.path.dirname(CRED_PATH)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)
    tmp = CRED_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, CRED_PATH)
    try:
        os.chmod(CRED_PATH, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
    except Exception:
        pass

def _api_base():
    base = "http://87.106.56.156:8000"
    return base

def _machine_fingerprint() -> str:
    parts = []
    try:
        if os.path.exists("/etc/machine-id"):
            parts.append(open("/etc/machine-id","r").read().strip())
    except Exception:
        pass
    try:
        parts.append(socket.gethostname())
    except Exception:
        pass
    try:
        parts.append(str(uuid.getnode()))
    except Exception:
        pass
    raw = "|".join(p for p in parts if p)
    fp = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return fp

def ensure_enrollment(nickname: str, version: str, server_str: str) -> dict | None:
    creds = _read_creds()
    if creds:
        return creds

    base = _api_base()
    if not base:
        return None

    fp = _machine_fingerprint()
    payload = {
        "fingerprint": fp,
        "nickname": nickname,
        "system": f"{platform.system()} {platform.release()}",
        "version": version,
        "server": server_str,
    }

    try:
        r = requests.post(f"{base.rstrip('/')}/api/request_enroll", json=payload, timeout=10)
    except Exception as e:
        return None

    if r.status_code == 200:
        data = r.json()
        creds = {"bot_id": data["bot_id"], "hmac_secret": data["hmac_secret"]}
        _write_creds(creds)
        return creds
    elif r.status_code in (201, 202):
        return None
    else:
        return None

def send_heartbeat(bot_id: str, hmac_secret: str, payload: dict) -> bool:
    base = _api_base()
    if not base:
        return False

    url = f"{base.rstrip('/')}/api/heartbeat"

    # inject bot_id dacă nu e deja
    full_payload = dict(payload)
    full_payload.setdefault("bot_id", bot_id)

    body = json.dumps(full_payload, separators=(",", ":")).encode("utf-8")
    ts = str(time.time())
    sig = hmac.new(hmac_secret.encode("utf-8"), body + ts.encode("utf-8"), hashlib.sha256).hexdigest()

    token = os.getenv("MONITOR_TOKEN", "7CWwLTDh6ReQLY7rEe2fk5BvBvEeAg3pF6MqhphewjREsMgLLXDVavlY1VI6o3Lc")
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Timestamp": ts,
        "X-Signature": f"sha256={sig}",
        "Content-Type": "application/json",
    }

    try:
        r = requests.post(url, data=body, headers=headers, timeout=5)
        if r.status_code == 200:
            return True
        else:
            return False
    except Exception as e:
        return False
