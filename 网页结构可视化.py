#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# 模块说明
# 输入参数：HTTP POST 表单（起止日期、StructCfg、sync 开关等）
# 输出参数：JSON 任务状态；ECharts HTML 至 outputs/structure/
# 中文映射：
#   - structure_viz_core -> 快照缓存与 HTML 渲染
#   - viz_bundle_id -> 按 StructCfg + 日线文件 hash 分目录缓存
# 用途：浏览器配置并异步生成结构可视化；生成前可选增量 sync
# 使用说明：python 网页结构可视化.py 或 启动网页结构可视化.bat（默认 :8766）

from __future__ import annotations

import html
import json
import mimetypes
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlparse

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = BASE_DIR / 'scripts'
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from engine.data_portal import DataPortal
from strategy.backtest_params import STRUCT_FIELDS, parse_struct_params_from_mapping
from strategy.feature_store import _struct_cfg

from structure_viz_core import run_structure_plot, viz_bundle_id

try:
    from scripts.live_data_sync import sync_live_data
except ImportError:
    from live_data_sync import sync_live_data  # noqa: F401 — scripts/ 已在 sys.path

DEFAULT_DATA_DIR = BASE_DIR / 'data'
DEFAULT_DAILY_FILE = '上证指数日线加入实时数据.csv'
OUTPUT_DIR = BASE_DIR / 'outputs' / 'structure'
CACHE_ROOT = OUTPUT_DIR / 'cache'
HOST = '127.0.0.1'
PORT = 8766

_TASKS: dict[str, dict] = {}
_TASKS_LOCK = threading.Lock()


