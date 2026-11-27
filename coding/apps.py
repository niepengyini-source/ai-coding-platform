from django.apps import AppConfig
from django.db.backends.signals import connection_created
from django.forms import BaseForm


def configure_sqlite(sender, connection, **kwargs):
    if connection.vendor == 'sqlite' and not str(connection.settings_dict['NAME']).startswith('file:memory'):
        with connection.cursor() as cursor:
            cursor.execute('PRAGMA journal_mode=WAL')
            cursor.execute('PRAGMA synchronous=FULL')


def configure_form_labels():
    if getattr(BaseForm, '_platform_clean_label_suffix', False):
        return
    original_init = BaseForm.__init__

    def init_without_colon(self, *args, **kwargs):
        explicit = (len(args) > 6 and args[6] is not None) or (kwargs.get('label_suffix') is not None)
        original_init(self, *args, **kwargs)
        if not explicit:
            self.label_suffix = ''

    BaseForm.__init__ = init_without_colon
    BaseForm._platform_clean_label_suffix = True


class CodingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'coding'

    def ready(self):
        connection_created.connect(configure_sqlite, dispatch_uid='platform-sqlite-wal')
        configure_form_labels()
