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


def _suggest_view(sg):
    """把 suggest 整理成界面能直接用的样子。

    move 用的是 target（路径），new 用的是 newName（类别名），
    两者合并成一个 target 字段，外加 targetLabel 供显示。
    """
    if not sg:
        return None
    mode = sg.get("mode")
    if mode == "move":
        target = sg.get("target") or ""
        target_label = sg.get("targetLabel") or ""
    elif mode == "new":
        target = "__new__"
        target_label = sg.get("newName") or ""
    elif mode == "keep":
        target = "__keep__"
        target_label = "保持原位"
    else:
        target = ""
        target_label = ""
    return {
        "mode": mode,
        "target": target,
        "targetLabel": target_label,
        "newParent": sg.get("newParent"),
        "needsCheck": bool(sg.get("needsCheck")),
        "evidence": sg.get("evidence") or [],
        "reason": sg.get("reason") or "",
    }


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
                # AI 判断链（有就带，没有为空）
                "ai": (x.get("suggest") or {}).get("aiJudgement") or None,
                "suggest": _suggest_view(x.get("suggest")),
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
.kv{display:flex;gap:10px;padding:2px 0;font-size:11.5px;line-height:1.55}
.kv i{font-style:normal;color:var(--fg3);flex:0 0 34px;font-family:var(--mono)}
.kv span{color:var(--fg2);flex:1;word-break:break-word}
.kv span b{color:var(--accent);font-weight:600}
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

