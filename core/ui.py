#!/usr/bin/env python3
"""
ui.py —— 生成可视化界面（单文件 HTML，四套主题）

用法:
    python core/ui.py --root /path/to/disk
    python core/ui.py --root /path/to/disk --out ui.html --open

只读取 probe/*.json，不修改任何东西。
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SYSTEM_DIRS = {
    '$recycle.bin', 'system volume information', 'recovery',
    'config.msi', 'windows', 'program files', 'program files (x86)',
    '$windows.~bt', '$windows.~ws', 'perflogs', 'intel', 'amd',
}


def load_probes(probe_dir):
    probes, units, groups = {}, {}, {}
    if not probe_dir.is_dir():
        return probes, units, groups
    for name in os.listdir(probe_dir):
        if not name.endswith(".json"):
            continue
        try:
            data = json.loads((probe_dir / name).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not data.get("path"):
            continue
        path = data["path"]
        if name.startswith("units_"):
            units[path] = data
        elif name.startswith("group_"):
            groups[path] = data
        elif not name.startswith("dup_"):
            probes[path] = data
    return probes, units, groups


def unit_num(item):
    v = item.get("units")
    if isinstance(v, list):
        return len(v)
    if isinstance(v, int):
        return v
    if isinstance(item.get("unit_count"), int):
        return item["unit_count"]
    return 0


def build_model(root, probes, units, groups):
    nodes = []
    for path in sorted(set(probes) | set(units) | set(groups)):
        if not os.path.isdir(path):
            continue
        p = probes.get(path) or {}
        u = units.get(path) or {}
        g = groups.get(path) or {}
        nodes.append({
            "path": path,
            "name": os.path.basename(path.rstrip(os.sep)),
            "action": p.get("action"),
            "confidence": p.get("confidence"),
            "what": (p.get("actual") or {}).get("what", ""),
            "unit_why": p.get("unit_why", ""),
            "rename_to": p.get("rename_to"),
            "total_units": u.get("total_units"),
            "groups": [{
                "name": x.get("name", ""),
                "why": x.get("why", ""),
                "relation": x.get("relation", ""),
                "confidence": x.get("confidence", ""),
                "n": unit_num(x),
            } for x in (g.get("groups") or [])],
            "solo_n": len(g.get("solo_units") or []),
            "uncertain_n": len(g.get("uncertain") or []),
        })
    return nodes


def collect_zones(root):
    zones = []
    try:
        for e in os.scandir(root):
            if not e.is_dir(follow_symlinks=False):
                continue
            low = e.name.casefold()
            if low in SYSTEM_DIRS or e.name.startswith(('_', '.', '$')):
                continue
            try:
                attrs = getattr(e.stat(follow_symlinks=False),
                                'st_file_attributes', 0)
                if attrs & 0x2 or attrs & 0x4:
                    continue
            except OSError:
                pass
            zones.append(e.path)
    except OSError:
        pass
    return sorted(zones)


CSS = r"""
:root{
  --bg:#fbfcfd; --surface:#fff; --surface2:#f4f6f9; --hover:#eef2f7;
  --line:#e2e8f0; --line2:#cbd5e1;
  --fg:#0f172a; --fg2:#475569; --fg3:#94a3b8;
  --accent:#2563eb; --accent-d:#1d4ed8; --accent-fg:#fff;
  --ok:#059669; --warn:#d97706; --err:#dc2626;
  --r:10px; --r-sm:6px;
  --mono:ui-monospace,"SF Mono",Consolas,monospace;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --bg:#0b0f14; --surface:#121821; --surface2:#1a2230; --hover:#212b3a;
    --line:#243040; --line2:#334155;
    --fg:#e2e8f0; --fg2:#94a3b8; --fg3:#64748b;
    --accent:#3b82f6; --accent-d:#2563eb; --accent-fg:#fff;
    --ok:#10b981; --warn:#f59e0b; --err:#ef4444;
  }
}
html[data-theme="dark"]{
  --bg:#0b0f14; --surface:#121821; --surface2:#1a2230; --hover:#212b3a;
  --line:#243040; --line2:#334155;
  --fg:#e2e8f0; --fg2:#94a3b8; --fg3:#64748b;
  --accent:#3b82f6; --accent-d:#2563eb; --accent-fg:#fff;
  --ok:#10b981; --warn:#f59e0b; --err:#ef4444;
}
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%}
body{
  font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  background:var(--bg);color:var(--fg);-webkit-font-smoothing:antialiased;
  display:flex;flex-direction:column;overflow:hidden;
}
code,.mono,.num{font-family:var(--mono)}
::selection{background:var(--accent);color:var(--accent-fg)}

