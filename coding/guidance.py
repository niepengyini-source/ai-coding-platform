"""Read-only project guidance; preparation does not reset a coding round."""
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.urls import reverse

from .models import Membership, Unit


PHASES = (
    '研究与材料准备', '编码本与单元准备', '试编码与改进',
    '正式编码', '协商定稿与成果',
)


def project_guidance(project, user, role, round_=None):
    if not Membership.objects.filter(project=project, user=user, role=role).exists():
        raise PermissionDenied
    manager = role in ('owner', 'reviewer')
    writable = role in ('owner', 'reviewer', 'coder')
    if round_ is not None and (round_.project_id != project.pk or
                              (round_.kind == 'quick' and round_.started_by_id != user.pk and not manager)):
        raise PermissionDenied
    book = round_.book if round_ is not None else project.codebooks.first()
    has_units = Unit.objects.filter(record__document__project=project, active=True,
                                    record__document__units_confirmed=True).exists()
    if round_ is not None:
        assigned = round_.assignments.filter(superseded=False)
        if not manager:
            assigned = assigned.filter(coder=user)
        has_units = has_units or assigned.exists()
    has_codes = bool(book and book.codes.exists())
    result = {
        'phase': 1, 'title': '', 'detail': '', 'next_label': '', 'next_url': '',
        'round_id': None, 'round_name': '', 'round_kind': '', 'round_state': '',
        'progress': {'submitted': 0, 'total': 0, 'remaining': 0, 'scope': 'my'},
        'ready': has_units and bool(book), 'missing': '', 'can_manage': manager,
        'has_units': has_units, 'has_codes': has_codes,
        'book_id': book.pk if book else None, 'has_rounds': project.rounds.exists(),
    }

    def show(phase, title, detail, label, url):
        result.update(phase=phase, title=title, detail=detail,
                      next_label=label, next_url=url)
        result['phases'] = [{'number': i, 'label': name, 'current': i == phase}
                            for i, name in enumerate(PHASES, 1)]
        if result['round_kind'] == 'quick':
            result['phases'][3]['label'] = '个人编码'
            result['phases'][4]['label'] = '编码结果与备份'
        return result

    # A personal batch never becomes another member's recommended team work.
    from .workflow import current_coding_round
    source_round = round_ or current_coding_round(project, user)
    if source_round and source_round.sampling.get('source_coding'):
        from .source_coding import progress_for
        progress = progress_for(source_round, user)
        result.update(ready=True, round_id=source_round.pk, round_name=source_round.name,
                      round_kind=source_round.kind, round_state=source_round.state,
                      progress={'submitted': progress['done'], 'total': progress['total'],
                                'remaining': progress['total'] - progress['done'], 'scope': 'my'})
        return show(4, '继续原文编码' if source_round.state == 'active' else '回看原文编码',
                    '在阅读中确定片段、使用或新增代码；材料完成状态由本人确认。',
                    '打开编码工作台', reverse('workbench', args=[source_round.pk]))
    rounds = project.rounds.filter(~Q(kind='quick') | Q(started_by=user))
    retained = '已有轮次和编码记录仍保留，可从原轮次继续。' if not result['ready'] and rounds.exists() else ''
    if not has_units:
        result['missing'] = 'materials'
        from .source_coding import coding_documents
        pending = coding_documents(project).filter(units_confirmed=False).first()
        if pending:
            if pending.normalized.get('type') == 'image':
                return show(2, '图片已导入，可以开始编码',
                            '可在原图中框选需要编码的区域。',
                            '开始编码' if writable else '查看原图',
                            reverse('coding_entry', args=[project.pk]) if writable else
                            reverse('source_preview', args=[project.pk, pending.pk]))
            return show(2, '材料已导入，可以开始编码',
                        '可在阅读中划选片段并逐步建立代码，也可先整理编码单元。',
                        '开始编码' if writable else '查看文档',
                        reverse('coding_entry', args=[project.pk]) if writable else
                        reverse('document_prepare', args=[project.pk, pending.pk]))
        return show(1, '准备编码材料',
                    ('导入材料并检查编码单元。' if manager else '等待负责人导入编码材料。') + retained,
                    '导入材料' if manager else '查看材料',
                    reverse('materials' if manager else 'units', args=[project.pk]))
    if not has_codes:
        if book and writable:
            return show(3, '可以边读边建立编码',
                        '可导入已有编码本，也可在工作台逐步新增代码。',
                        '开始编码', reverse('coding_entry', args=[project.pk]))
        return show(2, '准备编码本', '等待负责人准备编码规则。',
                    '查看项目', reverse('project', args=[project.pk]))

    own_pending = (rounds.filter(state='active', assignments__coder=user,
                                assignments__superseded=False, assignments__status='draft')
                   .order_by('-assignments__updated_at', '-assignments__id').first())
    participating = (rounds.filter(state__in=['active', 'review'], assignments__coder=user,
                                   assignments__superseded=False).order_by('-id').first()) if not manager else None
    current = (round_ or own_pending or participating or rounds.filter(state__in=['active', 'review']).order_by('-id').first()
               or rounds.order_by('-id').first())
    if not current:
        return show(3, '材料与编码本已准备好',
                    '可先培训、试编码并检验规则，也可按研究方案开始个人编码。' if writable else '等待负责人安排编码。',
                    '开始编码' if writable else '查看编码规则',
                    reverse('coding_entry', args=[project.pk]) if writable else
                    reverse('codebook', args=[project.pk, book.pk]))

    result.update(round_id=current.pk, round_name=current.name,
                  round_kind=current.kind, round_state=current.state)
    live = current.assignments.filter(superseded=False)
    own = live.filter(coder=user)
    own_total = own.count()
    own_pending = own.filter(status='draft').exists()
    team_work = current.kind != 'quick' and current.state == 'active' and not own_pending
    scope = 'team' if manager and (not own_total or team_work) else 'my'
    progress_query = live if scope == 'team' else own
    progress = progress_query.aggregate(total=Count('id'), submitted=Count('id', filter=Q(status='submitted')))
    result['progress'] = {**progress, 'remaining': progress['total'] - progress['submitted'], 'scope': scope}
    workbench = reverse('workbench', args=[current.pk])
    team_progress = reverse('progress', args=[project.pk])
    trial = current.kind in ('training', 'pilot')
    phase = 3 if trial else 4

    if current.kind == 'quick' and current.started_by_id != user.pk:
        return show(5 if current.state == 'closed' else 4,
                    '个人编码批次已结束' if current.state == 'closed' else '个人编码批次进行中',
                    '该个人批次由创建者操作，可查看项目进度。',
                    '查看项目进度', team_progress)

    if current.state == 'active':
        if writable and own_pending:
            return show(phase, '继续我的编码', '完成当前轮次的个人任务，已有记录会保留。',
                        '继续我的编码', workbench)
        if current.kind == 'quick':
            can_finish = writable and current.started_by_id == user.pk
            return show(phase, '本批个人编码已提交', '检查记录后，由本人确认结束本批编码。',
                        '检查并结束本批' if can_finish else '查看编码进度',
                        workbench + '#round-actions' if can_finish else team_progress)
        all_submitted = live.exists() and not live.filter(status='draft').exists()
        if manager and all_submitted:
            title = '本轮培训练习已全部提交' if current.kind == 'training' else '本轮独立编码已全部提交'
            if current.kind != 'training' and current.cycle > 1:
                title = '本轮编码已全部提交'
            return show(phase, title, '由负责人确认进入协商；本提示不会公开答案或改变轮次。',
                        '检查并进入复核', workbench + '#round-actions')
        if manager:
            return show(phase, '查看团队编码进度', '本轮仍有未提交任务，可在进度页面查看安排。',
                        '查看团队进度', team_progress)
        return show(phase, '我的任务已提交' if own_total else '等待安排编码任务',
                    '等待负责人组织下一步工作。', '查看编码进度', team_progress)

    if current.state == 'review':
        phase = 3 if trial else 5
        if not manager:
            return show(phase, '本轮正在协商复核', '由负责人和复核者整理本轮结论。',
                        '查看编码进度', team_progress)
        unit_ids = live.values('unit_id').distinct()
        total = unit_ids.count()
        done = current.decisions.filter(cycle=current.cycle, uncertain=False,
                                        unit_id__in=unit_ids).count()
        result['progress'] = {'submitted': done, 'total': total, 'remaining': total - done, 'scope': 'final'}
        if total and done == total:
            return show(phase, '本轮结论已填写', '检查结果后，由负责人确认结束本轮。',
                        '检查并结束本轮', workbench + '#round-actions')
        return show(phase, '继续协商定稿', '核对分歧、不确定项及尚未形成结论的单元。',
                    '继续协商定稿', reverse('review', args=[current.pk]))

    if trial:
        title = '本轮培训练习已结束' if current.kind == 'training' else '本轮试编码已结束'
        detail = ('回看练习中的问题并完善规则；培训练习不代替独立试编码。' if current.kind == 'training' else
                  '结合信度与分歧决定是否修订、复测；结束本轮不代表规则已经合格。')
        return show(3, title,
                    detail,
                    '查看本轮结果' if manager else '查看我的编码历史',
                    reverse('disagreements', args=[current.pk]) if manager else
                    reverse('history', args=[project.pk]) + '?round_id=' + str(current.pk))
    target = 'mine' if current.kind == 'quick' else 'final'
    return show(5, '本轮编码已结束', '可以导出成果；补充材料或后续轮次不需要重新创建项目。',
                '导出编码结果', reverse('exports', args=[project.pk]) + '?target=' + target + '&round=' + str(current.pk))


