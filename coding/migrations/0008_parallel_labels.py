from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('coding', '0007_material_documents')]
    operations = [
        migrations.AlterField(model_name='codebook', name='policy', field=models.CharField(
            choices=[('single', '单标签'), ('multi', '主代码＋可选次代码'), ('parallel', '多个并列代码')],
            default='multi', max_length=10)),
        migrations.AddField(model_name='assignment', name='labels', field=models.JSONField(default=list)),
        migrations.AddField(model_name='decision', name='labels', field=models.JSONField(default=list)),
    ]
