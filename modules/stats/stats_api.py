"""
IRC Statistics - Web UI (Single Endpoint)
=========================================
FastAPI server care servește o singură pagină UI: /ui/{channel}

Cerinte:
- FARA endpoint-uri /api/* pentru statistici: totul e render server-side in /ui/{channel}
- Sidebar stanga cu lista canalelor, selectabil
- botId preluat din instanta care a pornit serverul (init_api)
- SQL via SQLManager singleton sau sql_instance injectat

Note:
- Pagina include Chart.js (CDN) si datele sunt injectate in HTML ca JSON.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple

from fastapi import FastAPI, HTTPException, Path, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from modules.stats.stats_user_analytics import get_analytics

from core.sql_manager import SQLManager


app = FastAPI(title="IRC Stats UI", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Injectate de StatsAPIServerThread
sql = None
DEFAULT_BOT_ID: Optional[int] = None


def init_api(bot_id: int, sql_instance=None):
    """
    Cheama asta din stats_api_threaded inainte sa pornesti uvicorn,
    ca UI sa stie botId-ul corect si SQL handle-ul corect.
    """
    global DEFAULT_BOT_ID, sql
    DEFAULT_BOT_ID = int(bot_id)
    if sql_instance is not None:
        sql = sql_instance


@app.on_event("startup")
async def _startup():
    global sql
    if sql is None:
        try:
            sql = SQLManager.get_instance()
        except Exception as e:
            print(f"[STATS_UI] Failed to init SQLManager: {e}")


def _require_sql():
    if not sql:
        raise HTTPException(status_code=500, detail="Database not initialized")


def _ensure_channel_hash(channel: str) -> str:
    if not channel:
        return channel
    return channel if channel.startswith("#") else f"#{channel}"


def _fmt_last_activity(ts: Optional[int]) -> str:
    if not ts:
        return "never"
    delta = time.time() - float(ts)
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta/60)}m ago"
    if delta < 86400:
        return f"{int(delta/3600)}h ago"
    return f"{int(delta/86400)}d ago"


def _safe_int(x, default=0) -> int:
    try:
        return int(x)
    except Exception:
        return default


def _safe_str(x, default="") -> str:
    if x is None:
        return default
    return str(x)


def _get_bot_id() -> int:
    return int(DEFAULT_BOT_ID or 1)


def _query_channels(bot_id: int) -> List[Dict[str, Any]]:
    """
    Lista canale din STATS_CHANNEL (doar #...).
    """
    q = """
        SELECT channel, total_messages, total_users, last_event_ts
        FROM STATS_CHANNEL
        WHERE botId = ?
        ORDER BY total_messages DESC
    """
    rows = sql.sqlite_select(q, (bot_id,))
    out = []
    for ch, msgs, users, last_ts in rows:
        if not ch or not str(ch).startswith("#"):
            continue
        out.append({
            "channel": str(ch),
            "messages": _safe_int(msgs),
            "users": _safe_int(users),
            "last_activity": _fmt_last_activity(last_ts),
        })
    return out


def _query_channel_summary(bot_id: int, channel: str) -> Dict[str, Any]:
    q = """
        SELECT total_messages, total_words, total_users,
               top_talker_nick, top_talker_count,
               first_event_ts, last_event_ts
        FROM STATS_CHANNEL
        WHERE botId = ? AND channel = ?
    """
    rows = sql.sqlite_select(q, (bot_id, channel))
    if not rows:
        return {
            "exists": False,
            "channel": channel,
            "total_messages": 0,
            "total_words": 0,
            "total_users": 0,
            "top_talker_nick": None,
            "top_talker_count": 0,
            "first_event_ts": None,
            "last_event_ts": None,
            "last_activity": "never",
        }

    msgs, words, users, top_nick, top_count, first_ts, last_ts = rows[0]
    return {
        "exists": True,
        "channel": channel,
        "total_messages": _safe_int(msgs),
        "total_words": _safe_int(words),
        "total_users": _safe_int(users),
        "top_talker_nick": top_nick,
        "top_talker_count": _safe_int(top_count),
        "first_event_ts": first_ts,
        "last_event_ts": last_ts,
        "last_activity": _fmt_last_activity(last_ts),
    }


def _query_timeline(bot_id: int, channel: str, days: int = 30) -> List[Dict[str, Any]]:
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    q = """
        SELECT date,
               SUM(messages + actions) AS msgs,
               SUM(words) AS wrds,
               COUNT(DISTINCT nick) AS usrs
        FROM STATS_DAILY
        WHERE botId = ? AND channel = ? AND date >= ?
        GROUP BY date
        ORDER BY date ASC
    """
    rows = sql.sqlite_select(q, (bot_id, channel, cutoff))
    out = []
    for d, msgs, wrds, usrs in rows:
        out.append({
            "date": _safe_str(d),
            "messages": _safe_int(msgs),
            "words": _safe_int(wrds),
            "users": _safe_int(usrs),
        })
    return out


def _query_heatmap(bot_id: int, channel: str) -> List[Dict[str, Any]]:
    q = """
        SELECT hour, day_of_week, total_messages
        FROM STATS_HOURLY
        WHERE botId = ? AND channel = ?
        ORDER BY day_of_week ASC, hour ASC
    """
    rows = sql.sqlite_select(q, (bot_id, channel))
    out = []
    for hour, day, msgs in rows:
        out.append({
            "hour": _safe_int(hour),
            "day": _safe_int(day),
            "messages": _safe_int(msgs),
        })
    return out


def _query_top_talkers(bot_id: int, channel: str, days: int = 30, limit: int = 15) -> List[Dict[str, Any]]:
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    q = f"""
        SELECT nick,
               SUM(messages + actions) AS msgs,
               SUM(words) AS wrds,
               SUM(chars) AS chrs,
               SUM(smiles) AS smiles,
               SUM(sads) AS sads,
               SUM(laughs) AS laughs,
               SUM(angries) AS angries,
               SUM(hearts) AS hearts,
               SUM(urls) AS urls,
               SUM(caps_msgs) AS caps,
               SUM(questions) AS questions,
               MAX(last_seen_ts) AS last_seen
        FROM STATS_DAILY
        WHERE botId = ? AND channel = ? AND date >= ?
        AND nick NOT LIKE '%.%.%'              -- Exclude servers
        AND nick NOT LIKE '%Serv'              -- Exclude services
        GROUP BY nick
        ORDER BY msgs DESC
        LIMIT ?
    """
    rows = sql.sqlite_select(q, (bot_id, channel, cutoff, limit))
    out = []
    for r in rows:
        out.append({
            "nick": r[0],
            "msgs": _safe_int(r[1]),
            "words": _safe_int(r[2]),
            "chars": _safe_int(r[3]),
            "smiles": _safe_int(r[4]),
            "sads": _safe_int(r[5]),
            "laughs": _safe_int(r[6]),
            "angries": _safe_int(r[7]),
            "hearts": _safe_int(r[8]),
            "urls": _safe_int(r[9]),
            "caps": _safe_int(r[10]),
            "questions": _safe_int(r[11]),
            "last_seen_ts": _safe_int(r[12], 0),
        })
    return out


def _query_last_spoken_for_nicks(bot_id: int, channel: str, nicks: List[str]) -> Dict[str, Dict[str, Any]]:
    if not nicks:
        return {}
    # SQLite doesn't like huge IN without placeholders, but here limit is small (<=15)
    placeholders = ",".join(["?"] * len(nicks))
    q = f"""
        SELECT nick, last_ts, last_message, last_word
        FROM STATS_LAST_SPOKEN
        WHERE botId = ? AND channel = ? AND nick IN ({placeholders})
    """
    rows = sql.sqlite_select(q, tuple([bot_id, channel] + nicks))
    out = {}
    for nick, ts, msg, lw in rows:
        out[str(nick)] = {
            "last_ts": _safe_int(ts),
            "last_message": msg,
            "last_word": lw,
        }
    return out


def _query_top_words(bot_id: int, channel: str, days: int = 30, limit: int = 25) -> List[Dict[str, Any]]:
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    q = f"""
        SELECT word, SUM(count) AS total
        FROM STATS_WORDS_DAILY
        WHERE botId = ? AND channel = ? AND date >= ?
        GROUP BY word
        ORDER BY total DESC
        LIMIT ?
    """
    rows = sql.sqlite_select(q, (bot_id, channel, cutoff, limit))
    out = []
    for w, total in rows:
        out.append({"word": _safe_str(w), "count": _safe_int(total)})
    return out


def _query_records(bot_id: int, channel: str) -> Dict[str, Any]:
    q = """
        SELECT
          longest_chars, longest_nick, longest_ts, longest_message,
          most_emojis, most_emojis_nick, most_emojis_ts, most_emojis_message,
          peak_minute_count, peak_minute_ts, peak_minute_label
        FROM STATS_CHANNEL_RECORDS
        WHERE botId = ? AND channel = ?
    """
    rows = sql.sqlite_select(q, (bot_id, channel))
    if not rows:
        return {
            "longest": None,
            "most_emojis": None,
            "peak_minute": None,
        }
    r = rows[0]
    return {
        "longest": {
            "chars": _safe_int(r[0]),
            "nick": r[1],
            "ts": _safe_int(r[2], 0),
            "message": r[3],
        } if r[0] else None,
        "most_emojis": {
            "count": _safe_int(r[4]),
            "nick": r[5],
            "ts": _safe_int(r[6], 0),
            "message": r[7],
        } if r[4] else None,
        "peak_minute": {
            "count": _safe_int(r[8]),
            "ts": _safe_int(r[9], 0),
            "label": r[10],
        } if r[8] else None
    }


def _query_reply_pairs(bot_id: int, channel: str, limit: int = 12) -> List[Dict[str, Any]]:
    q = f"""
        SELECT from_nick, to_nick, count, last_ts
        FROM STATS_REPLY_PAIRS
        WHERE botId = ? AND channel = ?
        ORDER BY count DESC
        LIMIT ?
    """
    rows = sql.sqlite_select(q, (bot_id, channel, limit))
    out = []
    for a, b, c, ts in rows:
        out.append({
            "from": _safe_str(a),
            "to": _safe_str(b),
            "count": _safe_int(c),
            "last_ts": _safe_int(ts, 0),
        })
    return out


def _query_funny_leaders(bot_id: int, channel: str, days: int = 30) -> Dict[str, List[Dict[str, Any]]]:
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    def top(metric: str, limit: int = 8):
        q = f"""
                SELECT nick, SUM({metric}) AS total
                FROM STATS_DAILY
                WHERE botId = ? AND channel = ? AND date >= ?
                GROUP BY nick
                HAVING SUM({metric}) >= 1
                ORDER BY total DESC
                LIMIT ?
            """
        rows = sql.sqlite_select(q, (bot_id, channel, cutoff, limit))
        out = []
        for nick, total in rows:
            out.append({"nick": _safe_str(nick), "count": _safe_int(total)})
        return out

    return {
        "smiles": top("smiles"),
        "sads": top("sads"),
        "laughs": top("laughs"),
        "angries": top("angries"),
        "hearts": top("hearts"),
        "caps": top("caps_msgs"),
        "urls": top("urls"),
        "questions": top("questions"),
    }


def _query_retention(bot_id: int, channel: str, days: int = 7) -> Dict[str, Any]:
    """
    Newcomers = first_seen in last N days
    Active_now = last_seen in last N days
    Returned = active_now AND first_seen older than N days (i.e. not newcomer)
    """
    cutoff_ts = int((datetime.now() - timedelta(days=days)).timestamp())

    q_new = """
        SELECT COUNT(*)
        FROM STATS_NICK_ACTIVITY
        WHERE botId = ? AND channel = ? AND first_seen_ts >= ?
    """
    q_active = """
        SELECT COUNT(*)
        FROM STATS_NICK_ACTIVITY
        WHERE botId = ? AND channel = ? AND last_seen_ts >= ?
    """
    q_ret = """
        SELECT COUNT(*)
        FROM STATS_NICK_ACTIVITY
        WHERE botId = ? AND channel = ? AND last_seen_ts >= ? AND (first_seen_ts IS NULL OR first_seen_ts < ?)
    """

    newcomers = sql.sqlite_select(q_new, (bot_id, channel, cutoff_ts))
    active_now = sql.sqlite_select(q_active, (bot_id, channel, cutoff_ts))
    returned = sql.sqlite_select(q_ret, (bot_id, channel, cutoff_ts, cutoff_ts))

    return {
        "days": days,
        "cutoff_ts": cutoff_ts,
        "newcomers": _safe_int(newcomers[0][0]) if newcomers else 0,
        "active_now": _safe_int(active_now[0][0]) if active_now else 0,
        "returned": _safe_int(returned[0][0]) if returned else 0,
    }


@app.get("/", response_class=HTMLResponse)
async def index():
    _require_sql()
    bot_id = _get_bot_id()
    chans = _query_channels(bot_id)
    if chans:
        # redirect-like: render UI for first channel (server-side)
        first = chans[0]["channel"].lstrip("#")
        return await web_ui(first)
    return HTMLResponse("<h2>No channels yet. Wait for stats aggregation.</h2>")


@app.get("/ui/{channel}", response_class=HTMLResponse)
async def web_ui(channel: str = Path(..., description="Channel name (with or without #)")):
    _require_sql()
    bot_id = _get_bot_id()
    channel = _ensure_channel_hash(channel)

    channels = _query_channels(bot_id)
    summary = _query_channel_summary(bot_id, channel)
    timeline = _query_timeline(bot_id, channel, days=30)
    heatmap = _query_heatmap(bot_id, channel)
    top_talkers = _query_top_talkers(bot_id, channel, days=30, limit=15)
    nicks = [x["nick"] for x in top_talkers]
    last_spoken = _query_last_spoken_for_nicks(bot_id, channel, nicks)
    top_words = _query_top_words(bot_id, channel, days=30, limit=25)
    records = _query_records(bot_id, channel)
    pairs = _query_reply_pairs(bot_id, channel, limit=12)
    funny = _query_funny_leaders(bot_id, channel, days=30)
    retention = _query_retention(bot_id, channel, days=7)

    # enrich top talkers with last word + last seen label
    for t in top_talkers:
        ls = last_spoken.get(t["nick"])
        if ls:
            t["last_word"] = ls.get("last_word")
            t["last_message"] = ls.get("last_message")
            t["last_ts"] = ls.get("last_ts", 0)
        else:
            t["last_word"] = None
            t["last_message"] = None
            t["last_ts"] = 0

        t["last_seen_label"] = _fmt_last_activity(t.get("last_seen_ts", 0))

    # JSON for charts
    data_json = json.dumps({
        "summary": summary,
        "timeline": timeline,
        "heatmap": heatmap,
        "top_talkers": top_talkers,
        "top_words": top_words,
        "records": records,
        "reply_pairs": pairs,
        "funny": funny,
        "retention": retention,
    }, ensure_ascii=False)

    # Sidebar selection
    sidebar_items = []
    for ch in channels:
        active = "active" if ch["channel"] == channel else ""
        url = f"/ui/{ch['channel'].lstrip('#')}"
        sidebar_items.append(f"""
            <a class="chan {active}" href="{url}">
                <div class="chan-name">{ch['channel']}</div>
                <div class="chan-meta">{ch['messages']:,} msgs · {ch['users']} users</div>
            </a>
        """)
    sidebar_html = "\n".join(sidebar_items) if sidebar_items else "<div class='empty'>No channels yet</div>"

    # Render
    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>IRC Stats - {channel}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">

  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>

  <style>
    :root {{
      --bg: #0b1220;
      --panel: rgba(255,255,255,0.06);
      --panel2: rgba(255,255,255,0.08);
      --text: rgba(255,255,255,0.92);
      --muted: rgba(255,255,255,0.65);
      --muted2: rgba(255,255,255,0.45);
      --stroke: rgba(255,255,255,0.10);
      --accent: #7c5cff;
      --accent2: #33d6a6;
      --warn: #ffcc66;
      --danger: #ff5c7a;
      --radius: 18px;
    }}

    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: radial-gradient(1200px 900px at 10% 10%, rgba(124,92,255,0.15), transparent 55%),
                  radial-gradient(1200px 900px at 90% 30%, rgba(51,214,166,0.12), transparent 55%),
                  var(--bg);
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, "Apple Color Emoji","Segoe UI Emoji";
    }}

    .layout {{
      display: grid;
      grid-template-columns: 320px 1fr;
      min-height: 100vh;
    }}

    .sidebar {{
      padding: 18px;
      border-right: 1px solid var(--stroke);
      background: rgba(0,0,0,0.25);
      backdrop-filter: blur(12px);
    }}

    .brand {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 14px;
      border: 1px solid var(--stroke);
      border-radius: var(--radius);
      background: var(--panel);
      margin-bottom: 14px;
    }}

    .brand .title {{
      font-weight: 800;
      letter-spacing: 0.2px;
    }}

    .pill {{
      font-size: 12px;
      color: rgba(0,0,0,0.85);
      background: rgba(255,255,255,0.85);
      padding: 4px 10px;
      border-radius: 999px;
    }}

    .channels {{
      display: grid;
      gap: 10px;
      margin-top: 10px;
    }}

    .chan {{
      display: block;
      text-decoration: none;
      color: var(--text);
      padding: 12px 12px;
      border: 1px solid var(--stroke);
      border-radius: 14px;
      background: rgba(255,255,255,0.04);
      transition: transform .12s ease, background .12s ease, border-color .12s ease;
    }}
    .chan:hover {{
      transform: translateY(-1px);
      background: rgba(255,255,255,0.06);
      border-color: rgba(255,255,255,0.18);
    }}
    .chan.active {{
      background: linear-gradient(135deg, rgba(124,92,255,0.22), rgba(51,214,166,0.10));
      border-color: rgba(124,92,255,0.35);
    }}

    .chan-name {{
      font-weight: 700;
      margin-bottom: 4px;
    }}
    .chan-meta {{
      font-size: 12px;
      color: var(--muted);
    }}

    .main {{
      padding: 22px;
    }}

    .header {{
      padding: 18px 18px;
      border-radius: var(--radius);
      border: 1px solid var(--stroke);
      background: linear-gradient(135deg, rgba(124,92,255,0.22), rgba(51,214,166,0.10));
      box-shadow: 0 10px 40px rgba(0,0,0,0.25);
    }}

    .header h1 {{
      margin: 0;
      font-size: 22px;
      letter-spacing: 0.2px;
    }}
    .header .sub {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 13px;
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
    }}

    .grid {{
      margin-top: 18px;
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 14px;
    }}

    .card {{
      border: 1px solid var(--stroke);
      background: var(--panel);
      border-radius: var(--radius);
      padding: 14px;
      box-shadow: 0 10px 30px rgba(0,0,0,0.20);
    }}

    .card h3 {{
      margin: 0 0 10px 0;
      font-size: 14px;
      color: rgba(255,255,255,0.85);
      font-weight: 800;
      letter-spacing: 0.2px;
    }}

    .stat {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      padding: 10px 12px;
      border-radius: 14px;
      border: 1px solid rgba(255,255,255,0.08);
      background: rgba(255,255,255,0.04);
      margin-top: 10px;
    }}
    .stat .k {{
      color: var(--muted);
      font-size: 12px;
    }}
    .stat .v {{
      font-weight: 900;
      font-size: 18px;
    }}

    .col-3 {{ grid-column: span 3; }}
    .col-4 {{ grid-column: span 4; }}
    .col-5 {{ grid-column: span 5; }}
    .col-6 {{ grid-column: span 6; }}
    .col-7 {{ grid-column: span 7; }}
    .col-8 {{ grid-column: span 8; }}
    .col-12 {{ grid-column: span 12; }}

    .table {{
      width: 100%;
      border-collapse: collapse;
      overflow: hidden;
      border-radius: 14px;
    }}

    .table th, .table td {{
      text-align: left;
      padding: 10px 10px;
      border-bottom: 1px solid rgba(255,255,255,0.08);
      font-size: 13px;
      vertical-align: top;
    }}
    .table th {{
      color: rgba(255,255,255,0.75);
      font-weight: 800;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.8px;
    }}
    .muted {{ color: var(--muted); }}
    .muted2 {{ color: var(--muted2); }}

    .tag {{
      display: inline-block;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 12px;
      background: rgba(255,255,255,0.10);
      border: 1px solid rgba(255,255,255,0.14);
      color: rgba(255,255,255,0.85);
    }}

    .chips {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 10px;
    }}

    .chip {{
      display: inline-flex;
      gap: 8px;
      align-items: center;
      padding: 8px 10px;
      border-radius: 14px;
      border: 1px solid rgba(255,255,255,0.10);
      background: rgba(255,255,255,0.04);
      font-size: 13px;
    }}
    .chip b {{ font-weight: 900; }}

    .record {{
      padding: 10px 12px;
      border-radius: 14px;
      border: 1px solid rgba(255,255,255,0.10);
      background: rgba(255,255,255,0.04);
      margin-top: 10px;
    }}
    .record .line {{
      margin-top: 6px;
      font-size: 12px;
      color: rgba(255,255,255,0.78);
      white-space: pre-wrap;
      word-break: break-word;
      max-height: 120px;
      overflow: auto;
      border-left: 2px solid rgba(124,92,255,0.45);
      padding-left: 10px;
    }}

    @media (max-width: 980px) {{
      .layout {{ grid-template-columns: 1fr; }}
      .sidebar {{ border-right: none; border-bottom: 1px solid var(--stroke); }}
    }}
  </style>
</head>

<body>
  <div class="layout">
    <aside class="sidebar">
      <div class="brand">
        <div>
          <div class="title">📊 BlackBoT Stats</div>
        </div>
        <div class="pill">UI</div>
      </div>

      <div class="muted" style="font-size:12px; margin:10px 2px 6px;">
        Channels
      </div>

      <div class="channels">
        {sidebar_html}
      </div>
    </aside>

    <main class="main">
      <section class="header">
        <h1>{channel}</h1>
        <div class="sub">
          <span class="tag">Last activity: {summary.get("last_activity","never")}</span>
          <span class="tag">Total msgs: {summary.get("total_messages",0):,}</span>
          <span class="tag">Words: {summary.get("total_words",0):,}</span>
          <span class="tag">Users: {summary.get("total_users",0):,}</span>
          <span class="tag">Top talker: {_safe_str(summary.get("top_talker_nick"), "-")} ({_safe_int(summary.get("top_talker_count"),0):,})</span>
        </div>
      </section>

      <section class="grid">
        <div class="card col-3">
          <h3>Last 7 days</h3>
          <div class="stat"><div class="k">Newcomers</div><div class="v">{retention["newcomers"]}</div></div>
          <div class="stat"><div class="k">Active users</div><div class="v">{retention["active_now"]}</div></div>
          <div class="stat"><div class="k">Returned</div><div class="v">{retention["returned"]}</div></div>
        </div>

        <div class="card col-9">
          <h3>Activity timeline (last 30 days)</h3>
          <canvas id="timelineChart"></canvas>
        </div>

        <div class="card col-6">
          <h3>Heatmap (avg msgs / hour)</h3>
          <canvas id="heatmapChart"></canvas>
        </div>

        <div class="card col-6">
          <h3>Top words (last 30 days)</h3>
          <div class="chips" id="wordsChips"></div>
        </div>

        <div class="card col-8">
          <h3>Top talkers (last 30 days) + last word</h3>
          <table class="table" id="talkersTable">
            <thead>
              <tr>
                <th style="width:42px;">#</th>
                <th>Nick</th>
                <th style="width:110px;">Msgs</th>
                <th style="width:110px;">Words</th>
                <th style="width:140px;">Last seen</th>
                <th style="width:160px;">Last word</th>
              </tr>
            </thead>
            <tbody></tbody>
          </table>
          <div class="muted2" style="margin-top:10px; font-size:12px;">
            Tip: “last word” vine din STATS_LAST_SPOKEN și se actualizează la agregare.
          </div>
        </div>

        <div class="card col-4">
          <h3>Funny leaders (last 30 days)</h3>
          <div id="funnyBox"></div>
        </div>

        <div class="card col-6">
          <h3>Records</h3>
          <div id="recordsBox"></div>
        </div>

        <div class="card col-6">
          <h3>Reply pairs (who replies to whom)</h3>
          <table class="table" id="pairsTable">
            <thead>
              <tr>
                <th>From</th>
                <th>To</th>
                <th style="width:90px;">Count</th>
                <th style="width:120px;">Last</th>
              </tr>
            </thead>
            <tbody></tbody>
          </table>
        </div>
      </section>

      <script>
        const DATA = {data_json};
        DATA.channel = "{channel}";
         var AUTO_REFRESH_SECONDS = 60;

          function _safeGetSession(key) {{
            try {{
              return sessionStorage.getItem(key);
            }} catch (e) {{
              return null;
            }}
          }}
        
          function _safeSetSession(key, value) {{
            try {{
              sessionStorage.setItem(key, value);
            }} catch (e) {{}}
          }}
        
          function _restoreScroll(scrollKey) {{
            var saved = _safeGetSession(scrollKey);
            if (saved !== null) {{
              var y = parseInt(saved, 10) || 0;
              window.scrollTo(0, y);
            }}
          }}
        
          function _enableScrollSave(scrollKey) {{
            window.addEventListener("scroll", function () {{
              _safeSetSession(scrollKey, String(window.scrollY));
            }}, {{ passive: true }});
          }}
        
          function _enableAutoRefresh(seconds) {{
            setInterval(function () {{
              window.location.reload();
            }}, seconds * 1000);
          }}
        
          (function () {{
            if (AUTO_REFRESH_SECONDS <= 0) {{
              return;
            }}
        
            var scrollKey = "stats_ui_scroll_" + window.location.pathname;
        
            _restoreScroll(scrollKey);
            _enableScrollSave(scrollKey);
            _enableAutoRefresh(AUTO_REFRESH_SECONDS);
          }})();
                
        
        function fmtAgo(ts) {{
          if (!ts) return "never";
          const now = Math.floor(Date.now()/1000);
          const d = now - ts;
          if (d < 60) return d + "s ago";
          if (d < 3600) return Math.floor(d/60) + "m ago";
          if (d < 86400) return Math.floor(d/3600) + "h ago";
          return Math.floor(d/86400) + "d ago";
        }}

        // Timeline chart
        (function() {{
          const t = DATA.timeline || [];
          const labels = t.map(x => x.date);
          const values = t.map(x => x.messages);
          const ctx = document.getElementById("timelineChart");
          new Chart(ctx, {{
            type: "line",
            data: {{
              labels,
              datasets: [{{
                label: "Messages",
                data: values,
                tension: 0.35,
                fill: true
              }}]
            }},
            options: {{
              responsive: true,
              plugins: {{ legend: {{ display: false }} }},
              scales: {{
                x: {{ ticks: {{ color: "rgba(255,255,255,0.65)" }} }},
                y: {{ ticks: {{ color: "rgba(255,255,255,0.65)" }} }}
              }}
            }}
          }});
        }})();

        // Heatmap (avg by hour)
        (function() {{
          const cells = DATA.heatmap || [];
          const matrix = Array.from({{length: 7}}, () => Array.from({{length: 24}}, () => 0));
          cells.forEach(c => {{
            const d = c.day, h = c.hour;
            if (d>=0 && d<7 && h>=0 && h<24) matrix[d][h] = c.messages || 0;
          }});
          const hours = Array.from({{length: 24}}, (_, i) => i);
          const avg = hours.map(h => {{
            let s = 0;
            for (let d=0; d<7; d++) s += matrix[d][h];
            return s/7;
          }});
          const ctx = document.getElementById("heatmapChart");
          new Chart(ctx, {{
            type: "bar",
            data: {{
              labels: hours.map(h => String(h).padStart(2,"0") + ":00"),
              datasets: [{{
                label: "Avg msgs / hour",
                data: avg
              }}]
            }},
            options: {{
              responsive: true,
              plugins: {{ legend: {{ display: false }} }},
              scales: {{
                x: {{ ticks: {{ color: "rgba(255,255,255,0.65)" }} }},
                y: {{ ticks: {{ color: "rgba(255,255,255,0.65)" }} }}
              }}
            }}
          }});
        }})();

        // Top words chips
        (function() {{
          const list = DATA.top_words || [];
          const box = document.getElementById("wordsChips");
          if (!list.length) {{
            box.innerHTML = '<div class="muted">No words yet. Wait for aggregation.</div>';
            return;
          }}
          box.innerHTML = list.map(x => `
            <span class="chip"><span class="muted2">#</span> <b>${{x.word}}</b> <span class="muted2">${{x.count}}</span></span>
          `).join("");
        }})();

        // Top talkers table
        (function() {{
          const rows = DATA.top_talkers || [];
          const tb = document.querySelector("#talkersTable tbody");
          if (!rows.length) {{
            tb.innerHTML = `<tr><td colspan="6" class="muted">No data yet. Wait for aggregation.</td></tr>`;
            return;
          }}
          tb.innerHTML = rows.map((r, idx) => `
            <tr>
              <td>${{idx+1}}</td>
              <td>
                    <b><a href="/user/${{encodeURIComponent(DATA.channel || window.location.pathname.split('/').pop())}}/${{encodeURIComponent(r.nick)}}" style="color: var(--accent2); text-decoration: none;">${{r.nick}}</a></b>                <div class="muted2" style="margin-top:4px;">
                  😄 ${{r.smiles}} · 😢 ${{r.sads}} · 😂 ${{r.laughs}} · 😡 ${{r.angries}} · ❤️ ${{r.hearts}}
                </div>
              </td>
              <td>${{(r.msgs||0).toLocaleString()}}</td>
              <td>${{(r.words||0).toLocaleString()}}</td>
              <td>${{r.last_seen_label || fmtAgo(r.last_seen_ts)}}</td>
              <td>${{r.last_word ? `<span class="tag">${{r.last_word}}</span>` : `<span class="muted2">—</span>`}}</td>
            </tr>
          `).join("");
        }})();

        // Funny leaders box
        (function() {{
          const f = DATA.funny || {{}};
          const items = [
            ["😄 Smilers", f.smiles],
            ["😢 Sads", f.sads],
            ["😂 Laughers", f.laughs],
            ["😡 Angries", f.angries],
            ["❤️ Hearts", f.hearts],
            ["🔊 Caps", f.caps],
            ["🔗 Links", f.urls],
            ["❓ Questions", f.questions],
        ].filter(([_, arr]) => Array.isArray(arr) && arr.length > 0);

          function renderList(arr) {{
            if (!arr || !arr.length) return '<div class="muted2">—</div>';
            return arr.slice(0,5).map((x,i) => `
              <div class="stat" style="margin-top:8px;">
                <div class="k">${{i+1}}. <a href="/user/${{encodeURIComponent(DATA.channel || window.location.pathname.split('/').pop())}}/${{encodeURIComponent(x.nick)}}" style="color: var(--accent2); text-decoration: none;">${{x.nick}}</a></div>                <div class="v" style="font-size:16px;">${{(x.count||0).toLocaleString()}}</div>
              </div>
            `).join("");
          }}

          const box = document.getElementById("funnyBox");
          box.innerHTML = items.map(([title, arr]) => `
            <div style="margin-bottom:12px;">
              <div class="muted" style="font-weight:800; font-size:12px;">${{title}}</div>
              ${{renderList(arr)}}
            </div>
          `).join("");
        }})();

        // Records
        (function() {{
          const r = DATA.records || {{}};
          const box = document.getElementById("recordsBox");

          function recCard(title, obj, kind) {{
              if (!obj) {{
                return `
                  <div class="record">
                    <b>${{title}}</b>
                    <div class="muted2" style="margin-top:6px;">—</div>
                  </div>
                `;
              }}
            
              let meta = "";
              let line = "";
              const channelPath = DATA.channel || window.location.pathname.split('/').pop();
            
              if (kind === "longest") {{
                const nickLink = obj.nick
                  ? `<a href="/user/${{encodeURIComponent(channelPath)}}/${{encodeURIComponent(obj.nick)}}"
                        style="color: var(--accent2); text-decoration: none;">
                        ${{obj.nick}}
                     </a>`
                  : "-";
            
                meta = `
                  <span class="tag">${{obj.chars}} chars</span>
                  <span class="tag">${{nickLink}}</span>
                  <span class="tag">${{fmtAgo(obj.ts)}}</span>
                `;
            
                line = obj.message
                  ? `<div class="line">${{obj.message}}</div>`
                  : "";
              }}
              else if (kind === "emoji") {{
                const nickLink = obj.nick
                  ? `<a href="/user/${{encodeURIComponent(channelPath)}}/${{encodeURIComponent(obj.nick)}}"
                        style="color: var(--accent2); text-decoration: none;">
                        ${{obj.nick}}
                     </a>`
                  : "-";
            
                meta = `
                  <span class="tag">${{obj.count}} emojis</span>
                  <span class="tag">${{nickLink}}</span>
                  <span class="tag">${{fmtAgo(obj.ts)}}</span>
                `;
              }}
            
              return `
                <div class="record">
                  <b>${{title}}</b>
                  <div class="meta">${{meta}}</div>
                  ${{line}}
                </div>
              `;
            }}

          box.innerHTML =
            recCard("📏 Longest line", r.longest, "longest") +
            recCard("😂 Most emojis in one line", r.most_emojis, "emoji") +
            recCard("💥 Peak minute", r.peak_minute, "peak");
        }})();

        // Reply pairs
        (function() {{
          const rows = DATA.reply_pairs || [];
          const tb = document.querySelector("#pairsTable tbody");
          if (!rows.length) {{
            tb.innerHTML = `<tr><td colspan="4" class="muted">No reply pairs yet.</td></tr>`;
            return;
          }}
          tb.innerHTML = rows.map(r => `
            <tr>
              <td><b><a href="/user/${{encodeURIComponent(DATA.channel || window.location.pathname.split('/').pop())}}/${{encodeURIComponent(r.from)}}" style="color: var(--accent2); text-decoration: none;">${{r.from}}</a></b></td>
                <td><b><a href="/user/${{encodeURIComponent(DATA.channel || window.location.pathname.split('/').pop())}}/${{encodeURIComponent(r.to)}}" style="color: var(--accent2); text-decoration: none;">${{r.to}}</a></b></td>
              <td>${{(r.count||0).toLocaleString()}}</td>
              <td class="muted">${{fmtAgo(r.last_ts)}}</td>
            </tr>
          `).join("");
        }})();
      </script>
    </main>
  </div>
</body>
</html>
"""
    return HTMLResponse(content=html)


@app.get("/api/user/{channel}/{nick}/peak-hours")
async def get_user_peak_hours(
        channel: str,
        nick: str,
        days: int = Query(30, ge=1, le=365)
):
    """Get user's peak activity hours"""
    _require_sql()

    channel = _ensure_channel_hash(channel)
    analytics = get_analytics(sql)

    try:
        result = analytics.get_user_peak_hours(_get_bot_id(), channel, nick, days)
        return {
            "channel": channel,
            "nick": nick,
            "period_days": days,
            "peak_hours": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/user/{channel}/{nick}/activity-heatmap")
async def get_user_activity_heatmap(
        channel: str,
        nick: str,
        days: int = Query(30, ge=1, le=365)
):
    """Get user's activity heatmap (hour x day)"""
    _require_sql()

    channel = _ensure_channel_hash(channel)
    analytics = get_analytics(sql)

    try:
        result = analytics.get_user_activity_heatmap(_get_bot_id(), channel, nick, days)
        return {
            "channel": channel,
            "nick": nick,
            "period_days": days,
            **result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/user/{channel}/{nick}/conversation-partners")
async def get_conversation_partners(
        channel: str,
        nick: str,
        days: int = Query(30, ge=1, le=365),
        limit: int = Query(10, ge=1, le=50)
):
    """Get who this user talks to most"""
    _require_sql()

    channel = _ensure_channel_hash(channel)
    analytics = get_analytics(sql)

    try:
        result = analytics.get_conversation_partners(_get_bot_id(), channel, nick, days, limit)
        return {
            "channel": channel,
            "nick": nick,
            "period_days": days,
            "partners": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/user/{channel}/{nick}/sentiment")
async def get_user_sentiment(
        channel: str,
        nick: str,
        days: int = Query(30, ge=1, le=365)
):
    """Analyze user's sentiment (positive/negative/neutral)"""
    _require_sql()

    channel = _ensure_channel_hash(channel)
    analytics = get_analytics(sql)

    try:
        result = analytics.get_user_sentiment(_get_bot_id(), channel, nick, days)
        return {
            "channel": channel,
            "nick": nick,
            "period_days": days,
            **result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/user/{channel}/{nick}/profile")
async def get_user_profile(
        channel: str,
        nick: str,
        days: int = Query(30, ge=1, le=365)
):
    """Get complete user behavioral profile"""
    _require_sql()

    channel = _ensure_channel_hash(channel)
    analytics = get_analytics(sql)

    try:
        result = analytics.get_user_profile(_get_bot_id(), channel, nick, days)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/user/{channel}/compare")
async def compare_users(
        channel: str,
        nick1: str = Query(..., description="First user to compare"),
        nick2: str = Query(..., description="Second user to compare"),
        days: int = Query(30, ge=1, le=365)
):
    """Compare two users side-by-side"""
    _require_sql()

    channel = _ensure_channel_hash(channel)
    analytics = get_analytics(sql)

    try:
        result = analytics.compare_users(_get_bot_id(), channel, nick1, nick2, days)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/channel/{channel}/conversation-graph")
async def get_conversation_graph(
        channel: str,
        min_interactions: int = Query(5, ge=1, le=100)
):
    """Get conversation graph for channel (network analysis)"""
    _require_sql()

    channel = _ensure_channel_hash(channel)
    analytics = get_analytics(sql)

    try:
        result = analytics.get_conversation_graph(_get_bot_id(), channel, min_interactions)
        return {
            "channel": channel,
            "min_interactions": min_interactions,
            **result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Web UI Page for User Analytics
# =============================================================================

@app.get("/user/{channel}/{nick}", response_class=HTMLResponse)
async def user_analytics_page(
        channel: str = Path(..., description="Channel name"),
        nick: str = Path(..., description="User nickname")
):
    """
    User analytics dashboard - same style as /ui
    """
    _require_sql()
    bot_id = _get_bot_id()
    channel = _ensure_channel_hash(channel)

    try:
        analytics = get_analytics(sql)
        profile = analytics.get_user_profile(bot_id, channel, nick, days=30)

        if 'error' in profile:
            return HTMLResponse(f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <title>User Not Found</title>
                <style>
                    body {{
                        font-family: system-ui;
                        background: #0b1220;
                        color: white;
                        padding: 40px;
                        text-align: center;
                    }}
                    a {{
                        color: #7c5cff;
                        text-decoration: none;
                    }}
                </style>
            </head>
            <body>
                <h1>❌ No Data Found</h1>
                <p>User <strong>{nick}</strong> not found in <strong>{channel}</strong></p>
                <p><a href="/ui/{channel.lstrip('#')}">← Back to {channel}</a></p>
            </body>
            </html>
            """)

        # Get channels for sidebar
        channels = _query_channels(bot_id)
        sidebar_items = []
        for ch in channels:
            url = f"/ui/{ch['channel'].lstrip('#')}"
            sidebar_items.append(f"""
                <a class="chan" href="{url}">
                    <div class="chan-name">{ch['channel']}</div>
                    <div class="chan-meta">{ch['messages']:,} msgs · {ch['users']} users</div>
                </a>
            """)
        sidebar_html = "\n".join(sidebar_items) if sidebar_items else "<div class='empty'>No channels yet</div>"

        # Prepare chart data
        peak_hours_labels = [h['time_label'] for h in profile['patterns']['peak_hours'][:10]]
        peak_hours_data = [h['messages'] for h in profile['patterns']['peak_hours'][:10]]

        sentiment_trend = profile['sentiment']['sentiment_trend'][-14:]  # Last 14 days
        sentiment_trend_labels = [s['date'] for s in sentiment_trend]
        sentiment_trend_data = [s['score'] for s in sentiment_trend]

        # Sentiment color
        sent = profile['sentiment']['overall_sentiment']
        if sent == 'positive':
            sent_color = '#33d6a6'
            sent_badge = 'linear-gradient(135deg, rgba(51,214,166,0.25), rgba(51,214,166,0.15))'
        elif sent == 'negative':
            sent_color = '#ff5c7a'
            sent_badge = 'linear-gradient(135deg, rgba(255,92,122,0.25), rgba(255,92,122,0.15))'
        else:
            sent_color = '#ffcc66'
            sent_badge = 'linear-gradient(135deg, rgba(255,204,102,0.25), rgba(255,204,102,0.15))'

        # HTML page (same style as /ui)
        html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>{nick} @ {channel} - User Analytics</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">

  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>

  <style>
    :root {{
      --bg: #0b1220;
      --panel: rgba(255,255,255,0.06);
      --panel2: rgba(255,255,255,0.08);
      --text: rgba(255,255,255,0.92);
      --muted: rgba(255,255,255,0.65);
      --muted2: rgba(255,255,255,0.45);
      --stroke: rgba(255,255,255,0.10);
      --accent: #7c5cff;
      --accent2: #33d6a6;
      --warn: #ffcc66;
      --danger: #ff5c7a;
      --radius: 18px;
    }}

    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: radial-gradient(1200px 900px at 10% 10%, rgba(124,92,255,0.15), transparent 55%),
                  radial-gradient(1200px 900px at 90% 30%, rgba(51,214,166,0.12), transparent 55%),
                  var(--bg);
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, "Apple Color Emoji","Segoe UI Emoji";
    }}

    .layout {{
      display: grid;
      grid-template-columns: 320px 1fr;
      min-height: 100vh;
    }}

    .sidebar {{
      padding: 18px;
      border-right: 1px solid var(--stroke);
      background: rgba(0,0,0,0.25);
      backdrop-filter: blur(12px);
    }}

    .brand {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 14px;
      border: 1px solid var(--stroke);
      border-radius: var(--radius);
      background: var(--panel);
      margin-bottom: 14px;
    }}

    .brand .title {{
      font-weight: 800;
      letter-spacing: 0.2px;
    }}

    .pill {{
      font-size: 12px;
      color: rgba(0,0,0,0.85);
      background: rgba(255,255,255,0.85);
      padding: 4px 10px;
      border-radius: 999px;
    }}

    .channels {{
      display: grid;
      gap: 10px;
      margin-top: 10px;
    }}

    .chan {{
      display: block;
      text-decoration: none;
      color: var(--text);
      padding: 12px 12px;
      border: 1px solid var(--stroke);
      border-radius: 14px;
      background: rgba(255,255,255,0.04);
      transition: transform .12s ease, background .12s ease, border-color .12s ease;
    }}
    .chan:hover {{
      transform: translateY(-1px);
      background: rgba(255,255,255,0.06);
      border-color: rgba(255,255,255,0.18);
    }}

    .chan-name {{
      font-weight: 700;
      margin-bottom: 4px;
    }}
    .chan-meta {{
      font-size: 12px;
      color: var(--muted);
    }}

    .main {{
      padding: 22px;
    }}

    .back-link {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      text-decoration: none;
      font-size: 14px;
      margin-bottom: 14px;
      padding: 8px 12px;
      border-radius: 12px;
      border: 1px solid var(--stroke);
      background: var(--panel);
      transition: all .12s ease;
    }}
    .back-link:hover {{
      color: var(--text);
      border-color: rgba(255,255,255,0.18);
      background: var(--panel2);
    }}

    .header {{
      padding: 18px 18px;
      border-radius: var(--radius);
      border: 1px solid var(--stroke);
      background: linear-gradient(135deg, rgba(124,92,255,0.22), rgba(51,214,166,0.10));
      box-shadow: 0 10px 40px rgba(0,0,0,0.25);
    }}

    .header h1 {{
      margin: 0;
      font-size: 22px;
      letter-spacing: 0.2px;
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .header .sub {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 13px;
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
    }}

    .grid {{
      margin-top: 18px;
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 14px;
    }}

    .card {{
      border: 1px solid var(--stroke);
      background: var(--panel);
      border-radius: var(--radius);
      padding: 14px;
      box-shadow: 0 10px 30px rgba(0,0,0,0.20);
    }}

    .card h3 {{
      margin: 0 0 10px 0;
      font-size: 14px;
      color: rgba(255,255,255,0.85);
      font-weight: 800;
      letter-spacing: 0.2px;
    }}

    .stat {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      padding: 10px 12px;
      border-radius: 14px;
      border: 1px solid rgba(255,255,255,0.08);
      background: rgba(255,255,255,0.04);
      margin-top: 10px;
    }}
    .stat .k {{
      color: var(--muted);
      font-size: 12px;
    }}
    .stat .v {{
      font-weight: 900;
      font-size: 18px;
    }}

    .col-3 {{ grid-column: span 3; }}
    .col-4 {{ grid-column: span 4; }}
    .col-5 {{ grid-column: span 5; }}
    .col-6 {{ grid-column: span 6; }}
    .col-7 {{ grid-column: span 7; }}
    .col-8 {{ grid-column: span 8; }}
    .col-12 {{ grid-column: span 12; }}

    .table {{
      width: 100%;
      border-collapse: collapse;
      overflow: hidden;
      border-radius: 14px;
    }}

    .table th, .table td {{
      text-align: left;
      padding: 10px 10px;
      border-bottom: 1px solid rgba(255,255,255,0.08);
      font-size: 13px;
      vertical-align: top;
    }}
    .table th {{
      color: rgba(255,255,255,0.75);
      font-weight: 800;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.8px;
    }}
    .muted {{ color: var(--muted); }}
    .muted2 {{ color: var(--muted2); }}

    .tag {{
      display: inline-block;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 12px;
      background: rgba(255,255,255,0.10);
      border: 1px solid rgba(255,255,255,0.14);
      color: rgba(255,255,255,0.85);
    }}

    .sentiment-badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 14px;
      border-radius: 999px;
      font-weight: 800;
      font-size: 14px;
      background: {sent_badge};
      border: 1px solid {sent_color};
      color: {sent_color};
    }}

    @media (max-width: 980px) {{
      .layout {{ grid-template-columns: 1fr; }}
      .sidebar {{ border-right: none; border-bottom: 1px solid var(--stroke); }}
    }}
  </style>
</head>

<body>
  <div class="layout">
    <aside class="sidebar">
      <div class="brand">
        <div>
          <div class="title">📊 BlackBoT Stats</div>
        </div>
        <div class="pill">USER</div>
      </div>

      <div class="muted" style="font-size:12px; margin:10px 2px 6px;">
        Channels
      </div>

      <div class="channels">
        {sidebar_html}
      </div>
    </aside>

    <main class="main">
      <a href="/ui/{channel.lstrip('#')}" class="back-link">
        ← Back to {channel}
      </a>

      <section class="header">
        <h1>
          👤 {nick}
          <span class="sentiment-badge">
            {profile['sentiment']['overall_sentiment'].upper()}
          </span>
        </h1>
        <div class="sub">
          <span class="tag">Period: Last 30 days</span>
          <span class="tag">Channel: {channel}</span>
          <span class="tag">Score: {profile['sentiment']['average_score']:.2f}</span>
        </div>
      </section>

      <section class="grid">
        <!-- Stats Cards -->
        <div class="card col-3">
          <h3>Activity</h3>
          <div class="stat"><div class="k">Total messages</div><div class="v">{profile['activity']['total_messages']}</div></div>
          <div class="stat"><div class="k">Total words</div><div class="v">{profile['activity']['total_words']:,}</div></div>
          <div class="stat"><div class="k">Avg words/msg</div><div class="v">{profile['activity']['avg_words_per_message']:.1f}</div></div>
        </div>

        <div class="card col-3">
          <h3>Behavior</h3>
          <div class="stat"><div class="k">Questions</div><div class="v">{profile['activity']['questions']}</div></div>
          <div class="stat"><div class="k">Question rate</div><div class="v">{profile['activity']['question_rate']:.1f}%</div></div>
          <div class="stat"><div class="k">URLs shared</div><div class="v">{profile['activity']['urls_shared']}</div></div>
        </div>

        <div class="card col-3">
          <h3>Emotions</h3>
          <div class="stat"><div class="k">Positive</div><div class="v" style="color: #33d6a6;">{profile['emotions']['positive_emotes']}</div></div>
          <div class="stat"><div class="k">Negative</div><div class="v" style="color: #ff5c7a;">{profile['emotions']['negative_emotes']}</div></div>
          <div class="stat"><div class="k">Ratio</div><div class="v">{profile['emotions']['emote_ratio']:.1f}x</div></div>
        </div>

        <div class="card col-3">
          <h3>Sentiment Stats</h3>
          <div class="stat"><div class="k">Positive msgs</div><div class="v" style="color: #33d6a6;">{profile['sentiment']['positive_messages']}</div></div>
          <div class="stat"><div class="k">Negative msgs</div><div class="v" style="color: #ff5c7a;">{profile['sentiment']['negative_messages']}</div></div>
          <div class="stat"><div class="k">Neutral msgs</div><div class="v">{profile['sentiment']['neutral_messages']}</div></div>
        </div>

        <!-- Peak Hours Chart -->
        <div class="card col-6">
          <h3>Peak Activity Hours</h3>
          <canvas id="peakHoursChart"></canvas>
        </div>

        <!-- Sentiment Trend Chart -->
        <div class="card col-6">
          <h3>Sentiment Trend (Last 14 Days)</h3>
          <canvas id="sentimentTrendChart"></canvas>
        </div>

        <!-- Top Conversation Partners -->
        <div class="card col-12">
          <h3>Top Conversation Partners</h3>
          <table class="table">
            <thead>
              <tr>
                <th>#</th>
                <th>Partner</th>
                <th>Replies To</th>
                <th>Replies From</th>
                <th>Total Interactions</th>
                <th>Score</th>
              </tr>
            </thead>
            <tbody>
              {"".join(f'''
              <tr>
                <td>{i + 1}</td>
                <td><b><a href="/user/{channel.lstrip('#')}/{p['partner']}" style="color: var(--accent2); text-decoration: none;">{p['partner']}</a></b></td>
                <td>{p['replies_to']}</td>
                <td>{p['replies_from']}</td>
                <td><b>{p['total_interactions']}</b></td>
                <td class="muted">{p['interaction_score']:.1f}</td>
              </tr>
              ''' for i, p in enumerate(profile['social']['top_conversation_partners']))}
            </tbody>
          </table>
        </div>
      </section>

      <script>
        // Peak Hours Chart
        new Chart(document.getElementById('peakHoursChart'), {{
          type: 'bar',
          data: {{
            labels: {json.dumps(peak_hours_labels)},
            datasets: [{{
              label: 'Messages',
              data: {json.dumps(peak_hours_data)},
              backgroundColor: 'rgba(124, 92, 255, 0.6)',
              borderColor: 'rgba(124, 92, 255, 1)',
              borderWidth: 1
            }}]
          }},
          options: {{
            responsive: true,
            plugins: {{ legend: {{ display: false }} }},
            scales: {{
              x: {{ ticks: {{ color: "rgba(255,255,255,0.65)" }} }},
              y: {{ ticks: {{ color: "rgba(255,255,255,0.65)" }} }}
            }}
          }}
        }});

        // Sentiment Trend Chart
        new Chart(document.getElementById('sentimentTrendChart'), {{
          type: 'line',
          data: {{
            labels: {json.dumps(sentiment_trend_labels)},
            datasets: [{{
              label: 'Sentiment Score',
              data: {json.dumps(sentiment_trend_data)},
              borderColor: '{sent_color}',
              backgroundColor: 'rgba(124, 92, 255, 0.1)',
              tension: 0.4,
              fill: true
            }}]
          }},
          options: {{
            responsive: true,
            plugins: {{ legend: {{ display: false }} }},
            scales: {{
              y: {{
                min: -1,
                max: 1,
                ticks: {{
                  color: "rgba(255,255,255,0.65)",
                  callback: function(value) {{
                    if (value > 0.2) return 'Positive';
                    if (value < -0.2) return 'Negative';
                    return 'Neutral';
                  }}
                }}
              }},
              x: {{ ticks: {{ color: "rgba(255,255,255,0.65)" }} }}
            }}
          }}
        }});
      </script>
    </main>
  </div>
</body>
</html>
"""

        return HTMLResponse(content=html)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

