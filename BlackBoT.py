# added alternative nick change, if not available


## import esential modules
import sys
import ssl
import subprocess
import pkg_resources
import sqlite3


## import external files
import SQL
###


## Esential information about the bot

#Set here the nickname
nickname = "WorkIT"
#Set here the alternative nickname
altnick = "WorkIT_"
#Set here the realname
realname = "version"
#Set here the server [in the future, it will have the posibility to add more servers]
server = "irc.libera.chat"
port = 6667
#Set here 1 if you want to use SSL, else set 0
ssl_use = 0
#Set away message
away = "No Away"
#Set here the SQLite database name (<name>.db)
sqlite3_database = "work.db"

###
#Do not change these values
tables_created = 0


## check required modules, install them if necesary
required = {'twisted','pyopenssl','service_identity'}
installed = {pkg.key for pkg in pkg_resources.working_set}
missing = required - installed

if missing:
    python = sys.executable
    subprocess.check_call([python, '-m', 'pip', 'install', *missing], stdout=subprocess.DEVNULL)
###


## after instalation, import the required modules
from twisted.internet import protocol, ssl, reactor
from twisted.words.protocols import irc

class Bot(irc.IRCClient):
    def __init__(self, api_key, nickname, channels, rate_limit):
        self.api_key = api_key
        self.nickname = nickname
        self.realname = realname
        self.channels = channels
        self.rate_limit = rate_limit
        self.message_log = {}
        self.ignore_list = {}
        self.max_tokens = 500
        self.heat = 0.5
        self.connection = sqlite3.connect(sqlite3_database)
        self.change_nick = 0
        ## create tables if not already created.
        SQL.sqlite3_createTables(self)

    def signedOn(self):
        print("Signed on to the server")
        self.sendLine("AWAY :" + away)
        for channel in self.channels:
            self.join(channel)
    def lineReceived(self, line):
        try:
            line = line.decode("utf-8-sig")
        except UnicodeDecodeError:
            return

        super().lineReceived(line)

    def privmsg(self, user, channel, msg):
        nick = user.split("!")[0]
        host = user.split("@")[1]

        if f"{nick}@{host}" in self.ignore_list and self.ignore_list[f"{nick}@{host}"] > self.get_time():
            return

        if msg.startswith("!ignore"):
            parts = msg.split()
            if len(parts) != 3:
                self.send_message(channel, "Invalid ignore command. Use !ignore [nick] [time].")
            else:
                ignore_nick = parts[1]
                ignore_time_str = parts[2]
                if not ignore_time_str.endswith("m"):
                    self.send_message(channel, "Invalid time. Use minutes (m).")
                else:
                    try:
                        ignore_time = int(ignore_time_str[:-1])
                    except ValueError:
                        self.send_message(channel, "Invalid time. Please enter an integer.")
                    else:
                        nick_host = f"{nick}@{host}"
                        self.ignore_list[nick_host] = self.get_time() + ignore_time * 60  # Convert minutes to seconds
                        self.send_message(channel, f"Ignoring {nick_host} for {ignore_time} minutes.")
        elif msg.startswith(self.nickname):
            host = user.split("@")[1]
            if f"{nick}@{host}" in self.message_log:
                last_message_time = self.message_log[f"{nick}@{host}"]
                time_diff = self.rate_limit - (self.get_time() - last_message_time)
                if time_diff > 0:
                    print(f"Rate limiting user {host} for {time_diff} seconds")
                    self.send_message(channel, f"Rate limiting user {host} for {time_diff} seconds")
                    return
            self.message_log[f"{nick}@{host}"] = self.get_time()
            question = msg.split(self.nickname)[1].strip()
            response = self.call_gpt3_api(question)
            self.send_message(channel, response)
        elif msg.startswith("!generate"):
            host = user.split("@")[1]
            if f"{nick}@{host}" in self.message_log:
                last_message_time = self.message_log[f"{nick}@{host}"]
                time_diff = self.rate_limit - (self.get_time() - last_message_time)
                if time_diff > 0:
                    print(f"Rate limiting user {host} for {time_diff} seconds")
                    self.send_message(channel, f"Rate limiting user {host} for {time_diff} seconds")
                    return
            self.message_log[f"{nick}@{host}"] = self.get_time()
            request = msg.split("!generate")[1].strip()
            image_url = self.call_dalle_api(request)
            self.send_message(channel, image_url)
        elif msg.startswith("!heat"):
            host = user.split("@")[1]
            if f"{nick}@{host}" in self.message_log:
                last_message_time = self.message_log[f"{nick}@{host}"]
                time_diff = self.rate_limit - (self.get_time() - last_message_time)
                if time_diff > 0:
                    print(f"Rate limiting user {host} for {time_diff} seconds")
                    self.send_message(channel, f"Rate limiting user {host} for {time_diff} seconds")
                    return
            self.message_log[f"{nick}@{host}"] = self.get_time()
            heat_value = msg.split("!heat")[1].strip()
            try:
                heat_value = float(heat_value)
            except ValueError:
                self.send_message(channel, "Invalid heat value. Please enter a float between 0 and 1.")
                return
            if heat_value < 0 or heat_value > 1:
                self.send_message(channel, "Invalid heat value. Please enter a float between 0 and 1.")
                return
            self.heat = heat_value
            self.send_message(channel, f"Heat setting updated to {self.heat}.")
        elif msg.startswith("!tokens"):
            host = user.split("@")[1]
            if f"{nick}@{host}" in self.message_log:
                last_message_time = self.message_log[f"{nick}@{host}"]
                time_diff = self.rate_limit - (self.get_time() - last_message_time)
                if time_diff > 0:
                    print(f"Rate limiting user {host} for {time_diff} seconds")
                    self.send_message(channel, f"Rate limiting user {host} for {time_diff} seconds")
                    return
            self.message_log[f"{nick}@{host}"] = self.get_time()
            token_value = msg.split("!tokens")[1].strip()
            try:
                token_value = int(token_value)
            except ValueError:
                self.send_message(channel, "Invalid token value. Please enter an integer.")
                return
            if token_value < 1:
                self.send_message(channel, "Invalid token value. Please enter an integer greater than 0.")
                return
            self.max_tokens = token_value
            self.send_message(channel, f"Max token setting updated to {self.max_tokens}.")

    def send_message(self, channel, message):
        for line in message.split("\n"):
            if line.strip():
                self.msg(channel, line)

    def get_time(self):
        return int(reactor.seconds())
    
    def irc_unknown(self, prefix, command, params):
        if command == "433":  # ERR_NICKNAMEINUSE
            print(f"Nickname {self.nickname} is already in use. Trying another...")
            self.change_nick = 1

class BotFactory(protocol.ReconnectingClientFactory):
    def __init__(self, api_key, nickname, channels, rate_limit):
        self.api_key = api_key
        self.nickname = nickname
        self.channels = channels
        self.rate_limit = rate_limit
        

    def buildProtocol(self, addr):
        print("Creating new protocol instance")
        if hasattr(self, 'change_nick'):
            if self.change_nick == 1:
                self.nickname = altnick
                self.change_nick = 0
        bot = Bot(self.api_key, self.nickname, self.channels, self.rate_limit)
        bot.factory = self
        return bot

    def clientConnectionLost(self, connector, reason):
        print(f"Connection lost: {reason}. Attempting to reconnect...")
        self.retry(connector)

if __name__ == '__main__':
    api_key = "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"
    channels = ["#BT"]
    rate_limit = 30  # limit to one request every second
    
    if (ssl_use == 1):
        sslContext = ssl.ClientContextFactory()
        reactor.connectSSL(server, port, BotFactory(api_key, nickname, channels, rate_limit), sslContext)
    else:
        reactor.connectTCP(server, port, BotFactory(api_key, nickname, channels, rate_limit))
    reactor.run()

