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
from django.db.models import F
from django.http import JsonResponse, HttpResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import *
from .forms import ProjectForm, MemberForm, BookForm, CodeForm
from .importing import parse_upload
from .services import (Conflict, audit, annotation_data, import_records, clone_book, create_round,
                       save_assignment, save_decision, export_annotations, export_final, export_disagreements, project_archive)
from .reliability import report_for
from .reviewing import disagreement_rows
from .account_forms import InvitationForm, JoinReviewForm
from .group_services import mark_approved


def access(request, project_id, roles=None):
    m = get_object_or_404(Membership.objects.select_related('project'), project_id=project_id, user=request.user)
    if roles and m.role not in roles:
        raise PermissionDenied
    return m.project, m.role


def round_access(request, round_id, roles=None):
    r = get_object_or_404(Round.objects.select_related('project', 'book'), pk=round_id)
    p, role = access(request, r.project_id, roles)
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
        'rounds': project.rounds.select_related('book').order_by('-id')})


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
    return render(request, 'coding/members.html', {'project': project, 'role': role, 'form': form,
        'members': project.memberships.select_related('user'), 'invitation': invitation, 'new_code': new_code,
        'invite_form': InvitationForm(initial={'revision': invitation.revision if invitation else 0, 'days': 7}),
        'now': timezone.now(), 'requests_page': requests_page})


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
                headers, rows = parse_upload(draft.filename, bytes(draft.raw))
                text_col, context_col = request.POST.get('text_column', ''), request.POST.get('context_column', '')
                if text_col not in headers or (context_col and (context_col not in headers or context_col == text_col)):
                    raise ValueError('请选择有效且不同的文本列和上下文分组列。')
                import_records(project, request.user, draft, rows, text_col, context_col)
                messages.success(request, '导入完成。原始文件与附加列均已保留；空白文本不生成单元。')
                return redirect('units', project.id)
            upload = request.FILES.get('file')
            if not upload:
                raise ValueError('请选择文件。')
            if upload.size > 8 * 1024 * 1024:
                raise ValueError('单个文件最大8MB。')
            raw = upload.read()
            headers, rows = parse_upload(upload.name, raw)
            draft = UploadDraft.objects.create(project=project, user=request.user, filename=Path(upload.name).name[:200], raw=raw)
            default = next((h for h in ['content', 'unit_text', 'text'] if h in headers), headers[0])
            return render(request, 'coding/import_preview.html', {'project': project, 'role': role,
                'draft': draft, 'headers': headers, 'preview': [[row.get(h, '')[:200] for h in headers] for row in rows[:5]],
                'row_count': len(rows), 'default_text': default})
        except (ValueError, TypeError, OperationalError) as exc:
            error = str(exc) if not isinstance(exc, OperationalError) else '数据库忙，请稍后重试；没有确认导入成功。'
    return render(request, 'coding/materials.html', {'project': project, 'role': role, 'error': error,
                                                  'documents': project.documents.all()})


@login_required
def units(request, project_id):
    project, role = access(request, project_id)
    queryset = Unit.objects.filter(record__document__project=project, active=True).select_related('record__document')
    query = request.GET.get('q', '')[:200]
    if query:
        queryset = queryset.filter(text__icontains=query)
    page = Paginator(queryset, 30).get_page(request.GET.get('page'))
    return render(request, 'coding/units.html', {'project': project, 'role': role, 'page': page, 'q': query})


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
def codebook(request, project_id, book_id):
    project, role = access(request, project_id)
    book = get_object_or_404(Codebook, project=project, pk=book_id)
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
                if locked.frozen:
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
        'codes': book.codes.select_related('parent'), 'form': form, 'editing': code.pk,
        'book_form': BookForm(initial={'title': book.title, 'policy': book.policy})})


