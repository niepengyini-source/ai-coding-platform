"""Display source facts, without inferring recipients, identities or coding labels."""
import hashlib
import json
from collections import defaultdict

from django.db.models import F, TextField
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast

from .models import SourceRecord


DISPLAY_FIELDS = (
    ('speaker', '发言者'), ('speaker_type', '身份'), ('role', '角色'), ('time', '时间'),
    ('message_id', '消息编号'), ('reference_id', '引用消息编号'),
    ('reference_preview', '引用内容预览'), ('recipient', '明确接收者'),
)
# Each level must match one heading uniquely; contents are never used to guess mappings.
ALIASES = {
    'speaker': [('sender_name', 'speaker_name', '发言者', '说话人', '姓名'),
                ('sender_username', 'username'), ('sender_id', 'speaker_id', 'speaker')],
    'speaker_type': [('sender_type', 'speaker_type', '发言者类型', '身份', '说话人类型')],
    'role': [('agent_role', 'speaker_role', 'role', '角色')],
    'time': [('created_at', 'timestamp', 'sent_at', '发言时间', '时间')],
    'message_id': [('message_id', 'msg_id', '消息编号', '消息id')],
    'reference_id': [('source_message_id', 'reply_to_message_id', 'in_reply_to', '引用消息编号')],
    'reference_preview': [('source_content_preview_snapshot', 'reply_preview', '引用内容预览')],
    'recipient': [('recipient_name', 'receiver_name', '接收者', '接收对象'),
                  ('recipient_id', 'receiver_id', 'to_user_id')],
}
IDENTITIES = {'sendertype.student': '学生', 'student': '学生', '学生': '学生',
              'sendertype.agent': 'AI', 'agent': 'AI', 'ai': 'AI',
              'human': '人类', 'person': '人类', '人类': '人类'}


def normalized_heading(value):
    return ''.join(char for char in value.casefold() if char.isalnum())


def suggested_display_mapping(headers):
    result = {}
    for name, _ in DISPLAY_FIELDS:
        result[name] = ''
        for aliases in ALIASES[name]:
            choices = [header for header in headers if normalized_heading(header) in
                       {normalized_heading(alias) for alias in aliases}]
            if choices:
                result[name] = choices[0] if len(choices) == 1 else ''
                break
    return result


def available_headers(record):
    return list(dict.fromkeys([record.document.text_column, *record.metadata]))


def display_mapping(document, headers):
    configured = document.display_mapping
    return ({name: configured.get(name, '') if configured.get(name, '') in headers else ''
             for name, _ in DISPLAY_FIELDS} if configured else suggested_display_mapping(headers))


def mapping_token(document):
    return hashlib.sha256(json.dumps(document.display_mapping, sort_keys=True,
        ensure_ascii=False).encode('utf-8')).hexdigest()


def source_value(record, document, column):
    value = record.text if column == document.text_column else record.metadata.get(column, '')
    return str(value) if value is not None else ''


def attach_source_information(records, document):
    """Attach safe summaries and resolve references in one query for this document batch."""
    records = list(records)
    headers = list(dict.fromkeys([document.text_column, *(key for rec in records for key in rec.metadata)]))
    mapping = display_mapping(document, headers)
    references = defaultdict(list)
    reference_ids = {source_value(rec, document, mapping['reference_id']) for rec in records
                     if mapping['reference_id'] and source_value(rec, document, mapping['reference_id'])}
    if reference_ids and mapping['message_id']:
        key = (F('text') if mapping['message_id'] == document.text_column else
               KeyTextTransform(mapping['message_id'], 'metadata'))
        matches = (SourceRecord.objects.filter(document_id=document.pk,
                    context_key__in={rec.context_key for rec in records})
                   .annotate(source_message_key=Cast(key, output_field=TextField()))
                   .filter(source_message_key__in=reference_ids))
        for rec in matches:
            references[(rec.context_key, source_value(rec, document, mapping['message_id']))].append(rec)
    for rec in records:
        items = []
        for name, label in DISPLAY_FIELDS:
            if name in ('message_id', 'reference_id', 'reference_preview'):
                continue
            value = source_value(rec, document, mapping[name]) if mapping[name] else ''
            if value:
                items.append({'label': label, 'value': IDENTITIES.get(value.casefold(), value)
                              if name == 'speaker_type' else value})
        ref_id = source_value(rec, document, mapping['reference_id']) if mapping['reference_id'] else ''
        preview = source_value(rec, document, mapping['reference_preview']) if mapping['reference_preview'] else ''
        matches = references.get((rec.context_key, ref_id), [])
        reference = None
        if ref_id or preview:
            reference = {'label': '引用消息', 'text': preview or ref_id, 'position': None}
            if len(matches) == 1:
                reference.update(text=matches[0].text, position=matches[0].position)
            elif len(matches) > 1:
                reference['label'] = '引用消息（无法唯一定位）'
            elif ref_id:
                reference['label'] = '引用消息（未定位）'
        rec.source_information = {'items': items, 'reference': reference}
    return records