.top{
  flex:0 0 auto;display:flex;align-items:center;gap:12px;padding:11px 20px;
  background:var(--surface);border-bottom:1px solid var(--line);
}
.logo{font-weight:600;font-size:14px}
.logo em{font-style:normal;font-size:11px;color:var(--fg3);
  font-family:var(--mono);margin-left:8px}
.grow{flex:1}
.tbtn{width:28px;height:28px;border-radius:6px;border:1px solid var(--line);
  background:transparent;color:var(--fg2);cursor:pointer;font-size:12px;
  display:flex;align-items:center;justify-content:center}
.tbtn:hover{background:var(--hover)}

.core{
  flex:0 0 auto;padding:22px 20px 18px;text-align:center;
  background:var(--surface);border-bottom:1px solid var(--line);
}
.core .n{
  font-size:48px;font-weight:700;line-height:1;letter-spacing:-.04em;
  font-family:var(--mono);color:var(--warn);display:block;margin-bottom:6px;
}
.core .n.zero{color:var(--ok)}
.core .lbl{font-size:14px;font-weight:600;margin-bottom:6px}
.core .sub{font-size:12.5px;color:var(--fg3);max-width:480px;margin:0 auto;line-height:1.65}
.core .sub b{color:var(--fg2)}
.core .cta{display:flex;gap:8px;justify-content:center;margin-top:14px}

.stage{flex:1;overflow-y:auto;min-height:0}
.wrap{max-width:880px;margin:0 auto;padding:16px 18px 40px}

.needcard{
  background:var(--surface);
  border:1px solid color-mix(in srgb,var(--warn) 40%,var(--line));
  border-radius:var(--r);overflow:hidden;margin-bottom:14px;
  box-shadow:0 1px 3px rgba(217,119,6,.08);
}
.needcard .hd{
  padding:10px 16px;background:color-mix(in srgb,var(--warn) 8%,var(--surface));
  border-bottom:1px solid color-mix(in srgb,var(--warn) 25%,var(--line));
  font-size:12px;font-weight:600;color:var(--warn);
}
.needitem{
  display:grid;grid-template-columns:1fr 230px;gap:14px;align-items:center;
  padding:13px 16px;border-bottom:1px solid var(--line);
}
.needitem:last-child{border-bottom:0}
.needitem .nm{font-size:13px;font-weight:600;margin-bottom:3px}
.needitem .ds{font-size:12px;color:var(--fg2);line-height:1.55}
.needitem .ds em{font-style:normal;color:var(--accent);font-weight:500;
  display:block;margin-top:4px}

.fold{
  background:var(--surface);border:1px solid var(--line);
  border-radius:var(--r);margin-bottom:10px;overflow:hidden;
}
.fold-hd{
  display:flex;align-items:center;gap:10px;padding:11px 16px;
  cursor:pointer;user-select:none;
}
.fold-hd:hover{background:var(--hover)}
.fold-hd .t{flex:1;font-size:13px;font-weight:500}
.fold-hd .c{font-size:11.5px;color:var(--fg3);font-family:var(--mono)}
.fold-hd .chev{color:var(--fg3);font-size:10px;transition:transform .15s}
.fold.open .chev{transform:rotate(90deg)}
.fold-bd{display:none;border-top:1px solid var(--line)}
.fold.open .fold-bd{display:block}

