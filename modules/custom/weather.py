"""
Weather Plugin (OOP / PluginBase)
=================================

Commands (single entrypoint):
  .weather [city]              -> show weather (or default city if omitted)
  .weather city <city>         -> save default city (per-nick)
  .weather apikey <key>        -> set OpenWeatherMap API key (restricted)
  .weather help                -> help (from description)

Notes:
- Uses OpenWeatherMap API.
- Stores default city in sqlite table weather_prefs (per nick).
- API key stored in instances/{instance_name}/.env (OPENWEATHER_API_KEY=...).
- Threaded execution via deferToThread (Twisted).

Access policy (single source of truth):
- apikey: N/n/m (BossOwner/Owner/Manager) [OR semantics]

Based on existing weather module logic. :contentReference[oaicite:1]{index=1}
"""

import os
import sqlite3
from pathlib import Path
from datetime import datetime

import requests
from twisted.internet.threads import deferToThread

from core.log import get_logger
from core.environment_config import config
from core.plugin_manager import PluginBase

logger = get_logger("weather")

PLUGIN_INFO = {
    "name": "weather",
    "version": "1.0.0",
    "author": "BlackBoT Team",
    "description": "Weather plugin (OpenWeatherMap) with prefs + API key management",
    "dependencies": ["requests", "twisted"],
}

# ---------------------------------------------------------------------------
# Single source of truth for access rules
# ---------------------------------------------------------------------------

ACCESS = {
    # api key management: BossOwner/Owner/Manager (OR semantics)
    "apikey": "Nnm",
}

# ---------------------------------------------------------------------------
# OpenWeatherMap config
# ---------------------------------------------------------------------------

API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
BASE_URL = "http://api.openweathermap.org/data/2.5/weather"

WEATHER_ICONS = {
    "Clear": "☀️",
    "Clouds": "☁️",
    "Rain": "🌧️",
    "Drizzle": "🌦️",
    "Thunderstorm": "⛈️",
    "Snow": "❄️",
    "Mist": "🌫️",
    "Fog": "🌁",
    "Smoke": "💨",
    "Haze": "😶‍🌫️",
    "Tornado": "🌪️",
}


# =============================================================================
# Helper: Environment Update
# =============================================================================

def _update_env_file(new_key: str) -> bool:
    """Actualizează sau adaugă cheia în fișierul .env cu formatare frumoasă."""
    instance_name = config.instance_name
    env_path = Path(f"instances/{instance_name}/.env")
    env_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        weather_section = f"""# ═══════════════════════════════════════════════════════════════════
# Weather Module Configuration
# ═══════════════════════════════════════════════════════════════════

# OpenWeatherMap API Key
# Get your free API key at: https://openweathermap.org/api
OPENWEATHER_API_KEY={new_key}

"""

        if not env_path.exists():
            with open(env_path, "w", encoding="utf-8") as f:
                f.write(weather_section)
            logger.info(f"Created .env with weather section at {env_path}")
            return True

        with open(env_path, "r", encoding="utf-8") as f:
            content = f.read()

        lines = content.split("\n")

        weather_section_exists = False
        weather_key_line = -1
        notes_section_line = -1

        for i, line in enumerate(lines):
            if line.strip().startswith("OPENWEATHER_API_KEY="):
                weather_key_line = i
                for j in range(max(0, i - 10), i):
                    if "Weather Module" in lines[j] or "Weather Configuration" in lines[j]:
                        weather_section_exists = True
                        break

            if "# Notes" in line and i > 0 and "═" in lines[i - 1]:
                notes_section_line = i - 1

        new_lines = []
        inserted = False

        if weather_section_exists and weather_key_line >= 0:
            for i, line in enumerate(lines):
                if i == weather_key_line:
                    new_lines.append(f"OPENWEATHER_API_KEY={new_key}")
                    inserted = True
                else:
                    new_lines.append(line)

        elif notes_section_line >= 0:
            for i, line in enumerate(lines):
                if i == notes_section_line:
                    new_lines.append(weather_section.rstrip("\n"))
                    new_lines.append("")
                    new_lines.append(line)
                    inserted = True
                else:
                    new_lines.append(line)

        else:
            new_lines = lines
            if new_lines and new_lines[-1].strip():
                new_lines.append("")
            new_lines.append(weather_section.rstrip("\n"))
            inserted = True

        if not inserted:
            return False

        with open(env_path, "w", encoding="utf-8") as f:
            f.write("\n".join(new_lines))

        logger.info(f"Updated .env with weather section at {env_path}")
        return True

    except Exception as e:
        logger.error(f"Failed to write .env at {env_path}: {e}", exc_info=True)
        return False


def _load_env_file():
    """Load .env file from instances/{instance_name}/ into os.environ."""
    instance_name = config.instance_name
    env_path = Path(f"instances/{instance_name}/.env")

    if env_path.exists():
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, value = line.split("=", 1)
                        os.environ[key.strip()] = value.strip()
            logger.debug(f"Loaded .env file from {env_path}")
        except Exception as e:
            logger.error(f"Failed to load .env file from {env_path}: {e}", exc_info=True)
    else:
        logger.debug(f".env file not found at {env_path}")


