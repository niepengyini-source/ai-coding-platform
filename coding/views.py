import json
import uuid
from pathlib import Path
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import PasswordChangeView
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction, OperationalError
from django.db.models import F, OuterRef, Subquery
from django.http import JsonResponse, HttpResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from .models import *
from .forms import ProjectForm, MemberForm, BookForm, CodeForm, SourceDisplayForm
from .importing import parse_upload, material_sheets, suggested_columns, MAX_BYTES, MAX_DOCUMENT_BYTES
from .material_normalization import normalize_material, DOCUMENT_FORMATS
from .document_reading import reading_context
from .services import (Conflict, audit, annotation_data, import_records, clone_book, create_round,
                       save_assignment, save_decision, export_annotations, export_final, export_disagreements)
from .reliability import report_for
from .reviewing import disagreement_rows
from .account_forms import InvitationForm, JoinReviewForm
from .group_services import mark_approved
from .workflow import visible_rounds, coding_assignments, next_coding_url, history_permissions
from .splitting import split_personal, suggest_split, split_restore_options
from .context import context_page
from .source_information import (attach_source_information, available_headers, display_mapping,
                                 mapping_token)
from .book_updates import begin_update, apply_book_update
from .code_references import my_code_references
from .ai_recommendations import workbench_context


def access(request, project_id, roles=None):
    m = get_object_or_404(Membership.objects.select_related('project'),
                          project_id=project_id, user=request.user, project__trashed_at__isnull=True)
    if roles and m.role not in roles:
        raise PermissionDenied
    return m.project, m.role


def round_access(request, round_id, roles=None):
    r = get_object_or_404(Round.objects.select_related('project', 'book'), pk=round_id)
    p, role = access(request, r.project_id, roles)
    if r.kind == 'quick' and r.started_by_id != request.user.pk and role not in ('owner', 'reviewer'):
        raise Http404
    return r, role


def review_access(request, r, role):
    if r.state == 'active' and r.kind != 'training':
        raise PermissionDenied('独立编码尚未锁定。')
    if role == 'viewer' and r.state != 'closed':
        raise PermissionDenied


def api_error(error):
    if isinstance(error, OperationalError):
        return JsonResponse({'error': '数据库正在忙，未确认保存成功。输入仍保留，请稍后使用同一保存请求重试。'}, status=503)
    return JsonResponse({'error': str(error)}, status=409 if isinstance(error, Conflict) else 400)


def body_json(request):
    if request.content_type != 'application/json':
        raise ValueError('需要JSON格式。')
    try:
        value = json.loads(request.body)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, UnicodeError):
        raise ValueError('无法读取输入内容。')


def object_id(value):
    try:
        result = int(value)
        if isinstance(value, bool) or result < 1:
            raise ValueError
        return result
    except (ValueError, TypeError):
        raise Http404


def context_records(record):
    qs = SourceRecord.objects.filter(document=record.document, context_key=record.context_key)
    previous = list(qs.filter(position__lt=record.position).order_by('-position')[:8])
    following = list(qs.filter(position__gt=record.position).order_by('position')[:8])
    return list(reversed(previous)) + [record] + following


class PlatformPasswordChangeView(PasswordChangeView):
    template_name = 'registration/password_change_form.html'
    success_url = '/accounts/password/done/'

    def form_valid(self, form):
        response = super().form_valid(form)
        Profile.objects.filter(user=self.request.user).update(must_change_password=False)
        return response


@login_required
def home(request):
    memberships = request.user.membership_set.select_related('project').order_by('-project__created_at')
    applications = Paginator(request.user.project_join_requests.select_related('project'), 10).get_page(request.GET.get('requests_page'))
    return render(request, 'coding/home.html', {'memberships': memberships, 'applications': applications})


@login_required
def project_new(request):
    form = ProjectForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            project = form.save(commit=False)
            project.owner = request.user
            project.save()
            Membership.objects.create(project=project, user=request.user, role='owner')
            Codebook.objects.create(project=project, version=1)
            audit(project, request.user, 'project.create', project, after={'name': project.name, 'goal': project.goal})
        return redirect('project', project.id)
    return render(request, 'coding/form.html', {'form': form, 'title': '创建研究项目', 'button': '创建项目'})


@login_required
def project_view(request, project_id):
    project, role = access(request, project_id)
    form = ProjectForm(request.POST or None, instance=project)
    if request.method == 'POST':
        if role != 'owner':
            raise PermissionDenied
        if form.is_valid():
            with transaction.atomic():
                locked = Project.objects.select_for_update().get(pk=project.id)
                if request.POST.get('revision') != str(locked.revision):
                    form.add_error(None, '他人已修改项目说明，输入保留，请刷新核对。')
                else:
                    before = {'name': locked.name, 'goal': locked.goal, 'description': locked.description}
                    for key in form.Meta.fields:
                        setattr(locked, key, form.cleaned_data[key])
                    locked.revision += 1
                    locked.save()
                    audit(project, request.user, 'project.edit', locked, before=before, after={k: getattr(locked, k) for k in form.Meta.fields})
                    messages.success(request, '项目说明已保存。')
                    return redirect('project', project.id)
    return render(request, 'coding/project.html', {'project': project, 'role': role, 'form': form,
        'unit_count': Unit.objects.filter(record__document__project=project, active=True).count(),
        'rounds': visible_rounds(project, request.user, role).select_related('book', 'started_by').order_by('-id')})


