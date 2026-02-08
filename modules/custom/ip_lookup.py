"""
IP Lookup Plugin
================
BlackIP.tcl-style IP geolocation and information lookup.

Migrated from commands.py to plugin system with FLAGS support.

Features:
- Resolve A+AAAA for hostnames
- Prefer IPv6 as primary when available
- Geolocation via ip-api.com
- Proxy detection via proxycheck.io
- WHO lookup support for nicks
- Show "Other rDNS" for multiple IPs

Commands:
  .ip <ip|host|nick>  - IP geolocation and info lookup

Flags: - (public - oricine poate folosi)

Author: BlackBoT Team
Version: 1.1.0
"""

from twisted.internet.threads import deferToThread
from core.plugin_manager import PluginBase
import socket
import ipaddress
import requests

# Plugin metadata
PLUGIN_INFO = {
    'name': 'ip_lookup',
    'version': '1.1.0',
    'author': 'BlackBoT Team',
    'description': 'IP geolocation and information lookup (BlackIP.tcl-style)',
    'dependencies': ['twisted', 'requests'],
}


# =============================================================================
# Helper Functions
# =============================================================================

def _is_ip(s: str) -> bool:
    """Check if string is a valid IP address"""
    try:
        ipaddress.ip_address(s)
        return True
    except Exception:
        return False


def _is_ip_or_hostname(text: str) -> bool:
    """
    Check if text is an IP address or resolvable hostname.
    If not, assume it's a nickname.
    """
    # Check if it's an IP
    if _is_ip(text):
        return True

    # Check if it looks like a hostname (contains dots or is localhost)
    if '.' in text or text.lower() == 'localhost':
        return True

    return False


def _resolve_all_ips(name: str):
    """
    Resolve hostname to all IPs (both IPv4 and IPv6).

    Returns:
        (v4_list, v6_list) - Lists of unique IPs in discovery order
    """
    v4, v6 = [], []
    try:
        infos = socket.getaddrinfo(name, None, proto=socket.IPPROTO_TCP)
        for fam, _, _, _, sockaddr in infos:
            ip = sockaddr[0]
            if fam == socket.AF_INET:
                if ip not in v4:
                    v4.append(ip)
            elif fam == socket.AF_INET6:
                if ip not in v6:
                    v6.append(ip)
    except Exception:
        pass

    return v4, v6


def _pick_primary_and_others(v4, v6):
    """
    BlackIP.tcl behavior:
      - primary = first IPv6 if exists else first IPv4
      - others = remaining IPs (both v4 and v6) excluding primary

    Returns:
        (primary_ip, other_ips_list)
    """
    primary = None
    if v6:
        primary = v6[0]
        v6_rest = v6[1:]
        v4_rest = v4
    else:
        primary = v4[0] if v4 else None
        v4_rest = v4[1:] if v4 else []
        v6_rest = v6

    others = []
    # In TCL: first they append ipv4_list then ipv6_list (rest only)
    if v4_rest:
        others.extend(v4_rest)
    if v6_rest:
        others.extend(v6_rest)

    return primary, others


# =============================================================================
# API Lookups
# =============================================================================

def _ip_api_lookup(ip: str, timeout=5):
    """
    Geolocation lookup via ip-api.com (same as BlackIP.tcl).

    Returns:
        {'ok': True, 'raw': data} on success
        {'ok': False, 'error': msg} on failure
    """
    url = (
        f"http://ip-api.com/json/{ip}"
        "?fields=country,regionName,city,lat,lon,timezone,mobile,proxy,query,reverse,status,message,isp"
    )

    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code != 200:
            return {"ok": False, "error": f"ip-api.com HTTP {r.status_code}"}

        data = r.json()

        if data.get("status") != "success":
            return {"ok": False, "error": data.get("message") or "ip-api.com fail"}

        return {"ok": True, "raw": data}

    except Exception as e:
        return {"ok": False, "error": str(e)}


def _proxycheck_lookup(ip: str, timeout=5):
    """
    Proxy check via proxycheck.io (same as BlackIP.tcl).

    Returns:
        True if proxy, False if not, None on error
    """
    try:
        url = f"http://proxycheck.io/v1/{ip}"
        r = requests.get(url, timeout=timeout)

        if r.status_code != 200:
            return None

        data = r.json()

        # proxycheck returns a dict keyed by the IP:
        # { "status":"ok", "IP":"x.x.x.x", "x.x.x.x": {"proxy":"yes"/"no", ... } }
        node = data.get(ip)
        if isinstance(node, dict):
            p = (node.get("proxy") or "").lower()
            if p in ("yes", "no"):
                return (p == "yes")

        return None

    except Exception:
        return None


# =============================================================================
# Formatting
# =============================================================================

