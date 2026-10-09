import sqlite3
from pathlib import Path
from datetime import datetime
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = '使用SQLite在线备份API生成一致的服务器备份；PostgreSQL请使用pg_dump。'

    def add_arguments(self, parser):
        parser.add_argument('--output-dir', default=str(settings.BASE_DIR / 'backups'))

    def handle(self, *args, **options):
        db = settings.DATABASES['default']
        if db['ENGINE'] != 'django.db.backends.sqlite3':
            raise CommandError('PostgreSQL请使用pg_dump并在测试环境恢复验证，不能复制正在运行的数据文件。')
        directory = Path(options['output_dir']).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / ('platform_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.sqlite3')
        source = sqlite3.connect(f'file:{Path(db["NAME"]).as_posix()}?mode=ro', uri=True)
        dest = sqlite3.connect(target)
        try:
            source.backup(dest)
            result = dest.execute('PRAGMA integrity_check').fetchone()[0]
            if result != 'ok':
                raise CommandError('备份完整性检查失败：' + result)
        finally:
            source.close()
            dest.close()
        self.stdout.write(f'备份完成并通过完整性检查：{target}')