@login_required
def members(request, project_id):
    project, role = access(request, project_id, ['owner'])
    form = MemberForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            Project.objects.select_for_update().get(pk=project.id)
            user = get_user_model().objects.filter(username=data['username']).first()
            if not user:
                user = get_user_model().objects.create_user(data['username'], password=data['password'])
                Profile.objects.create(user=user, must_change_password=True)
            if user == project.owner:
                form.add_error('username', '不能改变项目负责人的权限。')
            elif Assignment.objects.filter(round__project=project, round__state='active', coder=user).exists():
                form.add_error('username', '该成员正在编码，请锁定相关轮次后再调整权限。')
            else:
                member, created = Membership.objects.get_or_create(project=project, user=user, defaults={'role': data['role']})
                old_role = member.role
                member.role = data['role']
                member.save()
                application = ProjectJoinRequest.objects.filter(project=project, user=user, status='pending').first()
                if application:
                    mark_approved(application, request.user, '负责人直接添加为项目成员。')
                audit(project, request.user, 'membership.set', member, before={'role': old_role}, after={'username': user.username, 'role': member.role})
                messages.success(request, '成员已保存；新账号首次登录需修改初始密码。')
                return redirect('members', project.id)
    invitation = ProjectInvitation.objects.filter(project=project).first()
    new_invitation = request.session.pop('new_invitation_' + str(project.id), None)
    new_code = new_invitation['code'] if (new_invitation and invitation and invitation.enabled
        and invitation.expires_at > timezone.now() and new_invitation['revision'] == invitation.revision) else None
    requests_page = Paginator(project.join_requests.filter(status='pending').select_related('user'), 20).get_page(request.GET.get('join_page'))
    for application in requests_page:
        application.review_form = JoinReviewForm(auto_id=f'request-{application.id}-%s',
            initial={'revision': application.revision, 'role': 'coder'})
    from .collaborator_invites import historical_collaborators, pending_direct_invites
    return render(request, 'coding/members.html', {'project': project, 'role': role, 'form': form,
        'members': project.memberships.select_related('user'), 'invitation': invitation, 'new_code': new_code,
        'invite_form': InvitationForm(initial={'revision': invitation.revision if invitation else 0, 'days': 7}),
        'now': timezone.now(), 'requests_page': requests_page,
        'historical_members': historical_collaborators(project, request.user),
        'sent_direct_invites': pending_direct_invites(project)})


@login_required
def materials(request, project_id):
    project, role = access(request, project_id, ['owner', 'reviewer'])
    error = None
    if request.method == 'POST':
        try:
            if request.POST.get('draft_id'):
                try:
                    draft_id = uuid.UUID(request.POST['draft_id'])
                except ValueError:
                    raise Http404
                draft = get_object_or_404(UploadDraft, pk=draft_id, project=project, user=request.user)
            else:
                upload = request.FILES.get('file')
                if not upload:
                    raise ValueError('请选择文件。')
                document_upload = Path(upload.name).suffix.lower() in DOCUMENT_FORMATS
                if upload.size > (MAX_DOCUMENT_BYTES if document_upload else MAX_BYTES):
                    raise ValueError('PDF、Word和图片最大80MB，表格和文本最大8MB。')
                raw = upload.read()
                # Validate the file before retaining it; an empty Excel cover can be changed below.
                sheets = material_sheets(upload.name, raw)
                if not sheets and not document_upload:
                    parse_upload(upload.name, raw)
                draft = UploadDraft.objects.create(project=project, user=request.user, filename=Path(upload.name).name[:200], raw=raw)
            sheets = material_sheets(draft.filename, bytes(draft.raw))
            sheet = request.POST.get('sheet_name', '') or (sheets[0] if sheets else '')
            if sheet and sheet not in sheets:
                raise ValueError('所选工作表不存在，请重新选择。')
            preview_error = None
            normalized = None
            try:
                if Path(draft.filename).suffix.lower() in DOCUMENT_FORMATS:
                    normalized = normalize_material(draft.filename, bytes(draft.raw))
                    headers = ['text', 'source_document_id']
                    rows = [{'text': normalized['text'], 'source_document_id': normalized['source']['sha256']}]
                else:
                    headers, rows = parse_upload(draft.filename, bytes(draft.raw), sheet_name=sheet)
            except ValueError as exc:
                if not sheets or (request.POST.get('draft_id') and request.POST.get('action') != 'preview'):
                    raise
                headers, rows, preview_error = [], [], str(exc)
            if request.POST.get('draft_id') and request.POST.get('action') != 'preview':
                text_col, context_col = request.POST.get('text_column', ''), request.POST.get('context_column', '')
                try:
                    if normalized and normalized.get('pages_to_review') and request.POST.get('acknowledge_pdf_pages') != 'yes':
                        raise ValueError('请确认 PDF 中需要核对的页面后再导入。')
                    if text_col not in headers or (context_col and (context_col not in headers or context_col == text_col)):
                        raise ValueError('请选择有效且不同的文本列和上下文分组列。')
                    display_form = SourceDisplayForm(request.POST, headers=headers, prefix='source')
                    if not display_form.is_valid():
                        raise ValueError('显示字段包含无效列，请重新选择。')
                    display_values = display_form.cleaned_data if any(key.startswith('source-') for key in request.POST) else None
                    imported = import_records(project, request.user, draft, rows, text_col, context_col, sheet,
                                              display_mapping=display_values, normalized=normalized)
                    if normalized:
                        if normalized['source']['format'] == 'pdf' and not normalized['text']:
                            messages.success(request, 'PDF 原件已保留。请先完成 OCR 并校对，再导入可编码的文字版本。')
                            return redirect('materials', project.id)
                        messages.success(request, '文档已导入，原件已保留，可以开始编码。')
                        return redirect(reverse('source_start', args=[project.id]) + f'?document={imported.id}')
                    counts = imported.import_counts
                    messages.success(request, f'材料已导入：新增 {counts["units"]} 个编码单元，跳过 {counts["skipped"]} 条完全重复记录。原有材料和编码保持不变。')
                    return redirect('units', project.id)
                except (ValueError, OperationalError) as exc:
                    preview_error = str(exc) if not isinstance(exc, OperationalError) else '数据库忙，未确认导入成功，请稍后重试。'
            default, context = suggested_columns(headers)
            if request.POST.get('draft_id') and request.POST.get('action') != 'preview':
                default = text_col if text_col in headers else ''
                context = context_col if context_col in headers else ''
            return render(request, 'coding/import_preview.html', {'project': project, 'role': role,
                'draft': draft, 'headers': headers, 'preview': [[row.get(h, '')[:200] for h in headers] for row in rows[:5]],
                'row_count': len(rows), 'default_text': default, 'default_context': context,
                'sheet_names': sheets, 'sheet_name': sheet, 'error': preview_error,
                'document_preview': normalized,
                'display_form': SourceDisplayForm(request.POST if request.POST.get('draft_id') and
                    request.POST.get('action') != 'preview' and any(key.startswith('source-') for key in request.POST)
                    else None, headers=headers, prefix='source')})
        except (ValueError, TypeError, OperationalError) as exc:
            error = str(exc) if not isinstance(exc, OperationalError) else '数据库忙，请稍后重试；没有确认导入成功。'
    samples = SourceRecord.objects.filter(document_id=OuterRef('pk')).order_by('position').values('metadata')[:1]
    documents = list(project.documents.annotate(display_sample=Subquery(samples)))
    for document in documents:
        headers = [document.text_column, *(document.display_sample or {})]
        document.display_form = SourceDisplayForm(headers=headers,
            mapping=display_mapping(document, headers), prefix=f'source-{document.pk}')
        document.mapping_token = mapping_token(document)
    from .source_coding import coding_documents
    return render(request, 'coding/materials.html', {'project': project, 'role': role, 'error': error,
                  'documents': documents, 'has_coding_documents': coding_documents(project).exists()})


