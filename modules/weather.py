"""
Weather Module for BlackBoT
===========================
Provides weather information using OpenWeatherMap API.
Features:
- !weather <city>
- !weather city <city> (Save default)
- !weather apikey <key> (Owner only - Set API Key)
- Threaded execution
- English output
"""

import os
import sqlite3
import requests
import re
from pathlib import Path
from core.log import get_logger
from datetime import datetime
from core.environment_config import config

logger = get_logger("weather")

# API Configuration
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
    "Tornado": "🌪️"
}

# =============================================================================
# Helper: Environment Update
# =============================================================================

def _update_env_file(new_key):
    """Actualizează sau adaugă cheia în fișierul .env cu formatare frumoasă"""

    # Get correct .env path from config
    instance_name = config.instance_name
    env_path = Path(f"instances/{instance_name}/.env")

    # Create instances directory if missing
    env_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Weather section template
        weather_section = f"""# ═══════════════════════════════════════════════════════════════════
# Weather Module Configuration
# ═══════════════════════════════════════════════════════════════════

# OpenWeatherMap API Key
# Get your free API key at: https://openweathermap.org/api
OPENWEATHER_API_KEY={new_key}

"""

        if not env_path.exists():
            # Create new file with weather section
            with open(env_path, "w", encoding="utf-8") as f:
                f.write(weather_section)
            logger.info(f"Created .env with weather section at {env_path}")
            return True

        # Read existing file
        with open(env_path, "r", encoding="utf-8") as f:
            content = f.read()

        lines = content.split("\n")

        # Check if weather section already exists
        weather_section_exists = False
        weather_key_line = -1
        notes_section_line = -1

        for i, line in enumerate(lines):
            # Find existing weather key
            if line.strip().startswith("OPENWEATHER_API_KEY="):
                weather_key_line = i
                # Check if there's a weather section header above
                for j in range(max(0, i - 10), i):
                    if "Weather Module" in lines[j] or "Weather Configuration" in lines[j]:
                        weather_section_exists = True
                        break

            # Find Notes section
            if "# Notes" in line and i > 0 and "═" in lines[i - 1]:
                notes_section_line = i - 1  # Line before "# Notes"

        new_lines = []
        inserted = False

        if weather_section_exists and weather_key_line >= 0:
            # Update existing key in place
            for i, line in enumerate(lines):
                if i == weather_key_line:
                    new_lines.append(f"OPENWEATHER_API_KEY={new_key}")
                    inserted = True
                else:
                    new_lines.append(line)

        elif notes_section_line >= 0:
            # Insert weather section before Notes
            for i, line in enumerate(lines):
                if i == notes_section_line:
                    # Add weather section before Notes separator
                    new_lines.append(weather_section.rstrip("\n"))
                    new_lines.append('')  # Empty line
                    new_lines.append(line)  # Notes separator
                    inserted = True
                else:
                    new_lines.append(line)

        else:
            # Append at the end
            new_lines = lines
            if new_lines and new_lines[-1].strip():
                new_lines.append('')
            new_lines.append(weather_section.rstrip("\n"))
            inserted = True

        # Write back
        with open(env_path, "w", encoding="utf-8") as f:
            f.write("\n".join(new_lines))

        logger.info(f"Updated .env with weather section at {env_path}")
        return True

    except Exception as e:
        logger.error(f"Failed to write .env at {env_path}: {e}")
        return False


