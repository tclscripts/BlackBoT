import time
import sqlite3
from sqlite3 import Error
import Variables as v
import re
import datetime


##
# check method for connection availability
class SQL:

    def __init__(self, sqlite3_database):
        self.database = sqlite3_database

    def create_connection(self):  # create connection
        try:
            connection = sqlite3.connect(self.database)
            return connection
        except Error as e:
            print("Error while connecting to SQL", e)
            return None

    ##
    # execute method
    def sqlite3_execute(self, sql):
        connection = self.create_connection()
        cursor = connection.cursor()
        try:
            out = cursor.execute(sql)
            connection.commit()
        finally:
            cursor.close()
            connection.close()
        return out

    def sqlite3_update(self, sql, what):  # update sql
        connection = self.create_connection()
        cursor = connection.cursor()
        try:
            out = cursor.execute(sql, what)
            connection.commit()
        finally:
            cursor.close()
            connection.close()
        return out

    ##
    # execute method
    def sqlite3_insert(self, sql, data):  # insert sql
        connection = self.create_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(sql, data)
            last_row_id = cursor.lastrowid
            connection.commit()
        finally:
            cursor.close()
            connection.close()
        return last_row_id

    ##
    # select method
    def sqlite_select(self, query, params=None):
        connection = self.create_connection()
        cursor = connection.cursor()
        try:
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            result = cursor.fetchall()
        finally:
            cursor.close()
            connection.close()
        return result

    def sqlite_has_active_ignores(self, botId):
        now = int(time.time())
        query = """
            SELECT 1 FROM IGNORES
            WHERE botId = ? AND expires > ?
            LIMIT 1
        """
        result = self.sqlite_select(query, (botId, now))
        return bool(result)

    def sqlite_add_ignore(self, selfbot, botId, host, duration_seconds, reason):
        now = int(time.time())
        expires = now + duration_seconds
        query = """
            INSERT INTO IGNORES (botId, host, added, expires, reason)
            VALUES (?, ?, ?, ?, ?)
        """
        self.sqlite3_insert(query, [botId, host, now, expires, reason])
        selfbot.start_ignore_cleanup_if_needed()

    def sqlite_is_ignored(self, botId, host):
        now = int(time.time())
        query = """
            SELECT id FROM IGNORES
            WHERE botId = ? AND host = ? AND expires > ?
            LIMIT 1
        """
        result = self.sqlite_select(query, [botId, host, now])
        if result:
            return True
        else:
            return False

    def sqlite_cleanup_ignores(self):
        now = int(time.time())
        query = "DELETE FROM IGNORES WHERE expires <= ?"
        self.sqlite3_insert(query, [now])

    def sqlite_log_login_attempt(self, botId, nick, host, userId, success):
        query = """
        INSERT INTO USERLOGINS (botId, nick, host, userId, success, timestamp)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        """
        self.sqlite3_insert(query, (botId, nick, host, userId, int(success)))

    def sqlite_get_user_setting(self, botId, userId, setting_name):
        query = """
            SELECT settingValue
            FROM USERSSETTINGS
            WHERE botId = ? AND userId = ? AND setting = ?
        """
        result = self.sqlite_select(query, (botId, userId, setting_name))
        if result:
            return result[0][0]
        return None

    def sqlite_update_user_setting(self, botId, userId, setting, value):
        # Verificăm dacă setarea există deja
        select_query = """
            SELECT 1 FROM USERSSETTINGS
            WHERE botId = ? AND userId = ? AND setting = ?
        """
        exists = self.sqlite_select(select_query, [botId, userId, setting])

        if exists:
            # Dacă există, facem UPDATE
            update_query = """
                UPDATE USERSSETTINGS
                SET settingValue = ?
                WHERE botId = ? AND userId = ? AND setting = ?
            """
            self.sqlite3_insert(update_query, [value, botId, userId, setting])
        else:
            # Dacă nu există, facem INSERT
            insert_query = """
                INSERT INTO USERSSETTINGS (botId, userId, setting, settingValue)
                VALUES (?, ?, ?, ?)
            """
            self.sqlite3_insert(insert_query, [botId, userId, setting, value])

    # get userId by name
    def sqlite_get_user_id_by_name(self, botId, username):
        query = "SELECT id FROM users WHERE botId = ? AND username = ? COLLATE NOCASE"
        result = self.sqlite_select(query, (botId, username))
        return result[0][0] if result else None

    # set password
    def sqlite_set_password(self, userId, hashed_password):
        query = "UPDATE users SET password = ? WHERE id = ?"
        self.sqlite3_insert(query, (hashed_password, userId))

    # get password
    def sqlite_get_user_password(self, userId):
        query = "SELECT password FROM users WHERE id = ?"
        result = self.sqlite_select(query, (userId,))
        if result and result[0][0]:
            return result[0][0]
        return None

    def sqlite3_bot_birth(self, username, nickname, realname, away):  # add new bot to database
        query = "SELECT botId from BOTSETTINGS where botUsername = '{}'".format(username)
        check = self.sqlite_select(query)
        if check:
            return [1, check[0][0]]
        else:  # add the bot to the database
            timestamp = time.time()
            query = "INSERT INTO BOTSETTINGS (botUsername, botNick, botRealname, botAway, bornTime, maxConnectTime, MaxUptime) VALUES (?, ?, ?, ?, ?, ?, ?)"
            last_row_id = self.sqlite3_insert(query, (username, nickname, realname, away, timestamp, 0, 0))

            for access in v.access_list:  # populate the access type list table
                query = "INSERT INTO VALIDACCESS (accessType, accessFlag, accessName, description) VALUES (?, ?, ?, ?)"
                self.sqlite3_insert(query, (access['name'], access['flag'], access['fullname'], access['desc']))

            for setting in v.settings:  # populate the settings table
                query = "INSERT INTO VALIDSETTINGS (setting, settingType, description) VALUES (?, ?, ?)"
                self.sqlite3_insert(query, (setting['name'], setting['type'], setting['description']))
            return [0, last_row_id]

    ##
    # add channel to database
    def sqlite3_addchan(self, channel, user, botId):
        timestamp = time.time()
        query = "INSERT INTO CHANNELS(botId, channelName, addedBy, addedTime, status, lastChangedTime, comment) VALUES (?, ?, ?, ?, ?, ?, ?)"
        data = [botId, channel, user, timestamp, 0, timestamp, ""]
        last_row_id = self.sqlite3_insert(query, data)
        return last_row_id

    def sqlite3_delchan(self, channel, botId):
        query = "DELETE FROM CHANNELS WHERE channelName='{}' AND botId = '{}'".format(channel, botId)
        self.sqlite3_execute(query)

    ##
    # get stored channels
    def sqlite3_channels(self, botId):
        query = "SELECT channelName from CHANNELS where botId = '{}'".format(botId)
        return self.sqlite_select(query)

    ###
    # get number of stored users
    def sqlite_usersNumber(self, botId):
        query = "SELECT COUNT(*) from USERS where botId = '{}'".format(botId)
        check = self.sqlite_select(query)
        return check[0][0]

    ###
    # check if there is a boss owner.
    def sqlite_isBossOwner(self, botId):
        query = ("SELECT COUNT(*) from USERS, GLOBALACCESS, VALIDACCESS where USERS.botId = GLOBALACCESS.botId and "
                 "GLOBALACCESS.botId = '{}' and GLOBALACCESS.accessId = VALIDACCESS.accessId and "
                 "VALIDACCESS.accessFlag = 'N'").format(botId)
        check = self.sqlite_select(query)
        return check[0][0]

    ##
    # check if valid channel
    def sqlite_validchan(self, channel):
        query = "SELECT id from CHANNELS where channelName = '{}'".format(channel)
        check = self.sqlite_select(query)
        if check:
            return True
        else:
            return False

    # auto suspend SQL procedure
    def sqlite_auto_suspend_channel(self, channel, reason):
        timestamp = time.time()
        self.sqlite3_insert("UPDATE CHANNELS SET status = 1, comment = ?, lastChangedTime = ? WHERE channelName = ?", (reason , timestamp, channel))
        print(f"[INFO] Marked channel {channel} as suspended in DB")

    # get max connect time si max uptime
    def sqlite_get_bot_stats(self, bot_id):
        query = """
            SELECT maxConnectTime, maxUptime 
            FROM botsettings 
            WHERE botId = ?
        """
        result = self.sqlite_select(query, (bot_id,))
        if result:
            return result[0]  # (maxConnectTime, maxUptime)
        return (0, 0)

    # check if channel is suspended
    def sqlite_is_channel_suspended(self, channel):
        query = "SELECT status FROM CHANNELS WHERE channelName = ?"
        result = self.sqlite_select(query, (channel,))
        if result and result[0][0] == 1:
            return True
        return False

    def sqlite_update_uptime(self, selfbot, botId):
        while True:
            timestamp = time.time()
            uptime_duration = int(timestamp - selfbot.current_start_time) if selfbot.current_start_time else 0
            connect_duration = int(timestamp - selfbot.current_connect_time) if selfbot.current_connect_time else 0

            current_max_uptime = self.sqlite_get_max_uptime(botId)
            current_max_connect = self.sqlite_get_max_ontime(botId)

            if uptime_duration > current_max_uptime:
                self.sqlite3_insert(
                    "UPDATE BOTSETTINGS SET maxUptime = ? WHERE botId = ?",
                    (uptime_duration, botId)
                )

            if selfbot.connected and connect_duration > current_max_connect:
                self.sqlite3_insert(
                    "UPDATE BOTSETTINGS SET maxConnectTime = ? WHERE botId = ?",
                    (connect_duration, botId)
                )

            time.sleep(60)

    def sqlite_get_max_uptime(self, botId):  # get maximum uptime
        query = "SELECT maxUptime from BOTSETTINGS where botId = '{}'".format(botId)
        check = self.sqlite_select(query)
        if check:
            return check[0][0]
        else:
            return 0

    def sqlite_get_max_ontime(self, botId):  # get maximum connect time
        query = "SELECT maxConnectTime from BOTSETTINGS where botId = '{}'".format(botId)
        check = self.sqlite_select(query)
        if check:
            return check[0][0]
        else:
            return 0

    def sqlite_add_user(self, botId, username, password):
        timestamp = time.time()
        query = "INSERT INTO USERS (botid, username, password, added) VALUES (?, ?, ?, ?)"
        data = [botId, username, password, timestamp]
        last_row_id = self.sqlite3_insert(query, data)
        return last_row_id

    def sqlite_add_user_host(self, botId, userId, hostname):
        query = "INSERT INTO USERSHOSTNAMES (botid, userId, hostname) VALUES (?, ?, ?)"
        data = [botId, userId, hostname]
        self.sqlite3_insert(query, data)

    def sqlite_add_local_access(self, botId, userId, channelId, accessId, addedBy):
        timestamp = time.time()
        query = (
            "INSERT INTO CHANNELACCESS (botid, accessId, channelId, userId, addedBy, lastModifiedBy, lastModified, "
            "autoop, autovoice, autohalfop) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
        data = [botId, accessId, channelId, userId, addedBy, "N/A", timestamp, 0, 0, 0]
        self.sqlite3_insert(query, data)

    def sqlite_add_global_access(self, botId, userId, accessId, addedBy):
        timestamp = time.time()

        check_query = """
            SELECT 1 FROM GLOBALACCESS
            WHERE botId = ? AND userId = ?
        """
        exists = self.sqlite_select(check_query, (botId, userId))

        if exists:
            # UPDATE
            update_query = """
                UPDATE GLOBALACCESS
                SET accessId = ?, lastModifiedBy = ?, lastModified = ?
                WHERE botId = ? AND userId = ?
            """
            self.sqlite3_update(update_query, (accessId, addedBy, timestamp, botId, userId))
        else:
            # INSERT
            insert_query = """
                INSERT INTO GLOBALACCESS
                (botid, accessId, userId, addedBy, lastModifiedBy, lastModified,
                 autoop, autovoice, autohalfop)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            data = [botId, accessId, userId, addedBy, "N/A", timestamp, 0, 0, 0]
            self.sqlite3_insert(insert_query, data)

    def sqlite_get_access_id(self, flag):
        flags = list(flag)
        placeholders = ','.join(['?'] * len(flags))
        query = f"SELECT accessId FROM VALIDACCESS WHERE accessFlag IN ({placeholders})"
        check = self.sqlite_select(query, flags)
        if check:
            return check[0][0]
        else:
            return 0

    def sqlite_check_global_access(self, botId, userId, accessId):
        query = "SELECT COUNT(*) from GLOBALACCESS where botId = '{}' and userId = '{}' and accessId = '{}'".format(botId, userId, accessId)
        result = self.sqlite_select(query)
        return result[0][0]

    def sqlite_check_channel_access(self, botId, userId, accessId, channel):
        query = ("""
            SELECT COUNT(*) 
            FROM CHANNELACCESS ca
            JOIN CHANNELS ch ON ch.id = ca.channelId
            WHERE ch.channelName = ? COLLATE NOCASE
              AND ch.botId = ?
              AND ca.botId = ?
              AND ca.userId = ?
              and ca.accessId = ?
        """)
        result = self.sqlite_select(query, (channel, botId, botId, userId, accessId))
        return result[0][0]

    def sqlite_has_access_flags(self, botId, userId, flags, channel=None):
        flags_list = list(flags)
        placeholders = ','.join(['?'] * len(flags_list))

        if channel:
            query = f"""
                SELECT 1 FROM CHANNELACCESS ca
                JOIN VALIDACCESS va ON ca.accessId = va.accessId
                JOIN CHANNELS ch ON ca.channelId = ch.id
                WHERE ca.botId = ? AND ca.userId = ? AND ch.channelName = ? AND va.accessFlag IN ({placeholders})
                LIMIT 1
            """
            params = [botId, userId, channel] + flags_list
        else:
            query = f"""
                SELECT 1 FROM GLOBALACCESS ga
                JOIN VALIDACCESS va ON ga.accessId = va.accessId
                WHERE ga.botId = ? AND ga.userId = ? AND va.accessFlag IN ({placeholders})
                LIMIT 1
            """
            params = [botId, userId] + flags_list

        return bool(self.sqlite_select(query, tuple(params)))

    def string_match_nocase(self, pattern, string):
        regex_pattern = '^' + re.escape(pattern).replace(r'\*', '.*').replace(r'\?', '.') + '$'
        return re.match(regex_pattern, string, re.IGNORECASE)

    def sqlite_handle(self, botId, nickname, nick_host):
        query = "SELECT id, username FROM USERS WHERE botId = ?"
        results = self.sqlite_select(query, (botId,))

        if not results:
            return None

        for row in results:
            user_id, username = row
            query = "SELECT hostname FROM USERSHOSTNAMES WHERE botId = ? AND userId = ?"
            host_results = self.sqlite_select(query, (botId, user_id))
            for host_row in host_results:
                host = host_row[0]
                if self.string_match_nocase(host, f"{nickname}!{nick_host}"):
                    return user_id, username
        return None

    # host for user exists
    def sqlite_check_user_host_exists(self, botId, userId, hostname):
        query = "SELECT 1 FROM USERSHOSTNAMES WHERE botId = ? AND userId = ? AND LOWER(hostname) = LOWER(?) LIMIT 1"
        result = self.sqlite_select(query, (botId, userId, hostname))
        return bool(result)

    def sqlite_has_local_access(self, botId, userId, channel):
        query = """
            SELECT 1 FROM CHANNELACCESS ca
            JOIN CHANNELS ch ON ca.channelId = ch.id
            WHERE ca.botId = ? AND ca.userId = ? AND ch.channelName = ?
            LIMIT 1
        """
        return bool(self.sqlite_select(query, (botId, userId, channel)))

    def sqlite_get_channel_id(self, botId, channel_name):
        query = "SELECT id FROM CHANNELS WHERE botId = ? AND channelName = ?"
        result = self.sqlite_select(query, (botId, channel_name))
        return result[0][0] if result else None

    def sqlite_create_user_with_host(self, botId, nickname, host):
        userId = self.sqlite_add_user(botId, nickname, "")
        self.sqlite_add_user_host(botId, userId, host)
        return userId

    def sqlite_get_user_channel_access_id(self, botId, userId, channel):
        query = """
            SELECT ca.accessId
            FROM CHANNELACCESS ca
            JOIN CHANNELS ch ON ca.channelId = ch.id
            WHERE ca.botId = ? AND ca.userId = ? AND ch.channelName = ?
        """
        result = self.sqlite_select(query, (botId, userId, channel))
        return result[0][0] if result else None

    def sqlite_get_flag_by_access_id(self, accessId):
        query = "SELECT accessFlag FROM VALIDACCESS WHERE accessId = ?"
        result = self.sqlite_select(query, (accessId,))
        return result[0][0] if result else None

    def sqlite_list_users_by_access(self, botId, channel, flag):
        query = """
            SELECT u.username, va.accessFlag, va.accessName
            FROM USERS u
            JOIN CHANNELACCESS ca ON ca.userId = u.id
            JOIN VALIDACCESS va ON va.accessId = ca.accessId
            JOIN CHANNELS ch ON ch.id = ca.channelId
            WHERE ch.channelName = ? AND ch.botId = ? AND va.accessFlag = ?
        """
        return self.sqlite_select(query, (channel, botId, flag))

    def sqlite_get_max_flag(self, botId, userId, channel=None):
        flag_priority = {
            'N': 1, 'n': 2, 'm': 3, 'M': 4,
            'A': 5, 'O': 6, 'V': 7, 'P': 8, 'B': 9
        }

        flags = []

        query = """
                        SELECT va.accessFlag FROM VALIDACCESS va
                        JOIN GLOBALACCESS ga ON ga.accessId = va.accessId
                        WHERE ga.botId = ? AND ga.userId = ?
                    """
        results = self.sqlite_select(query, (botId, userId))
        flags = [row[0] for row in results]
        if not flags:
            query = """
                            SELECT va.accessFlag FROM VALIDACCESS va
                            JOIN CHANNELACCESS ca ON ca.accessId = va.accessId
                            JOIN CHANNELS ch ON ca.channelId = ch.id
                            WHERE ca.botId = ? AND ca.userId = ? AND ch.channelName = ?
                        """
            results = self.sqlite_select(query, (botId, userId, channel))
            flags = [row[0] for row in results]
            if not flags:
                return None
            else:
                return min(flags, key=lambda f: flag_priority.get(f, 100))
        else:
            return min(flags, key=lambda f: flag_priority.get(f, 100))

    def sqlite_list_all_global_access(self, botId):
        query = """
            SELECT u.username, va.accessFlag
            FROM USERS u
            JOIN GLOBALACCESS ga ON u.id = ga.userId
            JOIN VALIDACCESS va ON ga.accessId = va.accessId
            WHERE ga.botId = ?
        """
        return self.sqlite_select(query, (botId,))

    def sqlite_list_all_channel_access(self, botId, channel):
        query = """
            SELECT u.username, va.accessFlag
            FROM USERS u
            JOIN CHANNELACCESS ca ON u.id = ca.userId
            JOIN CHANNELS ch ON ca.channelId = ch.id
            JOIN VALIDACCESS va ON ca.accessId = va.accessId
            WHERE ca.botId = ? AND ch.channelName = ?
        """
        return self.sqlite_select(query, (botId, channel))

    def sqlite_list_global_users_by_flag(self, botId, flag):
        query = """
            SELECT u.username
            FROM USERS u
            JOIN GLOBALACCESS ga ON u.id = ga.userId
            JOIN VALIDACCESS va ON ga.accessId = va.accessId
            WHERE ga.botId = ? AND va.accessFlag = ?
        """
        return [row[0] for row in self.sqlite_select(query, (botId, flag))]

    def sqlite_add_channel_access(self, botId, channel, userId, accessId, addedBy):
        chan_id_query = "SELECT id FROM CHANNELS WHERE botId = ? AND channelName COLLATE NOCASE = ?"
        channelId = self.sqlite_select(chan_id_query, (botId, channel))

        if not channelId:
            return False
        channelId = channelId[0][0]
        check_query = "SELECT * FROM CHANNELACCESS WHERE botId = ? AND channelId = ? AND userId = ?"
        exists = self.sqlite_select(check_query, (botId, channelId, userId))

        now = datetime.datetime.now()
        if exists:
            # Update
            update_query = """
                UPDATE CHANNELACCESS
                SET accessId = ?, lastModifiedBy = ?, lastModified = ?
                WHERE botId = ? AND channelId = ? AND userId = ?
            """
            self.sqlite3_insert(update_query, (accessId, addedBy, now, botId, channelId, userId))
        else:
            # Insert
            insert_query = """
                INSERT INTO CHANNELACCESS (botId, accessId, channelId, userId, addedBy, lastModifiedBy, lastModified)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """
            self.sqlite3_insert(insert_query, (botId, accessId, channelId, userId, addedBy, addedBy, now))
        return True

    def sqlite_delete_global_access(self, botId, userId):
        query = "DELETE FROM GLOBALACCESS WHERE botId = ? AND userId = ?"
        self.sqlite3_update(query, (botId, userId))

    def sqlite_delete_channel_access(self, botId, userId, channel):
        channel_id_query = "SELECT id FROM CHANNELS WHERE botId = ? AND channelName = ? COLLATE NOCASE"
        result = self.sqlite_select(channel_id_query, (botId, channel))
        if not result:
            return False
        channelId = result[0][0]
        query = "DELETE FROM CHANNELACCESS WHERE botId = ? AND userId = ? AND channelId = ?"
        self.sqlite3_update(query, (botId, userId, channelId))
        return True

    def sqlite_delete_user(self, botId, userId):
        queries = [
            "DELETE FROM USERSHOSTNAMES WHERE botId = ? AND userId = ?",
            "DELETE FROM USERSSETTINGS WHERE botId = ? AND userId = ?",
            "DELETE FROM GLOBALACCESS WHERE botId = ? AND userId = ?",
            "DELETE FROM CHANNELACCESS WHERE botId = ? AND userId = ?",
            "DELETE FROM USERLOGINS WHERE botId = ? AND userId = ?",
            "DELETE FROM USERS WHERE botId = ? AND id = ?"
        ]
        for q in queries:
            self.sqlite3_update(q, (botId, userId))

    def sqlite_get_channel_info(self, botId, channel):
        query = """
            SELECT addedBy, addedTime, status, comment
            FROM CHANNELS
            WHERE botId = ? AND channelName = ?
            COLLATE NOCASE
        """
        result = self.sqlite_select(query, (botId, channel))
        return result[0] if result else None

    def sqlite_get_user_added_time(self, botId, userId):
        query = "SELECT added FROM USERS WHERE botId = ? AND id = ?"
        result = self.sqlite_select(query, (botId, userId))
        return result[0][0] if result else None

    def sqlite_get_user_hosts(self, botId, userId):
        query = "SELECT hostname FROM USERSHOSTNAMES WHERE botId = ? AND userId = ?"
        results = self.sqlite_select(query, (botId, userId))
        return [row[0] for row in results]

    def sqlite_get_user_logins(self, botId, userId):
        query = "SELECT nick, host, timestamp FROM USERLOGINS WHERE botId = ? AND userId = ? ORDER BY timestamp DESC"
        return self.sqlite_select(query, (botId, userId))

    def sqlite_user_has_global_access(self, botId, userId):
        query = "SELECT 1 FROM GLOBALACCESS WHERE botId = ? AND userId = ? LIMIT 1"
        return bool(self.sqlite_select(query, (botId, userId)))

    def sqlite_user_has_channel_access(self, botId, userId, channel):
        query = """
            SELECT 1
            FROM CHANNELACCESS ca
            JOIN CHANNELS ch ON ca.channelId = ch.id
            WHERE ca.botId = ? AND ch.channelName = ? AND ca.userId = ?
            COLLATE NOCASE LIMIT 1
        """
        return bool(self.sqlite_select(query, (botId, channel, userId)))

    def sqlite_get_user_settings(self, botId, userId):
        query = "SELECT setting, settingValue FROM USERSSETTINGS WHERE botId = ? AND userId = ?"
        results = self.sqlite_select(query, (botId, userId))
        return {row[0]: row[1] for row in results}

    def sqlite_get_user_audit(self, botId, userId):
        query_gl = "SELECT addedBy, lastModifiedBy FROM GLOBALACCESS WHERE botId = ? AND userId = ?"
        gl = self.sqlite_select(query_gl, (botId, userId))
        if gl:
            return gl[0]

        query_ch = """
            SELECT ca.addedBy, lastModifiedBy FROM CHANNELACCESS ca
            JOIN CHANNELS ch ON ca.channelId = ch.id
            WHERE ca.botId = ? AND ca.userId = ?
            LIMIT 1
        """
        ch = self.sqlite_select(query_ch, (botId, userId))
        return ch[0] if ch else (None, None)

    def sqlite3_createTables(self):
        users_query = """ CREATE TABLE IF NOT EXISTS USERS (
                               botId INTEGER NOT NULL,
                               id INTEGER PRIMARY KEY AUTOINCREMENT,
                               username TEXT NOT NULL,
                               password TEXT,
                               added TIMESTAMP,
                               FOREIGN KEY (botId) REFERENCES BOTSETTINGS(botId) ON UPDATE NO ACTION ON DELETE CASCADE
                        ); """
        users_hostnames = """ CREATE TABLE IF NOT EXISTS USERSHOSTNAMES (
                                botId INTEGER NOT NULL,
                                userId INTEGER NOT NULL,
                                hostname TEXT,
                                FOREIGN KEY (userId) REFERENCES USERS(id) ON UPDATE NO ACTION ON DELETE CASCADE,
                                FOREIGN KEY (botId) REFERENCES BOTSETTINGS(botId) ON UPDATE NO ACTION ON DELETE CASCADE
        );"""
        users_settings = """ CREATE TABLE IF NOT EXISTS USERSSETTINGS (
                                botId INTEGER NOT NULL,
                                userId INTEGER NOT NULL,
                                setting TEXT,
                                settingValue TEXT,
                                FOREIGN KEY (userId) REFERENCES USERS(id) ON UPDATE NO ACTION ON DELETE CASCADE,
                                FOREIGN KEY (botId) REFERENCES BOTSETTINGS(botId) ON UPDATE NO ACTION ON DELETE CASCADE
        );"""
        channels_query = """ CREATE TABLE IF NOT EXISTS CHANNELS (
                                 botId INTEGER NOT NULL,
                                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                                 channelName TEXT NOT NULL,
                                 addedBy TEXT,
                                 addedTime TIMESTAMP,
                                 status INTEGER,
                                 lastChangedTime TIMESTAMP,
                                 comment TEXT,
                                 FOREIGN KEY (botId) REFERENCES BOTSETTINGS(botId) ON UPDATE NO ACTION ON DELETE CASCADE
                        ); """
        channel_settings = """ CREATE TABLE IF NOT EXISTS SETTINGS (
                                   botId INTEGER NOT NULL,
                                   channelId INTEGER NOT NULL,
                                   settingId INTEGER NOT NULL,
                                   timeSet TIMESTAMP,
                                   setBy TEXT,
                                   readOnly INTEGER,
                                   settingValue TEXT,
                                   FOREIGN KEY (channelId) REFERENCES CHANNELS(id) ON UPDATE NO ACTION ON DELETE CASCADE,
                                   FOREIGN KEY (settingId) REFERENCES VALIDSETTINGS(id) ON UPDATE NO ACTION ON DELETE CASCADE,
                                   FOREIGN KEY (botId) REFERENCES BOTSETTINGS(botId) ON UPDATE NO ACTION ON DELETE CASCADE
                        ); """
        valid_settings = """ CREATE TABLE IF NOT EXISTS VALIDSETTINGS (
                                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                                 setting TEXT NOT NULL,
                                 settingType INTEGER,
                                 description TEXT
                        ); """
        channel_access = """ CREATE TABLE IF NOT EXISTS CHANNELACCESS (
                                botId INTEGER NOT NULL,
                                accessId INTEGER NOT NULL,
                                channelId INTEGER NOT NULL,
                                userId INTEGER NOT NULL,
                                addedBy TEXT,
                                lastModifiedBy TEXT,
                                lastModified TIMESTAMP,
                                autoop INTEGER,
                                autovoice INTEGER,
                                autohalfop INTEGER,
                                FOREIGN KEY (userId) REFERENCES USERS(id) ON UPDATE NO ACTION ON DELETE CASCADE,
                                FOREIGN KEY (accessId) REFERENCES VALIDACCESS(accessId) ON UPDATE NO ACTION ON DELETE CASCADE,
                                FOREIGN KEY (channelId) REFERENCES CHANNELS(id) ON UPDATE NO ACTION ON DELETE CASCADE,
                                FOREIGN KEY (botId) REFERENCES BOTSETTINGS(botId) ON UPDATE NO ACTION ON DELETE CASCADE
                        );"""
        global_access = """ CREATE TABLE IF NOT EXISTS GLOBALACCESS (
                                botId INTEGER NOT NULL,
                                accessId INTEGER NOT NULL,
                                userId INTEGER NOT NULL,
                                addedby TEXT,
                                lastModifiedBy TEXT,
                                lastModified TIMESTAMP,
                                autoop INTEGER,
                                autovoice INTEGER,
                                autohalfop INTEGER,
                                FOREIGN KEY (userId) REFERENCES USERS(id) ON UPDATE NO ACTION ON DELETE CASCADE,
                                FOREIGN KEY (accessId) REFERENCES VALIDACCESS(accessId) ON UPDATE NO ACTION ON DELETE CASCADE,
                                FOREIGN KEY (botId) REFERENCES BOTSETTINGS(botId) ON UPDATE NO ACTION ON DELETE CASCADE
        );"""
        valid_access = """ CREATE TABLE IF NOT EXISTS VALIDACCESS (
                                accessId INTEGER PRIMARY KEY AUTOINCREMENT,
                                accessType VARCHAR(32),
                                accessFlag VARCHAR(32),
                                accessName VARCHAR(32),
                                description TEXT
        );"""
        bot_settings = """ CREATE TABLE IF NOT EXISTS BOTSETTINGS (
                                botId INTEGER PRIMARY KEY AUTOINCREMENT,
                                botUsername VARCHAR(32),
                                botNick VARCHAR(32),
                                botRealname TEXT,
                                botAway TEXT,
                                bornTime TIMESTAMP,
                                maxConnectTime INTEGER,
                                maxUptime INTEGER
        );"""

        user_logins = """
            CREATE TABLE IF NOT EXISTS USERLOGINS (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                botId INTEGER NOT NULL,
                userId INTEGER,
                nick TEXT,
                host TEXT,
                success INTEGER NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (botId) REFERENCES BOTSETTINGS(botId),
                FOREIGN KEY (userId) REFERENCES USERS(id)
            );
            """

        ignores = """
            CREATE TABLE IF NOT EXISTS IGNORES (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            botId INTEGER NOT NULL,
            host TEXT,
            added TIMESTAMP,
            expires TIMESTAMP,
            reason TEXT,
            FOREIGN KEY (botId) REFERENCES BOTSETTINGS(botId)
            )
        """

        self.sqlite3_execute(bot_settings)
        self.sqlite3_execute(valid_settings)
        self.sqlite3_execute(channels_query)
        self.sqlite3_execute(users_query)
        self.sqlite3_execute(users_settings)
        self.sqlite3_execute(users_hostnames)
        self.sqlite3_execute(channel_settings)
        self.sqlite3_execute(valid_access)
        self.sqlite3_execute(channel_access)
        self.sqlite3_execute(global_access)
        self.sqlite3_execute(user_logins)
        self.sqlite3_execute(ignores)
###
