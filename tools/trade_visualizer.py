#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# 模块说明
# 输入参数：run_output_dir（含 trades.csv）；data_dir（K 线 CSV）
# 输出参数：run_dir/visuals/ 下 ECharts HTML（成交点、收益率等）
# 用途：回测后可视化；由 run_backtest --viz 与 生成交易可视化.py 调用
# 使用说明：from tools.trade_visualizer import render_backtest_visuals

from __future__ import annotations

import json
from bisect import bisect_left
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

ECHARTS = 'https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js'
DEFAULT_KLINE_NAME = '上证指数30分钟加入实时数据.csv'
DEFAULT_INDEX_DAILY_NAME = '上证指数日线加入实时数据.csv'
COLOR_UP = '#ef5350'
COLOR_DOWN = '#26a69a'


def parse_dt(v):
    if v is None:
        return None
    s = v.strftime('%Y-%m-%d %H:%M:%S') if isinstance(v, datetime) else str(v).strip()
    if not s:
        return None
    for fmt in (
        '%Y/%m/%d %H:%M',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M',
        '%Y/%m/%d %H:%M:%S',
        '%Y-%m-%d',
        '%Y/%m/%d',
    ):
        try:
            return datetime.strptime(s, fmt).replace(second=0, microsecond=0)
        except ValueError:
            pass
    return None


def infer_tag(path: Path) -> str:
    return path.stem


def load_kline(data_dir: Path, kline_filename: str = DEFAULT_KLINE_NAME):
    kline_csv = data_dir / kline_filename
    if not kline_csv.exists():
        raise FileNotFoundError(f'K线文件不存在: {kline_csv}')
    df = pd.read_csv(kline_csv, encoding='utf-8-sig').rename(
        columns={'交易时间': 'datetime', '开盘价': 'open', '最高价': 'high', '最低价': 'low', '收盘价': 'close', '成交量': 'volume'}
    )
    out = []
    for row in df[['datetime', 'open', 'high', 'low', 'close', 'volume']].itertuples(index=False):
        dt = parse_dt(row.datetime)
        if dt is None:
            continue
        try:
            out.append({
                'dt': dt,
                'open': float(row.open),
                'high': float(row.high),
                'low': float(row.low),
                'close': float(row.close),
                'volume': float(row.volume),
            })
        except Exception:
            continue
    return sorted(out, key=lambda x: x['dt'])


def _safe_float(v, default=None):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return default
    s = str(v).strip()
    if not s:
        return default
    try:
        return float(s)
    except Exception:
        return default


def _map_trade_row(row: dict) -> dict | None:
    dt = parse_dt(row.get('交易时间') or row.get('时间'))
    if dt is None:
        return None
    action = str(row.get('动作') or row.get('类型') or '').strip()
    side = str(row.get('方向') or '').strip()
    if action in ('开', '开仓'):
        kind = '开仓'
    elif action in ('平', '平仓'):
        kind = '平仓'
    else:
        kind = action
    if side in ('多', 'long'):
        side = '多'
    elif side in ('空', 'short'):
        side = '空'
    pnl = _safe_float(row.get('盈亏金额', row.get('pnl')), 0.0) or 0.0
    wait_raw = row.get('等待bar数', row.get('等待BAR数'))
    wait_bars = _safe_float(wait_raw, None)
    if wait_bars is not None:
        wait_bars = int(wait_bars)
    lots_raw = _safe_float(row.get('手数'), None)
    lots = int(lots_raw) if lots_raw is not None else None
    price = _safe_float(row.get('成交价'), None)
    return {
        'dt': dt,
        'kind': kind,
        'side': side,
        'reason': str(row.get('原因', '') or '').strip(),
        'pnl': pnl,
        'price': price,
        'lots': lots,
        'wait_bars': wait_bars,
        'position_type': str(row.get('持仓类型', '') or '').strip(),
    }


def load_trades(path: Path):
    rows = pd.read_csv(path, encoding='utf-8-sig').to_dict('records')
    out = []
    for row in rows:
        mapped = _map_trade_row(row)
        if mapped:
            out.append(mapped)
    return sorted(out, key=lambda x: x['dt'])


def ema(vals, n):
    a = 2.0 / (n + 1.0)
    out, prev = [], None
    for x in vals:
        prev = x if prev is None else prev + a * (x - prev)
        out.append(prev)
    return out


