from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('coding', '0005_simplified_workbench')]
    operations = [migrations.AddField(model_name='sourcedocument', name='display_mapping',
                                    field=models.JSONField(blank=True, default=dict))]