@login_required
@require_POST
def source_display(request, project_id, document_id):
    project, _ = access(request, project_id, ['owner', 'reviewer'])
    document = get_object_or_404(SourceDocument, pk=document_id, project=project)
    selected_id = request.POST.get('assignment_id', '').strip()
    assignment = Assignment.objects.filter(pk=selected_id if len(selected_id) <= 18 and
        selected_id.isascii() and selected_id.isdecimal() else None,
        coder=request.user, superseded=False, unit__record__document=document).first()
    destination = (reverse('workbench', args=[assignment.round_id]) + f'?a={assignment.pk}#source-information-settings'
                   if assignment else reverse('materials', args=[project.pk]))
    try:
        headers, _ = parse_upload(document.name, bytes(document.raw), sheet_name=document.sheet_name or None)
        form = SourceDisplayForm(request.POST, headers=headers, prefix=f'source-{document.pk}')
        if not form.is_valid():
            raise ValueError('显示字段包含无效列，请重新选择。')
        with transaction.atomic():
            document = SourceDocument.objects.select_for_update().get(pk=document.pk)
            if request.POST.get('mapping_token') != mapping_token(document):
                raise Conflict('显示字段已被修改，请刷新核对后重试。')
            before = document.display_mapping
            document.display_mapping = form.cleaned_data
            document.save(update_fields=['display_mapping'])
            audit(project, request.user, 'source.display_mapping', document,
                  before=before, after=document.display_mapping)
        messages.success(request, '显示字段已保存，原材料与编码保持不变。')
    except (ValueError, OperationalError) as error:
        messages.error(request, str(error) if not isinstance(error, OperationalError) else '数据库忙，请稍后重试。')
    return redirect(destination)


@login_required
def units(request, project_id):
    project, role = access(request, project_id)
    queryset = Unit.objects.filter(record__document__project=project, active=True,
                                   record__document__units_confirmed=True).select_related('record__document')
    query = request.GET.get('q', '')[:200]
    if query:
        queryset = queryset.filter(text__icontains=query)
    page = Paginator(queryset, 30).get_page(request.GET.get('page'))
    suggestions = []
    if role in ('owner', 'reviewer'):
        recent = list(AuditEvent.objects.filter(project=project, action='unit.split_suggest')
                      .select_related('actor').order_by('-id')[:50])
        valid_ids = set(queryset.filter(pk__in=[e.after.get('unit_id') for e in recent]).values_list('id', flat=True))
        suggestions = [e for e in recent if e.after.get('unit_id') in valid_ids]
    return render(request, 'coding/units.html', {'project': project, 'role': role, 'page': page, 'q': query,
                                               'split_suggestions': suggestions})


@require_POST
@login_required
def change_unit(request, project_id, unit_id):
    project, role = access(request, project_id, ['owner', 'reviewer'])
    try:
        with transaction.atomic():
            Project.objects.select_for_update().get(pk=project.id)
            unit = get_object_or_404(Unit.objects.select_for_update(), pk=unit_id, record__document__project=project, active=True)
            if request.POST.get('revision') != str(unit.revision):
                raise Conflict('单元已经变化，请刷新。')
            action = request.POST.get('action')
            note = request.POST.get('note', '').strip()[:2000]
            if not note:
                raise ValueError('请说明调整理由。')
            created = []
            if action == 'split':
                offset = int(request.POST.get('offset', '0'))
                if not 0 < offset < len(unit.text):
                    raise ValueError('切分位置必须在文本中间（按Unicode字符计数）。')
                for start, end in [(unit.start, unit.start + offset), (unit.start + offset, unit.end)]:
                    created.append(Unit.objects.create(record=unit.record, start=start, end=end,
                                                       text=unit.record.text[start:end], note=note))
            elif action == 'merge':
                following = Unit.objects.select_for_update().filter(record=unit.record, active=True, start=unit.end).first()
                if not following:
                    raise ValueError('只能合并同一原记录中紧邻的下一片段。')
                created.append(Unit.objects.create(record=unit.record, start=unit.start, end=following.end,
                                                   text=unit.record.text[unit.start:following.end], note=note))
                following.active = False
                following.revision += 1
                following.save(update_fields=['active', 'revision'])
            elif action != 'exclude':
                raise ValueError('无效操作。')
            unit.active = False
            unit.revision += 1
            unit.save(update_fields=['active', 'revision'])
            audit(project, request.user, 'unit.' + action, unit, before={'active': True},
                  after={'active': False, 'new_unit_ids': [u.id for u in created]}, reason=note)
        messages.success(request, '已调整。已有轮次仍使用旧单元，新单元只用于未来轮次。')
    except (ValueError, Conflict, OperationalError) as error:
        messages.error(request, str(error))
    return redirect('units', project.id)


@login_required
@require_GET
def code_references(request, project_id, book_id, code_id):
    project, role = access(request, project_id)
    code = get_object_or_404(Code.objects.select_related('book'), pk=code_id,
                             book_id=book_id, book__project=project)
    page = my_code_references(code, request.user, request.GET.get('page'))
    return render(request, 'coding/code_references.html', {'project': project, 'role': role,
                  'book': code.book, 'code': code, 'page': page})


