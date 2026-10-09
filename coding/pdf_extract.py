"""Limited local subprocess for untrusted text PDFs; never runs embedded content."""
import io
import json
import sys


def main():
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
            limits.ProcessMemoryLimit = 256 * 1024 * 1024
            if not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
                raise ValueError('PDF安全解析环境不可用，请将规则另存为Word或Excel。')
        else:
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        from pypdf import PdfReader
        raw = sys.stdin.buffer.read(8 * 1024 * 1024 + 1)
        if not raw.startswith(b'%PDF-') or len(raw) > 8 * 1024 * 1024:
            raise ValueError('PDF文件无效或超过8MB。')
        reader = PdfReader(io.BytesIO(raw), strict=True)
        if reader.is_encrypted:
            raise ValueError('不导入加密PDF，请先由你解密并另存。')
        if len(reader.pages) > 200:
            raise ValueError('编码本PDF最多200页，请只保留规则部分。')
        texts, size, empty_pages = [], 0, 0
        for page in reader.pages:
            text = page.extract_text() or ''
            empty_pages += int(not text.strip())
            size += len(text)
            if size > 2 * 1024 * 1024:
                raise ValueError('PDF提取文字超过2MB，请拆分文件。')
            texts.append(text)
        text = '\n\n'.join(texts)
        if not text.strip():
            raise ValueError('PDF没有可读取文字，可能是扫描件。请先转为文字表格或Word；本阶段不提供OCR。')
        print(json.dumps({'text': text, 'empty_pages': empty_pages}, ensure_ascii=False))
    except Exception as error:
        message = str(error) if isinstance(error, ValueError) else 'PDF解析失败或超过安全限制，请将规则另存为Word或Excel。'
        print(json.dumps({'error': message}, ensure_ascii=False))
        return 1
    finally:
        if job:
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(job)
    return 0


if __name__ == '__main__':
    sys.exit(main())
