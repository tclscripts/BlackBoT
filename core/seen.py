# core/seen.py
import time
from typing import Optional, Tuple, List

def format_seen_stats(sql, botId: int, channel_hint: str | None = None) -> str:
    total = sql.sqlite_select("SELECT COUNT(*) FROM SEEN WHERE botId=?", (botId,))[0][0]
    uniq_n = sql.sqlite_select("SELECT COUNT(DISTINCT LOWER(nick)) FROM SEEN WHERE botId=?", (botId,))[0][0]
    uniq_h = sql.sqlite_select("SELECT COUNT(DISTINCT LOWER(ident||'@'||host)) FROM SEEN WHERE botId=?", (botId,))[0][0]
    msg = [f"🌍 Global: {total} records, {uniq_n} nicks, {uniq_h} hosts"]
    if channel_hint and channel_hint.startswith("#"):
        c_tot = sql.sqlite_select("SELECT COUNT(*) FROM SEEN WHERE botId=? AND channel=?", (botId, channel_hint))[0][0]
        c_n = sql.sqlite_select("SELECT COUNT(DISTINCT LOWER(nick)) FROM SEEN WHERE botId=? AND channel=?", (botId, channel_hint))[0][0]
        c_h = sql.sqlite_select("SELECT COUNT(DISTINCT LOWER(ident||'@'||host)) FROM SEEN WHERE botId=? AND channel=?", (botId, channel_hint))[0][0]
        msg.append(f"{channel_hint}: {c_tot} records, {c_n} nicks, {c_h} hosts")
    return " | ".join(msg)

# ---------- small helpers ----------

def _human_ago(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    for unit_sec, suf in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= unit_sec:
            return f"{seconds // unit_sec}{suf}"
    return f"{seconds}s"

def now_ts() -> int:
    return int(time.time())

def _escape_like(s: str) -> str:
    # escape \ % _
    return (s or "").replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")

def _wild_to_like(s: str) -> str:
    # * -> %, ? -> _
    return _escape_like(s).replace("*", "%").replace("?", "_")

def _parse_irc_mask(pat: str):
    """
    Returnează (nick_like, ident_like, host_like, is_mask_with_at)
    - Dacă pattern conține '@', îl considerăm mască IRC.
    - nick_like poate fi None (ignorat), ident_like/host_like sunt LIKE-uri (cu %/_)
    - Exemplu: '*!*@193.*' -> (None, '%', '193.%', True)  # ident „orice”, host „193.%”
    """
    if not pat or "@" not in pat:
        return (None, None, None, False)

    left, host = pat.split("@", 1)
    host_like = _wild_to_like(host.strip())

    nick_like = None
    ident_like = None

    if "!" in left:
        nick, ident = left.split("!", 1)
        nick_like = _wild_to_like(nick.strip()) if nick.strip() else "%"
        ident_like = _wild_to_like(ident.strip()) if ident.strip() else "%"
    else:
        # forma 'ident@host' (fără '!')
        nick_like = None
        ident_like = _wild_to_like(left.strip()) if left.strip() else "%"

    # normalizează „*” -> „%”
    if nick_like == "*":
        nick_like = "%"
    if ident_like == "*":
        ident_like = "%"

    return (nick_like, ident_like, host_like, True)

def _host_repr(ident: str, host: str) -> str:
    ident = (ident or "").strip()
    host = (host or "").strip()
    if ident and host:
        return f"{ident}@{host}"
    return host or ident or ""

# ---------- Core write (UPSERT) ----------

def _upsert(sql, botId: int, channel: str, nick: str,
            host: Optional[str], ident: Optional[str],
            event: str, reason: Optional[str] = None,
            join_ts: Optional[int] = None):
    """
    Insert/Update o singură linie (botId, channel, nick).
    - join_ts se ACTUALIZEAZĂ doar când event == 'JOIN' și avem valoare.
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
            join_ts    = CASE
                            WHEN excluded.last_event = 'JOIN' AND excluded.join_ts IS NOT NULL
                              THEN excluded.join_ts
                            ELSE SEEN.join_ts
                         END;
    """, (botId, channel, nick, host or "", ident or "", event, reason or "", ts, join_ts))

def on_join(sql, botId: int, channel: str, nick: str, ident: str, host: str):
    _upsert(sql, botId, channel, nick, host, ident, "JOIN", None, join_ts=now_ts())

