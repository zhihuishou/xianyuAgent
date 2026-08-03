"""
简单的日志查看器，运行后访问 http://localhost:8765
实时显示最新日志，每 2 秒自动刷新，支持 ANSI 颜色转换
"""
import os
import re
import glob
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from datetime import datetime

LOG_DIR = Path(__file__).parent / 'logs'
PORT = 8765


def strip_ansi(text: str) -> str:
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


def ansi_to_html(text: str) -> str:
    """把 loguru ANSI 颜色转成 HTML span"""
    color_map = {
        '32': 'color:#4ec94e',   # INFO  绿
        '31': 'color:#ff5f5f',   # ERROR 红
        '33': 'color:#f0c040',   # WARNING 黄
        '34': 'color:#5fa8ff',   # DEBUG 蓝
        '36': 'color:#5fd7ff',   # 模块名 青
        '1':  'font-weight:bold',
    }
    def replace(m):
        codes = m.group(1).split(';')
        styles = ';'.join(color_map[c] for c in codes if c in color_map)
        return f'<span style="{styles}">' if styles else ''

    text = re.sub(r'\x1b\[([0-9;]+)m', replace, text)
    text = re.sub(r'\x1b\[0m', '</span>', text)
    return text


def get_latest_log() -> str:
    logs = sorted(glob.glob(str(LOG_DIR / '*.log')), reverse=True)
    if not logs:
        return '暂无日志文件'
    with open(logs[0], encoding='utf-8', errors='replace') as f:
        lines = f.readlines()
    # 取最后 300 行
    lines = lines[-300:]
    html_lines = []
    for line in lines:
        line = line.rstrip()
        colored = ansi_to_html(line)
        html_lines.append(f'<div class="line">{colored}</div>')
    return '\n'.join(html_lines), os.path.basename(logs[0])


HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>闲鱼客服日志</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ background: #0d1117; color: #c9d1d9; font-family: 'Consolas','Courier New',monospace; font-size: 13px; }}
.toolbar {{ position: fixed; top: 0; left: 0; right: 0; background: #161b22; border-bottom: 1px solid #30363d;
           padding: 8px 16px; display: flex; align-items: center; gap: 16px; z-index: 10; }}
.toolbar h1 {{ font-size: 14px; color: #58a6ff; }}
.badge {{ background: #21262d; border: 1px solid #30363d; border-radius: 4px; padding: 2px 8px; font-size: 12px; }}
.badge.live {{ border-color: #3fb950; color: #3fb950; }}
#countdown {{ color: #8b949e; font-size: 12px; }}
#log-file {{ color: #8b949e; font-size: 12px; }}
#log-wrap {{ margin-top: 44px; padding: 12px 16px; }}
.line {{ padding: 1px 0; line-height: 1.6; white-space: pre-wrap; word-break: break-all; }}
.line:hover {{ background: #161b22; }}
</style>
</head>
<body>
<div class="toolbar">
  <h1>🐟 闲鱼客服日志</h1>
  <span class="badge live">● LIVE</span>
  <span class="badge" id="log-file">{log_file}</span>
  <span id="countdown">2s 后刷新</span>
  <span style="margin-left:auto;color:#8b949e">{now}</span>
</div>
<div id="log-wrap">
{lines}
</div>
<script>
let t = 2;
const cd = document.getElementById('countdown');
setInterval(() => {{
  t--;
  cd.textContent = t + 's 后刷新';
  if (t <= 0) {{ location.reload(); }}
}}, 1000);
window.onload = () => window.scrollTo(0, document.body.scrollHeight);
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # 关掉访问日志噪音

    def do_GET(self):
        if self.path == '/raw':
            logs = sorted(glob.glob(str(LOG_DIR / '*.log')), reverse=True)
            content = open(logs[0], encoding='utf-8', errors='replace').read() if logs else ''
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(content.encode())
            return

        result = get_latest_log()
        if isinstance(result, str):
            lines, log_file = result, '无日志'
        else:
            lines, log_file = result

        html = HTML_TEMPLATE.format(
            lines=lines,
            log_file=log_file,
            now=datetime.now().strftime('%H:%M:%S'),
        )
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode())


if __name__ == '__main__':
    LOG_DIR.mkdir(exist_ok=True)
    print(f'日志查看器启动：http://localhost:{PORT}')
    print(f'监控目录：{LOG_DIR}')
    HTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
