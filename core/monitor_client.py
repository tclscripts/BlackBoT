import os, json, time, stat, platform, hmac, hashlib, requests, socket, uuid
import settings as s

CRED_PATH = os.getenv("BLACKBOT_CRED", "./files/cred.json")


def _read_creds():
    try:
        with open(CRED_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not data.get("bot_id") or not data.get("hmac_secret"):
            return None
        return data
    except Exception:
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
    """
    Global kill-switch: if monitor_status is False, disable all monitor calls.
    """
    if not getattr(s, "monitor_status", False):
        return None
    return "http://87.106.56.156:8000"


def _machine_fingerprint() -> str:
    parts = []
    try:
        if os.path.exists("/etc/machine-id"):
            parts.append(open("/etc/machine-id", "r").read().strip())
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
    """
    Returns credentials dict with bot_id & hmac_secret, or None if enrollment
    is disabled or not yet provisioned.
    """
    base = _api_base()
    if not base:
        return None

    creds = _read_creds()
    if creds:
        return creds

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
    except Exception:
        return None

    if r.status_code == 200:
        try:
            data = r.json()
        except Exception:
            return None
        bot_id = data.get("bot_id")
        hmac_secret = data.get("hmac_secret")
        if bot_id and hmac_secret:
            creds = {"bot_id": bot_id, "hmac_secret": hmac_secret}
            _write_creds(creds)
            return creds
        return None
    elif r.status_code in (201, 202):
        # Pending or accepted; no credentials yet.
        return None
    else:
        return None


def _build_headers_and_body(hmac_secret: str | None, payload: dict):
    """
    Returns (headers, body) or (None, None) if secret is missing.
    """
    if not hmac_secret:
        return None, None

    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ts = str(time.time())
    sig = hmac.new(hmac_secret.encode("utf-8"), body + ts.encode("utf-8"), hashlib.sha256).hexdigest()

    token = os.getenv(
        "MONITOR_TOKEN",
        "7CWwLTDh6ReQLY7rEe2fk5BvBvEeAg3pF6MqhphewjREsMgLLXDVavlY1VI6o3Lc",
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Timestamp": ts,
        "X-Signature": f"sha256={sig}",
        "Content-Type": "application/json",
    }
    return headers, body


def send_monitor_offline(bot_id: str | None, hmac_secret: str | None) -> bool:
    """
    Safe: silently no-ops if disabled or creds missing.
    """
    base = _api_base()
    if not base or not bot_id or not hmac_secret:
        return False

    url = f"{base.rstrip('/')}/api/offline"
    payload = {"bot_id": bot_id}
    headers, body = _build_headers_and_body(hmac_secret, payload)
    if headers is None:
        return False

    try:
        resp = requests.post(url, headers=headers, data=body, timeout=5)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def send_heartbeat(bot_id: str | None, hmac_secret: str | None, payload: dict) -> bool:
    """
    Safe: silently no-ops if disabled or creds missing.
    """
    base = _api_base()
    if not base or not bot_id or not hmac_secret:
        return False

    url = f"{base.rstrip('/')}/api/heartbeat"
    full_payload = dict(payload)
    full_payload.setdefault("bot_id", bot_id)

    headers, body = _build_headers_and_body(hmac_secret, full_payload)
    if headers is None:
        return False

    try:
        r = requests.post(url, data=body, headers=headers, timeout=5)
        return r.status_code == 200
    except requests.RequestException:
        return False
