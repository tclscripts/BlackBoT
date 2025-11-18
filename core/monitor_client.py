# core/monitor_client.py
import os, json, time, stat, platform, hmac, hashlib, requests, socket, uuid
from core.environment_config import config
from core.log import get_logger
from core.environment_config import config as cfg

# ───────────────────────────────────────────────
# Global Loggers
# ───────────────────────────────────────────────
logger = get_logger("monitor")
hb_logger = get_logger("heartbeat")  # log separat, util pentru debug rapid

CRED_PATH = os.getenv(
    "BLACKBOT_CRED",
    cfg.get("monitor_cred_file", "./files/cred.json")
)

# ───────────────────────────────────────────────
# Credentials (disk)
# ───────────────────────────────────────────────
def _read_creds():
    """Read local credential file safely."""
    try:
        with open(CRED_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not data.get("bot_id") or not data.get("hmac_secret"):
            logger.warning("Monitor creds file missing keys (bot_id or hmac_secret)")
            return None
        logger.debug("Monitor credentials loaded successfully")
        return data
    except FileNotFoundError:
        logger.info("Monitor credentials file not found — enrollment required")
        return None
    except json.JSONDecodeError as e:
        logger.error("Failed to parse monitor creds JSON: %s", e)
        return None
    except Exception as e:
        logger.error("Unexpected error reading monitor creds: %s", e)
        return None


def _write_creds(data: dict):
    """Write credentials to disk safely."""
    d = os.path.dirname(CRED_PATH)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)
    tmp = CRED_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, CRED_PATH)
        os.chmod(CRED_PATH, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
        logger.info("Monitor credentials saved successfully")
    except Exception as e:
        logger.error("Failed to write monitor credentials: %s", e)


# ───────────────────────────────────────────────
# API helpers
# ───────────────────────────────────────────────
def _api_base():
    """Return base API URL or disable monitoring if turned off."""
    if not getattr(config, "monitor_status", False):
        logger.debug("Monitoring disabled via settings.monitor_status = False")
        return None
    return "http://87.106.56.156:8000"


def _machine_fingerprint() -> str:
    """Generate machine fingerprint (no PII)."""
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
    logger.debug("Machine fingerprint generated")
    return fp


def ensure_enrollment(nickname: str, version: str, server_str: str) -> dict | None:
    """Ensure the bot is enrolled with the monitor server."""
    base = _api_base()
    if not base:
        logger.info("Monitoring is disabled — skipping enrollment")
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
    except requests.RequestException as e:
        logger.warning("Monitor enrollment request failed: %s", e)
        return None

    if r.status_code == 200:
        try:
            data = r.json()
        except Exception as e:
            logger.error("Invalid JSON in enrollment response: %s", e)
            return None

        bot_id = data.get("bot_id")
        hmac_secret = data.get("hmac_secret")
        if bot_id and hmac_secret:
            creds = {"bot_id": bot_id, "hmac_secret": hmac_secret}
            _write_creds(creds)
            logger.info("Enrollment successful: bot_id=%s", bot_id)
            return creds
        logger.warning("Enrollment response missing bot_id or hmac_secret")
        return None

    elif r.status_code in (201, 202):
        logger.info("Enrollment pending (status %s) — waiting for approval", r.status_code)
        return None
    else:
        logger.warning("Enrollment rejected or unexpected status: %s", r.status_code)
        return None


def _build_headers_and_body(hmac_secret: str | None, payload: dict):
    """Build signed headers and JSON body for monitor API."""
    if not hmac_secret:
        logger.warning("Cannot build monitor headers: missing HMAC secret")
        return None, None

    try:
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
    except Exception as e:
        logger.error("Failed to build monitor request headers: %s", e)
        return None, None


# ───────────────────────────────────────────────
# API calls
# ───────────────────────────────────────────────
def send_monitor_offline(bot_id: str | None, hmac_secret: str | None) -> bool:
    """Notify monitor that this bot is going offline."""
    base = _api_base()
    if not base or not bot_id or not hmac_secret:
        logger.debug("Offline report skipped (missing parameters or disabled)")
        return False

    url = f"{base.rstrip('/')}/api/offline"
    payload = {"bot_id": bot_id}
    headers, body = _build_headers_and_body(hmac_secret, payload)
    if headers is None:
        return False

    try:
        resp = requests.post(url, headers=headers, data=body, timeout=10)
        if resp.status_code == 200:
            return True
        else:
            logger.warning("Offline report failed (HTTP %s): %s", resp.status_code, (resp.text or "")[:300])
            return False
    except requests.RequestException as e:
        logger.warning("Error sending offline report: %s", e)
        return False


def send_heartbeat(bot_id: str | None, hmac_secret: str | None, payload: dict) -> bool:
    """Send periodic heartbeat to monitor."""
    base = _api_base()
    if not base or not bot_id or not hmac_secret:
        hb_logger.debug("Heartbeat skipped (disabled or missing credentials)")
        return False

    url = f"{base.rstrip('/')}/api/heartbeat"
    full_payload = dict(payload)
    full_payload.setdefault("bot_id", bot_id)

    headers, body = _build_headers_and_body(hmac_secret, full_payload)
    if headers is None:
        return False

    try:
        r = requests.post(url, data=body, headers=headers, timeout=10)
        if r.status_code == 200:
            return True
        # non-200 → log util
        hb_logger.warning("Heartbeat failed (HTTP %s): %s", r.status_code, (r.text or "")[:300])
        return False
    except requests.RequestException as e:
        hb_logger.warning("Heartbeat error: %s", e, exc_info=True)
        return False


# ───────────────────────────────────────────────
# Worker (guardian/threadworker-ready)
# ───────────────────────────────────────────────
def _guess_server_str() -> str:
    """Build a user-friendly 'server:port' from settings.servers."""
    try:
        first = (getattr(config, "servers", []) or [[None, None]])[0]
        if first and len(first) >= 2 and first[0] and first[1]:
            return f"{first[0]}:{first[1]}"
    except Exception:
        pass
    return "unknown:0"


def _read_local_version() -> str:
    try:
        from core.update import read_local_version
        return read_local_version()
    except Exception:
        return "0.0.0"


def monitor_worker(stop_event, beat):

    interval = int(getattr(config, "monitor_interval", 30))
    max_backoff = int(getattr(config, "monitor_max_backoff", 300))
    backoff = interval

    nickname = getattr(config, "nickname", "BlackBoT")
    version = _read_local_version()
    server_str = _guess_server_str()

    creds = _read_creds()
    if not creds:
        creds = ensure_enrollment(nickname, version, server_str)

    bot_id = (creds or {}).get("bot_id")
    hmac_secret = (creds or {}).get("hmac_secret")

    if bot_id and hmac_secret:
        hb_logger.info("✅ Monitor enrolled (bot_id=%s...). Starting heartbeat.", str(bot_id)[:8])
    else:
        hb_logger.info("ℹ️ Monitor not enrolled yet; will retry with backoff.")

    try:
        while not stop_event.is_set():
            beat()
            if not (bot_id and hmac_secret):
                creds = ensure_enrollment(nickname, version, server_str)
                if creds:
                    bot_id = creds.get("bot_id")
                    hmac_secret = creds.get("hmac_secret")
                    if bot_id and hmac_secret:
                        hb_logger.info("✅ Monitor enrolled (bot_id=%s...). Starting heartbeat.", str(bot_id)[:8])
                        backoff = interval

                if stop_event.wait(backoff):
                    break
                backoff = min(backoff * 2, max_backoff)
                continue

            try:
                import psutil, os
                proc = psutil.Process(os.getpid())
                payload = {
                    "bot_id": bot_id,
                    "ts": time.time(),
                    "pid": os.getpid(),
                    "cpu": proc.cpu_percent(interval=None),
                    "rss_mb": proc.memory_info().rss / (1024 * 1024),
                    "threads": len(__import__("threading").enumerate()),
                    "system": f"{platform.system()} {platform.release()}",
                    "version": version,
                    "nickname": nickname,
                }
            except Exception:
                payload = {"bot_id": bot_id, "ts": time.time()}

            ok = send_heartbeat(bot_id, hmac_secret, payload)
            if ok:
                backoff = interval
            else:
                backoff = min(backoff * 2, max_backoff)
                hb_logger.debug("Heartbeat loop backoff (next=%ss)", backoff)

            if stop_event.wait(backoff):
                break

    finally:
        try:
            if bot_id and hmac_secret:
                send_monitor_offline(bot_id, hmac_secret)
        except Exception:
            pass
