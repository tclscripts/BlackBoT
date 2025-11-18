# core/nettools.py
from __future__ import annotations

import socket
import subprocess
import platform
import ipaddress
import re
import threading
import time
from typing import Dict, List, Tuple, Optional

try:
    import requests
except Exception:
    requests = None

try:
    import settings as s
except Exception:
    class s:  # defaults dacă nu există settings încărcat
        net_ipinfo_endpoint = "https://ipapi.co/{ip}/json/"
        net_ipinfo_timeout = 5
        net_ping_count = 3
        net_ping_timeout_ms = 1000


# ── IP info cache (thread-safe) ────────────────────────────────────────────────
_IPINFO_CACHE_LOCK = threading.RLock()
_IPINFO_CACHE: Dict[str, Tuple[float, dict]] = {}

# defaults sigure la import; le actualizăm din settings când avem nevoie
_IPINFO_TTL_OK = 300       # secunde pentru rezultate valide
_IPINFO_TTL_ERR = 60       # secunde pentru erori
_IPINFO_MAXSIZE = 512      # număr maxim de IP-uri cache

# ── ASN info cache (thread-safe) ───────────────────────────────────────────────
_ASN_CACHE_LOCK = threading.RLock()
_ASN_CACHE: Dict[str, Tuple[float, dict]] = {}  # key="AS12345", value=(ts, result)

_ASN_TTL_OK  = 300   # default 5 min
_ASN_TTL_ERR = 60    # default 1 min
_ASN_MAXSIZE = 512   # max entries


def _refresh_cache_settings():
    """Actualizează valorile IP info cache din settings, dacă există (fără a arunca)."""
    global _IPINFO_TTL_OK, _IPINFO_TTL_ERR, _IPINFO_MAXSIZE
    try:
        import settings as s  # type: ignore
        _IPINFO_TTL_OK  = int(getattr(s, "net_ipinfo_ttl",  _IPINFO_TTL_OK))
        _IPINFO_TTL_ERR = int(getattr(s, "net_ipinfo_err_ttl", _IPINFO_TTL_ERR))
        _IPINFO_MAXSIZE = int(getattr(s, "net_ipinfo_maxsize", _IPINFO_MAXSIZE))
    except Exception:
        pass


def _refresh_asn_cache_settings():
    """Actualizează valorile ASN TTL din settings, dacă există (fără a arunca)."""
    global _ASN_TTL_OK, _ASN_TTL_ERR, _ASN_MAXSIZE
    try:
        import settings as s  # type: ignore
        _ASN_TTL_OK  = int(getattr(s, "net_asn_ttl",  _ASN_TTL_OK))
        _ASN_TTL_ERR = int(getattr(s, "net_asn_err_ttl", _ASN_TTL_ERR))
        _ASN_MAXSIZE = int(getattr(s, "net_asn_maxsize", _ASN_MAXSIZE))
    except Exception:
        pass


# ───────────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────────

def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
        return True
    except Exception:
        return False


def _clean_host(text: str) -> str:
    """
    Normalizează hostul:
      - taie schema/paths (http://..., /path)
      - suport pentru IPv6 literal: "[2001:db8::1]:6667" -> "2001:db8::1"
      - pentru forme host:port taie portul, dar nu distruge IPv6 ne-bracketed
    """
    if not text:
        return text
    text = text.strip()
    # elimină schema (http://, irc:// etc.)
    text = re.sub(r"^[a-zA-Z]+://", "", text)
    # taie path
    text = text.split("/", 1)[0]

    # [IPv6]:port  sau [IPv6]
    if text.startswith("["):
        m = re.match(r"^\[([0-9a-fA-F:.]+)\](?::\d+)?$", text)
        if m:
            return m.group(1)

    # dacă e IP (v4 sau v6), returnează-l ca atare
    if _is_ip(text):
        return text

    # host:port (dar nu forma IPv6 literal fără paranteze)
    # tăiem portul doar dacă e exact un singur ':' urmat de cifre
    if re.match(r"^[^:]+:\d+$", text) and not _is_ip(text):
        return text.split(":", 1)[0]

    return text


