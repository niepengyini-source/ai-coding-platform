"""Optional source annotations, separate from preassigned coding units."""
from django.conf import settings
from django.db import models


class SourceCodingTask(models.Model):
    round = models.ForeignKey('Round', on_delete=models.CASCADE, related_name='source_tasks')
    document = models.ForeignKey('SourceDocument', on_delete=models.PROTECT, related_name='coding_tasks')
    coder = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    completed = models.BooleanField(default=False)
    revision = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['document_id', 'id']
        constraints = [models.UniqueConstraint(fields=['round', 'document', 'coder'], name='unique_source_coding_task')]


class SourceAnnotation(models.Model):
    task = models.ForeignKey(SourceCodingTask, on_delete=models.CASCADE, related_name='annotations')
    start = models.PositiveIntegerField()
    end = models.PositiveIntegerField()
    text = models.TextField()
    region = models.JSONField(null=True, blank=True)
    primary = models.ForeignKey('Code', null=True, blank=True, on_delete=models.PROTECT)
    secondary = models.JSONField(default=list)
    labels = models.JSONField(default=list)
    uncertain = models.BooleanField(default=False)
    no_code = models.BooleanField(default=False)
    note = models.TextField(blank=True)
    revision = models.PositiveIntegerField(default=0)
    last_token = models.CharField(max_length=64, blank=True)
    book_snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['start', 'end', 'id']
        constraints = [
            models.UniqueConstraint(fields=['task', 'start', 'end'], condition=models.Q(region__isnull=True),
                                    name='unique_source_annotation_range'),
            models.UniqueConstraint(fields=['task', 'region'], condition=models.Q(region__isnull=False),
                                    name='unique_source_annotation_region'),
        ]
