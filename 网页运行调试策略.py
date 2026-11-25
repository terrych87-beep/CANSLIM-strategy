#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# 模块说明
# 输入参数：HTTP POST 表单（起止日期、资金、策略参数、sync/viz/rebuild-features 等）
# 输出参数：JSON 任务状态；子进程生成 outputs/backtest/{run_name}/
# 中文映射：
#   - /status -> 轮询回测进度
#   - ALL_PARAM_FIELDS -> 网页表单字段定义（来自 backtest_params）
# 用途：浏览器配置并异步触发 run_backtest.py
# 使用说明：python 网页运行调试策略.py 或 启动网页回测.bat

from __future__ import annotations

import html
import json
import mimetypes
import subprocess
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from strategy.backtest_params import ALL_PARAM_FIELDS, PARAM_GROUPS, parse_strategy_params_from_mapping

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = BASE_DIR / 'data'
DEFAULT_OUTPUT_DIR = BASE_DIR / 'outputs' / 'backtest'
HOST = '127.0.0.1'
PORT = 8765

_TASKS: dict[str, dict] = {}
_TASKS_LOCK = threading.Lock()


def _json_response(handler: BaseHTTPRequestHandler, code: int, payload: dict) -> None:
    b = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    handler.send_response(code)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(b)))
    handler.end_headers()
    handler.wfile.write(b)


