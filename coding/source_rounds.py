"""Team whole-document source coding rounds: assignment, blind vs sync isolation, and multi-coder review."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import OperationalError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .models import Code, Membership, Project, Round
from .services import audit, clone_book
from .source_coding import canonical_source, coding_documents
from .source_models import SourceAnnotation, SourceCodingTask
from .views import access, review_access, round_access


class DocumentRoundForm(forms.Form):
    name = forms.CharField(label='任务名称', max_length=120)
    kind = forms.ChoiceField(
        label='工作阶段',
        choices=[(k, v) for k, v in Round._meta.get_field('kind').choices if k != 'quick'],
        initial='pilot',
    )
    collab_mode = forms.ChoiceField(
        label='协作与同步模式',
        choices=[
            ('independent', '各自独立编码（默认：互不可见他人划选片段与自建代码，完成后再汇总对比）'),
            ('sync', '同步协作编码（实时共享同一套代码，可按需开启实时查看同伴片段）'),
        ],
        initial='independent',
    )
    share_annotations = forms.BooleanField(
        label='同步模式下，同时实时显示其他成员已划选的片段（同伴片段只读，本人片段可改）',
        required=False,
        initial=True,
    )
    allow_coder_codes = forms.BooleanField(
        label='允许参与成员在阅读原文时随时新建代码',
        required=False,
        initial=True,
    )
    distribution = forms.ChoiceField(
        label='材料分配方式',
        choices=[
            ('all', '共同编码所选材料（每位成员都获得全部所选整篇文档，适合对比一致性）'),
            ('distributed', '按篇分工（将多份整篇文档轮流分给不同成员）'),
        ],
        initial='all',
    )
    coder_ids = forms.TypedMultipleChoiceField(label='参与成员', coerce=int, widget=forms.CheckboxSelectMultiple)
    document_ids = forms.TypedMultipleChoiceField(label='选择整篇材料', coerce=int, widget=forms.CheckboxSelectMultiple)

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project
        self.fields['coder_ids'].choices = [
            (m.user_id, f'{m.user.username}（{m.get_role_display()}）')
            for m in project.memberships.exclude(role='viewer').select_related('user').filter(user__is_active=True)
        ]
        docs = list(coding_documents(project))
        self.fields['document_ids'].choices = [(d.pk, d.name) for d in docs]

    def clean(self):
        data = super().clean()
        coders = list(dict.fromkeys(data.get('coder_ids') or []))
        docs = list(dict.fromkeys(data.get('document_ids') or []))
        if not coders:
            self.add_error('coder_ids', '请至少勾选一位参与编码的成员。')
        if not docs:
            self.add_error('document_ids', '请至少勾选一份已导入材料。')
        data['coder_ids'] = coders
        data['document_ids'] = docs
        return data


@transaction.atomic
def create_document_round(project, actor, cleaned):
    project = Project.objects.select_for_update().get(pk=project.pk, trashed_at__isnull=True)
    role = Membership.objects.filter(project=project, user=actor, user__is_active=True).values_list('role', flat=True).first()
    if role not in ('owner', 'reviewer'):
        raise PermissionDenied('只有负责人或复核者可以安排团队任务。')
    book = project.codebooks.first()
    if not book:
        raise ValueError('项目缺少初始编码本，请先恢复或创建编码本。')
    valid_docs = list(coding_documents(project).filter(pk__in=cleaned['document_ids']))
    if len(valid_docs) != len(cleaned['document_ids']):
        raise ValueError('所选材料包含不可编码或不属于本项目的文件，请刷新后重选。')
    valid_members = list(Membership.objects.filter(
        project=project, user_id__in=cleaned['coder_ids'], user__is_active=True
    ).exclude(role='viewer').select_related('user'))
    if len(valid_members) != len(cleaned['coder_ids']):
        raise ValueError('所选成员状态已变化，请刷新页面后重新勾选。')
    ordered_users = [next(m.user for m in valid_members if m.user_id == uid) for uid in cleaned['coder_ids']]
    collab_mode = cleaned['collab_mode']
    if collab_mode == 'sync' and book.frozen:
        book = clone_book(project, actor, book, book.title, book.policy, '同步整篇编码使用可编辑规则副本。')
    share_annotations = bool(collab_mode == 'sync' and cleaned.get('share_annotations'))
    allow_coder_codes = bool(cleaned.get('allow_coder_codes'))
    round_obj = Round.objects.create(
        project=project,
        book=book,
        name=cleaned['name'].strip(),
        kind=cleaned['kind'],
        started_by=actor,
        sampling={
            'source_coding': True,
            'team_source_round': True,
            'open_coding': allow_coder_codes,
            'allow_coder_codes': allow_coder_codes,
            'collab_mode': collab_mode,
            'share_annotations': share_annotations,
            'distribution': cleaned['distribution'],
            'document_ids': [d.pk for d in valid_docs],
        },
    )
    tasks_to_create = []
    if cleaned['distribution'] == 'distributed':
        for idx, doc in enumerate(valid_docs):
            coder = ordered_users[idx % len(ordered_users)]
            tasks_to_create.append(SourceCodingTask(round=round_obj, document=doc, coder=coder))
    else:
        for doc in valid_docs:
            for coder in ordered_users:
                tasks_to_create.append(SourceCodingTask(round=round_obj, document=doc, coder=coder))
    SourceCodingTask.objects.bulk_create(tasks_to_create)
    audit(project, actor, 'round.create_source', round_obj, after={
        'collab_mode': collab_mode,
        'share_annotations': share_annotations,
        'documents': [d.pk for d in valid_docs],
        'coders': [u.pk for u in ordered_users],
        'task_count': len(tasks_to_create),
    })
    return round_obj


@login_required
def source_review_view(request, round_id):
    round_obj, role = round_access(request, round_id)
    if not round_obj.sampling.get('source_coding'):
        raise Http404
    is_sync_shared = (round_obj.sampling.get('collab_mode') == 'sync'
                      and round_obj.sampling.get('share_annotations'))
    if not is_sync_shared:
        review_access(request, round_obj, role)
    tasks = list(round_obj.source_tasks.select_related('document', 'coder').order_by('document_id', 'coder__username'))
    documents = []
    seen_docs = set()
    for t in tasks:
        if t.document_id not in seen_docs:
            seen_docs.add(t.document_id)
            documents.append(t.document)
    selected_doc_id = request.GET.get('document', '')
    selected_doc = (next((d for d in documents if str(d.pk) == str(selected_doc_id)), None)
                    or (documents[0] if documents else None))
    member_stats = []
    by_coder = {}
    for t in tasks:
        stat = by_coder.setdefault(t.coder_id, {
            'username': t.coder.username, 'done': 0, 'total': 0, 'annotations': 0
        })
        stat['total'] += 1
        if t.completed:
            stat['done'] += 1
    ann_counts = {}
    for row in SourceAnnotation.objects.filter(task__round=round_obj).values_list('task__coder_id', flat=True):
        ann_counts[row] = ann_counts.get(row, 0) + 1
    for cid, stat in by_coder.items():
        stat['annotations'] = ann_counts.get(cid, 0)
        member_stats.append(stat)

    doc_annotations = []
    private_codes = []
    if selected_doc:
        _, blocks = canonical_source(selected_doc) if selected_doc.normalized.get('type') != 'image' else ('', [])
        entries = list(SourceAnnotation.objects.filter(task__round=round_obj, task__document=selected_doc)
                       .select_related('task__coder', 'primary').order_by('start', 'end', 'task__coder__username', 'id'))
        for entry in entries:
            pages = sorted({b['page'] for b in blocks if b.get('page') and b['end'] > entry.start and b['start'] < entry.end})
            codes = [c.get('name') or c.get('key', '') for c in entry.book_snapshot.get('codes', [])]
            doc_annotations.append({
                'id': entry.pk,
                'coder': entry.task.coder.username,
                'location': f"第 {'、'.join(map(str, pages))} 页" if pages else (
                    '图片区域' if entry.region else f'字符 {entry.start}—{entry.end}'
                ),
                'text': entry.text or '（图片框选区域）',
                'codes': codes,
                'no_code': entry.no_code,
                'uncertain': entry.uncertain,
                'note': entry.note,
                'updated_at': entry.updated_at,
            })
    if role in ('owner', 'reviewer'):
        private_codes = list(round_obj.book.codes.filter(private_coder__isnull=False).select_related('private_coder'))

    all_done = bool(tasks) and all(t.completed for t in tasks)
    return render(request, 'coding/source_review.html', {
        'project': round_obj.project,
        'role': role,
        'round': round_obj,
        'documents': documents,
        'selected_document': selected_doc,
        'member_stats': member_stats,
        'doc_annotations': doc_annotations,
        'private_codes': private_codes,
        'all_done': all_done,
        'can_manage_round': role in ('owner', 'reviewer') and round_obj.kind != 'quick',
    })


@require_POST
@login_required
def promote_private_code_view(request, round_id, code_id):
    round_obj, _ = round_access(request, round_id, ['owner', 'reviewer'])
    if round_obj.state == 'active' and round_obj.kind != 'training' and round_obj.sampling.get('collab_mode') != 'sync':
        raise PermissionDenied('请先进入协商阶段后再汇总采纳成员独立代码。')
    try:
        with transaction.atomic():
            Project.objects.select_for_update().get(pk=round_obj.project_id)
            code = get_object_or_404(Code.objects.select_for_update(), pk=code_id, book=round_obj.book)
            if code.private_coder_id is not None:
                clash = round_obj.book.codes.filter(private_coder__isnull=True, name__iexact=code.name).exclude(pk=code.pk).first()
                if clash:
                    raise ValueError(f'正式编码本中已有同名代码「{clash.name}」（{clash.key}），无需重复采纳。')
                prev_coder = code.private_coder_id
                code.private_coder = None
                code.save(update_fields=['private_coder'])
                round_obj.book.revision += 1
                round_obj.book.save(update_fields=['revision'])
                audit(round_obj.project, request.user, 'code.promote_shared', code,
                      before={'private_coder': prev_coder}, after={'private_coder': None, 'name': code.name})
                messages.success(request, f'已将代码「{code.key} · {code.name}」采纳进项目共享编码本。')
    except (ValueError, OperationalError) as error:
        messages.error(request, '数据库忙，请稍后重试。' if isinstance(error, OperationalError) else str(error))
    return redirect('disagreements', round_obj.pk)
