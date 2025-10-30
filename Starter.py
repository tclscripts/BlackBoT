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

    host, port, vhost = BlackBoT.server_choose_to_connect()
    factory = BlackBoT.BotFactory(settings.nickname, settings.realname)
    factory.connect_to(host, port, vhost)
    print(f"🚀 BlackBoT started successfully! Connecting to {host}:{port}")
    BlackBoT.reactor.run()