def _page(body: str) -> bytes:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>本地回测调试</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 24px; color: #1f2937; }}
    .wrap {{ max-width: 980px; margin: 0 auto; }}
    h3 {{ margin: 20px 0 10px; font-size: 15px; color: #111827; }}
    .field-hint {{ font-size: 12px; color: #6b7280; margin-top: 4px; }}
    .card {{ border: 1px solid #e5e7eb; border-radius: 14px; padding: 20px; margin-bottom: 18px; box-shadow: 0 6px 24px rgba(0,0,0,0.05); }}
    h1 {{ margin-top: 0; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }}
    label {{ display: block; font-size: 14px; margin-bottom: 6px; color: #374151; }}
    input[type="text"], input[type="date"], input[type="number"] {{ width: 100%; box-sizing: border-box; padding: 10px 12px; border: 1px solid #d1d5db; border-radius: 10px; font-size: 14px; }}
    .check {{ display: flex; align-items: center; gap: 8px; margin-top: 8px; }}
    button {{ background: #2563eb; color: white; border: none; border-radius: 10px; padding: 12px 18px; font-size: 15px; cursor: pointer; }}
    button:hover {{ background: #1d4ed8; }}
    .muted {{ color: #6b7280; font-size: 13px; }}
    .ok {{ color: #065f46; }}
    .err {{ color: #b91c1c; white-space: pre-wrap; }}
    a {{ color: #2563eb; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .status {{ margin-top: 10px; font-size: 14px; }}
  </style>
</head>
<body>
  <div class="wrap">{body}</div>
</body>
</html>""".encode('utf-8')


def _human_error_message(e: Exception) -> str:
    msg = str(e) or repr(e)
    low = msg.lower()
    if 'getaddrinfo failed' in low or 'name resolution' in low:
        return f'网络/DNS 解析失败：{msg}'
    if 'connectionerror' in low or 'max retries exceeded' in low:
        return f'网络连接失败：{msg}'
    if 'permissionerror' in low or '文件被占用' in msg:
        return f'文件权限或占用问题：{msg}'
    return msg


from paths import prefer_local_drive_path


def _resolve_meta_path(stdout: str, output_dir: Path) -> Path:
    run_dir: Path | None = None
    for line in reversed((stdout or '').splitlines()):
        if line.startswith('backtest_output_dir='):
            run_dir = Path(line.split('=', 1)[1].strip())
            break
    if run_dir is not None:
        meta = prefer_local_drive_path(run_dir) / 'run_meta.json'
        if meta.exists():
            return meta
    output_dir = prefer_local_drive_path(output_dir)
    metas = sorted(output_dir.glob('*/run_meta.json'), key=lambda p: p.stat().st_mtime, reverse=True)
    if not metas:
        raise FileNotFoundError(f'未在 {output_dir} 下找到 run_meta.json')
    return metas[0]


def _strategy_param_sections_html() -> str:
    parts: list[str] = []
    for group in PARAM_GROUPS:
        parts.append(f'<h3>{html.escape(group["title"])}</h3>')
        parts.append('<div class="grid">')
        for field in group['fields']:
            name = field['name']
            hint = field.get('hint') or ''
            hint_html = f'<div class="field-hint">{html.escape(hint)}</div>' if hint else ''
            if field.get('type') == 'str' and name.startswith('OSC_RULES_MODEL'):
                default = str(field.get('default', 'v1'))
                opts = ''.join(
                    f'<option value="{v}"{" selected" if v == default else ""}>{v}</option>'
                    for v in ('v1', 'v2')
                )
                control = f'<select id="{name}" name="{name}">{opts}</select>'
            else:
                control = (
                    f'<input type="number" id="{name}" name="{name}" '
                    f'value="{field["default"]}" step="{field.get("step", "any")}" />'
                )
            parts.append(
                f'<div>'
                f'<label for="{name}">{html.escape(field["label"])}</label>'
                f'{control}'
                f'{hint_html}'
                f'</div>'
            )
        parts.append('</div>')
    return '\n'.join(parts)


def _strategy_params_from_form(data: dict) -> dict:
    raw = {spec['name']: (data.get(spec['name']) or [''])[0] for spec in ALL_PARAM_FIELDS}
    return parse_strategy_params_from_mapping(raw)


# _build_cli_cmd 方法说明
# 输入参数：params（表单解析后的参数字典）
# 输出参数：list[str]，run_backtest.py 子进程命令行
# 用途：将网页表单映射为 CLI 参数（含 --force-sync-data）
def _build_cli_cmd(params: dict) -> list[str]:
    data_dir = str(params.get('data_dir') or DEFAULT_DATA_DIR)
    output_dir = str(params.get('output_dir') or DEFAULT_OUTPUT_DIR)
    cmd = [
        sys.executable,
        str(BASE_DIR / 'run_backtest.py'),
        '--start-date',
        params['start_date'],
        '--end-date',
        params['end_date'],
        '--initial-cash',
        str(float(params['initial_cash'])),
        '--leverage',
        str(float(params['leverage'])),
        '--data-dir',
        data_dir,
        '--output-dir',
        output_dir,
    ]
    user_run_name = (params.get('run_name') or '').strip()
    if user_run_name:
        cmd.extend(['--run-name', user_run_name])
    env_file = params.get('env_file')
    if env_file:
        cmd.extend(['--env-file', str(env_file)])
    viz_asset = float(params.get('viz_initial_asset', params['initial_cash']))
    cmd.extend(['--viz-initial-asset', str(viz_asset)])
    if not bool(params.get('sync_data', True)):
        cmd.append('--no-sync-data')
    elif bool(params.get('force_sync_data', False)):
        cmd.append('--force-sync-data')
    if not bool(params.get('viz', True)):
        cmd.append('--no-viz')
    if bool(params.get('rebuild_features', False)):
        cmd.append('--rebuild-features')
    for key, val in (params.get('strategy_params') or {}).items():
        cmd.extend(['--strategy-param', f'{key}={val}'])
    return cmd


def _payload_from_run_meta(result: dict) -> dict:
    summary = result.get('summary') or {}
    viz = result.get('viz') or {}
    run_name = result.get('run_name', '')
    return {
        'return_pct': float(summary.get('total_return', 0) or 0) * 100.0,
        'trade_count': summary.get('trade_count'),
        'output_dir': result.get('output_dir'),
        'run_name': run_name,
        'trades_csv': result.get('trades_csv'),
        'summary_json': result.get('summary_json'),
        'sync_warnings': result.get('sync_warnings') or [],
        'trade_chart_url': f'/visuals/{run_name}/{Path(viz.get("trade_chart", "")).name}' if viz.get('trade_chart') else None,
        'equity_chart_url': f'/visuals/{run_name}/{Path(viz.get("equity_chart", "")).name}' if viz.get('equity_chart') else None,
    }


# _run_task 方法说明
# 输入参数：task_id、params
# 输出参数：无（更新 _TASKS 全局任务状态）
# 用途：后台线程执行 run_backtest 子进程并解析 run_meta.json
def _run_task(task_id: str, params: dict) -> None:
    with _TASKS_LOCK:
        _TASKS[task_id]['status'] = 'running'
        _TASKS[task_id]['message'] = '回测运行中（CLI 子进程）...'
    try:
        cmd = _build_cli_cmd(params)
        output_dir = Path(params.get('output_dir') or DEFAULT_OUTPUT_DIR)
        proc = subprocess.run(
            cmd,
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or '').strip()
            raise RuntimeError(detail or f'run_backtest.py 退出码 {proc.returncode}')
        meta_path = _resolve_meta_path(proc.stdout or '', output_dir)
        if not meta_path.exists():
            raise FileNotFoundError(f'回测完成但未找到元数据: {meta_path}')
        result = json.loads(meta_path.read_text(encoding='utf-8'))
        payload = _payload_from_run_meta(result)
        with _TASKS_LOCK:
            _TASKS[task_id]['status'] = 'done'
            _TASKS[task_id]['message'] = '运行完成'
            _TASKS[task_id]['result'] = payload
            _TASKS[task_id]['cli_stdout'] = (proc.stdout or '')[-4000:]
    except Exception as e:
        with _TASKS_LOCK:
            _TASKS[task_id]['status'] = 'failed'
            _TASKS[task_id]['message'] = _human_error_message(e)
            _TASKS[task_id]['traceback'] = traceback.format_exc(limit=25)


def _form_html(message: str = '', cls: str = 'muted') -> str:
    msg = f'<p class="{cls}">{html.escape(message)}</p>' if message else ''
    return f"""
<div class="card">
  <h1>本地回测调试</h1>
  <p class="muted">提交后通过 CLI 子进程运行 <code>run_backtest.py</code>（与命令行一致）；默认同步上证 1m/5m/30m/1d/1w（失败则用本地 CSV 继续）。</p>
  <p class="muted">合约表 <code>IF_front_next_runpoints.csv</code> 由 <code>data/实时数据运行.py</code>（或 <code>data/期货合约数据获取.py</code>）同步，本页回测不同步。</p>
  {msg}
  <form id="runForm" method="post" action="/run">
    <div class="grid">
      <div>
        <label for="start_date">起始日期</label>
        <input type="date" id="start_date" name="start_date" value="2021-01-01" />
      </div>
      <div>
        <label for="end_date">结束日期</label>
        <input type="date" id="end_date" name="end_date" value="2022-01-01" />
      </div>
      <div>
        <label for="initial_cash">初始资金</label>
        <input type="number" id="initial_cash" name="initial_cash" value="10000000" step="10000" />
      </div>
      <div>
        <label for="leverage">杠杆倍数（最大名义仓位倍数）</label>
        <input type="number" id="leverage" name="leverage" value="4" step="0.1" min="0.1" />
      </div>
      <div>
        <label for="run_name">run 目录名（可留空：运行时间+回放区间+收益率）</label>
        <input type="text" id="run_name" name="run_name" value="" placeholder="留空则 20260605_114530_20160101-20260605_R+10.34pct" />
      </div>
      <div>
        <label for="data_dir">数据目录</label>
        <input type="text" id="data_dir" name="data_dir" value="{html.escape(str(DEFAULT_DATA_DIR))}" />
      </div>
    </div>
    {_strategy_param_sections_html()}
    <div class="check">
      <input type="checkbox" id="sync_data" name="sync_data" value="1" checked />
      <label for="sync_data" style="margin:0">允许回测前同步上证行情（本地已覆盖区间时自动跳过）</label>
    </div>
    <div class="check">
      <input type="checkbox" id="force_sync_data" name="force_sync_data" value="1" />
      <label for="force_sync_data" style="margin:0">强制同步数据（--force-sync-data，忽略覆盖检查）</label>
    </div>
    <div class="check">
      <input type="checkbox" id="viz" name="viz" value="1" checked />
      <label for="viz" style="margin:0">回测后生成可视化（outputs/.../visuals/）</label>
    </div>
    <div class="check">
      <input type="checkbox" id="rebuild_features" name="rebuild_features" value="1" />
      <label for="rebuild_features" style="margin:0">重建特征表（--rebuild-features；14:57 break_up 或 1m 数据更新后勾选）</label>
    </div>
    <p style="margin-top: 16px;"><button type="submit">运行回测</button></p>
  </form>
  <div id="runStatus" class="status muted"></div>
</div>

<script>
(function() {{
  const form = document.getElementById('runForm');
  const statusEl = document.getElementById('runStatus');
  let pollTimer = null;

  function setStatus(html, cls='muted') {{
    statusEl.className = 'status ' + cls;
    statusEl.innerHTML = html;
  }}

  async function pollTask(taskId) {{
    try {{
      const resp = await fetch(`/status?task_id=${{encodeURIComponent(taskId)}}`);
      const data = await resp.json();
      if (!resp.ok) {{
        setStatus('查询状态失败：' + (data.error || resp.statusText), 'err');
        return;
      }}
      if (data.status === 'queued' || data.status === 'running') {{
        setStatus(html_escape(data.message || '任务运行中...'), 'muted');
        pollTimer = setTimeout(() => pollTask(taskId), 2000);
        return;
      }}
      if (data.status === 'failed') {{
        setStatus('<b>运行失败</b><br/>' + html_escape(data.message || ''), 'err');
        return;
      }}
      const r = data.result || {{}};
      let html = `<p class="ok"><b>运行完成</b> 收益率 ${{Number(r.return_pct || 0).toFixed(2)}}%，成交 ${{r.trade_count ?? '-'}} 笔</p>`;
      html += `<p class="muted">输出：${{html_escape(r.output_dir || '')}}</p>`;
      if (r.sync_warnings && r.sync_warnings.length) {{
        html += '<p class="muted"><b>同步警告：</b><br/>' + r.sync_warnings.map(html_escape).join('<br/>') + '</p>';
      }}
      if (r.trade_chart_url) html += `<p><a href="${{r.trade_chart_url}}" target="_blank">打开交易点可视化</a></p>`;
      if (r.equity_chart_url) html += `<p><a href="${{r.equity_chart_url}}" target="_blank">打开收益率走势图</a></p>`;
      if (r.trades_csv) html += `<p class="muted">trades: ${{html_escape(r.trades_csv)}}</p>`;
      setStatus(html, 'ok');
    }} catch (e) {{
      setStatus('轮询异常：' + String(e), 'err');
    }}
  }}

  function html_escape(s) {{
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }}

  form.addEventListener('submit', async (ev) => {{
    ev.preventDefault();
    if (pollTimer) clearTimeout(pollTimer);
    setStatus('已提交任务...', 'muted');
    try {{
      const fd = new FormData(form);
      if (!document.getElementById('sync_data').checked) fd.delete('sync_data');
      if (!document.getElementById('force_sync_data').checked) fd.delete('force_sync_data');
      if (!document.getElementById('viz').checked) fd.delete('viz');
      const resp = await fetch('/run', {{ method: 'POST', body: new URLSearchParams(fd) }});
      const data = await resp.json();
      if (!resp.ok) {{
        setStatus('提交失败：' + (data.error || resp.statusText), 'err');
        return;
      }}
      pollTask(data.task_id);
    }} catch (e) {{
      setStatus('提交异常：' + String(e), 'err');
    }}
  }});
}})();
</script>
"""


def _serve_visual_file(handler: BaseHTTPRequestHandler, run_name: str, filename: str) -> None:
    safe_run = Path(run_name).name
    safe_file = Path(unquote(filename)).name
    path = (DEFAULT_OUTPUT_DIR / safe_run / 'visuals' / safe_file).resolve()
    base = (DEFAULT_OUTPUT_DIR / safe_run / 'visuals').resolve()
    if not str(path).startswith(str(base)) or not path.exists():
        handler.send_error(404)
        return
    data = path.read_bytes()
    ctype = mimetypes.guess_type(str(path))[0] or 'application/octet-stream'
    handler.send_response(200)
    handler.send_header('Content-Type', f'{ctype}; charset=utf-8' if ctype.startswith('text') else ctype)
    handler.send_header('Content-Length', str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ('/', ''):
            body = _form_html()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(_page(body))
            return
        if parsed.path == '/status':
            q = parse_qs(parsed.query)
            task_id = (q.get('task_id') or [''])[0].strip()
            if not task_id:
                _json_response(self, 400, {'error': '缺少 task_id'})
                return
            with _TASKS_LOCK:
                task = _TASKS.get(task_id)
            if task is None:
                _json_response(self, 404, {'error': '任务不存在'})
                return
            _json_response(self, 200, task)
            return
        if parsed.path.startswith('/visuals/'):
            parts = parsed.path.strip('/').split('/')
            if len(parts) >= 3:
                _serve_visual_file(self, parts[1], parts[2])
                return
        self.send_error(404)

    def do_POST(self):
        if self.path != '/run':
            self.send_error(404)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            raw = self.rfile.read(length).decode('utf-8')
            data = parse_qs(raw)
            start_date = (data.get('start_date') or [''])[0].strip()
            end_date = (data.get('end_date') or [''])[0].strip()
            if not start_date or not end_date:
                _json_response(self, 400, {'error': '请填写起止日期'})
                return
            run_name_raw = (data.get('run_name') or [''])[0].strip()
            params = {
                'start_date': start_date,
                'end_date': end_date,
                'initial_cash': float((data.get('initial_cash') or ['10000000'])[0]),
                'leverage': float((data.get('leverage') or ['4'])[0]),
                'data_dir': (data.get('data_dir') or [str(DEFAULT_DATA_DIR)])[0].strip(),
                'output_dir': str(DEFAULT_OUTPUT_DIR),
                'run_name': run_name_raw or None,
                'sync_data': 'sync_data' in data,
                'force_sync_data': 'force_sync_data' in data,
                'viz': 'viz' in data,
                'rebuild_features': 'rebuild_features' in data,
                'env_file': str(Path((data.get('data_dir') or [str(DEFAULT_DATA_DIR)])[0].strip()) / '.env'),
                'viz_initial_asset': float((data.get('initial_cash') or ['10000000'])[0]),
                'strategy_params': _strategy_params_from_form(data),
            }
            task_id = uuid.uuid4().hex
            with _TASKS_LOCK:
                _TASKS[task_id] = {
                    'task_id': task_id,
                    'status': 'queued',
                    'message': '任务已提交，等待执行...',
                    'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                }
            th = threading.Thread(target=_run_task, args=(task_id, params), daemon=True)
            th.start()
            _json_response(self, 200, {'task_id': task_id, 'status': 'queued'})
        except Exception as e:
            _json_response(self, 500, {'error': _human_error_message(e), 'detail': traceback.format_exc(limit=15)})

    def log_message(self, format, *args):
        return


if __name__ == '__main__':
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f'Python: {sys.executable}')
    print(f'本地回测网页入口: http://{HOST}:{PORT}')
    print(f'数据目录默认: {DEFAULT_DATA_DIR}')
    print(f'输出目录: {DEFAULT_OUTPUT_DIR}')
    server.serve_forever()