def _split_chunks(s: str, maxlen: int = 400) -> List[str]:
    lines: List[str] = []
    while len(s) > maxlen:
        cut = s.rfind(" ", 0, maxlen)
        if cut == -1:
            cut = maxlen
        lines.append(s[:cut])
        s = s[cut:].lstrip()
    if s:
        lines.append(s)
    return lines


# ───────────────────────────────────────────────────────────────────────────────
# PING (cross-platform)
# ───────────────────────────────────────────────────────────────────────────────

def ping(host: str,
         count: Optional[int] = None,
         timeout_ms: Optional[int] = None) -> Dict[str, object]:
    host_in = _clean_host(host)
    if not host_in:
        return {"ok": False, "error": "Empty host"}

    count = int(count or getattr(s, "net_ping_count", 3))
    timeout_ms = int(timeout_ms or getattr(s, "net_ping_timeout_ms", 1000))

    # rezolvare IP + determină v4/v6
    ip = None
    is_v6 = False
    try:
        if _is_ip(host_in):
            ip = host_in
            is_v6 = ":" in host_in
        else:
            infos = socket.getaddrinfo(host_in, None, proto=socket.IPPROTO_TCP)
            v6 = [sa[4][0] for sa in infos if sa[0] == socket.AF_INET6]
            v4 = [sa[4][0] for sa in infos if sa[0] == socket.AF_INET]
            if v6:
                ip = v6[0]; is_v6 = True
            elif v4:
                ip = v4[0]; is_v6 = False
    except Exception:
        pass

    system = platform.system().lower()
    per_reply_s = max(1, int(round(timeout_ms / 1000.0)))

    if "windows" in system:
        # pe Windows, "ping" suportă v6 nativ
        cmd = ["ping", "-n", str(count), "-w", str(timeout_ms), host_in]
    else:
        # pe Unix, folosim -6 pentru IPv6 dacă e cazul
        base = ["ping", "-n", "-q", "-c", str(count), "-W", str(per_reply_s)]
        if is_v6:
            base.insert(1, "-6")
        cmd = base + [host_in]

    try:
        start = time.time()
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=max(5, count * (timeout_ms/1000.0 + 1.5))
        )
        raw = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
        duration = (time.time() - start)

        # extract metrice
        sent = recv = None
        loss = None
        avg = None

        m = re.search(r"(\d+)\s+packets? transmitted, (\d+)\s+received", raw)
        if not m:
            m = re.search(r"Sent = (\d+), Received = (\d+), Lost = (\d+)", raw)
            if m:
                sent = int(m.group(1)); recv = int(m.group(2))
                lost = int(m.group(3))
                if sent:
                    loss = int(round(lost * 100.0 / sent))
        else:
            sent = int(m.group(1)); recv = int(m.group(2))
            if sent:
                loss = int(round((sent - recv) * 100.0 / sent))

        m = re.search(r"rtt min/avg/max/[a-z]+ = [\d\.]+/([\d\.]+)/", raw)
        if not m:
            m = re.search(r"Average = (\d+)(?:ms)?", raw)
            if m:
                avg = float(m.group(1))
        else:
            avg = float(m.group(1))

        ok = (recv is not None and recv > 0 and (loss is None or loss < 100))
        return {
            "ok": ok,
            "host": host_in,
            "ip": ip,
            "sent": sent,
            "recv": recv,
            "loss": loss,
            "avg_ms": avg,
            "elapsed_s": round(duration, 2),
            "raw": raw.strip()
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "host": host_in, "ip": ip, "error": "Ping timeout"}
    except FileNotFoundError:
        return {"ok": False, "host": host_in, "ip": ip, "error": "OS ping command not found"}
    except Exception as e:
        return {"ok": False, "host": host_in, "ip": ip, "error": f"{e.__class__.__name__}: {e}"}


