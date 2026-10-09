from django.apps import AppConfig
from django.db.backends.signals import connection_created


def configure_sqlite(sender, connection, **kwargs):
    if connection.vendor == 'sqlite' and not str(connection.settings_dict['NAME']).startswith('file:memory'):
        with connection.cursor() as cursor:
            cursor.execute('PRAGMA journal_mode=WAL')
            cursor.execute('PRAGMA synchronous=FULL')


class CodingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'coding'

    def ready(self):
        connection_created.connect(configure_sqlite, dispatch_uid='platform-sqlite-wal')
