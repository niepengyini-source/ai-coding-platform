from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('coding', '0009_source_coding')]

    operations = [
        migrations.AddField(model_name='sourceannotation', name='region', field=models.JSONField(blank=True, null=True)),
        migrations.RemoveConstraint(model_name='sourceannotation', name='unique_source_annotation_range'),
        migrations.AddConstraint(model_name='sourceannotation', constraint=models.UniqueConstraint(
            fields=['task', 'start', 'end'], condition=models.Q(region__isnull=True), name='unique_source_annotation_range')),
        migrations.AddConstraint(model_name='sourceannotation', constraint=models.UniqueConstraint(
            fields=['task', 'region'], condition=models.Q(region__isnull=False), name='unique_source_annotation_region')),
    ]