@login_required
def codebook(request, project_id, book_id):
    project, role = access(request, project_id)
    book = get_object_or_404(Codebook, project=project, pk=book_id)
    if request.method == 'POST' and book.pk != project.codebooks.first().pk:
        messages.error(request, '只能编辑当前编码本，历史规则只读保留。')
        return redirect('codebook', project.pk, project.codebooks.first().pk)
    if request.method == 'POST' and book.frozen:
        messages.error(request, '该版本已经冻结，请复制成新版本再修改。')
        return redirect('codebook', project.id, book.id)
    code_id = request.GET.get('edit') or request.POST.get('code_id')
    code = get_object_or_404(Code, book=book, pk=object_id(code_id)) if code_id else Code(book=book)
    form = CodeForm(request.POST or None, instance=code, book=book, initial={'revision': book.revision})
    if request.method == 'POST':
        if role not in ('owner', 'reviewer'):
            raise PermissionDenied
        if form.is_valid():
            with transaction.atomic():
                Project.objects.select_for_update().get(pk=project.id)
                locked = Codebook.objects.select_for_update().get(pk=book.id)
                if locked.pk != project.codebooks.first().pk:
                    form.add_error(None, '当前编码本已变化，请打开最新编码本后再编辑。')
                elif locked.frozen:
                    form.add_error(None, '该版本已经冻结，请复制成新版本再修改。')
                elif form.cleaned_data['revision'] != locked.revision:
                    form.add_error(None, '其他人已保存编码本变化。输入保留，请核对最新版本。')
                elif book.codes.filter(key=form.cleaned_data['key']).exclude(pk=code.pk).exists():
                    form.add_error('key', '同一编码本中的代码标识不能重复。')
                else:
                    before = {f.name: str(getattr(Code.objects.get(pk=code.pk), f.name)) for f in Code._meta.fields} if code.pk else {}
                    saved = form.save()
                    locked.revision += 1
                    locked.save(update_fields=['revision'])
                    audit(project, request.user, 'code.save', saved, before=before,
                          after={f.name: str(getattr(saved, f.name)) for f in Code._meta.fields})
                    messages.success(request, '代码已保存。')
                    return redirect('codebook', project.id, book.id)
    return render(request, 'coding/codebook.html', {'project': project, 'role': role, 'book': book,
        'codes': book.codes.filter(private_coder__isnull=True).select_related('parent'), 'form': form, 'editing': code.pk,
        'book_form': BookForm(initial={'title': book.title, 'policy': book.policy}),
        'current_book': project.codebooks.first(),
        'book_history': project.codebooks.exclude(pk=book.pk),
        'update_rounds': project.rounds.filter(state='active').exclude(book=book).select_related('book')
                         if role in ('owner', 'reviewer') else Round.objects.none()})


@require_POST
@login_required
def book_update(request, project_id, book_id):
    project, role = access(request, project_id, ['owner', 'reviewer'])
    source = get_object_or_404(Codebook, project=project, pk=book_id)
    try:
        book = begin_update(project, request.user, source, int(request.POST.get('revision', '')))
        messages.success(request, '可以直接添加或编辑代码，无需重新导入。')
        return redirect(reverse('codebook', args=[project.pk, book.pk]) + '#code-editor')
    except (ValueError, OperationalError) as error:
        messages.error(request, '数据库正在忙，未确认更新成功，请刷新后重试。' if isinstance(error, OperationalError) else str(error))
    return redirect('codebook', project.pk, source.pk)


@require_POST
@login_required
def book_apply(request, project_id, book_id):
    project, role = access(request, project_id, ['owner', 'reviewer'])
    book = get_object_or_404(Codebook, project=project, pk=book_id)
    try:
        if request.POST.get('confirmed') != 'yes':
            raise ValueError('请确认所选批次将保留原标签并退回草稿，等待重新核对。')
        revisions = {object_id(value): int(request.POST.get('round_revision_' + value, ''))
                     for value in request.POST.getlist('round_ids')}
        count = apply_book_update(project, request.user, book, int(request.POST.get('revision', '')), revisions)
        messages.success(request, f'已更新 {count} 个进行中的批次。原标签保留，请核对后重新提交。')
    except (ValueError, OperationalError) as error:
        messages.error(request, '数据库正在忙，未确认同步成功，请刷新后核对。' if isinstance(error, OperationalError) else str(error))
    return redirect('codebook', project.pk, book.pk)


@require_POST
@login_required
def book_new(request, project_id):
    project, role = access(request, project_id, ['owner', 'reviewer'])
    source = get_object_or_404(Codebook, project=project, pk=object_id(request.POST.get('source_id')))
    form = BookForm(request.POST)
    if form.is_valid():
        try:
            with transaction.atomic():
                Project.objects.select_for_update().get(pk=project.pk)
                if source.pk != project.codebooks.first().pk:
                    raise Conflict('只能从当前编码本继续更新，请打开当前编码本后重试。')
                book = clone_book(project, request.user, source, **form.cleaned_data)
            return redirect('codebook', project.id, book.id)
        except (ValueError, OperationalError) as error:
            messages.error(request, '数据库正在忙，未确认更新成功，请刷新后重试。' if isinstance(error, OperationalError) else str(error))
    else:
        messages.error(request, '新版本需要名称、标签方式及修改目的。')
    return redirect('codebook', project.id, source.id)


@require_POST
@login_required
def book_freeze(request, project_id, book_id):
    project, role = access(request, project_id, ['owner', 'reviewer'])
    try:
        with transaction.atomic():
            Project.objects.select_for_update().get(pk=project.id)
            book = get_object_or_404(Codebook.objects.select_for_update(), project=project, pk=book_id)
            if request.POST.get('revision') != str(book.revision):
                raise Conflict('规则已变化，请刷新核对再冻结。')
            if not book.codes.exists():
                raise ValueError('请先填写编码规则，不能冻结空编码本。')
            if not book.frozen:
                book.frozen, book.revision = True, book.revision + 1
                book.save(update_fields=['frozen', 'revision'])
                audit(project, request.user, 'codebook.freeze', book, after={'version': book.version})
        messages.success(request, '版本已冻结。后续修改请复制新版本。')
    except (ValueError, OperationalError) as error:
        messages.error(request, str(error))
    return redirect('codebook', project.id, book_id)


