# core/seen.py
import time
from typing import Optional, Tuple, List

# ---------- small helpers ----------

def _human_ago(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    for unit_sec, suf in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= unit_sec:
            return f"{seconds // unit_sec}{suf}"
    return f"{seconds}s"

def _like_from_pattern(p: str) -> str:
    """
    Convert user pattern with * and ? to SQL LIKE.
    Escapes existing % and _ to avoid accidental wildcards.
    """
    if p is None:
        return "%"
    p = p.replace("%", r"[%]").replace("_", r"[_]")  # escape existing % and _
    p = p.replace("*", "%").replace("?", "_")        # * -> %, ? -> _
    return p

def _host_repr(ident: str, host: str) -> str:
    ident = (ident or "").strip()
    host = (host or "").strip()
    if ident and host:
        return f"{ident}@{host}"
    return host or ident or ""

def _norm_host(s: str) -> str:
    return (s or "").strip().lower()

def now_ts() -> int:
    return int(time.time())

def like_from_wildcard(pattern: str) -> str:
    if pattern is None:
        return "%"
    p = pattern.replace("%", r"\%").replace("_", r"\_")
    p = p.replace("*", "%").replace("?", "_")
    return p

# ---------- Core write (UPSERT) ----------

def _upsert(sql, botId: int, channel: str, nick: str,
            host: Optional[str], ident: Optional[str],
            event: str, reason: Optional[str] = None,
            join_ts: Optional[int] = None):
    """
    Insert/update one row for (botId, channel, nick).

    IMPORTANT:
    - `join_ts` is UPDATED **only** when event == 'JOIN' (and value is not NULL).
      For any other event, we KEEP the existing join_ts.
    """
    ts = now_ts()
    sql.sqlite3_insert("""
        INSERT INTO SEEN (botId, channel, nick, host, ident, last_event, reason, last_ts, join_ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(botId, channel, nick)
        DO UPDATE SET
            host       = excluded.host,
            ident      = excluded.ident,
            last_event = excluded.last_event,
            reason     = excluded.reason,
            last_ts    = excluded.last_ts,
            -- update join_ts ONLY on JOIN, otherwise keep existing value
            join_ts    = CASE
                            WHEN excluded.last_event = 'JOIN' AND excluded.join_ts IS NOT NULL
                              THEN excluded.join_ts
                            ELSE SEEN.join_ts
                         END;
    """, (botId, channel, nick, host or "", ident or "", event, reason or "", ts, join_ts))

def on_join(sql, botId: int, channel: str, nick: str, ident: str, host: str):
    # set a fresh join_ts (now) only on JOIN
    _upsert(sql, botId, channel, nick, host, ident, "JOIN", None, join_ts=now_ts())

def on_part(sql, botId: int, channel: str, nick: str, reason: Optional[str] = None):
    # do NOT touch join_ts
    _upsert(sql, botId, channel, nick, None, None, "PART", reason, join_ts=None)

def on_kick(sql, botId: int, channel: str, nick: str, kicker: str, message: Optional[str]):
    # do NOT touch join_ts
    _upsert(sql, botId, channel, nick, None, None, "KICK",
            f"kicked by {kicker}: {message or ''}", join_ts=None)

def on_quit(sql, botId: int, nick: str, message: Optional[str]):
    """
    QUIT has no channel; mark QUIT on all known channels for this nick.
    Keep join_ts to be able to say how long they stayed before quitting.
    """
    ts = now_ts()
    reason = message or ""
    rows = sql.sqlite_select(
        "SELECT DISTINCT channel FROM SEEN WHERE botId = ? AND nick = ? COLLATE NOCASE;",
        (botId, nick,)
    )
    for (channel,) in rows:
        sql.sqlite3_update("""
            UPDATE SEEN
            SET last_event = 'QUIT', reason = ?, last_ts = ?
            WHERE botId = ? AND channel = ? AND nick = ? COLLATE NOCASE;
        """, (reason, ts, botId, channel, nick))

def on_nick_change(sql, botId: int, oldnick: str, newnick: str):
    """
    When a user renames:
      - Keep oldnick rows: mark last_event = 'NICK', reason = 'renamed to <newnick>', last_ts = now (keep join_ts)
      - Upsert newnick rows for the same channels, carrying over host/ident/join_ts and refreshing last_ts = now
    """
    rows = sql.sqlite_select("""
        SELECT channel, host, ident, last_event, reason, last_ts, join_ts
        FROM SEEN
        WHERE botId = ? AND nick = ? COLLATE NOCASE;
    """, (botId, oldnick))
    if not rows:
        return

    ts_now = now_ts()

    for channel, host, ident, ev, reason, last_ts, jts in rows:
        # 1) Upsert NEW nick on the same channel, keep join_ts so we can show "since ..."
        sql.sqlite3_insert("""
            INSERT INTO SEEN (botId, channel, nick, host, ident, last_event, reason, last_ts, join_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(botId, channel, nick)
            DO UPDATE SET
                host       = excluded.host,
                ident      = excluded.ident,
                -- keep the *live* state if it was JOIN before (user is still on channel),
                -- otherwise carry over whatever last_event we had
                last_event = CASE
                               WHEN SEEN.last_event = 'JOIN' OR excluded.last_event = 'JOIN'
                                 THEN 'JOIN'
                               ELSE excluded.last_event
                             END,
                reason     = excluded.reason,
                last_ts    = ?,
                -- preserve or carry over the session start
                join_ts    = COALESCE(SEEN.join_ts, excluded.join_ts)
        """, (botId, channel, newnick, host, ident, ev, reason, last_ts, jts, ts_now))

        # 2) Mark OLD nick as a nick-change event (do not delete)
        sql.sqlite3_update("""
            UPDATE SEEN
            SET last_event = 'NICK',
                reason     = ?,
                last_ts    = ?
            WHERE botId = ? AND channel = ? AND nick = ? COLLATE NOCASE
        """, (f"renamed to {newnick}", ts_now, botId, channel, oldnick))

# ---------- Query & formatting ----------

def _search(sql, botId: int, pattern: str, channel_hint: Optional[str]) -> Tuple[int, List[tuple]]:
    """
    Return (total_count, rows_limited_to_5_by_recency).
    pattern: wildcard accepted on nick or host (LIKE over both).
    """
    like = like_from_wildcard(pattern)
    params = [botId, like, like]
    chan_clause = ""
    if channel_hint:
        chan_clause = "AND channel = ?"
        params.append(channel_hint)

    total = sql.sqlite_select(f"""
        SELECT COUNT(*) FROM SEEN
        WHERE botId = ?
          AND (nick LIKE ? ESCAPE '\\' OR host LIKE ? ESCAPE '\\')
          {chan_clause}
    """, tuple(params))[0][0]

    rows = sql.sqlite_select(f"""
        SELECT channel, nick, host, ident, last_event, reason, last_ts, join_ts
        FROM SEEN
        WHERE botId = ?
          AND (nick LIKE ? ESCAPE '\\' OR host LIKE ? ESCAPE '\\')
          {chan_clause}
        ORDER BY last_ts DESC
        LIMIT 5
    """, tuple(params))

    return int(total), rows

def format_seen(sql, botId: int, pattern: str, channel_hint: str | None = None, bot=None) -> str:
    """
    Pretty formatter for seen results:
      - supports * and ? on nick/host
      - shows count, most recent, and up to 3 "Other recent"
      - if most recent is online:
          * with join_ts -> "online since X ago"
          * without join_ts -> "is currently online"
      - if offline and join_ts exists -> append "(stayed Y)"
    """
    pattern = (pattern or "").strip()
    if not pattern:
        return "Usage: seen <nick|host-pattern>"

    like = _like_from_pattern(pattern)
    params = [botId, like, like]
    ch_clause = ""
    if channel_hint and channel_hint.startswith("#"):
        ch_clause = " AND channel = ?"
        params.append(channel_hint)

    rows = sql.sqlite_select(f"""
        SELECT channel, nick, host, ident, last_event, reason, last_ts, join_ts
        FROM SEEN
        WHERE botId = ?
          AND (nick LIKE ? ESCAPE '\\' OR host LIKE ? ESCAPE '\\')
          {ch_clause}
        ORDER BY last_ts DESC
        LIMIT 10
    """, tuple(params)) or []

    # Fallback: no DB rows but maybe user is online now (when pattern is exact nick)
    if not rows and bot is not None and channel_hint and channel_hint.startswith("#"):
        if "*" not in pattern and "?" not in pattern:
            try:
                for c, nick, ident, host, *_ in getattr(bot, "channel_details", []):
                    if c.lower() == channel_hint.lower() and nick.lower() == pattern.lower():
                        ih = (ident or "").strip()
                        hh = (host or "").strip()
                        bracket = f" [{ih}@{hh}]" if ih or hh else ""
                        return f"{nick}{bracket} is currently online in {channel_hint}."
            except Exception:
                pass
        return f"No matches for '{pattern}' in {channel_hint}."

    if not rows:
        where = f"in {channel_hint}" if channel_hint else "anywhere"
        return f"No matches for '{pattern}' {where}."

    cols = ["channel", "nick", "host", "ident", "last_event", "reason", "last_ts", "join_ts"]
    recs = [dict(zip(cols, r)) for r in rows]

    total = len(recs)
    most = recs[0]

    where = f"in {most['channel']}" if channel_hint else f"in {most['channel']}"
    header = f"Found {total} match(es) for '{pattern}' {where}."

    now = now_ts()
    last_ago = _human_ago(now - int(most.get("last_ts") or 0))
    jts = most.get("join_ts")
    ident_host = f"{most.get('ident') or ''}@{most.get('host') or ''}".strip("@")
    ident_host_br = f" [{ident_host}]" if ident_host and ident_host != "@" else ""

    # Is the most recent user currently on channel?
    online_suffix = ""
    is_online = False
    if bot is not None and hasattr(bot, "user_on_channel"):
        try:
            is_online = bot.user_on_channel(most["channel"], most["nick"])
        except Exception:
            pass

    if is_online:
        if (most.get("last_event") or "").upper() == "JOIN" and most.get("join_ts"):
            joined_ago = _human_ago(now - int(most["join_ts"]))
            most_line = (
                f"📌 Most recent → {most['nick']}{ident_host_br} is currently online "
                f"in {most['channel']} (since {joined_ago} ago)"
            )
        else:
            most_line = (
                f"📌 Most recent → {most['nick']}{ident_host_br} is currently online "
                f"in {most['channel']}"
            )
    else:
        now = int(time.time())
        last_ts = int(most.get("last_ts") or 0)
        join_ts = int(most.get("join_ts") or 0) if most.get("join_ts") else None

        most_ago = _human_ago(now - last_ts)
        reason = f" ({most['reason']})" if most.get("reason") else ""
        stay_info = ""
        if most.get("join_ts") and most.get("last_event") in ("PART", "QUIT", "KICK"):
            stay_info = f" (stayed {_human_ago(int(most['last_ts']) - int(most['join_ts']))})"
        most_line = (
            f"📌 Most recent → {most['nick']}{ident_host_br} @ {most['channel']} — "
            f"{(most.get('last_event') or '').upper()}{reason} {most_ago} ago{stay_info}"
        )

    # Other recent (up to 3), excluding the most recent
    others = recs[1:4]
    other_lines = []
    if others:
        other_lines.append("Other recent (up to 3):")
        base_host = (most.get("ident") or "", most.get("host") or "")
        for r in others:
            ago = _human_ago(now - int(r["last_ts"] or 0))
            r_host_tuple = (r.get("ident") or "", r.get("host") or "")
            show_host = r_host_tuple != base_host
            host_tag = f" [{r_host_tuple[0]}@{r_host_tuple[1]}]" if show_host and any(r_host_tuple) else ""
            reason = f" ({r['reason']})" if r.get("reason") else ""
            other_lines.append(
                f"• {r['nick']}{host_tag} @ {r['channel']} — {(r.get('last_event') or '').upper()}{reason} {ago} ago"
            )

    return "\n".join([header, most_line, *other_lines])
