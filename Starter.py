# Starter.py

# ───────────────────────────────────────────────
# BlackBoT Starter Script
# ───────────────────────────────────────────────

import BlackBoT
import settings
from twisted.internet import ssl
import os
import sys

old_source = ""

class ClientSSLContext(ssl.ClientContextFactory):
    def getContext(self):
        ctx = ssl.ClientContextFactory.getContext(self)
        cert = settings.ssl_cert_file
        key = settings.ssl_key_file

        if cert and key and os.path.isfile(cert) and os.path.isfile(key):
            try:
                ctx.use_certificate_file(cert)
                ctx.use_privatekey_file(key)
                print(f"🔐 Loaded SSL certificate and key for mutual TLS.")
            except Exception as e:
                print(f"❌ Failed to load SSL cert/key: {e}")
                sys.exit(1)
        return ctx


if __name__ == '__main__':
    old_source = settings.sourceIP

    if not settings.servers:
        print("❌ No servers in list to connect to.")
        exit(1)

    get_server = BlackBoT.server_choose_to_connect()
    host, port = get_server[0], int(get_server[1])

    if settings.ssl_use == 1:
        sslContext = ClientSSLContext()
        BlackBoT.reactor.connectSSL(
            host,
            port,
            BlackBoT.BotFactory(settings.nickname, settings.realname),
            sslContext
        )
    else:
        BlackBoT.reactor.connectTCP(
            host,
            port,
            BlackBoT.BotFactory(settings.nickname, settings.realname)
        )
    print("🚀 BlackBoT started successfully!")
    BlackBoT.reactor.run()