@require_POST
@login_required
def code_remove(request, project_id, code_id):
    project, role = access(request, project_id, ['owner', 'reviewer'])
    code = get_object_or_404(Code.objects.select_related('book'), pk=code_id, book__project=project)
    book_id = code.book_id
    try:
        with transaction.atomic():
            Project.objects.select_for_update().get(pk=project.id)
            book = Codebook.objects.select_for_update().get(pk=book_id)
            if book.pk != project.codebooks.first().pk:
                raise ValueError('只能修改当前编码本，历史规则保持不变。')
            if book.frozen:
                raise ValueError('冻结规则不能删除。请复制到新版本再调整。')
            if request.POST.get('revision') != str(book.revision):
                raise Conflict('规则已变化，请刷新。')
            code = get_object_or_404(Code, pk=code_id, book=book)
            if Code.objects.filter(parent=code).exists():
                raise ValueError('这条代码有下级代码，请先调整层级。')
            from .workbench_tools import code_is_used
            if code_is_used(code):
                raise ValueError('已有编码记录使用此代码，不能直接删除。请保留旧规则，通过新版本调整。')
            audit(project, request.user, 'code.remove_draft', code,
                  before={f.name: str(getattr(code, f.name)) for f in Code._meta.fields})
            code.delete()
            book.revision += 1
            book.save(update_fields=['revision'])
        messages.success(request, '已删除未使用的草稿代码，历史定义保留在日志中。')
    except (ValueError, OperationalError) as error:
        messages.error(request, str(error))
    return redirect('codebook', project.id, book_id)


@login_required
def round_new(request, project_id):
    from .task_arrangement import arrange_round
    return arrange_round(request, project_id)