def calc_macd(closes):
    dif = [a - b for a, b in zip(ema(closes, 12), ema(closes, 26))]
    dea = ema(dif, 9)
    macd = [2 * (d - e) for d, e in zip(dif, dea)]
    return dif, dea, macd


def calc_ma(values, n):
    out, run_sum = [], 0.0
    for i, v in enumerate(values):
        run_sum += v
        if i >= n:
            run_sum -= values[i - n]
        out.append(None if i + 1 < n else run_sum / float(n))
    return out


def nearest_bar_index(dt_list, target_dt, tolerance_minutes=31):
    idx = bisect_left(dt_list, target_dt)
    cands = [i for i in (idx, idx - 1) if 0 <= i < len(dt_list)]
    if not cands:
        return None
    best = min(cands, key=lambda i: abs((dt_list[i] - target_dt).total_seconds()))
    return best if abs(dt_list[best] - target_dt) <= timedelta(minutes=tolerance_minutes) else None


def write_html(path: Path, title: str, body: str, *, trade_panels: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if trade_panels:
        style = (
            'body{margin:0}#wrap{position:relative;width:100vw;height:95vh}'
            '#chart{width:100%;height:100%}'
            '.panel{position:absolute;left:68px;z-index:20;pointer-events:none;'
            'background:rgba(255,255,255,0.9);border:1px solid #bbb;'
            'padding:6px 10px;font:12px/1.55 "Microsoft YaHei",sans-serif;color:#222;min-width:170px}'
            '#ohlc-panel{top:52px}#macd-panel{top:calc(72% + 4px)}#trade-panel{top:128px;display:none}'
        )
        body_html = (
            "<div id='wrap'>"
            "<div id='ohlc-panel' class='panel'></div>"
            "<div id='macd-panel' class='panel'></div>"
            "<div id='trade-panel' class='panel'></div>"
            "<div id='chart'></div></div>"
        )
    else:
        style = 'body{margin:0}#chart{width:100vw;height:95vh}'
        body_html = "<div id='chart'></div>"
    html = (
        f"<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{title}</title><script src='{ECHARTS}'></script>"
        f"<style>{style}</style></head>"
        f"<body>{body_html}<script>{body}</script></body></html>"
    )
    path.write_text(html, encoding='utf-8')
    return path


def build_daily_closes(kline: list) -> dict:
    daily = {}
    for bar in kline:
        daily[bar['dt'].date()] = bar['close']
    return daily


def load_index_daily_closes(data_dir: Path, filename: str = DEFAULT_INDEX_DAILY_NAME) -> dict:
    """上证日线收盘价（按自然日），用于收益率图基准，勿用 equity 中的 IF 收盘估价。"""
    path = Path(data_dir) / filename
    if not path.exists():
        return {}
    df = pd.read_csv(path, encoding='utf-8-sig')
    col_time = '交易时间' if '交易时间' in df.columns else 'datetime'
    col_close = '收盘价' if '收盘价' in df.columns else 'close'
    if col_time not in df.columns or col_close not in df.columns:
        return {}
    daily: dict = {}
    for t_raw, c_raw in zip(df[col_time], df[col_close]):
        dt = parse_dt(t_raw)
        if dt is None:
            continue
        try:
            daily[dt.date()] = float(c_raw)
        except (TypeError, ValueError):
            continue
    return daily


def render_trade_chart(
    *,
    trade_csv: Path,
    output_dir: Path,
    data_dir: Path,
    output_tag: str | None = None,
    kline_filename: str = DEFAULT_KLINE_NAME,
):
    tag = str(output_tag or infer_tag(trade_csv))
    out_path = Path(output_dir) / f'{tag}_交易点可视化.html'
    kline, trades = load_kline(Path(data_dir), kline_filename), load_trades(trade_csv)
    if not kline:
        raise RuntimeError('K线数据为空')
    dt_list = [x['dt'] for x in kline]
    closes = [x['close'] for x in kline]
    dif, dea, macd = calc_macd(closes)
    categories = [x['dt'].strftime('%Y-%m-%d %H:%M') for x in kline]
    opens = [x['open'] for x in kline]
    highs = [x['high'] for x in kline]
    lows = [x['low'] for x in kline]
    ohlc = [[x['open'], x['close'], x['low'], x['high']] for x in kline]
    pts = {'buy': [], 'sell': [], 'win': [], 'loss': []}
    bar_trades: dict[str, dict] = {}
    for t in trades:
        bi = nearest_bar_index(dt_list, t['dt'])
        if bi is None:
            continue
        bar_trades[str(bi)] = {
            'time': t['dt'].strftime('%Y-%m-%d %H:%M'),
            'price': t['price'],
            'waitBars': t['wait_bars'],
            'lots': t['lots'],
            'pnl': t['pnl'] if t['kind'] == '平仓' else None,
            'reason': t['reason'],
            'posType': t['position_type'],
            'kind': t['kind'],
        }
        p = {'value': [categories[bi], float(kline[bi]['close'])]}
        if t['kind'] == '开仓' and t['side'] == '多':
            pts['buy'].append(p)
        elif t['kind'] == '开仓' and t['side'] == '空':
            pts['sell'].append(p)
        elif t['kind'] == '平仓' and t['pnl'] >= 0:
            pts['win'].append(p)
        elif t['kind'] == '平仓':
            pts['loss'].append(p)
    hits = [categories.index(p['value'][0]) for arr in pts.values() for p in arr if p['value'][0] in categories]
    total = len(categories)
    start_i = max(0, (max(hits) - min(220, total) + 1) if hits else (total - min(180, total)))
    start_pct = (start_i / max(total, 1)) * 100.0
    body = """
const categories=%s, opens=%s, highs=%s, lows=%s, closes=%s, ohlc=%s, dif=%s, dea=%s, macd=%s, macPos=%s, macNeg=%s, ma5=%s, ma13=%s, ma144=%s, buy=%s, sell=%s, win=%s, loss=%s, barTrades=%s;
const COLOR_UP='%s', COLOR_DOWN='%s';
const fmt=(v)=>Number(v).toFixed(2);
const fmtWait=(v)=>(v===null||v===undefined||v==='')?'-':String(v);
function updatePanels(idx){
  if(idx==null||idx<0||idx>=categories.length)return;
  document.getElementById('ohlc-panel').innerHTML=
    `<div>${categories[idx]}</div>`+
    `开 ${fmt(opens[idx])} &nbsp; 高 ${fmt(highs[idx])}<br/>`+
    `低 ${fmt(lows[idx])} &nbsp; 收 ${fmt(closes[idx])}`;
  document.getElementById('macd-panel').innerHTML=
    `DIF ${fmt(dif[idx])} &nbsp; DEA ${fmt(dea[idx])}<br/>MACD ${fmt(macd[idx])}`;
  const tp=barTrades[String(idx)], tradeEl=document.getElementById('trade-panel');
  if(tp){
    tradeEl.style.display='block';
    let html=`<b>成交</b><br/>交易时间 ${tp.time}<br/>成交价 ${fmt(tp.price)}<br/>等待BAR数 ${fmtWait(tp.waitBars)}<br/>成交手数 ${tp.lots??'-'}<br/>`;
    if(tp.pnl!=null)html+=`盈亏金额 ${fmt(tp.pnl)}<br/>`;
    html+=`原因 ${tp.reason||'-'}<br/>持仓类型 ${tp.posType||'-'}`;
    tradeEl.innerHTML=html;
  }else{tradeEl.style.display='none';}
}
const chart=echarts.init(document.getElementById('chart'));
chart.setOption({animation:false,legend:{top:8,data:['K线','MA5','MA13','MA144','买多','卖空','平赚','平亏','MACD+','MACD-','DIF','DEA']},axisPointer:{link:[{xAxisIndex:'all'}],label:{show:false}},tooltip:{show:false},grid:[{left:60,right:40,top:50,height:'58%%'},{left:60,right:40,top:'72%%',height:'18%%'}],xAxis:[{type:'category',data:categories,boundaryGap:false,axisPointer:{show:true}},{type:'category',gridIndex:1,data:categories,boundaryGap:false,axisLabel:{show:false},axisPointer:{show:true}}],yAxis:[{scale:true},{gridIndex:1,scale:true,splitNumber:3}],dataZoom:[{type:'inside',xAxisIndex:[0,1],start:%s,end:100},{type:'slider',xAxisIndex:[0,1],top:'92%%',start:%s,end:100}],series:[{name:'K线',type:'candlestick',data:ohlc,itemStyle:{color:COLOR_UP,color0:COLOR_DOWN,borderColor:COLOR_UP,borderColor0:COLOR_DOWN}},{name:'MA5',type:'line',data:ma5,showSymbol:false,lineStyle:{width:1}},{name:'MA13',type:'line',data:ma13,showSymbol:false,lineStyle:{width:1}},{name:'MA144',type:'line',data:ma144,showSymbol:false,lineStyle:{width:1}},{name:'买多',type:'scatter',symbol:'triangle',symbolSize:18,data:buy,label:{show:true,formatter:'买'},tooltip:{show:false}},{name:'卖空',type:'scatter',symbol:'triangle',symbolRotate:180,symbolSize:18,data:sell,label:{show:true,formatter:'空'},tooltip:{show:false}},{name:'平赚',type:'scatter',symbol:'circle',symbolSize:14,data:win,itemStyle:{color:COLOR_UP},label:{show:true,formatter:'盈',color:'#fff'},tooltip:{show:false}},{name:'平亏',type:'scatter',symbol:'circle',symbolSize:14,data:loss,itemStyle:{color:COLOR_DOWN},label:{show:true,formatter:'亏',color:'#fff'},tooltip:{show:false}},{name:'MACD+',type:'bar',xAxisIndex:1,yAxisIndex:1,data:macPos,itemStyle:{color:COLOR_UP}},{name:'MACD-',type:'bar',xAxisIndex:1,yAxisIndex:1,data:macNeg,itemStyle:{color:COLOR_DOWN}},{name:'DIF',type:'line',xAxisIndex:1,yAxisIndex:1,data:dif,showSymbol:false,lineStyle:{width:1}},{name:'DEA',type:'line',xAxisIndex:1,yAxisIndex:1,data:dea,showSymbol:false,lineStyle:{width:1}}]});
chart.on('updateAxisPointer',(e)=>{const info=e.axesInfo&&e.axesInfo[0];if(info!=null)updatePanels(info.value);});
chart.getZr().on('mousemove',(e)=>{const pt=[e.offsetX,e.offsetY];const r=chart.convertFromPixel({seriesIndex:0},pt);if(Array.isArray(r)&&r[0]!=null)updatePanels(Math.round(r[0]));});
updatePanels(categories.length-1);
window.addEventListener('resize',()=>chart.resize());
""" % (
        json.dumps(categories, ensure_ascii=False),
        json.dumps(opens),
        json.dumps(highs),
        json.dumps(lows),
        json.dumps(closes),
        json.dumps(ohlc, ensure_ascii=False),
        json.dumps(dif),
        json.dumps(dea),
        json.dumps(macd),
        json.dumps([v if v >= 0 else '-' for v in macd]),
        json.dumps([v if v < 0 else '-' for v in macd]),
        json.dumps(calc_ma(closes, 5)),
        json.dumps(calc_ma(closes, 13)),
        json.dumps(calc_ma(closes, 144)),
        json.dumps(pts['buy'], ensure_ascii=False),
        json.dumps(pts['sell'], ensure_ascii=False),
        json.dumps(pts['win'], ensure_ascii=False),
        json.dumps(pts['loss'], ensure_ascii=False),
        json.dumps(bar_trades, ensure_ascii=False),
        COLOR_UP,
        COLOR_DOWN,
        start_pct,
        start_pct,
    )
    return write_html(out_path, '交易点可视化', body, trade_panels=True)


def _close_pnl_by_date(trades: list) -> dict[str, float]:
    out: dict[str, float] = {}
    for t in trades:
        if t['kind'] == '平仓':
            out[t['dt'].strftime('%Y-%m-%d')] = float(t['pnl'])
    return out


def load_equity_series(
    equity_csv: Path,
    index_daily: dict,
    initial_asset: float,
    close_pnl_by_date: dict[str, float],
):
    df = pd.read_csv(equity_csv, encoding='utf-8-sig')
    x, y_strat, y_bench, y_ret, y_bench_ret, max_dd, pnl_vals, idx_vals = [], [], [], [], [], [], [], []
    base_px, peak = None, float(initial_asset)
    for row in df.to_dict('records'):
        dt = parse_dt(row.get('日期时间'))
        if dt is None:
            continue
        d = dt.date()
        d_str = dt.strftime('%Y-%m-%d')
        eq_raw = row.get('总权益')
        if eq_raw in (None, ''):
            continue
        eq = float(eq_raw)
        px = index_daily.get(d)
        if px is None:
            continue
        if base_px is None:
            base_px = px
        bench = float(initial_asset) * (px / float(base_px))
        peak = max(peak, eq)
        dd = (peak - eq) / peak * 100.0 if peak > 0 else 0.0
        x.append(d_str)
        y_strat.append(eq)
        y_bench.append(bench)
        y_ret.append((eq - float(initial_asset)) / float(initial_asset) * 100.0)
        y_bench_ret.append((bench - float(initial_asset)) / float(initial_asset) * 100.0)
        max_dd.append(dd)
        pnl_vals.append(close_pnl_by_date.get(d_str))
        idx_vals.append(px)
    return x, y_strat, y_bench, y_ret, y_bench_ret, max_dd, pnl_vals, idx_vals


def render_equity_chart(
    *,
    trade_csv: Path,
    output_dir: Path,
    data_dir: Path,
    initial_asset: float = 10_000_000.0,
    output_tag: str | None = None,
    kline_filename: str = DEFAULT_KLINE_NAME,
    equity_csv: Path | None = None,
):
    tag = str(output_tag or infer_tag(trade_csv))
    out_path = Path(output_dir) / f'{tag}_收益率走势图.html'
    data_dir = Path(data_dir)
    kline, trades = load_kline(data_dir, kline_filename), load_trades(trade_csv)
    close_pnl = _close_pnl_by_date(trades)
    index_daily = load_index_daily_closes(data_dir)

    if equity_csv and Path(equity_csv).exists():
        x, y_strat, y_bench, y_ret, y_bench_ret, max_dd, pnl_vals, idx_vals = load_equity_series(
            Path(equity_csv), index_daily, initial_asset, close_pnl
        )
        if not x:
            equity_csv = None

    if not equity_csv or not Path(equity_csv).exists():
        closes = [t for t in trades if t['kind'] == '平仓']
        if not closes:
            body = (
                "const chart=echarts.init(document.getElementById('chart'));"
                "chart.setOption({title:{text:'收益率走势图',subtext:'当前交易明细中暂无平仓记录'},"
                "xAxis:{type:'category',data:[]},yAxis:{type:'value'},series:[{type:'line',data:[]}]});"
                "window.addEventListener('resize',()=>chart.resize());"
            )
            return write_html(out_path, '收益率走势图', body)
        pts, cum_pnl, base_px0, peak = [], 0.0, None, float(initial_asset)
        x, y_strat, y_bench, y_ret, y_bench_ret, max_dd, pnl_vals, idx_vals = [], [], [], [], [], [], [], []
        for rec in closes:
            d = rec['dt'].date()
            idx_px = index_daily.get(d)
            if idx_px is None:
                continue
            idx_px = float(idx_px)
            if base_px0 is None:
                base_px0 = idx_px
            cum_pnl += float(rec['pnl'])
            strat = float(initial_asset) + cum_pnl
            bench = float(initial_asset) * (idx_px / float(base_px0)) if float(base_px0) != 0 else float(initial_asset)
            peak = max(peak, strat)
            dd = (peak - strat) / peak * 100.0 if peak > 0 else 0.0
            d_str = rec['dt'].strftime('%Y-%m-%d')
            x.append(d_str)
            y_strat.append(strat)
            y_bench.append(bench)
            y_ret.append((strat - float(initial_asset)) / float(initial_asset) * 100.0)
            y_bench_ret.append((bench - float(initial_asset)) / float(initial_asset) * 100.0)
            max_dd.append(dd)
            pnl_vals.append(float(rec['pnl']))
            idx_vals.append(idx_px)
        if not x:
            raise RuntimeError('平仓记录未能映射到K线时间轴')

    body = """
const xData=%s, yStrat=%s, yBench=%s, pnl=%s, idx=%s, initialAsset=%s;
const fmt=(v)=>Number(v).toFixed(2);
const fmtPct=(v)=>{const n=Number(v);const s=fmt(n);return(n>=0?'+':'')+s+'%%';};
const SEP='\\u3000\\u3000\\u3000\\u3000';
const LY={titleTop:8,metrics1Top:40,metrics2Top:62,metrics3Top:84,metrics4Top:106,legendTop:132,gridTop:168};
let viewI0=0, viewI1=Math.max(0,xData.length-1);
function idxFromPct(pct){const n=xData.length;if(n<=1)return 0;return Math.max(0,Math.min(n-1,Math.round(pct/100*(n-1))));}
function stratBase(i0){return i0===0?initialAsset:yStrat[i0];}
function benchBase(i0){return i0===0?initialAsset:yBench[i0];}
function buildReturns(i0){const sb=stratBase(i0),bb=benchBase(i0);const ret=[],bret=[];for(let i=0;i<yStrat.length;i++){ret.push(sb>0?(yStrat[i]-sb)/sb*100:0);bret.push(bb>0?(yBench[i]-bb)/bb*100:0);}return{ret,bret};}
function top3DrawdownEpisodes(slice,dates,retSlice){const eps=[];const empty={pct:0,start:'',end:'',peakRet:0,troughRet:0};if(!slice.length)return[empty,empty,empty];let peak=slice[0],trough=peak,peakIdx=0,troughIdx=0,inDd=false;const pushEp=()=>{const depth=(peak-trough)/peak*100;if(depth>1e-8)eps.push({pct:depth,start:dates[peakIdx]||'',end:dates[troughIdx]||'',peakRet:retSlice[peakIdx]||0,troughRet:retSlice[troughIdx]||0});};for(let i=0;i<slice.length;i++){const v=slice[i];if(v>=peak){if(inDd&&peak>0)pushEp();peak=v;trough=v;peakIdx=i;troughIdx=i;inDd=false;}else{inDd=true;if(v<trough){trough=v;troughIdx=i;}}}if(inDd&&peak>0)pushEp();eps.sort((a,b)=>b.pct-a.pct);while(eps.length<3)eps.push(empty);return eps.slice(0,3);}
function fmtDdLine(n,ep){if(!ep.pct)return'最大回撤'+n+'  —';const rg=(ep.start&&ep.end)?' ('+ep.start+'~'+ep.end+')':'';return'最大回撤'+n+'  '+fmt(ep.pct)+'%%'+rg;}
const DD_COLORS=['#e74c3c','#e67e22','#8e44ad'];
function buildDdMarks(dds){const markLine={yAxisIndex:1,symbol:['none','none'],silent:true,data:[]};const markArea={yAxisIndex:1,silent:true,data:[]};dds.forEach((ep,i)=>{if(!ep.pct||!ep.start||!ep.end)return;const col=DD_COLORS[i]||'#999';const n=i+1;const yLo=Math.min(ep.peakRet,ep.troughRet),yHi=Math.max(ep.peakRet,ep.troughRet);const ls={color:col,width:1.5,type:'dashed'};const lb=(t,pos)=>({show:true,formatter:t,fontSize:10,color:col,distance:2,position:pos});markArea.data.push([{xAxis:ep.start,yAxis:ep.peakRet,itemStyle:{color:col,opacity:0.22}},{xAxis:ep.end,yAxis:ep.troughRet}]);markLine.data.push([{coord:[ep.start,yLo],lineStyle:ls,label:lb('回撤'+n+'起','insideStartTop')},{coord:[ep.start,yHi]}],[{coord:[ep.end,yLo],lineStyle:ls,label:lb('回撤'+n+'止','insideEndTop')},{coord:[ep.end,yHi]}]);});return{markLine,markArea};}
function metricTextStyle(){return{fill:'#333',font:'12px Microsoft YaHei',textAlign:'center'};}
function buildMetricGraphics(line1,dd1,dd2,dd3){return[{id:'metrics1',type:'text',left:'center',top:LY.metrics1Top,z:100,style:Object.assign({text:line1},metricTextStyle())},{id:'metrics2',type:'text',left:'center',top:LY.metrics2Top,z:100,style:Object.assign({text:dd1},metricTextStyle())},{id:'metrics3',type:'text',left:'center',top:LY.metrics3Top,z:100,style:Object.assign({text:dd2},metricTextStyle())},{id:'metrics4',type:'text',left:'center',top:LY.metrics4Top,z:100,style:Object.assign({text:dd3},metricTextStyle())}];}
function readZoomRange(){const opt=chart.getOption();const dz=(opt.dataZoom&&opt.dataZoom[0])||{start:0,end:100};return{start:dz.start!=null?dz.start:0,end:dz.end!=null?dz.end:100};}
function updateRangeView(){const z=readZoomRange();viewI0=idxFromPct(z.start);viewI1=idxFromPct(z.end);if(viewI1<viewI0)viewI1=viewI0;const{bret,ret}=buildReturns(viewI0);const sliceDates=xData.slice(viewI0,viewI1+1);const retSlice=ret.slice(viewI0,viewI1+1);const dds=top3DrawdownEpisodes(yStrat.slice(viewI0,viewI1+1),sliceDates,retSlice);const marks=buildDdMarks(dds);const line1='累计收益率  '+fmtPct(ret[viewI1])+SEP+'基准收益率  '+fmtPct(bret[viewI1]);chart.setOption({graphic:buildMetricGraphics(line1,fmtDdLine(1,dds[0]),fmtDdLine(2,dds[1]),fmtDdLine(3,dds[2])),series:[{},{},{data:ret,markLine:marks.markLine,markArea:marks.markArea},{data:bret}]});}
const chart=echarts.init(document.getElementById('chart'));
chart.setOption({animation:false,title:{text:'收益率走势图',left:'center',top:LY.titleTop,textStyle:{fontSize:16}},legend:{top:LY.legendTop,left:'center',itemGap:32,data:['策略资产','基准资产(上证归一)','累计收益率','基准收益率']},graphic:buildMetricGraphics('','','',''),tooltip:{trigger:'axis',axisPointer:{type:'cross'},formatter:(ps)=>{const i=ps[0].dataIndex;const sb=stratBase(viewI0),bb=benchBase(viewI0);const r=sb>0?(yStrat[i]-sb)/sb*100:0;const br=bb>0?(yBench[i]-bb)/bb*100:0;let s=`${xData[i]}<br/>策略资产: ${fmt(yStrat[i])}<br/>基准资产: ${fmt(yBench[i])}<br/>区间累计收益率: ${fmtPct(r)}<br/>区间基准收益率: ${fmtPct(br)}<br/>上证收盘: ${fmt(idx[i])}`;if(pnl[i]!=null)s+=`<br/>当笔平仓盈亏: ${fmt(pnl[i])}`;return s;}},grid:{left:80,right:60,top:LY.gridTop,bottom:70},xAxis:{type:'category',data:xData,boundaryGap:false},yAxis:[{type:'value',scale:true,name:'资产',axisLabel:{formatter:(v)=>Number(v).toLocaleString('zh-CN',{maximumFractionDigits:0})}},{type:'value',scale:true,position:'right',name:'收益率',axisLabel:{formatter:(v)=>fmt(v)+'%%'}}],dataZoom:[{type:'inside',start:0,end:100},{type:'slider',start:0,end:100,bottom:20}],series:[{name:'策略资产',type:'line',step:'end',data:yStrat,showSymbol:false},{name:'基准资产(上证归一)',type:'line',step:'end',data:yBench,showSymbol:false},{name:'累计收益率',type:'line',yAxisIndex:1,step:'end',data:[],showSymbol:false},{name:'基准收益率',type:'line',yAxisIndex:1,step:'end',data:[],showSymbol:false,lineStyle:{type:'dashed'}}]});
chart.on('dataZoom',()=>updateRangeView());
updateRangeView();
window.addEventListener('resize',()=>chart.resize());
""" % (
        json.dumps(x, ensure_ascii=False),
        json.dumps(y_strat),
        json.dumps(y_bench),
        json.dumps(pnl_vals),
        json.dumps(idx_vals),
        json.dumps(float(initial_asset)),
    )
    return write_html(out_path, '收益率走势图', body)


def render_backtest_visuals(
    run_output_dir: Path | str,
    data_dir: Path | str,
    *,
    initial_asset: float = 10_000_000.0,
    trades_filename: str = 'trades.csv',
) -> dict[str, str]:
    run_dir = Path(run_output_dir)
    data_dir = Path(data_dir)
    trades_csv = run_dir / trades_filename
    if not trades_csv.exists():
        raise FileNotFoundError(f'未找到成交文件: {trades_csv}')
    viz_dir = run_dir / 'visuals'
    tag = run_dir.name
    equity_csv = run_dir / 'equity.csv'
    p1 = render_trade_chart(trade_csv=trades_csv, output_dir=viz_dir, data_dir=data_dir, output_tag=tag)
    p2 = render_equity_chart(
        trade_csv=trades_csv,
        output_dir=viz_dir,
        data_dir=data_dir,
        initial_asset=initial_asset,
        output_tag=tag,
        equity_csv=equity_csv if equity_csv.exists() else None,
    )
    return {'trade_chart': str(p1), 'equity_chart': str(p2), 'visuals_dir': str(viz_dir)}