@require_POST
@login_required
def book_new(request, project_id):
    project, role = access(request, project_id, ['owner', 'reviewer'])
    source = get_object_or_404(Codebook, project=project, pk=object_id(request.POST.get('source_id')))
    form = BookForm(request.POST)
    if form.is_valid():
        book = clone_book(project, request.user, source, **form.cleaned_data)
        return redirect('codebook', project.id, book.id)
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
            if book.frozen:
                raise ValueError('冻结规则不能删除。请复制到新版本再调整。')
            if request.POST.get('revision') != str(book.revision):
                raise Conflict('规则已变化，请刷新。')
            code = get_object_or_404(Code, pk=code_id, book=book)
            if Code.objects.filter(parent=code).exists():
                raise ValueError('这条代码有下级代码，请先调整层级。')
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
    from .round_forms import RoundForm
    project, role = access(request, project_id, ['owner', 'reviewer'])
    form = RoundForm(request.POST or None, project=project)
    if request.method == 'POST' and form.is_valid():
        try:
            r = create_round(project, request.user, **form.cleaned_data)
            return redirect('workbench', r.id)
        except (ValueError, OperationalError) as error:
            form.add_error(None, str(error))
    return render(request, 'coding/form.html', {'project': project, 'role': role, 'form': form,
                                               'title': '安排新一轮编码', 'button': '冻结规则并分配任务',
                                               'hint': '新轮次固定当前单元和规则版本。培训可公开讨论，独立试编码与正式编码提交前隐藏他人答案。'})