def on_part(sql, botId: int, channel: str, nick: str,
            ident: Optional[str] = None, host: Optional[str] = None,
            reason: Optional[str] = None):
    _upsert(sql, botId, channel, nick, host, ident, "PART", reason, join_ts=None)

def on_kick(sql, botId: int, channel: str, nick: str, kicker: str, message: Optional[str],
            ident: Optional[str] = None, host: Optional[str] = None):
    _upsert(sql, botId, channel, nick, host, ident, "KICK",
            f"kicked by {kicker}: {message or ''}", join_ts=None)

def on_quit(sql, botId: int, nick: str, message: Optional[str],
            ident: Optional[str] = None, host: Optional[str] = None):
    """
    QUIT nu are canal în mesaj; îl aplicăm pe toate canalele cunoscute.
    Completăm host/ident DOAR dacă lipsesc în DB.
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
            SET last_event = 'QUIT',
                reason     = ?,
                last_ts    = ?,
                host       = CASE WHEN (SEEN.host IS NULL OR SEEN.host = '')
                                   AND ? IS NOT NULL AND ? <> '' THEN ?
                                 ELSE SEEN.host END,
                ident      = CASE WHEN (SEEN.ident IS NULL OR SEEN.ident = '')
                                   AND ? IS NOT NULL AND ? <> '' THEN ?
                                 ELSE SEEN.ident END
            WHERE botId = ? AND channel = ? AND nick = ? COLLATE NOCASE;
        """, (reason, ts,
              host, host, host,
              ident, ident, ident,
              botId, channel, nick))