# =============================================================================
# Database Helper (User Preferences)
# =============================================================================

class WeatherDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_table()

    def _init_table(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS weather_prefs (
                        nick TEXT PRIMARY KEY,
                        city TEXT NOT NULL,
                        updated_at INTEGER
                    )
                """)
        except Exception as e:
            logger.error(f"Failed to init weather DB: {e}", exc_info=True)

    def get_city(self, nick: str):
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute("SELECT city FROM weather_prefs WHERE nick = ? COLLATE NOCASE", (nick,))
                row = cur.fetchone()
                return row[0] if row else None
        except Exception:
            return None

    def set_city(self, nick: str, city: str) -> bool:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO weather_prefs (nick, city, updated_at)
                    VALUES (?, ?, strftime('%s','now'))
                """, (nick, city))
            return True
        except Exception as e:
            logger.error(f"Failed to set city: {e}", exc_info=True)
            return False


# =============================================================================
# Core Logic
# =============================================================================

def _wind_dir(deg: int) -> str:
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[int((deg + 22.5) / 45) % 8]


def _fetch_weather_data(city: str):
    global API_KEY

    if not API_KEY:
        _load_env_file()
        API_KEY = os.getenv("OPENWEATHER_API_KEY", "")

    if not API_KEY:
        return None, f"❌ API Key missing. Owner usage: {config.char}weather apikey <key>"

    try:
        params = {"q": city, "appid": API_KEY, "units": "metric", "lang": "en"}
        r = requests.get(BASE_URL, params=params, timeout=5)

        if r.status_code == 200:
            return r.json(), None
        if r.status_code == 404:
            return None, f"❌ City '{city}' not found."
        if r.status_code == 401:
            return None, "❌ Invalid API Key. Please check your OpenWeatherMap key."
        return None, f"❌ API Error: {r.status_code}"

    except Exception as e:
        return None, f"❌ Connection error: {e}"


def _format_output(data: dict) -> str:
    """Output one line with emojis (safe)."""
    try:
        city_name = data.get("name", "Unknown")
        country = data.get("sys", {}).get("country", "??")

        main = data.get("main", {}) or {}
        temp = round(main.get("temp", 0))
        feels_like = round(main.get("feels_like", 0))
        humidity = main.get("humidity", 0)
        tmin = round(main.get("temp_min", temp))
        tmax = round(main.get("temp_max", temp))
        pressure = main.get("pressure", None)

        wind = data.get("wind", {}) or {}
        wind_speed_kmh = round((wind.get("speed", 0) or 0) * 3.6, 1)
        wind_deg = int(wind.get("deg", 0) or 0)
        wind_dir = _wind_dir(wind_deg) if wind_speed_kmh > 0 else "—"

        clouds = (data.get("clouds", {}) or {}).get("all", None)

        weather_list = data.get("weather", []) or []
        if weather_list:
            cond_data = weather_list[0] or {}
            description = (cond_data.get("description", "") or "").capitalize() or "N/A"
            main_cond = cond_data.get("main", "") or ""
            icon = WEATHER_ICONS.get(main_cond, "🌈")
        else:
            description = "N/A"
            icon = "🌈"

        sysd = data.get("sys", {}) or {}
        sunrise_ts = sysd.get("sunrise")
        sunset_ts = sysd.get("sunset")
        if isinstance(sunrise_ts, (int, float)) and isinstance(sunset_ts, (int, float)):
            sunrise = datetime.fromtimestamp(sunrise_ts).strftime("%H:%M")
            sunset = datetime.fromtimestamp(sunset_ts).strftime("%H:%M")
            daylight = int(sunset_ts - sunrise_ts)
            dh = daylight // 3600
            dm = (daylight % 3600) // 60
            sun_part = f" | 🌅{sunrise} 🌇{sunset} ({dh}h{dm}m)"
        else:
            sun_part = ""

        rain_snow = ""
        rain_1h = (data.get("rain") or {}).get("1h")
        snow_1h = (data.get("snow") or {}).get("1h")
        if isinstance(rain_1h, (int, float)) and rain_1h > 0:
            rain_snow = f" | 🌧️ {rain_1h}mm"
        elif isinstance(snow_1h, (int, float)) and snow_1h > 0:
            rain_snow = f" | ❄️ {snow_1h}mm"

        if feels_like >= 27:
            verdict = "😎 Hot"
        elif feels_like <= 5:
            verdict = "🥶 Cold"
        elif isinstance(humidity, (int, float)) and humidity >= 80:
            verdict = "💦 Humid"
        else:
            verdict = "🙂 OK"

        clouds_part = f" | ☁️{clouds}%" if isinstance(clouds, (int, float)) else ""
        pressure_part = f" | 🧭{pressure}hPa" if isinstance(pressure, (int, float)) else ""

        return (
            f"{icon} 🌍 {city_name} {country} | "
            f"🌡️ {temp}°C (feels {feels_like}°C) ⬆️{tmax}° ⬇️{tmin}° | "
            f"💧{humidity}% | 🌬️ {wind_speed_kmh} km/h {wind_dir}"
            f"{clouds_part}{sun_part}{rain_snow}{pressure_part}"
            f" | {description} | {verdict}"
        )

    except Exception as e:
        logger.error(f"Format error: {e} - Data: {data}", exc_info=True)
        return "❌ Error formatting weather data."