<div class="stage"><div class="wrap"><div id="proposals"></div><div id="main"></div></div></div>

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
const THEME_KEY = 'fo-theme';
const DEC_KEY = 'fo-dec-' + DATA.root.replace(/[\\/:*?"<>|]/g,'_');

/* 决策：key = "nodePath::idx" → {target, decided} */
let dec = {};

function esc(s){
  return String(s ?? '').replace(/[&<>"]/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}
function label(p){
  if(p === '__keep__') return '保持原位';
  if(p === '__delete__') return '移到待删除';
  const z = (DATA.zones || []).find(x =>
    (typeof x === 'object' ? x.path : x) === p);
  return z && typeof z === 'object' ? z.label : String(p).split(/[\\/]/).pop();
}
function gk(np, i){ return np + '::' + i; }

/* 主题 */
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
  localStorage.setItem(THEME_KEY, next);
  document.documentElement.dataset.theme = next;
  syncBtn();
};

/* 初始化：默认全填好，只有需要确认的空着 */
function init(){
  (DATA.nodes || []).forEach(n => {
    (n.groups || []).forEach((g, i) => {
      const k = gk(n.path, i);
      if(dec[k]) return;
      const sg = g.suggest || {};
      if(sg.mode === 'keep') dec[k] = {target: '__keep__'};
      else if(sg.mode === 'move') dec[k] = {target: sg.target || ''};
      else if(sg.mode === 'new') dec[k] = {target: '__new__'};
      else dec[k] = {target: ''};
    });
  });
}

/* 统计 */
function stats(){
  let total = 0, keep = 0, move = 0, del = 0, need = 0, withAI = 0;
  (DATA.nodes || []).forEach(n => {
    (n.groups || []).forEach((g, i) => {
      const d = dec[gk(n.path, i)] || {};
      total++;
      if(g.ai) withAI++;
      if(!d.target) need++;
      else if(d.target === '__keep__') keep++;
      else if(d.target === '__delete__') del++;
      else move++;
    });
  });
  return {total, keep, move, del, need, withAI};
}

function renderCore(){
  const s = stats();
  const nEl = document.getElementById('coreN');
  nEl.textContent = s.need;
  nEl.classList.toggle('zero', s.need === 0);

  document.getElementById('coreLbl').textContent =
    s.need === 0 ? '全部已就绪' : '组需要你确认';

  let sub = `共 <b>${s.total}</b> 组：` +
            `<b>${s.keep}</b> 保持原位 · <b>${s.move}</b> 移到别处 · ` +
            `<b>${s.del}</b> 待删除。`;
  if(s.withAI) sub += `<br>其中 <b>${s.withAI}</b> 组有 AI 判断链，可点开看依据。`;
  document.getElementById('coreSub').innerHTML = sub;

  document.getElementById('coreCta').innerHTML =
    `<button class="btn" onclick="showList()">生成执行清单</button>` +
    `<button class="btn gh" onclick="acceptAll()">全部采纳建议</button>`;

  renderProposals();
}

/* 候选类别 */
function renderProposals(){
  const el = document.getElementById('proposals');
  if(!el) return;
  const p = DATA.proposedCategories || {};
  const keys = Object.keys(p);
  if(!keys.length){ el.innerHTML = ''; return; }

  el.innerHTML = `
    <div class="needcard" style="border-color:#7c4dbd">
      <div class="hd" style="background:#7c4dbd14;border-color:#7c4dbd40;color:#7c4dbd">
        可能的候选类别　${keys.length} 个
      </div>
      ${keys.map(k => {
        const v = p[k];
        return `<div class="needitem">
          <div>
            <div class="nm">${esc(k)}</div>
            <div class="ds">${v.count} 组共享这一特征
              ${v.basis ? `（${esc(v.basis)}）` : ''}
              ${v.kinds && v.kinds.length ? `<br>内容形态：${esc(v.kinds.join(' / '))}` : ''}
            </div>
            <div class="ds" style="color:var(--fg3);margin-top:3px">
              ${v.members.map(m => esc(m)).join('　·　')}
            </div>
          </div><div></div>
        </div>`;
      }).join('')}
      <div style="padding:11px 16px;font-size:12px;color:var(--fg3);
                  border-top:1px solid var(--line)">
        这些是<b>建议</b>，不会自动创建。确认有用的话，
        把它们加进规则包的 categories，下次就能自动识别。
      </div>
    </div>`;
}

/* AI 判断链 */
function renderChain(ai){
  if(!ai) return '';
  const rows = [];
  const kv = (k, v) => `<div class="kv"><i>${k}</i><span>${esc(v)}</span></div>`;

  if(ai.verificationTier)
    rows.push(kv('级别', ai.verificationTier));
  if(ai.hypothesis)
    rows.push(kv('猜想', ai.hypothesis));

  const ver = ai.verification || {};
  if(ver.method) rows.push(kv('验证', ver.method));
  if(ver.findings) rows.push(kv('结果', ver.findings));
  if(ver.disproved && ver.disproved.length)
    rows.push(kv('推翻', ver.disproved.join('；')));

  const conf = ai.confirmation || {};
  if(conf.why || conf.holds !== undefined){
    const mark = conf.holds === false ? '✗ 站不住' : '✓ 站得住';
    rows.push(kv('确认', `${mark}　${conf.why || ''}　${conf.confidence || ''}`));
  }
  if(conf.residualRisk)
    rows.push(kv('残余', conf.residualRisk));

  return rows.length ? `<div class="det" style="display:block;margin-top:7px">${rows.join('')}</div>` : '';
}

/* 主渲染 */
function render(){
  const main = document.getElementById('main');
  let html = '';

  // ① 需要确认的组
  const needs = [];
  (DATA.nodes || []).forEach(n => {
    (n.groups || []).forEach((g, i) => {
      const d = dec[gk(n.path, i)] || {};
      const sg = g.suggest || {};
      if(!d.target || sg.needsCheck) needs.push({n, g, i, d, sg});
    });
  });

  if(needs.length){
    html += `<div class="needcard"><div class="hd">需要你确认　${needs.length} 组</div>`;
    needs.forEach(({n, g, i, d, sg}) => {
      const k = gk(n.path, i);
      html += `<div class="needitem">
        <div>
          <div class="nm">${esc(g.name)}</div>
          <div class="ds">${esc(n.name)} · ${g.n} 项
            ${g.why ? '· ' + esc(g.why.slice(0,60)) : ''}</div>
          ${sg.evidence && sg.evidence.length ?
            `<div class="ds" style="color:var(--fg3);margin-top:3px">
               ${sg.evidence.map(esc).join('；')}</div>` : ''}
          ${renderChain(g.ai)}
        </div>
        <div>
          <button class="pick${d.target?' has':''}" data-k="${esc(k)}"
            onclick="openPanel(this.dataset.k)">
            <span>${esc(d.target === '__new__' ? '＋ 新建类别' : label(d.target || ''))}</span>
            <span class="caret">▾</span>
          </button>
        </div>
      </div>`;
    });
    html += '</div>';
  }

  // ② 折叠：按去向分组
  const fold = (title, items, fmt) => {
    if(!items.length) return '';
    let h = `<div class="fold"><div class="fold-hd"
      onclick="this.parentElement.classList.toggle('open')">
      <span class="t">${title}</span><span class="c">${items.length} 组</span>
      <span class="chev">›</span></div><div class="fold-bd">`;
    items.forEach(it => { h += fmt(it); });
    return h + '</div></div>';
  };

  const all = [];
  (DATA.nodes || []).forEach(n => {
    (n.groups || []).forEach((g, i) => {
      all.push({n, g, i, d: dec[gk(n.path, i)] || {}});
    });
  });

  html += fold('保持原位',
    all.filter(x => x.d.target === '__keep__').filter(x => !x.g.suggest?.needsCheck),
    x => `<div class="row">
      <span class="n">${esc(x.g.name)}<span class="k">${esc(x.n.name)}</span></span>
      <span class="t">${x.g.n} 项</span>
      <span class="sz"></span></div>`);

  html += fold('移到别处',
    all.filter(x => x.d.target && x.d.target !== '__keep__'
                    && x.d.target !== '__delete__' && x.d.target !== '__new__'),
    x => `<div class="row">
      <span class="n">${esc(x.g.name)}<span class="k">${esc(x.n.name)}</span></span>
      <span class="t acc">→ ${esc(label(x.d.target))}</span>
      <span class="sz">${x.g.n} 项</span></div>`);

  html += fold('移到待删除',
    all.filter(x => x.d.target === '__delete__'),
    x => `<div class="row">
      <span class="n">${esc(x.g.name)}<span class="k">${esc(x.n.name)}</span></span>
      <span class="t err">待删除</span>
      <span class="sz">${x.g.n} 项</span></div>`);

  main.innerHTML = html;
}

/* 选择面板 */
let panelKey = null;

function openPanel(k){
  panelKey = k;
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
  if(panelKey){ dec[panelKey] = {target: v}; save(); }
  closePanel(); render(); renderCore(); renderStatus();
}

function renderPanel(q){
  const list = document.getElementById('plist');
  const cur = dec[panelKey]?.target || '';
  const lower = q.toLowerCase();
  const hit = t => !q || t.toLowerCase().indexOf(lower) >= 0;

  let h = '';
  [['__keep__','保持原位'], ['__delete__','移到待删除']].forEach(([v,t]) => {
    if(hit(t)) h += `<div class="pitem${cur===v?' on':''}"
      onclick="choose('${v}')">
      <span class="ck">${cur===v?'✓':''}</span>${t}</div>`;
  });

  const byTop = {};
  (DATA.zones || []).forEach(z => {
    const zp = (typeof z === 'string') ? z : z.path;
    const zl = (typeof z === 'string') ? zp : (z.label || z.path);
    const depth = (typeof z === 'string') ? 1 : (z.depth || 1);
    const top = zl.split('/')[0];
    (byTop[top] = byTop[top] || []).push({path: zp, label: zl, depth});
  });

  Object.keys(byTop).sort().forEach(top => {
    const items = byTop[top].filter(z => hit(z.label));
    if(!items.length) return;
    const self = items.find(x => x.depth === 1);
    if(self) h += `<div class="pgroup">${esc(top)}</div>`;
    if(self) h += `<div class="pitem${cur===self.path?' on':''}"
      data-p="${esc(self.path)}" onclick="choose(this.dataset.p)">
      <span class="ck">${cur===self.path?'✓':''}</span>${esc(top)}</div>`;
    items.filter(x => x.depth > 1).forEach(z => {
      h += `<div class="pitem sub${cur===z.path?' on':''}"
        data-p="${esc(z.path)}" onclick="choose(this.dataset.p)">
        <span class="ck">${cur===z.path?'✓':''}</span>
        ${esc(z.label.split('/').slice(1).join('/'))}</div>`;
    });
  });

  list.innerHTML = h || `<div class="pitem" style="color:var(--fg3)">没有匹配</div>`;
}

/* 操作 */
function acceptAll(){
  Object.keys(dec).forEach(k => {
    if(!dec[k].target) dec[k] = {target: '__keep__'};
  });
  save(); render(); renderCore(); renderStatus();
}

function showList(){
  const mv = [], kp = [], dl = [];
  (DATA.nodes || []).forEach(n => {
    (n.groups || []).forEach((g, i) => {
      const d = dec[gk(n.path, i)] || {};
      const t = d.target || '__keep__';
      if(t === '__keep__') kp.push(n.path + '\t' + g.name + '\t保持原位');
      else if(t === '__delete__') dl.push(n.path + '\t' + g.name + '\t待删除');
      else mv.push(n.path + '\t' + g.name + '\t' + t);
    });
  });
  let txt = `# 整理清单  ${DATA.generated}\n\n`;
  txt += `## 移到别处（${mv.length}）\n${mv.join('\n') || '（无）'}\n\n`;
  txt += `## 移到待删除（${dl.length}）\n${dl.join('\n') || '（无）'}\n\n`;
  txt += `## 保持原位（${kp.length}）\n${kp.slice(0,50).join('\n')}`;
  if(kp.length > 50) txt += `\n…另有 ${kp.length - 50} 组`;
  document.getElementById('mTitle').textContent = '执行清单';
  document.getElementById('mBody').textContent = txt;
  document.getElementById('mask').classList.add('on');
}
function closeM(){ document.getElementById('mask').classList.remove('on'); }

function save(){
  try{ localStorage.setItem(DEC_KEY, JSON.stringify({dec, at:Date.now()})); }catch(e){}
}
function load(){
  try{
    const raw = localStorage.getItem(DEC_KEY);
    if(!raw) return false;
    const o = JSON.parse(raw);
    if(!o || !o.at || Date.now()-o.at > 30*864e5) return false;
    dec = o.dec || {};
    return Object.keys(dec).length > 0;
  }catch(e){ return false; }
}

function renderStatus(){
  const s = stats();
  document.getElementById('stA').innerHTML = `共 <b>${s.total}</b> 组`;
  document.getElementById('stB').innerHTML =
    `原位 <b>${s.keep}</b> · 移动 <b>${s.move}</b> · 待删 <b>${s.del}</b>`;
  document.getElementById('stC').innerHTML =
    s.need ? `<b>${s.need}</b> 组待定` : '全部已定';
}

/* 对外接口：让 agent 能读状态 */
window.foState = () => ({dec, stats: stats()});
window.foPending = () => {
  const out = [];
  (DATA.nodes || []).forEach(n => {
    (n.groups || []).forEach((g, i) => {
      const d = dec[gk(n.path, i)] || {};
      if(!d.target || g.suggest?.needsCheck)
        out.push({node: n.name, group: g.name, target: d.target || null});
    });
  });
  return out;
};

const th = localStorage.getItem(THEME_KEY);
if(th) document.documentElement.dataset.theme = th;
syncBtn();
if(!load()) init();
render(); renderCore(); renderStatus();
document.getElementById('mask').onclick = e => {
  if(e.target.id === 'mask') closeM();
};
document.getElementById('panel').onclick = e => {
  if(e.target.id === 'panel') closePanel();
};
document.getElementById('pq').oninput = e => renderPanel(e.target.value);
document.addEventListener('keydown', e => {
  if(e.key === 'Escape'){ closeM(); closePanel(); }
});
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

    # 为每组附加建议；顺带收集匹配不上的，用于发现新类别
    unmatched = []
    for n in nodes:
        for g in n['groups']:
            g['relationText'] = dec_mod.RELATION_TEXT.get(
                g.get('relation', ''), g.get('relation', ''))
            raw_sug = dec_mod.suggest_group(
                g['name'], n['name'], zones_raw, n['path'])

            # 先记 AI 判断链（decisions.py 回填时写在这里）
            if raw_sug.get('aiJudgement'):
                g['ai'] = raw_sug['aiJudgement']

            # 界面用的视图要重新算一遍——build_model 里算的是空的
            g['suggest'] = _suggest_view(raw_sug)

            if raw_sug['mode'] == 'keep' and raw_sug.get('evidence'):
                for k in ('installer', 'media', 'image', 'code',
                          'document', 'archive'):
                    if any(k in e for e in raw_sug['evidence']):
                        unmatched.append((g['name'], k))
                        break

    data = {
        'root': root,
        'generated': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'nodes': nodes,
        'zones': zones_raw,
        'proposedCategories': {
            stem: {'count': info['count'],
                   'kinds': sorted(info['kinds']),
                   'basis': info['basis'],
                   'members': info['members'][:6]}
            for stem, info in dec_mod.propose_new_categories(unmatched).items()
        },
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