def _load_env_file():
    """Load .env file from instances/{instance_name}/ into os.environ"""
    instance_name = config.instance_name
    env_path = Path(f"instances/{instance_name}/.env")

    if env_path.exists():
        try:
            with open(env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    # Skip empty lines and comments
                    if not line or line.startswith('#'):
                        continue
                    # Parse KEY=VALUE
                    if '=' in line:
                        key, value = line.split('=', 1)
                        os.environ[key.strip()] = value.strip()
            logger.debug(f"Loaded .env file from {env_path}")
        except Exception as e:
            logger.error(f"Failed to load .env file from {env_path}: {e}")
    else:
        logger.debug(f".env file not found at {env_path}")

# =============================================================================
# Database Helper (User Preferences)
# =============================================================================
class WeatherDB:
    def __init__(self, db_path):
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
            logger.error(f"Failed to init weather DB: {e}")

    def get_city(self, nick):
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute("SELECT city FROM weather_prefs WHERE nick = ? COLLATE NOCASE", (nick,))
                row = cur.fetchone()
                return row[0] if row else None
        except Exception:
            return None

    def set_city(self, nick, city):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO weather_prefs (nick, city, updated_at)
                    VALUES (?, ?, strftime('%s','now'))
                """, (nick, city))
            return True
        except Exception as e:
            logger.error(f"Failed to set city: {e}")
            return False

# =============================================================================
# Core Logic
# =============================================================================

def _fetch_weather_data(city):
    global API_KEY

    if not API_KEY:
        _load_env_file()  # Reload .env
        API_KEY = os.getenv("OPENWEATHER_API_KEY", "")

    if not API_KEY:
        # Mesaj informativ clar dacă lipsește cheia
        return None, f"❌ API Key missing. Owner usage: {config.char}weather apikey <key>"

    try:
        params = {'q': city, 'appid': API_KEY, 'units': 'metric', 'lang': 'en'}
        r = requests.get(BASE_URL, params=params, timeout=5)

        if r.status_code == 200:
            return r.json(), None
        elif r.status_code == 404:
            return None, f"❌ City '{city}' not found."
        elif r.status_code == 401:
            return None, "❌ Invalid API Key. Please check your OpenWeatherMap key."
        else:
            return None, f"❌ API Error: {r.status_code}"

    except Exception as e:
        return None, f"❌ Connection error: {e}"



def _wind_dir(deg: int) -> str:
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[int((deg + 22.5) / 45) % 8]


def _format_output(data):
    """
    Formatează datele JSON.
    Output într-o singură linie, cu emoji, + protecții împotriva erorilor.
    """
    try:
        city_name = data.get('name', 'Unknown')
        country = data.get('sys', {}).get('country', '??')

        main = data.get('main', {})
        temp = round(main.get('temp', 0))
        feels_like = round(main.get('feels_like', 0))
        humidity = main.get('humidity', 0)
        tmin = round(main.get('temp_min', temp))
        tmax = round(main.get('temp_max', temp))
        pressure = main.get('pressure', None)

        wind = data.get('wind', {})
        wind_speed_kmh = round(wind.get('speed', 0) * 3.6, 1)  # m/s -> km/h
        wind_deg = int(wind.get('deg', 0) or 0)
        wind_dir = _wind_dir(wind_deg) if wind_speed_kmh > 0 else "—"

        clouds = data.get('clouds', {}).get('all', None)

        # Weather condition + icon (safe)
        weather_list = data.get('weather', [])
        if weather_list:
            cond_data = weather_list[0] or {}
            description = (cond_data.get('description', '') or '').capitalize() or "N/A"
            main_cond = cond_data.get('main', '') or ""
            icon = WEATHER_ICONS.get(main_cond, "🌈")
        else:
            description = "N/A"
            icon = "🌈"

        # Sunrise / Sunset + daylight (safe)
        sysd = data.get('sys', {}) or {}
        sunrise_ts = sysd.get('sunrise')
        sunset_ts = sysd.get('sunset')
        if sunrise_ts and sunset_ts and isinstance(sunrise_ts, (int, float)) and isinstance(sunset_ts, (int, float)):
            sunrise = datetime.fromtimestamp(sunrise_ts).strftime("%H:%M")
            sunset = datetime.fromtimestamp(sunset_ts).strftime("%H:%M")
            daylight = int(sunset_ts - sunrise_ts)
            dh = daylight // 3600
            dm = (daylight % 3600) // 60
            sun_part = f" | 🌅{sunrise} 🌇{sunset} ({dh}h{dm}m)"
        else:
            sun_part = ""

        # Rain / Snow (only if available)
        rain_snow = ""
        rain_1h = (data.get('rain') or {}).get('1h')
        snow_1h = (data.get('snow') or {}).get('1h')
        if isinstance(rain_1h, (int, float)) and rain_1h > 0:
            rain_snow = f" | 🌧️ {rain_1h}mm"
        elif isinstance(snow_1h, (int, float)) and snow_1h > 0:
            rain_snow = f" | ❄️ {snow_1h}mm"

        # Verdict (short)
        if feels_like >= 27:
            verdict = "😎 Hot"
        elif feels_like <= 5:
            verdict = "🥶 Cold"
        elif isinstance(humidity, (int, float)) and humidity >= 80:
            verdict = "💦 Humid"
        else:
            verdict = "🙂 OK"

        # Optional bits
        clouds_part = f" | ☁️{clouds}%" if isinstance(clouds, (int, float)) else ""
        pressure_part = f" | 🧭{pressure}hPa" if isinstance(pressure, (int, float)) else ""

        return (
            f"{icon} 🌍 {city_name} {country} | "
            f"🌡️ {temp}°C (feels {feels_like}°C) ⬆️{tmax}° ⬇️{tmin}° | "
            f"💧{humidity}% | 🌬️ {wind_speed_kmh} km/h {wind_dir}"
            f"{clouds_part}"
            f"{sun_part}"
            f"{rain_snow}"
            f"{pressure_part}"
            f" | {description} | {verdict}"
        )

    except Exception as e:
        logger.error(f"Format error: {e} - Data: {data}")
        return "❌ Error formatting weather data."

# =============================================================================
# Command Logics
# =============================================================================

def _perform_set_city(bot, feedback, nick, args):
    city = " ".join(args).strip()
    if not city:
        bot.send_message(feedback, f"⚠️ Usage: {config.char}weather city <City Name>")
        return

    data, error = _fetch_weather_data(city)
    if error:
        bot.send_message(feedback, f"❌ Could not verify city: {error}")
        return

    db = WeatherDB(bot.sql.database)
    official_name = data.get('name', city)
    sys_data = data.get('sys', {})
    country = sys_data.get('country', '??')

    if db.set_city(nick, official_name):
        bot.send_message(feedback, f"✅ Saved default city: {official_name}, {country}")
        msg = _format_output(data)
        bot.send_message(feedback, f"{nick}: {msg}")
    else:
        bot.send_message(feedback, "❌ Database error.")


def _perform_set_apikey(bot, feedback, nick, host, channel, args):
    """Setează OpenWeather API key (owner only)"""

    lhost = bot.get_hostname(nick, host, 0)
    userId = None
    botId = bot.botId

    # Try sqlite_handle
    info = bot.sql.sqlite_handle(bot.botId, nick, host)
    if info:
        userId = info[0]
    else:
        # Try logged_in_users
        for uid, data in bot.logged_in_users.items():
            if isinstance(data, dict) and lhost in data.get("hosts", []):
                userId = uid
                break

    if not userId:
        return

    # 2) Verificare owner flag (N/n/m) — asta e “owner only”
    user_flag = bot.sql.sqlite_get_max_flag(botId, userId)
    if user_flag not in ("N", "n", "m"):
        bot.send_message(feedback, f"❌ {nick}: Owner access required for API key management.")
        return

    # 3) Usage
    if not args:
        bot.send_message(feedback, f"⚠️ {nick}: Usage: {config.char}weather apikey <your_openweathermap_key>")
        return

    new_key = args[0].strip()
    if not new_key:
        bot.send_message(feedback, f"⚠️ {nick}: Usage: {config.char}weather apikey <your_openweathermap_key>")
        return

    # 4) Update .env + update runtime key
    if _update_env_file(new_key):
        global API_KEY
        API_KEY = new_key
        bot.send_message(feedback, f"✅ {nick}: API Key updated successfully! (Saved to .env)")
    else:
        bot.send_message(feedback, f"❌ {nick}: Failed to save API key to .env (check permissions).")


# =============================================================================
# Thread Worker
# =============================================================================

def cmd_weather_thread(bot, feedback, nick, host, channel, args):
    try:
        # Check subcommands
        if args:
            subcmd = args[0].lower()

            # !weather city <name>
            if subcmd == "city":
                _perform_set_city(bot, feedback, nick, args[1:])
                return

            # !weather apikey <key>
            if subcmd == "apikey":
                _perform_set_apikey(bot, feedback, nick, host, channel, args[1:])
                return

        # Normal weather check
        db = WeatherDB(bot.sql.database)
        city = " ".join(args).strip()

        if not city:
            city = db.get_city(nick)
            if not city:
                bot.send_message(feedback, f"{nick}: Specify city ({config.char}w London) or set default ({config.char}w city London).")
                return

        data, error = _fetch_weather_data(city)
        if error:
            bot.send_message(feedback, f"{nick}: {error}")
        else:
            msg = _format_output(data)
            bot.send_message(feedback, f"{nick}: {msg}")

    except Exception as e:
        logger.error(f"Weather error: {e}", exc_info=True)
        bot.send_message(feedback, "❌ Internal error.")

def cmd_setcity_thread(bot, feedback, nick, args):
    try:
        _perform_set_city(bot, feedback, nick, args)
    except Exception as e:
        logger.error(f"Setcity error: {e}")