# =============================================================================
# Plugin
# =============================================================================

class Plugin(PluginBase):
    def __init__(self, bot):
        super().__init__(bot)
        self.db = WeatherDB(bot.sql.database)

    def on_load(self):
        # help from description (multi-line, can include (FLAGS))
        apikey_flags = ACCESS["apikey"]

        desc = (
            ".weather [city]           -> show weather (or default city)\n"
            ".weather city <city>      -> save default city\n"
            f".weather apikey <key>     -> set API key ({apikey_flags})\n"
            ".weather help             -> show this help"
        )

        self.register_command(
            "weather",
            self.cmd_weather,
            flags="-",
            description=desc,
        )

        self.register_command(
            "w",
            self.cmd_weather,
            flags="-",
            description=desc,
        )

        # optional alias: uncomment if you want .w
        # self.register_command(
        #     "w",
        #     self.cmd_weather,
        #     flags="-",
        #     description=desc,
        # )

    # -------------------------
    # Access helper: OR semantics
    # -------------------------

    def _has_any_access(self, channel: str, nick: str, host: str, any_flags: str) -> bool:
        """
        True if user has ANY flag from any_flags (OR).
        Uses BlackBoT's bot.check_access().
        """
        # Prefer PluginBase.resolve_user_id if you added it there; else fallback
        uid = None
        if hasattr(self, "resolve_user_id") and callable(getattr(self, "resolve_user_id")):
            uid = self.resolve_user_id(nick, host)
        else:
            info = self.bot.sql.sqlite_handle(self.bot.botId, nick, host)
            uid = info[0] if info else None

        if not uid:
            return False

        flags = (any_flags or "").strip()
        if not flags or flags == "-":
            return True

        for f in flags:
            if f and self.bot.check_access(channel, uid, f):
                return True

        return False

    # -------------------------
    # Command
    # -------------------------

    def cmd_weather(self, bot, channel, feedback, nick, host, msg):
        args = (msg or "").strip().split()
        sub = args[0].lower() if args else ""

        # help
        if sub in ("help", "h", "?"):
            pm = getattr(bot, "plugin_manager", None)
            if pm and hasattr(pm, "send_command_help"):
                return pm.send_command_help(bot, feedback, channel, nick, host, "weather")
            bot.send_message(feedback, f"Usage: {config.char}weather [city] | {config.char}weather city <city> | {config.char}weather apikey <key>")
            return

        # city set
        if sub == "city":
            city = " ".join(args[1:]).strip()
            if not city:
                bot.send_message(feedback, f"⚠️ Usage: {config.char}weather city <City Name>")
                return

            def work():
                data, error = _fetch_weather_data(city)
                return city, data, error

            def done(result):
                city_in, data, error = result
                if error:
                    bot.send_message(feedback, f"❌ Could not verify city: {error}")
                    return

                official_name = data.get("name", city_in)
                country = (data.get("sys", {}) or {}).get("country", "??")

                if self.db.set_city(nick, official_name):
                    bot.send_message(feedback, f"✅ Saved default city: {official_name}, {country}")
                    bot.send_message(feedback, f"{nick}: {_format_output(data)}")
                else:
                    bot.send_message(feedback, "❌ Database error.")

            deferToThread(work).addCallback(done)
            return

        # apikey set (restricted)
        if sub == "apikey":
            if not self._has_any_access(channel, nick, host, ACCESS["apikey"]):
                return  # silent deny

            if len(args) < 2:
                bot.send_message(feedback, f"⚠️ {nick}: Usage: {config.char}weather apikey <your_openweathermap_key>")
                return

            new_key = args[1].strip()
            if not new_key:
                bot.send_message(feedback, f"⚠️ {nick}: Usage: {config.char}weather apikey <your_openweathermap_key>")
                return

            def work():
                ok = _update_env_file(new_key)
                return ok

            def done(ok: bool):
                global API_KEY
                if ok:
                    API_KEY = new_key
                    bot.send_message(feedback, f"✅ {nick}: API Key updated successfully! (Saved to .env)")
                else:
                    bot.send_message(feedback, f"❌ {nick}: Failed to save API key to .env (check permissions).")

            deferToThread(work).addCallback(done)
            return

        # normal weather lookup
        city = " ".join(args).strip()

        if not city:
            city = self.db.get_city(nick)
            if not city:
                bot.send_message(
                    feedback,
                    f"{nick}: Specify city ({config.char}w London) or set default ({config.char}w city London).",
                )
                return

        def work():
            data, error = _fetch_weather_data(city)
            return city, data, error

        def done(result):
            _, data, error = result
            if error:
                bot.send_message(feedback, f"{nick}: {error}")
            else:
                bot.send_message(feedback, f"{nick}: {_format_output(data)}")

        deferToThread(work).addCallback(done)


def register(bot):
    return Plugin(bot)