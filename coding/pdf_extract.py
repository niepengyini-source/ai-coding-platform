"""Limited local subprocess for untrusted text PDFs; never runs embedded content."""
import io
import json
import sys


def main():
    material = '--material' in sys.argv[1:]
    # Linux CI/deployment also has an address-space ceiling. Windows uses a job limit.
    job = None
    try:
        if sys.platform == 'win32':
            import ctypes
            from ctypes import wintypes
            class BASIC_LIMIT(ctypes.Structure):
                _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong), ('PerJobUserTimeLimit', ctypes.c_longlong),
                            ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                            ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                            ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]
            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [(name, ctypes.c_ulonglong) for name in ('ReadOperationCount', 'WriteOperationCount',
                            'OtherOperationCount', 'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]
            class EXTENDED_LIMIT(ctypes.Structure):
                _fields_ = [('BasicLimitInformation', BASIC_LIMIT), ('IoInfo', IO_COUNTERS),
                            ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                            ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CreateJobObjectW.restype = wintypes.HANDLE
            kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            job = kernel.CreateJobObjectW(None, None)
            limits = EXTENDED_LIMIT()
            limits.BasicLimitInformation.LimitFlags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
            limits.ProcessMemoryLimit = (512 if material else 256) * 1024 * 1024
            if not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
                raise ValueError('PDF安全解析环境不可用，请将规则另存为Word或Excel。')
        else:
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        from pypdf import PdfReader
        from pypdf.errors import DependencyError
        limit = (80 if material else 8) * 1024 * 1024
        raw = sys.stdin.buffer.read(limit + 1)
        if not raw.startswith(b'%PDF-') or len(raw) > limit:
            raise ValueError('PDF文件无效或超过文件大小限制。')
        try:
            reader = PdfReader(io.BytesIO(raw), strict=True)
        except DependencyError as error:
            raise ValueError('当前缺少此PDF加密格式所需的解析依赖，无法读取；不代表源文件损坏。请提供已获授权的未加密文字版。') from error
        if reader.is_encrypted:
            raise ValueError('不导入加密PDF，请先由你解密并另存。')
        if len(reader.pages) > (500 if material else 200):
            raise ValueError('材料PDF最多500页，请按章节拆分。' if material else '编码本PDF最多200页，请只保留规则部分。')
        texts, size, empty_pages = [], 0, 0
        pages = []
        for position, page in enumerate(reader.pages, 1):
            if material:
                contents = page.get_contents()
                content_size = len(contents.get_data()) if contents is not None else 0
                if content_size > 8 * 1024 * 1024:
                    raise ValueError(f'PDF第{position}页内容过大，请提供精简文字版。')
            text = page.extract_text() or ''
            if material:
                resources = page.get('/Resources', {})
                resources = resources.get_object() if hasattr(resources, 'get_object') else resources
                objects = resources.get('/XObject', {})
                objects = objects.get_object() if hasattr(objects, 'get_object') else objects
                has_image = any(obj.get_object().get('/Subtype') == '/Image' for obj in objects.values())
                meaningful = sum(character.isalnum() for character in text)
                page_data = {'page': position, 'text': text}
                if not meaningful and content_size:
                    page_data['review'] = 'missing_text'
                elif content_size and has_image and meaningful < 40:
                    page_data['review'] = 'sparse_text'
                pages.append(page_data)
            empty_pages += int(not text.strip())
            size += len(text)
            if size > 2 * 1024 * 1024:
                raise ValueError('PDF提取文字超过2MB，请拆分文件。')
            texts.append(text)
        text = '\n\n'.join(texts)
        if not text.strip() and not (material and any(page.get('review') for page in pages)):
            raise ValueError('PDF没有可读取文字，可能是扫描件。请先转为文字表格或Word；本阶段不提供OCR。')
        print(json.dumps({'pages': pages} if material else {'text': text, 'empty_pages': empty_pages}, ensure_ascii=False))
    except Exception as error:
        message = str(error) if isinstance(error, ValueError) else ('PDF解析失败或超过安全限制，请提供可读取的文字版。' if material else 'PDF解析失败或超过安全限制，请将规则另存为Word或Excel。')
        print(json.dumps({'error': message}, ensure_ascii=False))
        return 1
    finally:
        if job:
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(job)
    return 0


if __name__ == '__main__':
    sys.exit(main())