def on_nick_change(sql, botId: int, oldnick: str, newnick: str):
    """
    La rename:
      - păstrăm vechile rânduri (oldnick) marcate ca NICK (reason: 'renamed to <newnick>')
      - upsert pe newnick pe aceleași canale, păstrând join_ts și host/ident
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
        # upsert pe noul nick, păstrând join_ts și completând host/ident
        sql.sqlite3_insert("""
            INSERT INTO SEEN (botId, channel, nick, host, ident, last_event, reason, last_ts, join_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(botId, channel, nick)
            DO UPDATE SET
                host       = CASE
                               WHEN excluded.host IS NOT NULL AND excluded.host <> '' THEN excluded.host
                               ELSE SEEN.host
                             END,
                ident      = CASE
                               WHEN excluded.ident IS NOT NULL AND excluded.ident <> '' THEN excluded.ident
                               ELSE SEEN.ident
                             END,
                last_event = CASE
                               WHEN SEEN.last_event = 'JOIN' OR excluded.last_event = 'JOIN' THEN 'JOIN'
                               ELSE excluded.last_event
                             END,
                reason     = excluded.reason,
                last_ts    = ?,
                join_ts    = COALESCE(SEEN.join_ts, excluded.join_ts)
        """, (botId, channel, newnick, host, ident, ev, reason, last_ts, jts, ts_now))

        # marchează OLD ca NICK (nu șterge)
        sql.sqlite3_update("""
            UPDATE SEEN
            SET last_event = 'NICK',
                reason     = ?,
                last_ts    = ?
            WHERE botId = ? AND channel = ? AND nick = ? COLLATE NOCASE
        """, (f"renamed to {newnick}", ts_now, botId, channel, oldnick))

# ---------- Query & formatting ----------

def format_seen(sql, botId: int, pattern: str, channel_hint: str | None = None, bot=None) -> str:
    """
    Suportă * și ? pe nick/host și pe ident@host.
    Afișează:
      - Most recent (cu host mereu; dacă e online și avem join_ts -> „since X ago”)
      - Other recent (până la 3; completează cu useri de pe același host dacă e nevoie)
    """
    pattern = (pattern or "").strip()
    if not pattern:
        return "Usage: seen <nick|host-pattern>"

    # Încearcă să interpretezi ca mască IRC (cu '@')
    nick_like, ident_like, host_like, is_mask = _parse_irc_mask(pattern)

    ch_clause = ""
    ch_param = []
    if channel_hint and channel_hint.startswith("#"):
        ch_clause = " AND channel = ?"
        ch_param = [channel_hint]

    if is_mask:
        # Construim WHERE din ident/host (nick din mască îl ignorăm pentru DB; nu e în același câmp)
        where = "botId = ?"
        params = [botId]

        if ident_like and ident_like != "%":
            where += " AND ident LIKE ? ESCAPE '\\'"
            params.append(ident_like)

        # host este obligatoriu când e mască cu '@'
        where += " AND host LIKE ? ESCAPE '\\'"
        params.append(host_like)

        if ch_param:
            where += ch_clause
            params += ch_param

        rows = sql.sqlite_select(f"""
                SELECT channel, nick, host, ident, last_event, reason, last_ts, join_ts
                FROM SEEN
                WHERE {where}
                ORDER BY last_ts DESC
                LIMIT 10
            """, tuple(params)) or []

    else:
        # Fallback (fără '@'): căutăm pe nick, host, ident@host – ca înainte
        like = _wild_to_like(pattern)
        params = [botId, like, like, like] + ch_param
        rows = sql.sqlite_select(f"""
                SELECT channel, nick, host, ident, last_event, reason, last_ts, join_ts
                FROM SEEN
                WHERE botId = ?
                  AND (
                        nick                    LIKE ? ESCAPE '\\' COLLATE NOCASE
                     OR host                    LIKE ? ESCAPE '\\' COLLATE NOCASE
                     OR (ident || '@' || host)  LIKE ? ESCAPE '\\' COLLATE NOCASE
                  )
                  {ch_clause}
                ORDER BY last_ts DESC
                LIMIT 10
            """, tuple(params)) or []

    # fallback: poate e online acum chiar dacă nu avem SEEN
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

    # host_tag ALWAYS for most recent
    ident = (most.get('ident') or '').strip()
    host = (most.get('host') or '').strip()
    if ident and host:
        host_tag = f" [{ident}@{host}]"
    elif host:
        host_tag = f" [{host}]"
    elif ident:
        host_tag = f" [{ident}]"
    else:
        host_tag = ""

    # este online?
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
                f"📌 Most recent → {most['nick']}{host_tag} is currently online "
                f"in {most['channel']} (since {joined_ago} ago)"
            )
        else:
            most_line = (
                f"📌 Most recent → {most['nick']}{host_tag} is currently online "
                f"in {most['channel']}"
            )
    else:
        last_ts = int(most.get("last_ts") or 0)
        most_ago = _human_ago(now - last_ts)
        reason = f" ({most['reason']})" if most.get("reason") else ""
        stay_info = ""
        if most.get("join_ts") and (most.get("last_event") or "").upper() in ("PART", "QUIT", "KICK"):
            stay_info = f" (stayed {_human_ago(int(most['last_ts']) - int(most['join_ts']))})"
        most_line = (
            f"📌 Most recent → {most['nick']}{host_tag} @ {most['channel']} — "
            f"{(most.get('last_event') or '').upper()}{reason} {most_ago} ago{stay_info}"
        )

    # Other recent (up to 3)
    others = recs[1:4]
    other_lines = []

    # completează cu același host dacă avem mai puțin de 3
    if len(others) < 3 and host:
        seen_keys = {(most['channel'], most['nick'])}
        seen_keys.update({(r['channel'], r['nick']) for r in others})
        extra_limit = 3 - len(others)
        same_host_rows = sql.sqlite_select("""
            SELECT channel, nick, host, ident, last_event, reason, last_ts, join_ts
            FROM SEEN
            WHERE botId = ?
              AND host = ?
              AND (nick <> ? COLLATE NOCASE)
            ORDER BY last_ts DESC
            LIMIT ?
        """, (botId, host, most['nick'], extra_limit)) or []
        for r in same_host_rows:
            rr = dict(zip(cols, r))
            key = (rr['channel'], rr['nick'])
            if key not in seen_keys:
                others.append(rr)
                seen_keys.add(key)
                if len(others) >= 3:
                    break

    if others:
        other_lines.append("Other recent (up to 3):")
        base_host = (most.get("ident") or "", most.get("host") or "")
        for r in others:
            ago = _human_ago(now - int(r["last_ts"] or 0))
            r_ident = (r.get("ident") or "").strip()
            r_host  = (r.get("host")  or "").strip()
            show_host = (r_ident, r_host) != base_host
            tag = ""
            if show_host:
                if r_ident and r_host:
                    tag = f" [{r_ident}@{r_host}]"
                elif r_host:
                    tag = f" [{r_host}]"
                elif r_ident:
                    tag = f" [{r_ident}]"
            reason = f" ({r['reason']})" if r.get("reason") else ""
            other_lines.append(
                f"• {r['nick']}{tag} @ {r['channel']} — {(r.get('last_event') or '').upper()}{reason} {ago} ago"
            )

    return "\n".join([header, most_line, *other_lines])