def format_ping(result: Dict[str, object]) -> List[str]:
    if not result.get("ok"):
        err = result.get("error") or "Unreachable"
        ip = result.get("ip") or "?"
        return [f"❌ ping {result.get('host')} [{ip}]: {err}"]

    host = result.get("host")
    ip = result.get("ip") or "?"
    sent = result.get("sent")
    recv = result.get("recv")
    loss = result.get("loss")
    avg = result.get("avg_ms")
    parts = [
        f"✅ ping {host} [{ip}] — sent:{sent} recv:{recv} loss:{loss}% avg:{'%.1f' % avg if avg is not None else '?'}ms"
    ]
    return parts


# ───────────────────────────────────────────────────────────────────────────────
# DNS
# ───────────────────────────────────────────────────────────────────────────────

def dns_lookup(name: str) -> Dict[str, object]:
    """
    Returnează: { ok, host, A:[], AAAA:[], CNAME:[], error? }
    Folosește socket pentru A/AAAA; CNAME nu e expus de socket — încercăm prin getaddrinfo canonical.
    """
    host = _clean_host(name)
    if not host:
        return {"ok": False, "error": "Empty host"}

    v4, v6 = set(), set()
    cname = None

    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        for fam, _, _, canon, sockaddr in infos:
            if canon and canon != host:
                cname = canon
            if fam == socket.AF_INET:
                v4.add(sockaddr[0])
            elif fam == socket.AF_INET6:
                v6.add(sockaddr[0])
    except Exception as e:
        return {"ok": False, "host": host, "error": f"{e.__class__.__name__}: {e}"}

    return {"ok": True, "host": host, "A": sorted(v4), "AAAA": sorted(v6), "CNAME": cname}


def reverse_dns(ip_or_host: str) -> Dict[str, object]:
    """
    rDNS (PTR) cu suport IPv6. Dacă primește hostname,
    îl rezolvă și preferă o adresă IPv6 (dacă există), altfel IPv4.
    """
    target = _clean_host(ip_or_host)
    if not target:
        return {"ok": False, "error": "Empty input"}

    # normalizează la IP (preferă AAAA)
    ip = None
    if _is_ip(target):
        ip = target
    else:
        try:
            infos = socket.getaddrinfo(target, None, proto=socket.IPPROTO_TCP)
            # preferă AF_INET6
            v6 = [sa[4][0] for sa in infos if sa[0] == socket.AF_INET6]
            v4 = [sa[4][0] for sa in infos if sa[0] == socket.AF_INET]
            ip = (v6[0] if v6 else (v4[0] if v4 else None))
        except Exception:
            ip = None

    if not ip:
        return {"ok": False, "ip": target, "error": "Cannot resolve host"}

    try:
        name, aliases, _ = socket.gethostbyaddr(ip)
        return {"ok": True, "ip": ip, "ptr": name, "aliases": aliases}
    except Exception as e:
        return {"ok": False, "ip": ip, "error": f"{e.__class__.__name__}: {e}"}


def format_dns(result: Dict[str, object]) -> List[str]:
    if not result.get("ok"):
        return [f"❌ dns: {result.get('error','lookup failed')}"]
    host = result.get("host")
    a = ", ".join(result.get("A", []) or []) or "-"
    aaaa = ", ".join(result.get("AAAA", []) or []) or "-"
    cname = result.get("CNAME") or "-"
    out = f"🔎 DNS {host} → A: {a} | AAAA: {aaaa} | CNAME: {cname}"
    return _split_chunks(out)


def format_rdns(result: Dict[str, object]) -> List[str]:
    ip = result.get("ip") or "?"
    if not result.get("ok"):
        return [f"ℹ️ rDNS {ip}: (no PTR) {result.get('error','')}".strip()]
    ptr = result.get("ptr") or "-"
    aliases = ", ".join(result.get("aliases") or []) or "-"
    return [f"🔁 rDNS {ip} → PTR: {ptr} | aliases: {aliases}"]