@login_required
def workbench(request, round_id):
    r, role = round_access(request, round_id)
    if r.sampling.get('source_coding'):
        from .source_views import workbench as source_workbench
        return source_workbench(request, round_id)
    mine = coding_assignments(r, request.user)
    paginator = Paginator(mine, 50)
    if request.GET.get('a'):
        selected_id = object_id(request.GET['a'])
        if r.assignments.filter(pk=selected_id, coder=request.user, superseded=True).exists():
            messages.info(request, '此任务的拆分状态已调整，请继续编码当前有效单元。')
            return redirect('workbench', r.pk)
        a = get_object_or_404(mine, pk=selected_id)
    elif request.GET.get('page'):
        requested_page = paginator.get_page(request.GET['page'])
        page_ids = [item.id for item in requested_page]
        a = mine.filter(id__in=page_ids, status='draft').first() or mine.filter(id__in=page_ids).first()
    else:
        a = (mine.filter(status='draft', revision__gt=0).order_by('-updated_at', '-id').first()
             or mine.filter(status='draft').first() or mine.first())
    ids = list(mine.values_list('id', flat=True))
    index = ids.index(a.pk) if a else 0
    draft_ids = set(mine.filter(status='draft').values_list('id', flat=True))
    skip_id = next((pk for pk in ids[index + 1:] + ids[:index] if pk in draft_ids), None) if a else None
    skip_url = (reverse('workbench', args=[r.pk]) + '?a=' + str(skip_id) if skip_id else
                reverse('progress', args=[r.project_id]))
    task_page = paginator.get_page(index // 50 + 1)
    context = []
    context_options = {}
    if a:
        rec = a.unit.record
        before, more_before, before_cursor = context_page(rec, 'before', size=2)
        after, more_after, after_cursor = context_page(rec, 'after', size=2)
        context = before + [rec] + after
        attach_source_information(context, rec.document)
        context_options = {'context_before': before, 'context_after': after,
            'more_before': more_before, 'more_after': more_after,
            'before_cursor': before_cursor, 'after_cursor': after_cursor}
        if role in ('owner', 'reviewer'):
            headers = available_headers(rec)
            context_options.update(source_display_form=SourceDisplayForm(headers=headers,
                mapping=display_mapping(rec.document, headers), prefix=f'source-{rec.document_id}'),
                source_mapping_token=mapping_token(rec.document))
    codes = list(r.book.codes.all())
    restore_options = split_restore_options(a, request.user)
    submission_options = {'submitted_tasks': []}
    if role in ('owner', 'reviewer') and r.state == 'active':
        submitted = r.assignments.filter(status='submitted', superseded=False)
        available = submitted.exists()
        coders = list(r.assignments.filter(superseded=False).values('coder_id', 'coder__username').distinct()
                      .order_by('coder__username', 'coder_id'))
        unit_filter = request.GET.get('submitted_unit', '').strip()
        coder_filter = request.GET.get('submitted_coder', '').strip()
        filter_error = ''
        if unit_filter:
            if len(unit_filter) > 18 or not unit_filter.isascii() or not unit_filter.isdecimal() or int(unit_filter) < 1:
                filter_error = '请输入有效的系统单元编号，例如641；不是材料顺序号。'
            else:
                submitted = submitted.filter(unit_id=int(unit_filter))
        if coder_filter:
            if (len(coder_filter) > 18 or not coder_filter.isascii() or not coder_filter.isdecimal()
                    or int(coder_filter) not in {row['coder_id'] for row in coders}):
                filter_error = '请选择本轮有任务的编码员。'
            else:
                submitted = submitted.filter(coder_id=int(coder_filter))
        if filter_error:
            submitted = submitted.none()
        submitted_page = Paginator(submitted.order_by('unit__record__document_id', 'unit__record__position',
            'unit__start', 'coder_id', 'id').values('id', 'coder__username', 'unit_id', 'revision'), 50).get_page(
                request.GET.get('submitted_page'))
        params = request.GET.copy()
        if a:
            params['a'] = str(a.pk)
        base_params = params.copy()
        for name in ('submitted_page', 'submitted_unit', 'submitted_coder'):
            base_params.pop(name, None)
        def submission_page_url(number):
            values = params.copy()
            values['submitted_page'] = number
            return '?' + values.urlencode() + '#submission-controls'
        submission_options = {
            'show_submission_controls': available or any(name.startswith('submitted_') for name in request.GET),
            'submission_filters_active': any(name in request.GET for name in ('submitted_page', 'submitted_unit', 'submitted_coder')),
            'submitted_tasks': submitted_page, 'submitted_coders': coders,
            'submitted_unit_filter': unit_filter, 'submitted_coder_filter': coder_filter,
            'submission_filter_error': filter_error,
            'submission_context_fields': [(key, value) for key, values in base_params.lists() for value in values],
            'submission_return_fields': [('page', task_page.number), ('submitted_page', submitted_page.number)]
                + ([('a', a.pk)] if a else [])
                + ([('submitted_unit', unit_filter)] if unit_filter else [])
                + ([('submitted_coder', coder_filter)] if coder_filter else []),
            'submission_reset_url': '?' + base_params.urlencode() + '#submission-controls',
            'submission_previous_url': submission_page_url(submitted_page.previous_page_number()) if submitted_page.has_previous() else '',
            'submission_next_url': submission_page_url(submitted_page.next_page_number()) if submitted_page.has_next() else '',
        }
    return render(request, 'coding/workbench.html', {'project': r.project, 'role': role, 'round': r,
        'assignment': a, 'annotation': annotation_data(a) if a else {}, 'codes': codes, 'context': context,
        **context_options,
        **reading_context(a, request.GET),
        **workbench_context(a, role),
        'annotation_locked': role == 'viewer' or r.state != 'active' or (a is not None and a.status == 'submitted'),
        'editing_flags': history_permissions(a, request.user, role) if a else {},
        'next_url': next_coding_url(a) if a else '',
        'skip_url': skip_url,
        'can_split': a is not None and r.state == 'active' and role != 'viewer' and not (
            a.unit.record.document.normalized.get('type') == 'document' and r.kind in ('pilot', 'formal')),
        'can_suggest_split': False,
        'split_restore': restore_options[0] if restore_options else None,
        'tasks': task_page,
        'previous_task': mine.get(pk=ids[index - 1]) if a and index else None,
        'next_task': mine.get(pk=ids[index + 1]) if a and index + 1 < len(ids) else None,
        'next_draft': mine.filter(status='draft').exclude(pk=a.pk).first() if a else None,
        'mine_total': mine.count(), 'mine_done': mine.filter(status='submitted').count(),
        'can_review': r.state != 'active' or r.kind == 'training',
        **submission_options,
        'total': r.assignments.filter(superseded=False).count(),
        'total_done': r.assignments.filter(status='submitted', superseded=False).count()})


@login_required
def assignment_context(request, assignment_id):
    if request.method != 'GET':
        return HttpResponse(status=405)
    a = get_object_or_404(Assignment.objects.select_related('unit__record__document', 'round'),
                         pk=assignment_id, coder=request.user, superseded=False)
    access(request, a.round.project_id)
    try:
        raw_cursor = request.GET.get('cursor', '')
        if not raw_cursor.isascii() or not raw_cursor.isdigit() or len(raw_cursor) > 18:
            raise ValueError('上下文位置不正确，请刷新后重试。')
        items, more, cursor = context_page(a.unit.record, request.GET.get('direction'), int(raw_cursor))
        attach_source_information(items, a.unit.record.document)
        response = JsonResponse({'records': [{'id': rec.pk, 'position': rec.position, 'text': rec.text,
            'context_key': rec.context_key, 'source_information': rec.source_information} for rec in items],
            'has_more': more, 'cursor': cursor})
        response['Cache-Control'] = 'no-store'
        return response
    except ValueError as error:
        return api_error(error)


@login_required
def round_progress(request, round_id):
    r, role = round_access(request, round_id)
    mine = coding_assignments(r, request.user)
    current = None
    if request.GET.get('current'):
        current = get_object_or_404(mine, pk=object_id(request.GET['current']))
    unfinished = mine.filter(status='draft')
    following = unfinished.exclude(pk=current.pk).first() if current else unfinished.first()
    following = following or (current if current and current.status == 'draft' else None)
    response = JsonResponse({'state': r.state, 'my_total': mine.count(), 'my_done': mine.filter(status='submitted').count(),
                         'my_remaining': unfinished.count(),
                         'next_unfinished_url': reverse('workbench', args=[r.pk]) + '?a=' + str(following.pk) if following else '',
                         'total': r.assignments.filter(superseded=False).count(),
                         'done': r.assignments.filter(status='submitted', superseded=False).count()})
    response['Cache-Control'] = 'no-store'
    return response


@require_POST
@login_required
def assignment_split(request, assignment_id):
    a = get_object_or_404(Assignment.objects.select_related('round'), pk=assignment_id, coder=request.user)
    access(request, a.round.project_id, ['owner', 'reviewer', 'coder'])
    try:
        if a.unit.record.document.normalized.get('type') == 'document' and a.round.kind in ('pilot', 'formal'):
            raise ValueError('本轮使用统一文档片段，请由负责人在材料准备中修订范围；已有任务保留。')
        data = body_json(request)
        split_personal(a.pk, request.user, data)
        children = list(coding_assignments(a.round, request.user).filter(parent=a).order_by('unit__start'))
        position = a.unit.start + data.get('start', 0)
        child = next((c for c in children if c.unit.start <= position < c.unit.end), children[0] if children else None)
        return JsonResponse({'ok': True, 'next_url': f'/r/{a.round_id}/' + (f'?a={child.pk}' if child else '')})
    except (ValueError, OperationalError) as error:
        return api_error(error)


@require_POST
@login_required
def split_suggestion(request, assignment_id):
    a = get_object_or_404(Assignment.objects.select_related('round'), pk=assignment_id, coder=request.user)
    access(request, a.round.project_id, ['owner', 'reviewer', 'coder'])
    try:
        suggest_split(a.pk, request.user, body_json(request))
        return JsonResponse({'ok': True, 'message': '建议已交给负责人。当前多人任务和已有答案不改变。'})
    except (ValueError, OperationalError) as error:
        return api_error(error)


@require_POST
@login_required
def assignment_save(request, assignment_id):
    a = get_object_or_404(Assignment.objects.select_related('round'), pk=assignment_id, coder=request.user)
    access(request, a.round.project_id, ['owner', 'reviewer', 'coder'])
    try:
        saved = save_assignment(a.id, request.user, body_json(request))
        return JsonResponse({'ok': True, 'annotation': annotation_data(saved),
                             'next_url': next_coding_url(saved) if saved.status == 'submitted' else ''})
    except (ValueError, OperationalError) as error:
        return api_error(error)


@require_POST
@login_required
def round_manage(request, round_id):
    r, role = round_access(request, round_id, ['owner', 'reviewer'])
    if r.kind == 'quick':
        raise PermissionDenied('个人快速编码请由本人完成并归档，不使用协商轮次操作。')
    try:
        with transaction.atomic():
            r = Round.objects.select_for_update().get(pk=r.pk)
            if request.POST.get('revision') != str(r.revision):
                raise Conflict('轮次状态已更新，请刷新。')
            action, reason = request.POST.get('action'), request.POST.get('reason', '').strip()[:2000]
            old = {'state': r.state, 'cycle': r.cycle}
            if action == 'lock':
                if r.sampling.get('source_coding'):
                    if r.state != 'active' or not r.source_tasks.exists() or r.source_tasks.filter(completed=False).exists():
                        raise ValueError('请等待本轮全员标记“本份材料编码完成”后，再锁定进入协商对比。')
                elif r.state != 'active' or r.assignments.filter(status='draft', superseded=False).exists():
                    raise ValueError('必须全部独立提交后才能进入协商；未完成任务可在各自工作台标记不确定并写原因。')
                r.state = 'review'
            elif action == 'reopen':
                if r.state != 'review' or not reason:
                    raise ValueError('仅协商中的轮次可重开，且必须说明理由；归档轮次不改写。')
                r.state, r.cycle = 'active', r.cycle + 1
                if r.sampling.get('source_coding'):
                    r.source_tasks.update(completed=False, revision=F('revision') + 1)
                else:
                    r.assignments.filter(superseded=False).update(status='draft', last_token='', revision=F('revision') + 1)
                # Previously visible answers cannot become blind again.
                # Keep the round labelled re-opened; a NEW round is required for new independent reliability.
            elif action == 'close':
                if r.sampling.get('source_coding'):
                    if r.state != 'review':
                        raise ValueError('请先进入协商核对结果后，再结束归档本轮。')
                else:
                    unit_ids = set(r.assignments.filter(superseded=False).values_list('unit_id', flat=True))
                    valid = set(r.decisions.filter(cycle=r.cycle, uncertain=False).values_list('unit_id', flat=True))
                    if r.state != 'review' or unit_ids != valid:
                        raise ValueError('每个单元都需要本轮有效的、非待裁定的最终结论才能归档。')
                r.state = 'closed'
            else:
                raise ValueError('无效状态操作。')
            r.revision += 1
            r.save()
            audit(r.project, request.user, 'round.' + action, r, before=old, after={'state': r.state, 'cycle': r.cycle}, reason=reason)
        messages.success(request, '状态已更新。重开不抹去历史；需要新的独立信度时请新建轮次。')
    except (ValueError, OperationalError) as error:
        messages.error(request, str(error))
    if r.sampling.get('source_coding'):
        if r.state != 'active':
            return redirect('disagreements', r.id)
        if not r.source_tasks.filter(coder=request.user).exists():
            return redirect('progress', r.project_id)
    return redirect('workbench', r.id)


@require_POST
@login_required
def assignment_reopen(request, assignment_id):
    a = get_object_or_404(Assignment.objects.select_related('round'), pk=assignment_id)
    project, role = access(request, a.round.project_id, ['owner', 'reviewer'])
    try:
        with transaction.atomic():
            r = Round.objects.select_for_update().get(pk=a.round_id)
            a = Assignment.objects.select_for_update().get(pk=a.pk)
            reason = request.POST.get('reason', '').strip()[:2000]
            if r.state != 'active' or a.status != 'submitted' or a.superseded or not reason:
                raise ValueError('仅编码阶段已提交的任务可退回，并需说明理由。')
            if request.POST.get('revision') != str(a.revision):
                raise Conflict('任务状态已变化，请刷新。')
            old = annotation_data(a)
            a.status, a.last_token, a.revision = 'draft', '', a.revision + 1
            a.save(update_fields=['status', 'last_token', 'revision', 'updated_at'])
            audit(project, request.user, 'annotation.reopen', a, before=old, after=annotation_data(a), reason=reason)
        messages.success(request, '已退回给原编码员；没有修改其答案。')
    except (ValueError, OperationalError) as error:
        messages.error(request, str(error))
    return_params = request.GET.copy()
    return_params.clear()
    for name in ('a', 'page', 'submitted_page', 'submitted_unit', 'submitted_coder'):
        value = request.POST.get('return_' + name, '').strip()
        if value and len(value) <= 18 and value.isascii() and value.isdecimal() and int(value) > 0:
            return_params[name] = str(int(value))
    try:
        for name, criteria in (
                ('a', {'pk': return_params.get('a'), 'coder': request.user, 'superseded': False}),
                ('submitted_unit', {'unit_id': return_params.get('submitted_unit')}),
                ('submitted_coder', {'coder_id': return_params.get('submitted_coder'), 'superseded': False})):
            if name in return_params and not Assignment.objects.filter(round_id=a.round_id, **criteria).exists():
                return_params.pop(name)
    except OperationalError:
        return_params.clear()
    if return_params:
        return redirect(reverse('workbench', args=[a.round_id]) + '?' + return_params.urlencode() + '#submission-controls')
    return redirect('workbench', a.round_id)


@login_required
def review(request, round_id):
    r, role = round_access(request, round_id)
    if r.sampling.get('source_coding'):
        from .source_rounds import source_review_view
        return source_review_view(request, round_id)
    review_access(request, r, role)
    ids = list(r.assignments.filter(superseded=False).values_list('unit_id', flat=True).distinct().order_by('unit_id'))
    selected_page = request.GET.get('page')
    if request.GET.get('unit'):
        selected_id = object_id(request.GET['unit'])
        if selected_id not in ids:
            raise Http404
        selected_page = ids.index(selected_id) + 1
    page = Paginator(ids, 1).get_page(selected_page)
    unit = get_object_or_404(Unit.objects.select_related('record__document'), pk=page.object_list[0]) if ids else None
    entries = r.assignments.filter(unit=unit, superseded=False).select_related('coder', 'primary')
    decision = Decision.objects.filter(round=r, unit=unit).first()
    current_decision = decision if decision and decision.cycle == r.cycle else None
    decision_data = annotation_data(current_decision) if current_decision else {
        'revision': decision.revision if decision else 0, 'primary': None, 'secondary': [],
        'uncertain': False, 'no_code': False, 'note': ''}
    codes = list(r.book.codes.all())
    keys = {c.id: c.key for c in codes}
    entries = [(a, [keys.get(c, '') for c in (a.labels if r.book.policy == 'parallel' else a.secondary)]) for a in entries]
    return render(request, 'coding/review.html', {'project': r.project, 'role': role, 'round': r,
        'unit': unit, 'entries': entries, 'page': page, 'codes': codes,
        'decision_data': decision_data,
        'decision': current_decision, 'comments': Comment.objects.filter(round=r, unit=unit).select_related('author').order_by('id'),
        'context': context_records(unit.record) if unit else []})


@login_required
def disagreement_queue(request, round_id):
    r, role = round_access(request, round_id)
    if r.sampling.get('source_coding'):
        from .source_rounds import source_review_view
        return source_review_view(request, round_id)
    review_access(request, r, role)
    rows = disagreement_rows(r)
    counts = {'all': len(rows), 'attention': sum(row['needs_attention'] for row in rows),
              'undecided': sum(not row['resolved'] for row in rows)}
    mode = request.GET.get('filter', 'attention')
    if mode not in counts:
        mode = 'attention'
    if mode == 'attention':
        rows = [row for row in rows if row['needs_attention']]
    elif mode == 'undecided':
        rows = [row for row in rows if not row['resolved']]
    page = Paginator(rows, 30).get_page(request.GET.get('page'))
    return render(request, 'coding/disagreements.html', {'project': r.project, 'role': role, 'round': r,
                                                        'counts': counts, 'mode': mode, 'page': page})


@require_POST
@login_required
def decision_save(request, round_id, unit_id):
    r, role = round_access(request, round_id, ['owner', 'reviewer'])
    review_access(request, r, role)
    unit = get_object_or_404(Unit.objects.distinct(), pk=unit_id, assignment__round=r, assignment__superseded=False)
    try:
        saved = save_decision(r, unit, request.user, body_json(request))
        return JsonResponse({'ok': True, 'annotation': annotation_data(saved)})
    except (ValueError, OperationalError) as error:
        return api_error(error)


@require_POST
@login_required
def comment_add(request, round_id, unit_id):
    r, role = round_access(request, round_id, ['owner', 'reviewer', 'coder'])
    review_access(request, r, role)
    unit = get_object_or_404(Unit.objects.distinct(), pk=unit_id, assignment__round=r, assignment__superseded=False)
    text = request.POST.get('body', '').strip()
    if r.state == 'closed' or not 1 <= len(text) <= 5000:
        messages.error(request, '归档轮次不增加评论；评论需1至5000字符。')
    else:
        with transaction.atomic():
            locked = Round.objects.select_for_update().get(pk=r.pk)
            if locked.state == 'active' and locked.kind != 'training':
                raise PermissionDenied
            if locked.state == 'closed':
                raise PermissionDenied
            comment = Comment.objects.create(round=r, unit=unit, author=request.user, body=text)
            audit(r.project, request.user, 'comment.add', comment, after={'body': text, 'unit_id': unit_id})
    return redirect(f'/r/{r.id}/review/?page={request.POST.get("page", "1")}')


@login_required
def reliability(request, round_id):
    r, role = round_access(request, round_id, ['owner', 'reviewer'])
    if r.kind == 'quick':
        raise PermissionDenied('个人快速编码不是独立一致性评估，请安排多人独立编码轮次。')
    review_access(request, r, role)
    if r.state == 'active':
        raise PermissionDenied
    if request.method == 'POST':
        with transaction.atomic():
            locked = Round.objects.select_for_update().select_related('book').get(pk=r.pk)
            if locked.state == 'active':
                raise PermissionDenied
            data = report_for(locked)
            data['reopened_not_blind'] = locked.cycle > 1
            report = ReliabilityReport.objects.create(round=locked, cycle=locked.cycle, data=data, author=request.user)
            audit(r.project, request.user, 'report.create', report, after={'cycle': locked.cycle})
        return redirect('reliability', r.id)
    report = r.reports.filter(cycle=r.cycle).order_by('-id').first()
    return render(request, 'coding/reliability.html', {'project': r.project, 'role': role, 'round': r,
                                                     'report': report, 'data': report.data if report else {}})


@login_required
def export_round(request, round_id, mode):
    from .exporting import annotation_table, final_table, disagreement_table, export_response, MAX_EXPORT_ROWS
    r, role = round_access(request, round_id)
    if r.sampling.get('source_coding'):
        if mode != 'mine':
            raise PermissionDenied('原文选段是个人编码，请导出自己的选段记录。')
        from .source_views import export as source_export
        return source_export(request, round_id)
    if mode == 'mine':
        if r.assignments.filter(coder=request.user, superseded=False).count() > MAX_EXPORT_ROWS:
            return HttpResponse('一次最多导出50000行，请按轮次分批。', status=400)
        table = annotation_table(r, r.assignments.filter(coder=request.user, superseded=False))
    elif mode == 'all':
        review_access(request, r, role)
        if r.assignments.count() > MAX_EXPORT_ROWS:
            return HttpResponse('本轮超过50000条判断，请按编码员分别导出。', status=400)
        table = annotation_table(r, r.assignments.filter(superseded=False))
    elif mode == 'final':
        if r.kind == 'quick':
            raise PermissionDenied('个人快速编码没有多人协商定稿，请导出个人判断。')
        if r.state != 'closed':
            raise PermissionDenied('最终导出需要先完成协商并归档。')
        if r.decisions.filter(cycle=r.cycle).count() > MAX_EXPORT_ROWS:
            return HttpResponse('一次最多导出50000行，请按轮次分批。', status=400)
        table = final_table(r)
    elif mode == 'disagreements':
        review_access(request, r, role)
        if r.assignments.count() > MAX_EXPORT_ROWS:
            return HttpResponse('本轮超过50000条判断，请分批复核。', status=400)
        table = disagreement_table(r)
    else:
        raise Http404
    try:
        fields = request.GET.getlist('field') or table.headers
        return export_response(table, fields, fields, request.GET.get('format', 'csv'), f'round_{r.id}_{mode}')
    except ValueError as error:
        return HttpResponse(str(error), status=400, content_type='text/plain; charset=utf-8')


@login_required
def archive(request, project_id):
    from .archiving import archive as archive_page
    return archive_page(request, project_id)


@login_required
def guide(request):
    context = {}
    if 'project' in request.GET:
        if len(request.GET.getlist('project')) != 1:
            raise Http404
        project, role = access(request, object_id(request.GET['project']))
        context.update(project=project, role=role)
        if 'round' in request.GET:
            if len(request.GET.getlist('round')) != 1:
                raise Http404
            r = get_object_or_404(Round.objects.select_related('project', 'book'),
                                 pk=object_id(request.GET['round']), project=project)
            if r.kind == 'quick' and r.started_by_id != request.user.pk and role not in ('owner', 'reviewer'):
                raise Http404
            context['round'] = r
    elif 'round' in request.GET:
        raise Http404
    return render(request, 'coding/guide.html', context)
