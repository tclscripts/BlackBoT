# starter.py

# ───────────────────────────────────────────────
# BlackBoT starter
# ───────────────────────────────────────────────

import BlackBoT
import settings

old_source = ""

if __name__ == '__main__':
    old_source = settings.sourceIP
    bot_ip = BlackBoT.host_resolve(settings.sourceIP)
    if len(settings.servers) == 0:
        print(f"No servers in list to connect")
        exit()
    else:
        get_server = BlackBoT.server_choose_to_connect()
    if settings.ssl_use == 1:
        sslContext = BlackBoT.ssl.ClientContextFactory()
        BlackBoT.reactor.connectSSL(get_server[0], int(get_server[1]), BlackBoT.BotFactory(settings.nickname, settings.realname), BlackBoT.sslContext)
    else:
        BlackBoT.reactor.connectTCP(get_server[0], int(get_server[1]), BlackBoT.BotFactory(settings.nickname, settings.realname))
    BlackBoT.reactor.run()