.row{
  display:flex;align-items:center;gap:12px;padding:8px 16px;
  border-bottom:1px solid color-mix(in srgb,var(--line) 60%,transparent);
  font-size:12.5px;
}
.row:last-child{border-bottom:0}
.row .n{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.row .n .k{color:var(--fg3);font-size:11px;margin-left:6px}
.row .t{
  flex:0 0 auto;font-size:11.5px;color:var(--fg3);
  max-width:190px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
}
.row .t.acc{color:var(--accent)}
.row .t.err{color:var(--err)}
.row .sz{flex:0 0 58px;text-align:right;color:var(--fg3);
  font-family:var(--mono);font-size:11px}

.pick{
  width:100%;padding:7px 10px;border-radius:var(--r-sm);
  background:var(--surface2);border:1px solid var(--line2);
  color:var(--fg2);font:12.5px inherit;cursor:pointer;outline:none;
  text-align:left;display:flex;align-items:center;gap:6px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.pick:hover{border-color:var(--accent)}
.pick.has{border-color:var(--ok);color:var(--ok);font-weight:500}
.pick .caret{margin-left:auto;color:var(--fg3);font-size:10px;flex:0 0 auto}

/* 文件级分配 */
.filelist{
  grid-column:1/-1;margin-top:6px;border-top:1px dashed var(--line);
  padding-top:8px;
}
.frow{
  display:grid;grid-template-columns:1fr 200px;gap:10px;align-items:center;
  padding:5px 0;font-size:12px;
}
.frow .fn{color:var(--fg2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.frow .fn .fs{color:var(--fg3);font-family:var(--mono);font-size:10.5px;margin-left:6px}
.frow .fn .dup{color:var(--err);font-size:10.5px;margin-left:6px}
.pick.sm{padding:5px 8px;font-size:11.5px}
.expand{
  grid-column:1/-1;margin-top:6px;font-size:11.5px;color:var(--accent);
  cursor:pointer;user-select:none;
}
.expand:hover{text-decoration:underline}

/* 选择面板 */
.panel{
  position:fixed;inset:0;z-index:120;background:rgba(0,0,0,.45);
  display:none;align-items:center;justify-content:center;padding:20px;
}
.panel.on{display:flex}
.pbox{
  background:var(--surface);border:1px solid var(--line2);border-radius:var(--r);
  width:100%;max-width:420px;max-height:66vh;display:flex;flex-direction:column;
  box-shadow:0 20px 50px rgba(0,0,0,.4);
}
.pbox .ph{padding:11px 14px;border-bottom:1px solid var(--line);
  display:flex;gap:8px;align-items:center}
.pbox .ph input{
  flex:1;padding:7px 10px;border-radius:var(--r-sm);border:1px solid var(--line2);
  background:var(--surface2);color:var(--fg);font:12.5px inherit;outline:none;
}
.pbox .ph input:focus{border-color:var(--accent)}
.plist{flex:1;overflow-y:auto;padding:5px}
.pitem{
  padding:7px 10px;border-radius:var(--r-sm);cursor:pointer;
  font-size:12.5px;display:flex;align-items:center;gap:8px;
}
.pitem:hover{background:var(--hover)}
.pitem.on{background:color-mix(in srgb,var(--accent) 12%,transparent);color:var(--accent)}
.pitem.sub{padding-left:24px;color:var(--fg2);font-size:12px}
.pitem .ck{color:var(--accent);font-size:11px;width:12px;flex:0 0 12px}
.pgroup{
  padding:8px 10px 4px;font-size:10.5px;font-weight:700;letter-spacing:.06em;
  text-transform:uppercase;color:var(--fg3);
}

.btn{background:var(--accent);color:var(--accent-fg);border:0;
  padding:9px 20px;border-radius:var(--r-sm);font:600 13px inherit;
  cursor:pointer;transition:.12s}
.btn:hover{background:var(--accent-d)}
.btn.gh{background:transparent;border:1px solid var(--line2);color:var(--fg2)}
.btn.gh:hover{background:var(--hover);color:var(--fg)}
.btn.sm{padding:6px 13px;font-size:12.5px}

.status{
  flex:0 0 auto;display:flex;align-items:center;gap:10px;padding:9px 20px;
  background:var(--surface);border-top:1px solid var(--line);
  font-size:12px;color:var(--fg3);
}
.status b{color:var(--fg2);font-family:var(--mono)}

.mask{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:99;
  display:none;align-items:center;justify-content:center;padding:24px}
.mask.on{display:flex}
.modal{background:var(--surface);border:1px solid var(--line2);
  border-radius:var(--r);width:100%;max-width:700px;max-height:76vh;
  display:flex;flex-direction:column;box-shadow:0 20px 50px rgba(0,0,0,.35)}
.modal h3{padding:14px 18px;font-size:13.5px;font-weight:600;
  border-bottom:1px solid var(--line)}
.modal pre{flex:1;overflow:auto;margin:0;padding:14px 18px;
  font:11px/1.65 var(--mono);color:var(--fg2);white-space:pre-wrap;word-break:break-all}
.modal .ft{padding:11px 18px;border-top:1px solid var(--line);
  display:flex;gap:8px;justify-content:flex-end}

@media(max-width:760px){
  .needitem{grid-template-columns:1fr;gap:8px}
  .core{padding:18px 14px 14px}
  .core .n{font-size:38px}
  .wrap{padding:12px 12px 36px}
}
"""

BODY = r"""

<div class="top">
  <div class="logo">待办清单<em>__ROOT__</em></div>
  <div class="grow"></div>
  <button class="tbtn" id="themeBtn">◐</button>
</div>

<div class="core">
  <span class="n" id="coreN">—</span>
  <div class="lbl" id="coreLbl"></div>
  <div class="sub" id="coreSub"></div>
  <div class="cta" id="coreCta"></div>
</div>

<div class="stage"><div class="wrap" id="main"></div></div>

<div class="status">
  <span id="stA">—</span><span style="color:var(--line2)">·</span>
  <span id="stB">—</span>
  <div class="grow"></div>
  <span id="stC"></span>
</div>

<div class="panel" id="panel">
  <div class="pbox">
    <div class="ph">
      <input id="pq" placeholder="搜索目录…">
      <button class="btn gh sm" onclick="closePanel()">取消</button>
    </div>
    <div class="plist" id="plist"></div>
  </div>
</div>

<div class="mask" id="mask">
  <div class="modal">
    <h3 id="mTitle"></h3>
    <pre id="mBody"></pre>
    <div class="ft">
      <button class="btn gh sm" onclick="closeM()">关闭</button>
    </div>
  </div>
</div>

"""

JS = r"""
const DATA = __DATA__;
const KEY = 'fo-b5-' + DATA.root.replace(/[\\/:*?"<>|]/g,'_');

let dec = {};

/* 待确认项的建议值 —— 直接填好，不是空下拉 */
const FIXED = {
  "1": {
    target: '__split__',
    desc: '9 个文件横跨实验动画 / 凝胶电泳 pptx / 招生 pdf / 4 张照片 / ToDesk.exe',
    note: '每个文件单独指定去向，见下方逐项',
    files: [
      {name:'1-【实验动画化】四分钟秒懂 DNA 凝胶电泳-1080P 高码率-AVC.mp4',
       size:'32.47 MB', to:'G:\\学习资料\\生物', hint:'教学动画'},
      {name:'凝胶电泳及其应用 (1).pptx',
       size:'46.64 MB', to:'G:\\学习资料\\生物', hint:'课件'},
      {name:'山东省2023年普通高等学校招生考试信息平台 - 报名信息查看.pdf',
       size:'0.26 MB', to:'G:\\文件\\个人文档', hint:'招生信息'},
      {name:'IMG_20221112_095511 (1).jpg',
       size:'1.01 MB', to:'__delete__', dup:'与 IMG_20221112_095511.jpg 同哈希'},
      {name:'IMG_20221112_095511.jpg',
       size:'1.01 MB', to:'G:\\文件\\照片', hint:'手机拍摄'},
      {name:'IMG_20221112_095515.jpg',
       size:'1.16 MB', to:'G:\\文件\\照片', hint:'手机拍摄'},
      {name:'微信图片_20221106125216.jpg',
       size:'0.12 MB', to:'G:\\文件\\照片\\微信', hint:'微信保存'},
      {name:'微信图片_202211061252161.jpg',
       size:'0.12 MB', to:'G:\\文件\\照片\\微信', hint:'微信保存（哈希不同）'},
      {name:'ToDesk.exe',
       size:'1.48 MB', to:'G:\\文件\\软件安装包', hint:'远程工具'}
    ]
  },
  "saves": {
    target: '__merge__',
    desc: '6 个世界存档，与 Minecraft\\saves 有 4 个完全相同、2 个不同版本',
    note: '建议合并：补进 Minecraft\\saves，同名的不覆盖'
  }
};

/* 文件级分配状态：path -&gt; {文件名: 目标} */
let fileDec = {};

function esc(s){
  return String(s ?? '').replace(/[&<>"]/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

function syncBtn(){
  const t = document.documentElement.dataset.theme;
  const dark = t === 'dark' ||
    (!t && matchMedia('(prefers-color-scheme: dark)').matches);
  document.getElementById('themeBtn').textContent = dark ? '☾' : '☀';
}
document.getElementById('themeBtn').onclick = () => {
  const t = document.documentElement.dataset.theme;
  const dark = t === 'dark' ||
    (!t && matchMedia('(prefers-color-scheme: dark)').matches);
  const next = dark ? 'light' : 'dark';
  localStorage.setItem('fo-theme', next);
  document.documentElement.dataset.theme = next;
  syncBtn();
};

function init(){
  DATA.u.forEach(it => {
    if(FIXED[it.name]){
      dec[it.path] = {target: FIXED[it.name].target};
      return;
    }
    if(it.action === 'move') dec[it.path] = {target: it.target || ''};
    else if(it.action === 'delete') dec[it.path] = {target: '__delete__'};
    else if(it.action === 'keep') dec[it.path] = {target: '__keep__'};
    else dec[it.path] = {target: ''};
  });
  DATA.pending.forEach(it => { dec['P:'+it.path] = {target: ''}; });
}

function stats(){
  let mv=0, dl=0, kp=0, need=0, fx=0;
  DATA.u.forEach(it => {
    const t = dec[it.path]?.target || '';
    if(FIXED[it.name]) fx++;
    if(!t) need++;
    else if(t === '__delete__') dl++;
    else if(t === '__keep__') kp++;
    else mv++;
  });
  return {mv, dl, kp, need, fx};
}

function renderCore(){
  const s = stats();
  const nEl = document.getElementById('coreN');
  nEl.textContent = s.need;
  nEl.classList.toggle('zero', s.need === 0);

  document.getElementById('coreLbl').textContent =
    s.need === 0 ? '全部已就绪' : '项需要你确认';

  let sub = `u 目录 <b>${DATA.u.length}</b> 项：` +
            `<b>${s.mv}</b> 移到别处 · <b>${s.dl}</b> 待删除 · <b>${s.kp}</b> 不动。`;
  if(s.fx) sub += `<br>其中 <b>${s.fx}</b> 项我已填好建议值，确认即可。`;
  document.getElementById('coreSub').innerHTML = sub;

  document.getElementById('coreCta').innerHTML =
    `<button class="btn" onclick="showList()">生成执行清单</button>` +
    `<button class="btn gh" onclick="acceptAll()">全部采纳</button>`;
}

function render(){
  const main = document.getElementById('main');
  let html = '';

  // ① 待确认
  const needs = DATA.u.filter(it => FIXED[it.name] || !(dec[it.path]?.target));
  if(needs.length){
    html += `<div class="needcard"><div class="hd">需要你确认　${needs.length} 项</div>`;
    needs.forEach(it => {
      const d = dec[it.path] || {};
      const fx = FIXED[it.name];
      const hasFiles = fx && fx.files && fx.files.length;
      html += `<div class="needitem">
        <div>
          <div class="nm">${esc(it.name)}</div>
          <div class="ds">${esc(fx ? fx.desc : it.desc)}
            ${fx ? `<em>${esc(fx.note)}</em>` : ''}</div>
          ${!hasFiles ? `<button class="pick${d.target?' has':''}" data-k="${esc(it.path)}"
            style="margin-top:8px">
            <span>${esc(targetLabel(d.target || ''))}</span>
            <span class="caret">▾</span></button>` : ''}
        </div>
        ${!hasFiles ? '<div></div>' : '<div></div>'}
        ${hasFiles ? renderFiles(it, fx) : ''}
      </div>`;
    });
    html += '</div>';
  }

  // ② 折叠组
  const fold = (title, items, fmt) => {
    if(!items.length) return '';
    let h = `<div class="fold"><div class="fold-hd" onclick="this.parentElement.classList.toggle('open')">
      <span class="t">${title}</span>
      <span class="c">${items.length} 项</span>
      <span class="chev">›</span></div><div class="fold-bd">`;
    items.forEach(it => { h += fmt(it); });
    return h + '</div></div>';
  };

  const isFixed = it => !!FIXED[it.name];
  const tgt = it => dec[it.path]?.target || '';

  html += fold('建议删除',
    DATA.u.filter(it => !isFixed(it) && tgt(it) === '__delete__'),
    it => `<div class="row">
      <span class="n">${esc(it.name)}<span class="k">${esc(it.kind)}</span></span>
      <span class="t err">${esc(it.desc)}</span>
      <span class="sz">${it.gb} GB</span></div>`);

  html += fold('移到别处',
    DATA.u.filter(it => !isFixed(it) && tgt(it) && tgt(it) !== '__delete__' && tgt(it) !== '__keep__'),
    it => `<div class="row">
      <span class="n">${esc(it.name)}<span class="k">${esc(it.kind)}</span></span>
      <span class="t acc">→ ${esc(shortPath(tgt(it)))}</span>
      <span class="sz">${it.gb} GB</span></div>`);

  html += fold('保持不动',
    DATA.u.filter(it => !isFixed(it) && tgt(it) === '__keep__'),
    it => `<div class="row">
      <span class="n">${esc(it.name)}<span class="k">${esc(it.kind)}</span></span>
      <span class="t">${esc(it.desc.slice(0,36))}</span>
      <span class="sz">${it.gb} GB</span></div>`);

  // ③ 待删除区
  if(DATA.pending.length){
    html += `<div class="fold open"><div class="fold-hd" onclick="this.parentElement.classList.toggle('open')">
      <span class="t">待删除区</span>
      <span class="c">${DATA.pending.length} 项 · ${DATA.summary.pendingGB} GB</span>
      <span class="chev">›</span></div><div class="fold-bd">`;
    DATA.pending.forEach(it => {
      const v = dec['P:'+it.path]?.target || '';
      html += `<div class="row">
        <span class="n">${esc(it.name)}</span>
        <button class="pick${v?' has':''}" data-k="P:${esc(it.path)}"
          style="flex:0 0 140px">${v==='__delete__'?'确认删除':'留在待删除'}</button>
        <span class="sz">${it.gb} GB</span></div>`;
    });
    html += '</div></div>';
  }

  main.innerHTML = html;
  main.querySelectorAll('.pick').forEach(el => {
    el.onclick = () => {
      if(el.dataset.fk) openPanel(el.dataset.fk);
      else openPanel(el.dataset.k);
    };
  });
}

function renderFiles(it, fx){
  if(!fileDec[it.path]){
    fileDec[it.path] = {};
    fx.files.forEach(f => { fileDec[it.path][f.name] = f.to; });
  }
  const cur = fileDec[it.path];
  let h = `<div class="filelist">`;
  fx.files.forEach(f => {
    const v = cur[f.name] || '';
    const isDup = f.dup;
    h += `<div class="frow">
      <span class="fn">${esc(f.name.length > 56 ? f.name.slice(0,54)+'…' : f.name)}
        <span class="fs">${esc(f.size)}</span>
        ${isDup ? `<span class="dup">⚠ ${esc(f.dup)}</span>` : (f.hint ? `<span class="fs">${esc(f.hint)}</span>` : '')}
      </span>
      <button class="pick sm${v?' has':''}"
        data-fk="${esc(it.path)}||${esc(f.name)}">
        <span>${esc(targetLabel(v))}</span><span class="caret">▾</span>
      </button>
    </div>`;
  });
  return h + '</div>';
}

function shortPath(p){
  if(p === '__split__') return '拆开归位';
  if(p === '__merge__') return '合并';
  const z = (DATA.zones||[]).find(x => (typeof x === 'object' ? x.path : x) === p);
  return z && typeof z === 'object' ? z.label : String(p).split(/[\\/]/).pop();
}

let panelKey = null;

function targetLabel(v){
  if(!v) return '— 请选择 —';
  if(v === '__split__') return '拆开归位（建议）';
  if(v === '__merge__') return '合并（建议）';
  if(v === '__keep__') return '保持原位';
  if(v === '__delete__') return '移到待删除';
  return shortPath(v);
}

function openPanel(key){
  panelKey = key;
  document.getElementById('pq').value = '';
  renderPanel('');
  document.getElementById('panel').classList.add('on');
  setTimeout(() => document.getElementById('pq').focus(), 50);
}

function closePanel(){
  document.getElementById('panel').classList.remove('on');
  panelKey = null;
}

function choose(v){
  if(panelKey){
    if(panelKey.indexOf('||') >= 0){
      const [p, f] = panelKey.split('||');
      if(!fileDec[p]) fileDec[p] = {};
      fileDec[p][f] = v;
    } else {
      dec[panelKey] = {target: v};
    }
    save();
    render(); renderCore(); renderStatus();
  }
  closePanel();
}

function renderPanel(q){
  const list = document.getElementById('plist');
  let cur = '';
  if(panelKey && panelKey.indexOf('||') >= 0){
    const [p, f] = panelKey.split('||');
    cur = (fileDec[p] || {})[f] || '';
  } else {
    cur = dec[panelKey]?.target || '';
  }
  const lower = q.toLowerCase();
  const hit = t => !q || t.toLowerCase().indexOf(lower) >= 0;

  let h = '';

  // 建议项
  const fixed = (DATA.u||[]).find(it => it.path === panelKey);
  const sug = fixed ? FIXED[fixed.name] : null;
  if(sug && hit('建议')){
    h += `<div class="pitem${cur===sug.target?' on':''}" onclick="choose('${sug.target}')">
      <span class="ck">${cur===sug.target?'✓':''}</span>★ ${esc(targetLabel(sug.target))}</div>`;
  }

  // 通用选项
  [['__keep__','保持原位'], ['__delete__','移到待删除']].forEach(([v,t]) => {
    if(hit(t)) h += `<div class="pitem${cur===v?' on':''}" onclick="choose('${v}')">
      <span class="ck">${cur===v?'✓':''}</span>${t}</div>`;
  });

  // 按一级分组
  const byTop = {};
  (DATA.zones||[]).forEach(z => {
    const zp = (typeof z === 'string') ? z : z.path;
    const zl = (typeof z === 'string') ? zp : (z.label || z.path);
    const depth = (typeof z === 'string') ? 1 : (z.depth || 1);
    const top = zl.split('/')[0];
    (byTop[top] = byTop[top] || []).push({path: zp, label: zl, depth});
  });

  Object.keys(byTop).sort().forEach(top => {
    const all = byTop[top];
    const self = all.find(x => x.depth === 1);
    const subs = all.filter(x => x.depth > 1);

    // 一级目录本身作为可选项
    const selfHit = self && (hit(top));
    const subHit = subs.filter(z => hit(z.label));

    if(!selfHit && !subHit.length) return;

    // 只有存在子目录时才打分组头，否则直接列一项
    if(selfHit && subs.length) h += `<div class="pgroup">${esc(top)}</div>`;
    if(selfHit){
      h += `<div class="pitem${cur===self.path?' on':''}"
        onclick="choose(this.dataset.p)" data-p="${esc(self.path)}">
        <span class="ck">${cur===self.path?'✓':''}</span>${esc(top)}</div>`;
    }
    subHit.forEach(z => {
      const name = z.label.split('/').slice(1).join('/');
      h += `<div class="pitem sub${cur===z.path?' on':''}"
        onclick="choose(this.dataset.p)" data-p="${esc(z.path)}">
        <span class="ck">${cur===z.path?'✓':''}</span>${esc(name)}</div>`;
    });
  });

  if(!h) h = `<div class="pitem" style="color:var(--fg3)">没有匹配的目录</div>`;
  list.innerHTML = h;
}

function acceptAll(){
  DATA.u.forEach(it => {
    if(!dec[it.path]?.target) dec[it.path] = {target: '__keep__'};
  });
  save(); renderCore(); render(); renderStatus();
}

function showList(){
  const mv=[], dl=[], kp=[], sp=[], mg=[];
  DATA.u.forEach(it => {
    const t = dec[it.path]?.target || '__keep__';
    if(t === '__split__') sp.push(it.path);
    else if(t === '__merge__') mg.push(it.path);
    else if(t === '__delete__') dl.push(it.path);
    else if(t === '__keep__') kp.push(it.path);
    else mv.push(it.path + '  →  ' + t);
  });
  let txt = `# 待办清单  ${DATA.generated}\n\n`;
  txt += `## 移到别处（${mv.length}）\n${mv.join('\n') || '（无）'}\n\n`;
  txt += `## 拆开归位（${sp.length}）\n${sp.join('\n') || '（无）'}\n\n`;
  txt += `## 合并（${mg.length}）\n${mg.join('\n') || '（无）'}\n\n`;
  txt += `## 移到待删除（${dl.length}）\n${dl.join('\n') || '（无）'}\n\n`;
  txt += `## 保持不动（${kp.length}）\n${kp.map(x=>'  '+x).join('\n')}\n`;
  document.getElementById('mTitle').textContent = '执行清单';
  document.getElementById('mBody').textContent = txt;
  document.getElementById('mask').classList.add('on');
}
function closeM(){ document.getElementById('mask').classList.remove('on'); }

function save(){
  try{ localStorage.setItem(KEY, JSON.stringify({dec, fileDec, at:Date.now()})); }catch(e){}
}
function load(){
  try{
    const raw = localStorage.getItem(KEY);
    if(!raw) return false;
    const o = JSON.parse(raw);
    if(!o || !o.at || Date.now()-o.at > 30*864e5) return false;
    dec = o.dec || {};
    fileDec = o.fileDec || {};
    return Object.keys(dec).length > 0;
  }catch(e){ return false; }
}

function renderStatus(){
  const s = stats();
  document.getElementById('stA').innerHTML = `u: <b>${DATA.u.length}</b> 项`;
  document.getElementById('stB').innerHTML =
    `移动 <b>${s.mv}</b> · 待删 <b>${s.dl}</b> · 不动 <b>${s.kp}</b>`;
  document.getElementById('stC').innerHTML =
    s.need ? `<b>${s.need}</b> 项待定` : '全部已定';
}

const th = localStorage.getItem('fo-theme');
if(th) document.documentElement.dataset.theme = th;
syncBtn();
if(!load()) init();
renderCore();
render();
renderStatus();
document.getElementById('mask').onclick = e => {
  if(e.target.id === 'mask') closeM();
};
document.getElementById('pq').oninput = e => renderPanel(e.target.value);
document.getElementById('panel').onclick = e => {
  if(e.target.id === 'panel') closePanel();
};
document.addEventListener('keydown', e => {
  if(e.key === 'Escape') closePanel();
});
window.foState = () => ({ dec, stats: stats() });
"""


def render_html(root, data):
    return (
        '<!DOCTYPE html>\n<html lang="zh-CN"><head>'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<title>packtidy</title>\n<style>'
        + CSS + '</style></head><body>'
        + BODY.replace('__ROOT__', root)
        + '<script>'
        + JS.replace('__DATA__', json.dumps(data, ensure_ascii=False, default=str))
        + '</script></body></html>'
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', required=True)
    ap.add_argument('--probe')
    ap.add_argument('--out')
    ap.add_argument('--open', action='store_true')
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    probe_dir = Path(args.probe) if args.probe else Path(root) / '_organize' / 'probe'

    import decisions as dec_mod

    probes, units, groups = load_probes(probe_dir)
    nodes = build_model(root, probes, units, groups)

    # 用 decisions 模块算候选去向（含 3 层）和建议
    zones_raw = dec_mod.collect_zones(root)
    # 为每组附加建议
    for n in nodes:
        for g in n['groups']:
            g['relationText'] = dec_mod.RELATION_TEXT.get(
                g.get('relation', ''), g.get('relation', ''))
            g['suggest'] = dec_mod.suggest_group(
                g['name'], n['name'], zones_raw, n['path'])

    data = {
        'root': root,
        'generated': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'nodes': nodes,
        'zones': zones_raw,
    }

    out = Path(args.out) if args.out else Path(root) / '_organize' / 'ui.html'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(root, data), encoding='utf-8')

    total_groups = sum(len(n["groups"]) for n in nodes)
    print(f'已生成: {out}')
    print(f'  {len(nodes)} 节点 / {total_groups} 组 / {len(zones_raw)} 候选去向')

    if args.open:
        try:
            os.startfile(str(out))
        except AttributeError:
            import subprocess
            subprocess.run(['open' if sys.platform == 'darwin' else 'xdg-open',
                            str(out)], check=False)
    return 0


if __name__ == '__main__':
    sys.exit(main())
