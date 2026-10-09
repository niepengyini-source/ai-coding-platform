"""Windows-friendly WSGI entry point. Bind explicit interfaces, not the public internet."""
import argparse
import os
import ipaddress
import sys
import json
import logging
import atexit
import socket
from service_lock import ServiceLock, AlreadyRunning
from datetime import datetime, timezone
from pathlib import Path

parser = argparse.ArgumentParser(description='AI辅助编码平台：局域网服务')
parser.add_argument('--host', default=None, help='额外监听的局域网IPv4地址；始终保留127.0.0.1')
parser.add_argument('--port', type=int, default=None)
args = parser.parse_args()
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
from config import settings

host = args.host if args.host is not None else os.environ.get('PLATFORM_HOST', '127.0.0.1')
try:
    port = args.port if args.port is not None else int(os.environ.get('PLATFORM_PORT', '8000'))
    ip = ipaddress.ip_address(host)
except ValueError:
    raise SystemExit('地址或端口格式不正确，请检查启动参数及.env配置。')
if not (ip.is_private or ip.is_loopback) or ip.version != 4 or host == '0.0.0.0':
    raise SystemExit('仅监听明确的本机/局域网IPv4地址，不监听所有接口或公网。')
if not 1024 <= port <= 65535:
    raise SystemExit('应用端口需在1024至65535之间。')
try:
    instance_lock = ServiceLock(settings.RUNTIME_DIR / f'server_{port}.lock').__enter__()
except AlreadyRunning as error:
    raise SystemExit(str(error))
atexit.register(instance_lock.close)
if host not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append(host)
from config.wsgi import application
from waitress import create_server

listeners = [f'127.0.0.1:{port}']
if host != '127.0.0.1':
    listeners.append(f'{host}:{port}')
for address in ['127.0.0.1'] + ([host] if host != '127.0.0.1' else []):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1)
        if probe.connect_ex((address, port)) == 0:
            raise SystemExit('所选端口已有服务。请检查原服务或更换端口，不再启动另一个后台。')
logging.basicConfig()
try:
    platform_server = create_server(application, listen=' '.join(listeners), threads=8,
        max_request_body_size=10 * 1024 * 1024, channel_timeout=60,
        expose_tracebacks=False, clear_untrusted_proxy_headers=True)
except OSError:
    raise SystemExit('启动失败：地址可能已改变，或端口已被占用。请先打开原平台地址确认，不要重复启动；检查.env及当前局域网地址。')
try:
    # Record a stop target only after all requested interfaces have bound successfully.
    state_file = settings.RUNTIME_DIR / f'server_{port}.json'
    state_file.write_text(json.dumps({'pid': os.getpid(), 'port': port, 'listeners': listeners,
        'started_at': datetime.now(timezone.utc).isoformat(), 'executable': sys.executable,
        'base_executable': str(Path(sys.base_prefix) / 'python.exe'),
        'project_root': str(settings.BASE_DIR)}, ensure_ascii=False, indent=2), encoding='utf-8')
    print('AI辅助编码平台已启动。主机请保持开机，Ctrl+C停止。', flush=True)
    for addr in listeners:
        print('访问地址：http://' + addr, flush=True)
    print('不自动修改防火墙；仅在可信局域网使用。', flush=True)
    platform_server.run()
finally:
    platform_server.close()
    instance_lock.close()
