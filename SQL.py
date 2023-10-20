import psutil

## SQLite stuff

##
# check method for connection availability

def sqlite3_connection_status():
    global sqlite3_database
    for proc in psutil.process_iter():
        try:
            files = proc.get_open_files()
            if files:
                for _file in files:
                    if _file.path == sqlite3_database:
                        return True
        except psutil.NoSuchProcess as err:
            print(err)
    return False


##
# execute method
def sqlite3_execute(self, sql):
        cursor = self.connection.cursor()
        out = cursor.execute(sql)
        self.connection.commit()
        return out

##
# select method
def sqlite_select(self, query):
		cursor = self.handle.cursor()
		cursor.execute(query)
		return cursor.fetchall()

##
# create tables
def sqlite3_createTables(self):
        ## Create Users Table
        users_query = """ CREATE TABLE IF NOT EXISTS USERS (
                           id INT AUTO_INCREMENT PRIMARY KEY NOT NULL,
                           username VARCHAR(255) NOT NULL,
                           hostnames VARCHAR(999) not NULL,
                           password VARCHAR(999),
                           email VARCHAR(255),
                           greet VARCHAR(255),
                           lastseen INT,
                           lastSeenOn VARCHAR(255)
                    ); """
        channels_query = """ CREATE TABLE IF NOT EXISTS CHANNELS (
                             id INT AUTO_INCREMENT PRIMARY KEY NOT NULL,
                             channelName VARCHAR(999) NOT NULL,
                    ); """
        channel_settings = """ CREATE TABLE IF NOT EXISTS SETTINGS (
                               channelId INT,
                               settingId INT,
                               timeSet DATETIME,
                               userId INT,
                               readOnly INT,
                               INDEX `id_channelId_FK` (`channelId`), CONSTRAINT `id_channelId_FK` FOREIGN KEY (`channelId`) REFERENCES `CHANNELS` (`id`) ON UPDATE NO ACTION ON DELETE CASCADE,
                               INDEX `id_settingId_FK` (`settingId`), CONSTRAINT `id_settingId_FK` FOREIGN KEY (`settingId`) REFERENCES `VALIDSETTINGS` (`id`) ON UPDATE NO ACTION ON DELETE CASCADE,
                               INDEX `id_userId_FK` (`id_userId_FK`), CONSTRAINT `id_userId_FK` FOREIGN KEY (`userId`) REFERENCES `USERS` (`id`) ON UPDATE NO ACTION ON DELETE NO ACTION
                    ); """
        valid_settings = """ CREATE TABLE IF NOT EXISTS VALIDSETTINGS (
                             id INT AUTO_INCREMENT PRIMARY KEY NOT NULL,
                             setting VARCHAR(255) NOT NULL,
                             description VARCHAR(255)
                    ); """
        sqlite3_execute(self, valid_settings)
        sqlite3_execute(self, channel_settings)
        sqlite3_execute(self, users_query)
        sqlite3_execute(self, channels_query)

###
