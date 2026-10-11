import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('coding', '0004_quick_start_history')]
    operations = [
        migrations.RemoveConstraint(model_name='sourcedocument', name='no_duplicate_document'),
        migrations.AddField(model_name='sourcedocument', name='sheet_name',
                            field=models.CharField(blank=True, max_length=200)),
        migrations.AddConstraint(model_name='sourcedocument',
            constraint=models.UniqueConstraint(fields=('project', 'sha256', 'sheet_name'), name='no_duplicate_document')),
        migrations.AddField(model_name='assignment', name='parent',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='segments', to='coding.assignment')),
        migrations.AddField(model_name='assignment', name='superseded', field=models.BooleanField(default=False)),
    ]