def _latest_trade_date_iso(data_dir: Path, daily_file: str) -> str:
    try:
        portal = DataPortal(str(data_dir))
        dates = portal.get_trade_dates(daily_file)
        if dates:
            return pd.Timestamp(dates[-1]).strftime('%Y-%m-%d')
    except Exception:
        pass
    return pd.Timestamp.today().strftime('%Y-%m-%d')


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
  <title>结构可视化</title>
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
    .preset {{ display: flex; gap: 10px; flex-wrap: wrap; margin: 8px 0 4px; }}
    .preset button {{ background: #f3f4f6; color: #374151; border: 1px solid #d1d5db; border-radius: 8px; padding: 6px 12px; font-size: 13px; cursor: pointer; }}
    .preset button:hover {{ background: #e5e7eb; }}
    button.primary {{ background: #2563eb; color: white; border: none; border-radius: 10px; padding: 12px 18px; font-size: 15px; cursor: pointer; }}
    button.primary:hover {{ background: #1d4ed8; }}
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


def _struct_fields_html() -> str:
    parts = ['<h3>中枢 / 结构参数（StructCfg）</h3>', '<div class="grid">']
    for field in STRUCT_FIELDS:
        name = field['name']
        hint = field.get('hint') or ''
        hint_html = f'<div class="field-hint">{html.escape(hint)}</div>' if hint else ''
        step = field.get('step') or ''
        step_attr = f' step="{html.escape(str(step))}"' if step else ''
        default = field.get('default', '')
        if field.get('type') == 'int':
            input_type = 'number'
            val = int(default)
        else:
            input_type = 'number' if field.get('type') == 'float' else 'text'
            val = default
        parts.append(f"""
      <div>
        <label for="{html.escape(name)}">{html.escape(field.get('label', name))}</label>
        <input type="{input_type}" id="{html.escape(name)}" name="{html.escape(name)}" value="{html.escape(str(val))}"{step_attr} />
        {hint_html}
      </div>""")
    parts.append('</div>')
    return ''.join(parts)


def _form_html(
    *,
    default_end: str,
    message: str = '',
    cls: str = 'muted',
) -> str:
    msg = f'<p class="{cls}">{html.escape(message)}</p>' if message else ''
    return f"""
<div class="card">
  <h1>日线结构可视化</h1>
  <p class="muted">生成全历史笔 + 中枢/range 框 + 悬停日结构 Tooltip（ECharts HTML）。提交后先增量同步上证 CSV（<code>data/实时数据运行.py</code>），再绘图；快照按 StructCfg + 日线文件分目录缓存。</p>
  {msg}
  <form id="vizForm" method="post" action="/run">
    <h3>绘图区间</h3>
    <div class="preset">
      <button type="button" id="preset2025">2025 年至今</button>
      <button type="button" id="preset2014">2014 全历史</button>
    </div>
    <div class="grid">
      <div>
        <label for="start_date">起始日期</label>
        <input type="date" id="start_date" name="start_date" value="2025-01-01" />
      </div>
      <div>
        <label for="end_date">结束日期</label>
        <input type="date" id="end_date" name="end_date" value="{html.escape(default_end)}" />
      </div>
      <div>
        <label for="data_dir">数据目录</label>
        <input type="text" id="data_dir" name="data_dir" value="{html.escape(str(DEFAULT_DATA_DIR))}" />
      </div>
      <div>
        <label for="daily_file">日线文件名</label>
        <input type="text" id="daily_file" name="daily_file" value="{html.escape(DEFAULT_DAILY_FILE)}" />
      </div>
    </div>
    {_struct_fields_html()}
    <h3>数据同步</h3>
    <div style="display:flex;flex-direction:column;gap:10px;margin-bottom:8px;">
      <div style="display:flex;align-items:center;gap:8px;">
        <input type="checkbox" id="sync_data" name="sync_data" value="1" checked />
        <label for="sync_data" style="margin:0">生成前同步上证行情（调用 data/实时数据运行.py）</label>
      </div>
      <div style="display:flex;align-items:center;gap:8px;">
        <input type="checkbox" id="force_sync_data" name="force_sync_data" value="1" checked />
        <label for="force_sync_data" style="margin:0">强制同步（忽略覆盖检查；无新增时脚本仍会快速跳过）</label>
      </div>
    </div>
    <p style="margin-top: 16px;"><button type="submit" class="primary">生成结构图</button></p>
  </form>
  <div id="runStatus" class="status muted"></div>
</div>

<script>
(function() {{
  const form = document.getElementById('vizForm');
  const statusEl = document.getElementById('runStatus');
  const endDefault = {json.dumps(default_end)};
  let pollTimer = null;

  document.getElementById('preset2025').addEventListener('click', () => {{
    document.getElementById('start_date').value = '2025-01-01';
    document.getElementById('end_date').value = endDefault;
  }});
  document.getElementById('preset2014').addEventListener('click', () => {{
    document.getElementById('start_date').value = '2014-01-01';
    document.getElementById('end_date').value = endDefault;
  }});

  function setStatus(html, cls='muted') {{
    statusEl.className = 'status ' + cls;
    statusEl.innerHTML = html;
  }}

  function html_escape(s) {{
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
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
        pollTimer = setTimeout(() => pollTask(taskId), 1500);
        return;
      }}
      if (data.status === 'failed') {{
        setStatus('<b>生成失败</b><br/>' + html_escape(data.message || ''), 'err');
        return;
      }}
      const r = data.result || {{}};
      let html = '<p class="ok"><b>生成完成</b></p>';
      html += `<p class="muted">缓存 bundle: ${{html_escape(r.viz_bundle_id || '')}} · 已缓存 ${{r.cache_trade_days ?? '-'}} 日 · 本次新算 ${{r.computed_new ?? 0}} 日</p>`;
      if (r.chart_url) html += `<p><a href="${{r.chart_url}}" target="_blank">打开结构图</a></p>`;
      if (r.sync_warnings && r.sync_warnings.length) {{
        html += '<p class="muted"><b>同步：</b><br/>' + r.sync_warnings.map(html_escape).join('<br/>') + '</p>';
      }}
      if (r.output) html += `<p class="muted">文件：${{html_escape(r.output)}}</p>`;
      setStatus(html, 'ok');
    }} catch (e) {{
      setStatus('轮询异常：' + String(e), 'err');
    }}
  }}

  form.addEventListener('submit', async (ev) => {{
    ev.preventDefault();
    if (pollTimer) clearTimeout(pollTimer);
    setStatus('已提交任务...', 'muted');
    try {{
      const fd = new FormData(form);
      if (!document.getElementById('sync_data').checked) fd.delete('sync_data');
      if (!document.getElementById('force_sync_data').checked) fd.delete('force_sync_data');
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


def _struct_params_from_form(data: dict) -> dict:
    flat: dict = {}
    for field in STRUCT_FIELDS:
        name = field['name']
        vals = data.get(name)
        if vals:
            flat[name] = vals[0]
    return parse_struct_params_from_mapping(flat)


def _run_task(task_id: str, params: dict) -> None:
    def progress(msg: str) -> None:
        with _TASKS_LOCK:
            t = _TASKS.get(task_id)
            if t and t.get('status') == 'running':
                t['message'] = msg

    sync_warnings: list[str] = []
    try:
        with _TASKS_LOCK:
            _TASKS[task_id]['status'] = 'running'
            _TASKS[task_id]['message'] = '正在同步上证行情...'

        if params.get('sync_data', True):
            progress('正在同步上证行情（data/实时数据运行.py）...')
            sync_ret = sync_live_data(
                BASE_DIR,
                data_dir=params['data_dir'],
                fail_soft=True,
                start_date=params['start_date'],
                end_date=params['end_date'],
                force=bool(params.get('force_sync_data', False)),
            )
            sync_warnings.extend(sync_ret.get('warnings') or [])
            if sync_ret.get('skipped'):
                sync_warnings.append('[sync-data] 本地 CSV 已覆盖所选区间，已跳过同步')
            elif sync_ret.get('ok'):
                sync_warnings.append('[sync-data] 同步完成')
            else:
                err = sync_ret.get('error') or '未知错误'
                sync_warnings.append(f'[sync-data] 同步未成功，将使用本地 CSV 继续：{err}')

        progress('正在生成结构图...')

        sp = params['struct_params']
        struct_cfg = _struct_cfg(SimpleNamespace(**sp))

        result = run_structure_plot(
            start_date=params['start_date'],
            end_date=params['end_date'],
            data_dir=params['data_dir'],
            struct_cfg=struct_cfg,
            daily_file=params['daily_file'],
            cache_root=CACHE_ROOT,
            html_root=OUTPUT_DIR,
            progress_cb=progress,
        )
        chart_url = f"/chart/{result['chart_filename']}"
        result['chart_url'] = chart_url
        result['sync_warnings'] = sync_warnings

        with _TASKS_LOCK:
            _TASKS[task_id]['status'] = 'done'
            _TASKS[task_id]['message'] = '完成'
            _TASKS[task_id]['result'] = result
    except Exception as e:
        with _TASKS_LOCK:
            _TASKS[task_id]['status'] = 'failed'
            _TASKS[task_id]['message'] = str(e)
            _TASKS[task_id]['detail'] = traceback.format_exc(limit=20)
            if sync_warnings:
                _TASKS[task_id]['sync_warnings'] = sync_warnings


def _serve_chart(handler: BaseHTTPRequestHandler, filename: str) -> None:
    safe = Path(unquote(filename)).name
    path = (OUTPUT_DIR / safe).resolve()
    base = OUTPUT_DIR.resolve()
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
            default_end = _latest_trade_date_iso(DEFAULT_DATA_DIR, DEFAULT_DAILY_FILE)
            body = _form_html(default_end=default_end)
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
        if parsed.path.startswith('/chart/'):
            parts = parsed.path.strip('/').split('/')
            if len(parts) >= 2:
                _serve_chart(self, parts[1])
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
            data_dir = (data.get('data_dir') or [str(DEFAULT_DATA_DIR)])[0].strip()
            daily_file = (data.get('daily_file') or [DEFAULT_DAILY_FILE])[0].strip()
            struct_params = _struct_params_from_form(data)
            struct_cfg = _struct_cfg(SimpleNamespace(**struct_params))
            params = {
                'start_date': start_date,
                'end_date': end_date,
                'data_dir': data_dir,
                'daily_file': daily_file,
                'struct_params': struct_params,
                'viz_bundle_id': viz_bundle_id(struct_cfg, daily_file),
                'sync_data': 'sync_data' in data,
                'force_sync_data': 'force_sync_data' in data,
            }
            task_id = uuid.uuid4().hex
            with _TASKS_LOCK:
                _TASKS[task_id] = {
                    'task_id': task_id,
                    'status': 'queued',
                    'message': '任务已提交，等待执行...',
                    'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                    'params': {
                        'start_date': start_date,
                        'end_date': end_date,
                        'viz_bundle_id': params['viz_bundle_id'],
                    },
                }
            th = threading.Thread(target=_run_task, args=(task_id, params), daemon=True)
            th.start()
            _json_response(self, 200, {'task_id': task_id, 'status': 'queued'})
        except Exception as e:
            _json_response(self, 500, {'error': str(e), 'detail': traceback.format_exc(limit=15)})

    def log_message(self, format, *args):
        return


if __name__ == '__main__':
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f'Python: {sys.executable}')
    print(f'结构可视化网页: http://{HOST}:{PORT}')
    print(f'数据目录默认: {DEFAULT_DATA_DIR}')
    print(f'HTML 输出: {OUTPUT_DIR}')
    print(f'快照缓存: {CACHE_ROOT}')
    server.serve_forever()
