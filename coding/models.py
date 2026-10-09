import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone

UserRef = settings.AUTH_USER_MODEL


class Profile(models.Model):
    user = models.OneToOneField(UserRef, on_delete=models.CASCADE)
    must_change_password = models.BooleanField(default=False)


class Project(models.Model):
    name = models.CharField(max_length=120)
    goal = models.TextField(blank=True)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(UserRef, on_delete=models.PROTECT)
    revision = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Membership(models.Model):
    ROLES = [('owner', '负责人'), ('reviewer', '复核者'), ('coder', '编码员'), ('viewer', '只读成员')]
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(UserRef, on_delete=models.PROTECT)
    role = models.CharField(max_length=12, choices=ROLES, default='coder')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['project', 'user'], name='unique_project_member')]


class ProjectInvitation(models.Model):
    """Invitation records store only a digest; the raw code is displayed once."""
    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name='invitation')
    code_digest = models.CharField(max_length=64, unique=True)
    enabled = models.BooleanField(default=True)
    expires_at = models.DateTimeField()
    revision = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(UserRef, on_delete=models.PROTECT)
    updated_at = models.DateTimeField(auto_now=True)


class ProjectJoinRequest(models.Model):
    STATES = [('pending', '等待审核'), ('approved', '已通过'), ('rejected', '未通过'), ('cancelled', '已撤回')]
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='join_requests')
    user = models.ForeignKey(UserRef, on_delete=models.PROTECT, related_name='project_join_requests')
    status = models.CharField(max_length=12, choices=STATES, default='pending')
    note = models.CharField(max_length=500, blank=True)
    decision_note = models.CharField(max_length=500, blank=True)
    revision = models.PositiveIntegerField(default=1)
    requested_at = models.DateTimeField(default=timezone.now)
    reviewed_by = models.ForeignKey(UserRef, null=True, blank=True, on_delete=models.PROTECT,
                                   related_name='reviewed_join_requests')
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['project', 'user'], name='unique_project_join_request')]
        ordering = ['-requested_at', '-id']


class Codebook(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='codebooks')
    version = models.PositiveIntegerField()
    title = models.CharField(max_length=120, default='初始编码本')
    policy = models.CharField(max_length=10, choices=[('single', '单标签'), ('multi', '主代码＋可选次代码')], default='multi')
    revision = models.PositiveIntegerField(default=0)
    frozen = models.BooleanField(default=False)
    change_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['project', 'version'], name='unique_book_version')]
        ordering = ['-version']


class Code(models.Model):
    book = models.ForeignKey(Codebook, on_delete=models.CASCADE, related_name='codes')
    key = models.CharField(max_length=40)
    name = models.CharField(max_length=100)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT)
    definition = models.TextField()
    include_when = models.TextField(blank=True)
    exclude_when = models.TextField(blank=True)
    positive_example = models.TextField(blank=True)
    negative_example = models.TextField(blank=True)
    boundary_example = models.TextField(blank=True)
    coexist_priority = models.TextField(blank=True)
    color = models.CharField(max_length=7, default='#4372e8')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['book', 'key'], name='unique_code_key_in_book')]
        ordering = ['key']

    def __str__(self):
        return f'{self.key} · {self.name}'


class CodebookImportDraft(models.Model):
    """Private, bounded upload; confirmation is replay-safe and never replaces old rules."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    book = models.ForeignKey(Codebook, on_delete=models.PROTECT, related_name='import_drafts')
    user = models.ForeignKey(UserRef, on_delete=models.PROTECT)
    filename = models.CharField(max_length=200)
    raw = models.BinaryField()
    preview = models.JSONField(default=dict)
    applied_book = models.ForeignKey(Codebook, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name='import_sources')
    created_at = models.DateTimeField(auto_now_add=True)


class UploadDraft(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    user = models.ForeignKey(UserRef, on_delete=models.CASCADE)
    filename = models.CharField(max_length=200)
    raw = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)


class SourceDocument(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='documents')
    name = models.CharField(max_length=200)
    sha256 = models.CharField(max_length=64)
    raw = models.BinaryField()
    text_column = models.CharField(max_length=200)
    context_column = models.CharField(max_length=200, blank=True)
    import_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['project', 'sha256'], name='no_duplicate_document')]


class SourceRecord(models.Model):
    document = models.ForeignKey(SourceDocument, on_delete=models.CASCADE, related_name='records')
    position = models.PositiveIntegerField()
    text = models.TextField()
    metadata = models.JSONField(default=dict)
    context_key = models.CharField(max_length=500, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['document', 'position'], name='unique_record_position')]
        ordering = ['position']


class Unit(models.Model):
    record = models.ForeignKey(SourceRecord, on_delete=models.PROTECT, related_name='units')
    start = models.PositiveIntegerField(default=0)
    end = models.PositiveIntegerField()
    text = models.TextField()
    active = models.BooleanField(default=True)
    note = models.TextField(blank=True)
    revision = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['record__document_id', 'record__position', 'start', 'id']


class Round(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='rounds')
    book = models.ForeignKey(Codebook, on_delete=models.PROTECT)
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=10, choices=[('training', '培训练习'), ('pilot', '独立试编码'), ('formal', '正式编码')])
    state = models.CharField(max_length=10, choices=[('active', '编码中'), ('review', '协商中'), ('closed', '已归档')], default='active')
    sampling = models.JSONField(default=dict)
    cycle = models.PositiveIntegerField(default=1)
    revision = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)


class Assignment(models.Model):
    round = models.ForeignKey(Round, on_delete=models.CASCADE, related_name='assignments')
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT)
    coder = models.ForeignKey(UserRef, on_delete=models.PROTECT)
    primary = models.ForeignKey(Code, null=True, blank=True, on_delete=models.PROTECT)
    secondary = models.JSONField(default=list)
    uncertain = models.BooleanField(default=False)
    no_code = models.BooleanField(default=False)
    note = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=[('draft', '草稿'), ('submitted', '已提交')], default='draft')
    revision = models.PositiveIntegerField(default=0)
    last_token = models.CharField(max_length=64, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['round', 'unit', 'coder'], name='unique_independent_assignment')]


class Decision(models.Model):
    round = models.ForeignKey(Round, on_delete=models.CASCADE, related_name='decisions')
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT)
    primary = models.ForeignKey(Code, null=True, blank=True, on_delete=models.PROTECT)
    secondary = models.JSONField(default=list)
    no_code = models.BooleanField(default=False)
    uncertain = models.BooleanField(default=False)
    note = models.TextField()
    author = models.ForeignKey(UserRef, on_delete=models.PROTECT)
    revision = models.PositiveIntegerField(default=1)
    cycle = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['round', 'unit'], name='unique_final_decision')]


class Comment(models.Model):
    round = models.ForeignKey(Round, on_delete=models.CASCADE)
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT)
    author = models.ForeignKey(UserRef, on_delete=models.PROTECT)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)


class AuditEvent(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    actor = models.ForeignKey(UserRef, on_delete=models.PROTECT)
    action = models.CharField(max_length=80)
    object_type = models.CharField(max_length=40)
    object_id = models.CharField(max_length=80)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']


class ReliabilityReport(models.Model):
    round = models.ForeignKey(Round, on_delete=models.CASCADE, related_name='reports')
    cycle = models.PositiveIntegerField()
    data = models.JSONField()
    author = models.ForeignKey(UserRef, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