def preparation_guidance(project, role, workflow, page, *, material_preview=False, book_preview=False):
    """Describe preparation on the current page; confirmation stays in its existing POST form."""
    if workflow['has_rounds'] or role == 'viewer':
        return None
    has_units, has_codes = workflow['has_units'], workflow['has_codes']
    manager = workflow['can_manage']
    stage = 1 if not has_units else 2 if not has_codes else 3
    title, detail = workflow['title'], workflow['detail']
    label, url = '', ''
    if not has_units:
        title, detail = '第1步：导入材料', '上传文件，核对文本列和预览后确认导入。'
        if manager and page != 'materials':
            label, url = '下一步：导入材料', reverse('materials', args=[project.pk])
    elif not has_codes:
        title, detail = '材料已就绪', '接下来准备编码本，确定每个代码的含义。'
        if manager and workflow['book_id']:
            label, url = '下一步：准备编码本', reverse('codebook', args=[project.pk, workflow['book_id']])
    else:
        title, detail = '材料与编码本已就绪', '核对准备信息后，即可进入编码工作台。'
        label, url = '下一步：确认并开始', reverse('coding_entry', args=[project.pk])
    if page == 'codebook':
        if not has_codes and manager and workflow['book_id']:
            title, detail = '第2步：准备编码本', '导入已有编码本，或在本页添加代码。'
            label, url = '下一步：导入编码本', reverse('book_import', args=[project.pk, workflow['book_id']])
            stage = 2
        elif has_codes:
            title = '编码本已就绪'
    if not manager and not workflow['ready']:
        title = '等待导入编码材料' if not has_units else '等待准备编码本'
        detail = '请联系项目负责人完成准备，之后可继续编码。'
        label, url = '', ''
    if page == 'materials' and material_preview:
        stage, title = 1, '核对材料后，确认导入'
        detail, label, url = '检查下方文本列和预览，确认导入后继续下一步。', '', ''
    elif page == 'book_import':
        stage = 2
        title = '核对编码本后，确认导入' if book_preview else '第2步：导入编码本'
        detail = '核对定义和示例，确认后继续下一步。' if book_preview else '上传文件，再核对识别结果并确认导入。'
        label, url = '', ''
    elif page == 'quick_start' and workflow['ready']:
        stage, title = 3, '最后一步：确认并开始'
        detail, label, url = '核对下方材料和编码本，选择开始方式。', '', ''
    completed = [has_units and not material_preview, has_codes and page != 'book_import', False]
    return {'stage': stage, 'title': title, 'detail': detail, 'next_label': label, 'next_url': url,
            'confirm_anchor': '#book-import-confirm' if page == 'book_import' and book_preview else '',
            'steps': [{'number': index, 'label': name, 'complete': completed[index - 1], 'current': index == stage}
                      for index, name in enumerate(('导入材料', '准备编码本', '确认并开始'), 1)]}