def _format_blackip(host_display: str, ip: str, data: dict, proxy_bool, other_ips):
    """
    Format output in BlackIP.tcl style.

    Returns:
        List of output lines
    """
    city = (data.get("city") or "").strip()
    region = (data.get("regionName") or "").strip()
    country = (data.get("country") or "").strip()

    loc_parts = [p for p in (city, region, country) if p]
    location = ", ".join(loc_parts) if loc_parts else "-"

    lat = data.get("lat")
    lon = data.get("lon")
    latlon = f" ({lat}, {lon})" if lat is not None and lon is not None else ""

    tz = data.get("timezone") or "-"
    isp = data.get("isp") or "-"

    mobile = data.get("mobile")
    mobile_s = "Yes" if mobile is True else "No"

    proxy_val = proxy_bool
    if proxy_val is None:
        proxy_val = data.get("proxy")
    proxy_s = "Yes" if proxy_val is True else "No"

    # Main line (everything on one line)
    main_line = (
        f"🌐 Host: {host_display} | "
        f"📍 Location: {location}{latlon} | "
        f"🕒 Timezone: {tz} | "
        f"🛰️ IP: {ip} | "
        f"🏢 ISP: {isp} | "
        f"📱 Mobile: {mobile_s} | "
        f"🛡️ Proxy: {proxy_s}"
    )

    lines = [main_line]

    # Separate line for Other rDNS (only if present)
    if other_ips:
        lines.append(f"🔗 Other rDNS: {', '.join(other_ips)}")

    return lines


# =============================================================================
# Main Lookup Logic
# =============================================================================

def _lookup_for_host_or_ip(host_or_ip: str, header_line: str = None, host_label: str = None):
    """
    Main lookup logic for IP or hostname.

    Args:
        host_or_ip: IP or hostname to lookup
        header_line: Optional header line (for nick lookups)
        host_label: Optional label for host display

    Returns:
        List of output lines
    """
    # Decide primary IP and "other IPs"
    other_ips = []
    primary_ip = None

    if _is_ip(host_or_ip):
        primary_ip = host_or_ip
    else:
        v4, v6 = _resolve_all_ips(host_or_ip)
        primary_ip, other_ips = _pick_primary_and_others(v4, v6)
        if not primary_ip:
            return [f"❌ Cannot resolve host: {host_or_ip}"]

    # ip-api.com lookup
    ip_api = _ip_api_lookup(primary_ip, timeout=5)
    if not ip_api.get("ok"):
        return [f"❌ IP lookup failed for {primary_ip}: {ip_api.get('error', 'fail')}"]

    data = ip_api["raw"]

    # proxycheck lookup
    proxy_bool = None
    try:
        proxy_bool = _proxycheck_lookup(primary_ip, timeout=5)
    except Exception:
        proxy_bool = None

    # Host display
    host_display = host_label or host_or_ip

    lines = []
    if header_line:
        lines.append(header_line)

    lines.extend(_format_blackip(host_display, primary_ip, data, proxy_bool, other_ips))
    return lines


# =============================================================================
# Plugin Class (OOP Style) - RECOMMENDED for FLAG support
# =============================================================================

class Plugin(PluginBase):
    """
    IP Lookup Plugin - OOP Style

    Permite configurare ușoară a flag-urilor de access.
    """

    def __init__(self, bot):
        super().__init__(bot)

    def on_load(self):
        """Register commands with flags"""

        # Opțiuni de configurare pentru flags:
        self.register_command('ip', self.cmd_ip,
                            flags='-',
                            description='IP geolocation lookup (public)')

    def cmd_ip(self, bot, channel, feedback, nick, host, msg):
        """
        .ip <ip|host|nick>

        BlackIP.tcl-like implementation:
          - resolve A+AAAA for hostnames
          - prefer IPv6 as primary when available
          - geolocation via ip-api.com (same as BlackIP.tcl)
          - proxy flag via proxycheck.io (same as BlackIP.tcl)
          - show "Other rDNS:" as the other resolved IPs (A/AAAA leftovers)

        Flags: - (public - oricine)
        """
        from core.environment_config import config

        target = (msg or "").strip().split()[0] if msg else ""
        if not target:
            bot.send_message(feedback, f"Usage: {config.char}ip <ip|host|nick>")
            return

        # If it's an IP or hostname directly => do direct lookup
        if _is_ip(target) or _is_ip_or_hostname(target):
            def work():
                return _lookup_for_host_or_ip(target)

            def done(lines):
                for line in lines:
                    bot.send_message(feedback, line)

            deferToThread(work).addCallback(done)
            return

        # Else treat as nick => WHO lookup first
        def on_who_result(info):
            if not info:
                # Cache fallback
                if hasattr(bot, "channel_details"):
                    for row in bot.channel_details:
                        if len(row) > 3 and (row[1] or "").lower() == target.lower():
                            info = {'nick': row[1], 'host': row[3]}
                            break

            if not info:
                bot.send_message(feedback, f"❌ Cannot resolve user: {target}")
                return

            user_host = info.get('host', '')
            user_nick = info.get('nick', target)

            if not user_host:
                bot.send_message(feedback, f"❌ No host found for {user_nick}")
                return

            def work():
                # First line: "Nick: X ; Host: Y"
                header = f"Nick: {user_nick} ; Host: {user_host}"
                return _lookup_for_host_or_ip(user_host, header_line=header, host_label=user_host)

            def done(lines):
                for line in lines:
                    bot.send_message(feedback, line)

            deferToThread(work).addCallback(done)

        def on_who_error(failure):
            bot.send_message(feedback, f"❌ Cannot resolve user: {target}")

        if hasattr(bot, "get_user_info_async"):
            d = bot.get_user_info_async(target, timeout=5.0)
            d.addCallback(on_who_result)
            d.addErrback(on_who_error)
        else:
            bot.send_message(feedback, f"❌ WHO lookup not available. Try: {config.char}ip <ip|host>")


# =============================================================================
# Plugin Registration
# =============================================================================

def register(bot):
    """Register plugin"""
    return Plugin(bot)