@login_required
def workbench(request, round_id):
    r, role = round_access(request, round_id)
    mine = r.assignments.filter(coder=request.user).select_related('unit__record__document').order_by('id')
    paginator = Paginator(mine, 50)
    if request.GET.get('a'):
        a = get_object_or_404(mine, pk=object_id(request.GET['a']))
    elif request.GET.get('page'):
        requested_page = paginator.get_page(request.GET['page'])
        page_ids = [item.id for item in requested_page]
        a = mine.filter(id__in=page_ids, status='draft').first() or mine.filter(id__in=page_ids).first()
    else:
        a = mine.filter(status='draft').first() or mine.first()
    task_page = paginator.get_page(mine.filter(id__lt=a.id).count() // 50 + 1 if a else 1)
    context = []
    if a:
        rec = a.unit.record
        context = context_records(rec)
    codes = list(r.book.codes.all())
    return render(request, 'coding/workbench.html', {'project': r.project, 'role': role, 'round': r,
        'assignment': a, 'annotation': annotation_data(a) if a else {}, 'codes': codes, 'context': context,
        'tasks': task_page,
        'previous_task': mine.filter(id__lt=a.id).order_by('-id').first() if a else None,
        'next_task': mine.filter(id__gt=a.id).first() if a else None,
        'next_draft': mine.filter(status='draft').exclude(pk=a.pk).first() if a else None,
        'mine_total': mine.count(), 'mine_done': mine.filter(status='submitted').count(),
        'can_review': r.state != 'active' or r.kind == 'training',
        'submitted_tasks': list(r.assignments.filter(status='submitted').values('id', 'coder__username', 'unit_id', 'revision')[:50])
                           if role in ('owner', 'reviewer') and r.state == 'active' else [],
        'total': r.assignments.count(), 'total_done': r.assignments.filter(status='submitted').count()})


@login_required
def round_progress(request, round_id):
    r, role = round_access(request, round_id)
    mine = r.assignments.filter(coder=request.user)
    return JsonResponse({'state': r.state, 'my_total': mine.count(), 'my_done': mine.filter(status='submitted').count(),
                         'total': r.assignments.count(), 'done': r.assignments.filter(status='submitted').count()})


@require_POST
@login_required
def assignment_save(request, assignment_id):
    a = get_object_or_404(Assignment.objects.select_related('round'), pk=assignment_id, coder=request.user)
    access(request, a.round.project_id, ['owner', 'reviewer', 'coder'])
    try:
        saved = save_assignment(a.id, request.user, body_json(request))
        return JsonResponse({'ok': True, 'annotation': annotation_data(saved)})
    except (ValueError, OperationalError) as error:
        return api_error(error)


@require_POST
@login_required
def round_manage(request, round_id):
    r, role = round_access(request, round_id, ['owner', 'reviewer'])
    try:
        with transaction.atomic():
            r = Round.objects.select_for_update().get(pk=r.pk)
            if request.POST.get('revision') != str(r.revision):
                raise Conflict('轮次状态已更新，请刷新。')
            action, reason = request.POST.get('action'), request.POST.get('reason', '').strip()[:2000]
            old = {'state': r.state, 'cycle': r.cycle}
            if action == 'lock':
                if r.state != 'active' or r.assignments.filter(status='draft').exists():
                    raise ValueError('必须全部独立提交后才能进入协商；未完成任务可在各自工作台标记不确定并写原因。')
                r.state = 'review'
            elif action == 'reopen':
                if r.state != 'review' or not reason:
                    raise ValueError('仅协商中的轮次可重开，且必须说明理由；归档轮次不改写。')
                r.state, r.cycle = 'active', r.cycle + 1
                r.assignments.update(status='draft', last_token='', revision=F('revision') + 1)
                # Previously visible answers cannot become blind again.
                # Keep the round labelled re-opened; a NEW round is required for new independent reliability.
            elif action == 'close':
                unit_ids = set(r.assignments.values_list('unit_id', flat=True))
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
            if r.state != 'active' or a.status != 'submitted' or not reason:
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
    return redirect('workbench', a.round_id)


@login_required
def review(request, round_id):
    r, role = round_access(request, round_id)
    review_access(request, r, role)
    ids = list(r.assignments.values_list('unit_id', flat=True).distinct().order_by('unit_id'))
    selected_page = request.GET.get('page')
    if request.GET.get('unit'):
        selected_id = object_id(request.GET['unit'])
        if selected_id not in ids:
            raise Http404
        selected_page = ids.index(selected_id) + 1
    page = Paginator(ids, 1).get_page(selected_page)
    unit = get_object_or_404(Unit.objects.select_related('record__document'), pk=page.object_list[0]) if ids else None
    entries = r.assignments.filter(unit=unit).select_related('coder', 'primary')
    decision = Decision.objects.filter(round=r, unit=unit).first()
    codes = list(r.book.codes.all())
    keys = {c.id: c.key for c in codes}
    entries = [(a, [keys.get(c, '') for c in a.secondary]) for a in entries]
    return render(request, 'coding/review.html', {'project': r.project, 'role': role, 'round': r,
        'unit': unit, 'entries': entries, 'page': page, 'codes': codes,
        'decision_data': annotation_data(decision) if decision else {'revision': 0, 'primary': None, 'secondary': []},
        'decision': decision, 'comments': Comment.objects.filter(round=r, unit=unit).select_related('author').order_by('id'),
        'context': context_records(unit.record) if unit else []})


@login_required
def disagreement_queue(request, round_id):
    r, role = round_access(request, round_id)
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
    unit = get_object_or_404(Unit.objects.distinct(), pk=unit_id, assignment__round=r)
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
    unit = get_object_or_404(Unit.objects.distinct(), pk=unit_id, assignment__round=r)
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
    if mode == 'mine':
        if r.assignments.filter(coder=request.user).count() > MAX_EXPORT_ROWS:
            return HttpResponse('一次最多导出50000行，请按轮次分批。', status=400)
        table = annotation_table(r, r.assignments.filter(coder=request.user))
    elif mode == 'all':
        review_access(request, r, role)
        if r.assignments.count() > MAX_EXPORT_ROWS:
            return HttpResponse('本轮超过50000条判断，请按编码员分别导出。', status=400)
        table = annotation_table(r, r.assignments.all())
    elif mode == 'final':
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
    project, role = access(request, project_id, ['owner', 'reviewer'])
    with transaction.atomic():
        project = Project.objects.select_for_update().get(pk=project.id)
        if not project.rounds.exists() or project.rounds.exclude(state='closed').exists():
            messages.error(request, '完整项目归档要求所有轮次都已定稿，避免泄露独立编码中的答案。')
            return redirect('project', project.id)
        data = project_archive(project)
    response = HttpResponse(data, content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="project_{project.id}_archive.zip"'
    return response


@login_required
def guide(request):
    return render(request, 'coding/guide.html')
