# starter.py

# ───────────────────────────────────────────────
# BlackBoT starter
# ───────────────────────────────────────────────

import BlackBoT as B
import settings as s

old_source = ""

if __name__ == '__main__':
    old_source = s.sourceIP
    bot_ip = B.host_resolve(s.sourceIP)
    if len(s.servers) == 0:
        print(f"No servers in list to connect")
        exit()
    else:
        get_server = B.server_choose_to_connect()
    if s.ssl_use == 1:
        sslContext = B.ssl.ClientContextFactory()
        B.reactor.connectSSL(get_server[0], int(get_server[1]), B.BotFactory(s.nickname, s.realname), B.sslContext)
    else:
        B.reactor.connectTCP(get_server[0], int(get_server[1]), B.BotFactory(s.nickname, s.realname))
    B.reactor.run()