# smart dns
def smart_dns(target: str) -> dict:
    """
    Decide automat: forward (A/AAAA) vs reverse (PTR).
    Returnează dict cu cheie 'mode': 'forward' | 'reverse', plus payload specific.
    """
    t = _clean_host(target)
    if not t:
        return {"ok": False, "error": "Empty input", "mode": "forward"}

    if _is_ip(t):
        rd = reverse_dns(t)
        rd["mode"] = "reverse"
        return rd
    else:
        fw = dns_lookup(t)
        fw["mode"] = "forward"
        return fw


def format_smart_dns(result: dict) -> List[str]:
    mode = result.get("mode")
    if mode == "reverse":
        return format_rdns(result)
    # implicit forward
    return format_dns(result)


# ───────────────────────────────────────────────────────────────────────────────
# IP Info (ASN/Geo) cu fallback + cache
# ───────────────────────────────────────────────────────────────────────────────

def ip_info(ip_or_host: str) -> Dict[str, object]:
    _refresh_cache_settings()
    target = _clean_host(ip_or_host)
    if not target:
        return {"ok": False, "error": "Empty input"}

    # normalize to IP
    try:
        ip = target if _is_ip(target) else socket.gethostbyname(target)
    except Exception:
        return {"ok": False, "ip": target, "error": "Cannot resolve host"}

    # ---- CACHE HIT
    now = time.time()
    with _IPINFO_CACHE_LOCK:
        hit = _IPINFO_CACHE.get(ip)
        if hit:
            ts, cached = hit
            ttl = _IPINFO_TTL_OK if cached.get("ok") else _IPINFO_TTL_ERR
            if now - ts < ttl:
                return cached.copy()

    if requests is None:
        res = {"ok": False, "ip": ip, "error": "requests module not available"}
        with _IPINFO_CACHE_LOCK:
            _IPINFO_CACHE[ip] = (now, res)
        return res

    timeout = int(getattr(s, "net_ipinfo_timeout", 5))
    endpoints = [
        ("ipapi.co",  f"https://ipapi.co/{ip}/json/"),
        ("ipwho.is",  f"https://ipwho.is/{ip}"),
        ("ipinfo.io", f"https://ipinfo.io/{ip}/json"),
    ]

    def _normalize(name: str, data: dict) -> Dict[str, object]:
        org = data.get("org") or data.get("isp") or (data.get("connection") or {}).get("org")
        asn = data.get("asn") or (data.get("connection") or {}).get("asn") or None
        if not asn and isinstance(org, str):
            m = re.search(r"\bAS\d+\b", org)
            if m:
                asn = m.group(0)
        if asn and isinstance(asn, (int, float)) and not str(asn).startswith("AS"):
            asn = f"AS{int(asn)}"
        country = data.get("country_name") or data.get("country") or data.get("countryCode")
        region  = data.get("region") or data.get("region_name") or data.get("regionName")
        city    = data.get("city")
        tz      = data.get("timezone") or data.get("time_zone") or data.get("timezone_name")
        if isinstance(tz, dict):
            tz = tz.get("id") or tz.get("name") or tz.get("abbr")
        return {"org": org, "asn": asn, "country": country, "region": region, "city": city, "timezone": tz, "raw": data}

    result = None
    for name, url in endpoints:
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code != 200:
                continue
            data = {}
            try:
                data = r.json()
            except Exception:
                continue
            if name == "ipwho.is" and not data.get("success", True):
                continue
            norm = _normalize(name, data)
            result = {"ok": True, "ip": ip, "provider": name, **norm}
            break
        except requests.Timeout:
            continue
        except Exception:
            continue

    if result is None:
        result = {"ok": False, "ip": ip, "error": "All IP info providers failed or timed out"}

    # ---- CACHE SET (cu pruning simplu)
    with _IPINFO_CACHE_LOCK:
        if len(_IPINFO_CACHE) >= _IPINFO_MAXSIZE:
            # prune: șterge cele mai vechi ~10%
            cutoff = sorted(_IPINFO_CACHE.items(), key=lambda kv: kv[1][0])[: max(1, _IPINFO_MAXSIZE // 10)]
            for k, _ in cutoff:
                _IPINFO_CACHE.pop(k, None)
        _IPINFO_CACHE[ip] = (now, result)

    return result


def format_ipinfo(result: Dict[str, object]) -> List[str]:
    ip = result.get("ip") or "?"
    if not result.get("ok"):
        return [f"❌ ipinfo {ip}: {result.get('error','lookup failed')}"]

    # Curăță LOC fără duplicări
    parts: List[str] = []
    for p in [result.get("city"), result.get("region"), result.get("country")]:
        p = (p or "").strip()
        if p and (not parts or p.lower() != parts[-1].lower()):
            parts.append(p)
    loc = " / ".join(parts) if parts else "-"

    org = result.get("org") or "-"
    asn = result.get("asn") or "-"
    tz  = result.get("timezone") or "-"
    prov = result.get("provider") or ""

    line = f"🛰️ IP {ip} — ASN:{asn} | ORG:{org} | LOC:{loc} | TZ:{tz}"
    if prov:
        line += f" 〔via {prov}〕"
    return _split_chunks(line)


# ───────────────────────────────────────────────────────────────────────────────
# ASN info — bgp.tools → peeringdb (+enrich bgpview) → bgpview
# ───────────────────────────────────────────────────────────────────────────────

def _extract_asn_number(sas: str | None) -> Optional[int]:
    if not sas:
        return None
    m = re.search(r'AS?(\d+)', str(sas).upper())
    return int(m.group(1)) if m else None


def asn_info(target: str) -> Dict[str, object]:
    _refresh_asn_cache_settings()

    t = _clean_host(target)
    if not t:
        return {"ok": False, "error": "Empty input"}

    asn_num = _extract_asn_number(t)
    if asn_num is None:
        # normalizează la IP
        try:
            ip = t if _is_ip(t) else socket.gethostbyname(t)
        except Exception:
            ip = None
        if ip:
            res = ip_info(ip)
            asn_num = _extract_asn_number(res.get("asn")) if res.get("ok") else None

    if asn_num is None:
        return {"ok": False, "error": "Could not determine ASN from input"}

    if requests is None:
        return {"ok": False, "error": "requests module not available"}

    timeout = int(getattr(s, "net_ipinfo_timeout", 5))
    AS = f"AS{asn_num}"

    # 0) BGP.Tools (primar)
    def from_bgptools() -> Optional[dict]:
        try:
            r = requests.get(f"https://bgp.tools/api/{AS}", timeout=timeout)
            if r.status_code != 200:
                return None
            j = r.json() or {}
            if not isinstance(j, dict) or not j.get("asn"):
                return None
            return {
                "ok": True,
                "provider": "bgp.tools",
                "asn": j.get("asn"),
                "name": j.get("name") or j.get("org"),
                "country": j.get("country"),
                "rir": j.get("rir"),
                "prefixes": j.get("prefixes"),
                "peers": j.get("peers"),
                "upstreams": j.get("upstreams"),
                "downstreams": j.get("downstreams"),
                "org": j.get("org") or j.get("name"),
                "website": j.get("website") or j.get("org_site"),
                "raw": j
            }
        except Exception:
            return None

    bgpt = from_bgptools()
    if bgpt and bgpt.get("ok") and bgpt.get("prefixes"):
        return bgpt

    # 1) PeeringDB (fallback 1)
    try:
        r = requests.get(f"https://peeringdb.com/api/net?asn={asn_num}", timeout=timeout)
        if r.status_code == 200:
            j = r.json() or {}
            rows = j.get("data") or []
            if isinstance(rows, list) and rows:
                d = rows[0]
                name = d.get("name") or d.get("aka") or ""
                org = d.get("name_long") or d.get("aka") or name
                website = d.get("website") or ""
                rir_status = d.get("rir_status")  # ex: "ok"
                rir = rir_status or None
                pref4 = d.get("info_prefixes4")
                pref6 = d.get("info_prefixes6")
                total = None
                try:
                    p4 = int(pref4) if str(pref4).isdigit() else 0
                    p6 = int(pref6) if str(pref6).isdigit() else 0
                    total = (p4 or 0) + (p6 or 0)
                except Exception:
                    total = None

                result = {
                    "ok": True,
                    "provider": "peeringdb",
                    "asn": AS,
                    "name": name or None,
                    "country": None,
                    "rir": rir,
                    "prefixes": total,
                    "peers": None,
                    "upstreams": None,
                    "downstreams": None,
                    "org": org or name or None,
                    "website": website or None,
                    "raw": j,

                    # extra PeeringDB
                    "prefixes4": pref4,
                    "prefixes6": pref6,
                    "traffic": d.get("info_traffic"),
                    "ratio": d.get("info_ratio"),
                    "scope": d.get("info_scope"),
                    "ipv6": d.get("info_ipv6"),
                    "ix_count": d.get("ix_count"),
                    "fac_count": d.get("fac_count"),
                    "policy_url": d.get("policy_url"),
                    "irr_as_set": d.get("irr_as_set"),
                }

                # dacă lipsesc câmpuri, completăm din bgp.tools dacă există
                if bgpt and bgpt.get("ok"):
                    for k in ["country", "rir", "prefixes", "peers", "upstreams", "downstreams", "website", "name", "org"]:
                        if result.get(k) in (None, "", "-", 0) and bgpt.get(k):
                            result[k] = bgpt.get(k)
                    result["provider"] = "peeringdb+bgp.tools"
                else:
                    # altfel completăm cu bgpview
                    try:
                        rb = requests.get(f"https://api.bgpview.io/asn/{AS}", timeout=timeout)
                        if rb.status_code == 200:
                            jb = rb.json() or {}
                            db = jb.get("data") or {}
                            if not result.get("country"):
                                result["country"] = db.get("country_code")
                            if not result.get("prefixes") and db.get("prefixes_count"):
                                result["prefixes"] = db.get("prefixes_count")
                            for ksrc, kdst in (("peers_count","peers"),("upstreams_count","upstreams"),("downstreams_count","downstreams")):
                                if not result.get(kdst) and db.get(ksrc):
                                    result[kdst] = db.get(ksrc)
                            alloc = db.get("rir_allocation") or {}
                            rir_name = alloc.get("rir_name") or db.get("rir") or None
                            alloc_date = (alloc.get("date_allocated") or "").split(" ")[0] if alloc.get("date_allocated") else None
                            rir_fmt = f"{rir_name} ({alloc_date})" if (rir_name and alloc_date) else (rir_name or None)
                            if not result.get("rir") and rir_fmt:
                                result["rir"] = rir_fmt
                            result["provider"] = "peeringdb+bgpview.io"
                    except Exception:
                        pass

                return result
    except Exception:
        pass

    # 2) fallback doar bgp.tools sau bgpview dacă nimic nu a mers
    if bgpt and bgpt.get("ok"):
        return bgpt

    try:
        r = requests.get(f"https://api.bgpview.io/asn/{AS}", timeout=timeout)
        if r.status_code == 200:
            j = r.json() or {}
            d = j.get("data") or {}
            if d:
                alloc = d.get("rir_allocation") or {}
                rir_name = alloc.get("rir_name") or d.get("rir") or None
                alloc_date = (alloc.get("date_allocated") or "").split(" ")[0] if alloc.get("date_allocated") else None
                rir_fmt = f"{rir_name} ({alloc_date})" if (rir_name and alloc_date) else (rir_name or None)
                return {
                    "ok": True,
                    "provider": "bgpview.io",
                    "asn": AS,
                    "name": d.get("name") or d.get("description_short"),
                    "country": d.get("country_code"),
                    "rir": rir_fmt,
                    "prefixes": d.get("prefixes_count"),
                    "peers": d.get("peers_count"),
                    "upstreams": d.get("upstreams_count"),
                    "downstreams": d.get("downstreams_count"),
                    "org": d.get("description_full") or d.get("name"),
                    "website": d.get("website"),
                    "raw": j
                }
    except Exception:
        pass

    return {"ok": False, "error": f"ASN lookup failed for {AS}"}


def format_asn(result: Dict[str, object]) -> List[str]:
    if not result.get("ok"):
        return [f"❌ asn: {result.get('error','lookup failed')}"]

    AS   = result.get("asn") or "AS?"
    name = (result.get("name") or result.get("org") or "-").strip() or "-"
    cc   = (result.get("country") or "-") or "-"
    rir  = result.get("rir")
    # dacă cumva e dict (de la alți provideri), extrage un rezumat
    if isinstance(rir, dict):
        rir_name = rir.get("rir_name") or rir.get("name") or rir.get("rir")
        alloc_dt = (rir.get("date_allocated") or "").split(" ")[0] if rir.get("date_allocated") else None
        rir = f"{rir_name} ({alloc_dt})" if (rir_name and alloc_dt) else (rir_name or "-")
    rir  = rir or "-"

    def _fmt(x):
        return "-" if x is None else str(x)

    prefs = _fmt(result.get("prefixes"))
    peers = _fmt(result.get("peers"))
    ups   = _fmt(result.get("upstreams"))
    downs = _fmt(result.get("downstreams"))
    prov  = result.get("provider") or ""
    site  = (result.get("website") or "").strip()

    line = f"🏷️ {AS} — {name} | CC:{cc} | RIR:{rir} | Prefixes:{prefs} | Peers:{peers} | Up:{ups} | Down:{downs}"
    if prov:
        line += f" 〔via {prov}〕"
    out = _split_chunks(line)
    if site:
        out += _split_chunks(f"↗ {site}")
    return out


# ───────────────────────────────────────────────────────────────────────────────
# ASN FULL — mostre prefixe / relații cu fetch on-demand (fără cache global)
# ───────────────────────────────────────────────────────────────────────────────

def _http_get_json(url: str, timeout: int):
    if requests is None:
        return None
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def _bgpview_prefixes(asn: str, timeout: int) -> dict:
    """Returnează: {'v4': [cidr,...], 'v6':[cidr,...]} (fără cache global)."""
    j = _http_get_json(f"https://api.bgpview.io/asn/{asn}/prefixes", timeout)
    res = {"v4": [], "v6": []}
    if j and (d := j.get("data")):
        res["v4"] = [p.get("prefix") for p in (d.get("ipv4_prefixes") or []) if p.get("prefix")]
        res["v6"] = [p.get("prefix") for p in (d.get("ipv6_prefixes") or []) if p.get("prefix")]
    return res


def _bgpview_rel(asn: str, rel: str, timeout: int) -> List[dict]:
    """
    rel ∈ {'peers','upstreams','downstreams'} → listă dict cu asn/name/cc (fără cache global).
    """
    j = _http_get_json(f"https://api.bgpview.io/asn/{asn}/{rel}", timeout)
    out: List[dict] = []
    if j and (d := j.get("data")) and isinstance(d, list):
        for r in d:
            out.append({
                "asn": f"AS{r.get('asn')}" if r.get("asn") else "",
                "name": r.get("name") or r.get("description") or "",
                "cc": r.get("country_code") or ""
            })
    return out


def asn_full(target: str, *, sample: int = 6) -> dict:
    """
    Extins: bază din asn_info (PeeringDB + enrich bgpview/bgp.tools),
    apoi mostre prefixe și relații din BGPView — fără cache global.
    """
    base = asn_info(target)
    if not base.get("ok"):
        return base

    timeout = int(getattr(s, "net_ipinfo_timeout", 5))
    asn = base["asn"]  # ex. 'AS32934'

    pref = _bgpview_prefixes(asn, timeout)
    peers = _bgpview_rel(asn, "peers", timeout)
    ups   = _bgpview_rel(asn, "upstreams", timeout)
    downs = _bgpview_rel(asn, "downstreams", timeout)

    out = dict(base)
    v4_all, v6_all = pref.get("v4") or [], pref.get("v6") or []
    out["v4_count"] = len(v4_all)
    out["v6_count"] = len(v6_all)
    out["v4_sample"] = v4_all[:sample]
    out["v6_sample"] = v6_all[:sample]

    def _names(lst: List[dict]):
        labels: List[str] = []
        for x in lst[:sample]:
            a = (x.get("asn") or "").strip()
            n = (x.get("name") or "").strip()
            labels.append(f"{a} {n}".strip() if a or n else "-")
        return labels

    out["peers_count"] = len(peers)
    out["upstreams_count"] = len(ups)
    out["downstreams_count"] = len(downs)
    out["peers_sample"] = _names(peers)
    out["upstreams_sample"] = _names(ups)
    out["downstreams_sample"] = _names(downs)

    prov = out.get("provider") or ""
    if "bgpview.io" not in prov:
        prov = (prov + "+bgpview.io") if prov else "bgpview.io"
    out["provider"] = prov
    out["ok"] = True
    return out


def format_asn_full(data: dict) -> List[str]:
    """
    Formatter pentru afișare “full”.
    Include câmpurile PeeringDB (traffic/ratio/scope/ipv6/ix/fac/policy/irr)
    + mostre prefixe și relații.
    """
    if not data.get("ok"):
        return [f"❌ asn: {data.get('error','lookup failed')}"]

    AS    = data.get("asn") or "AS?"
    name  = (data.get("name") or data.get("org") or "-").strip() or "-"
    cc    = data.get("country") or "-"
    rir   = data.get("rir") or "-"
    prov  = data.get("provider") or ""
    site  = (data.get("website") or "").strip()

    v4c = data.get("v4_count") or 0
    v6c = data.get("v6_count") or 0
    peers = data.get("peers_count"); ups = data.get("upstreams_count"); downs = data.get("downstreams_count")

    lines: List[str] = []
    head = f"🏷️ {AS} — {name} | CC:{cc} | RIR:{rir} | v4:{v4c} | v6:{v6c} | Peers:{peers or '-'} | Up:{ups or '-'} | Down:{downs or '-'}"
    if prov:
        head += f" 〔via {prov}〕"
    lines.append(head)

    # PeeringDB extras (dacă există)
    traffic = data.get("traffic")
    ratio   = data.get("ratio")
    scope   = data.get("scope")
    ipv6    = data.get("ipv6")
    ix_cnt  = data.get("ix_count")
    fac_cnt = data.get("fac_count")
    irr     = data.get("irr_as_set")
    policy  = data.get("policy_url")

    extras: List[str] = []
    if traffic: extras.append(f"Traffic:{traffic}")
    if ratio:   extras.append(f"Ratio:{ratio}")
    if scope:   extras.append(f"Scope:{scope}")
    if ipv6 is not None: extras.append(f"IPv6:{'yes' if ipv6 else 'no'}")
    if ix_cnt is not None: extras.append(f"IX:{ix_cnt}")
    if fac_cnt is not None: extras.append(f"Facilities:{fac_cnt}")
    if irr:     extras.append(f"IRR:{irr}")
    if policy:  extras.append(f"Policy:{policy}")
    if extras:
        lines.append("ℹ️ " + " | ".join(extras))

    def _join(xs): return ", ".join(xs) if xs else "-"

    if data.get("v4_sample") or data.get("v6_sample"):
        lines.append(f"📦 Prefixes: v4: {_join(data.get('v4_sample'))} | v6: {_join(data.get('v6_sample'))}")
    if data.get("peers_sample"):
        lines.append(f"🤝 Peers: {_join(data.get('peers_sample'))}")
    if data.get("upstreams_sample"):
        lines.append(f"⬆️ Upstreams: {_join(data.get('upstreams_sample'))}")
    if data.get("downstreams_sample"):
        lines.append(f"⬇️ Downstreams: {_join(data.get('downstreams_sample'))}")

    if site:
        lines.append(f"↗ {site}")

    out: List[str] = []
    for ln in lines:
        out.extend(_split_chunks(ln, 420))
    return out
