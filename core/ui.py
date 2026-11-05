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
/* ── 双主题：跟随系统 + 手动覆盖 ── */
:root{
  --bg:#fbfcfd; --surface:#fff; --surface2:#f5f7fa; --hover:#eff3f8;
  --line:#e3e8ef; --line2:#cfd6e0;
  --fg:#111820; --fg2:#5a6572; --fg3:#8d98a6;
  --accent:#2563c4; --accent-d:#1d4fa3; --accent-fg:#fff;
  --ok:#157f52; --warn:#a86610; --err:#c23c46;
  --new:#7c4dbd;
  --r-sm:6px; --r:10px; --r-lg:16px;
  --shadow:0 1px 3px rgba(16,32,64,.07),0 1px 2px rgba(16,32,64,.04);
  --shadow-lg:0 24px 64px rgba(16,32,64,.18);
  --font:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  --mono:ui-monospace,"SF Mono",Consolas,monospace;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --bg:#0c0f13; --surface:#14181e; --surface2:#1a1f27; --hover:#212832;
    --line:#242b35; --line2:#333c49;
    --fg:#e8ecf2; --fg2:#9aa5b4; --fg3:#65707f;
    --accent:#5a9ee6; --accent-d:#4085cc; --accent-fg:#08121e;
    --ok:#4cb380; --warn:#d4a03c; --err:#e0707a;
    --new:#a884e0;
    --shadow:0 1px 2px rgba(0,0,0,.5);
    --shadow-lg:0 24px 64px rgba(0,0,0,.65);
  }
}
html[data-theme="dark"]{
  --bg:#0c0f13; --surface:#14181e; --surface2:#1a1f27; --hover:#212832;
  --line:#242b35; --line2:#333c49;
  --fg:#e8ecf2; --fg2:#9aa5b4; --fg3:#65707f;
  --accent:#5a9ee6; --accent-d:#4085cc; --accent-fg:#08121e;
  --ok:#4cb380; --warn:#d4a03c; --err:#e0707a;
  --new:#a884e0;
  --shadow:0 1px 2px rgba(0,0,0,.5);
  --shadow-lg:0 24px 64px rgba(0,0,0,.65);
}

*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%}
body{
  font:14.5px/1.6 var(--font); background:var(--bg); color:var(--fg);
  -webkit-font-smoothing:antialiased;
  display:flex; flex-direction:column; overflow:hidden;
  transition:background .18s,color .18s;
}
b,strong,h1,h2{font-weight:600}
code,.mono{font-family:var(--mono)}
::selection{background:var(--accent);color:var(--accent-fg)}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-thumb{background:var(--line2);border-radius:5px;
  border:3px solid var(--bg)}

/* ── 顶栏 ── */
.top{
  flex:0 0 auto; display:flex; align-items:center; gap:12px;
  padding:12px 24px; background:var(--surface);
  border-bottom:1px solid var(--line);
}
.logo{font-weight:600;font-size:14.5px}
.logo em{font-style:normal;font-weight:400;font-size:11.5px;
  color:var(--fg3);font-family:var(--mono);margin-left:8px}
.grow{flex:1}
.tbtn{
  width:32px;height:32px;border-radius:8px;border:1px solid var(--line);
  background:transparent;color:var(--fg2);cursor:pointer;font-size:14px;
  display:flex;align-items:center;justify-content:center;transition:.14s;
}
.tbtn:hover{background:var(--hover);color:var(--fg)}

/* ── 视觉核心 ── */
.core{
  flex:0 0 auto; padding:34px 24px 26px; text-align:center;
  background:var(--surface); border-bottom:1px solid var(--line);
}
.core .num{
  font-size:68px; font-weight:700; line-height:1; letter-spacing:-.045em;
  font-variant-numeric:tabular-nums; font-family:var(--mono);
  color:var(--accent); display:block; margin-bottom:8px;
}
.core .num.zero{color:var(--ok)}
.core .lbl{font-size:16.5px;font-weight:600;margin-bottom:8px}
.core .sub{
  font-size:13.5px;color:var(--fg3);max-width:580px;margin:0 auto;
  line-height:1.75;
}
.core .sub b{color:var(--fg2);font-weight:600}
.core .cta{
  display:flex;gap:10px;justify-content:center;margin-top:20px;flex-wrap:wrap;
}

/* ── 内容 ── */
.stage{flex:1;overflow-y:auto;min-height:0}
.wrap{max-width:1000px;margin:0 auto;padding:20px 24px 60px}

.node{
  background:var(--surface); border:1px solid var(--line);
  border-radius:var(--r-lg); margin-bottom:10px; overflow:hidden;
  box-shadow:var(--shadow);
}
.node-hd{
  display:flex;align-items:center;gap:11px;padding:12px 16px;
  cursor:pointer;user-select:none;transition:background .12s;
}
.node-hd:hover{background:var(--hover)}
.node-hd .dot{
  width:7px;height:7px;border-radius:50%;flex:0 0 7px;
  background:var(--line2);transition:.2s;
}
.node.attn .node-hd .dot{
  background:var(--warn);
  box-shadow:0 0 0 3px color-mix(in srgb,var(--warn) 20%,transparent);
}
.node-hd .t{flex:1;min-width:0}
.node-hd .t b{font-size:13.5px}
.node-hd .t span{
  font-size:11.5px;color:var(--fg3);margin-left:8px;font-family:var(--mono);
}
.node-hd .tag{
  font-size:11px;padding:2px 8px;border-radius:9px;font-weight:600;
  white-space:nowrap;
}
.node-hd .tag.warn{
  background:color-mix(in srgb,var(--warn) 15%,transparent);color:var(--warn);
}
.node-hd .tag.ok{color:var(--fg3);font-weight:400}
.node-hd .chev{color:var(--fg3);font-size:11px;transition:transform .16s}
.node.open .chev{transform:rotate(90deg)}
.node-bd{display:none;border-top:1px solid var(--line)}
.node.open .node-bd{display:block}

/* ── 组行 ── */
.grp{
  display:grid; grid-template-columns:1fr 280px; gap:14px;
  align-items:start; padding:11px 16px;
  border-bottom:1px solid color-mix(in srgb,var(--line) 55%,transparent);
}
.grp:last-child{border-bottom:0}
.grp:hover{background:var(--hover)}
.grp.attn{background:color-mix(in srgb,var(--warn) 4%,transparent)}
.grp .bd{min-width:0;padding-top:2px}
.grp .nm{
  font-size:13px;font-weight:500;display:flex;align-items:center;
  gap:7px;flex-wrap:wrap;
}
.grp .nm .rel{
  font-size:9.5px;padding:1px 6px;border-radius:7px;
  background:var(--surface2);color:var(--fg3);font-family:var(--mono);
  font-weight:400;
}
.grp .meta{font-size:11.5px;color:var(--fg3);margin-top:3px;line-height:1.55}

/* ── 目标选择器 ── */
.tgt{display:flex;flex-direction:column;gap:5px}
.tgt .row{display:flex;gap:5px}
select.z{
  flex:1;min-width:0;padding:7px 10px;border-radius:var(--r-sm);
  background:var(--surface2);border:1px solid var(--line);color:var(--fg2);
  font:12.5px var(--font);cursor:pointer;outline:none;transition:.14s;
}
select.z:focus{border-color:var(--accent)}
select.z.keep{color:var(--fg3)}
select.z.move{
  color:var(--accent);border-color:color-mix(in srgb,var(--accent) 45%,var(--line));
  background:color-mix(in srgb,var(--accent) 7%,var(--surface2));
}
select.z.new{
  color:var(--new);border-color:color-mix(in srgb,var(--new) 45%,var(--line));
  background:color-mix(in srgb,var(--new) 7%,var(--surface2));
}
select.z.need{color:var(--warn);border-color:var(--warn);
  background:color-mix(in srgb,var(--warn) 7%,var(--surface2))}
input.newdir{
  flex:1;min-width:0;padding:7px 10px;border-radius:var(--r-sm);
  background:var(--surface2);border:1px solid var(--new);color:var(--new);
  font:12.5px var(--mono);outline:none;
}
input.newdir::placeholder{color:var(--fg3)}
.tgt .fx{
  width:30px;flex:0 0 30px;border-radius:var(--r-sm);
  border:1px solid var(--line);background:transparent;color:var(--fg3);
  cursor:pointer;font-size:13px;transition:.14s;
}
.tgt .fx:hover{background:var(--hover);color:var(--fg)}

/* ── 按钮 ── */
.btn{
  background:var(--accent);color:var(--accent-fg);border:0;
  padding:10px 22px;border-radius:var(--r);font:600 13.5px var(--font);
  cursor:pointer;transition:.15s;
}
.btn:hover{background:var(--accent-d)}
.btn:active{transform:translateY(1px)}
.btn.lg{padding:12px 30px;font-size:14.5px}
.btn.gh{background:transparent;border:1px solid var(--line2);color:var(--fg2)}
.btn.gh:hover{background:var(--hover);color:var(--fg)}
.btn.sm{padding:6px 13px;font-size:12.5px}

/* ── 状态栏 ── */
.status{
  flex:0 0 auto;display:flex;align-items:center;gap:12px;
  padding:10px 24px;background:var(--surface);border-top:1px solid var(--line);
  font-size:12.5px;color:var(--fg3);
}
.status b{color:var(--fg2);font-family:var(--mono)}
.status .sep{color:var(--line2)}
.sdot{width:7px;height:7px;border-radius:50%;display:inline-block}

/* ── 弹层 ── */
.mask{
  position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:99;
  display:none;align-items:center;justify-content:center;padding:26px;
  backdrop-filter:blur(3px);
}
.mask.on{display:flex}
.modal{
  background:var(--surface);border:1px solid var(--line2);border-radius:var(--r-lg);
  width:100%;max-width:720px;max-height:76vh;display:flex;flex-direction:column;
  box-shadow:var(--shadow-lg);
}
.modal h3{
  padding:15px 20px;font-size:14px;font-weight:600;
  border-bottom:1px solid var(--line);
}
.modal pre{
  flex:1;overflow:auto;margin:0;padding:15px 20px;
  font:11.5px/1.7 var(--mono);color:var(--fg2);
  white-space:pre-wrap;word-break:break-all;
}
.modal .ft{
  padding:12px 20px;border-top:1px solid var(--line);
  display:flex;gap:9px;justify-content:flex-end;
}

@media(max-width:800px){
  .core{padding:24px 16px 20px}
  .core .num{font-size:50px}
  .wrap{padding:14px 13px 50px}
  .grp{grid-template-columns:1fr;gap:9px}
  .top{padding:10px 14px}
  .logo em{display:none}
}
"""

BODY = r"""

<div class="top">
  <div class="logo">整理方案<em>__ROOT__</em></div>
  <div class="grow"></div>
  <button class="tbtn" id="themeBtn" title="切换浅色 / 深色">◐</button>
</div>

<div class="core">
  <span class="num" id="coreNum">—</span>
  <div class="lbl" id="coreLbl"></div>
  <div class="sub" id="coreSub"></div>
  <div class="cta" id="coreCta"></div>
</div>

<div class="stage"><div class="wrap" id="main"></div></div>

<div class="status">
  <span id="stA">—</span><span class="sep">·</span>
  <span id="stB">—</span>
  <div class="grow"></div>
  <span id="stC"></span>
</div>

<div class="mask" id="mask">
  <div class="modal">
    <h3 id="mTitle"></h3>
    <pre id="mBody"></pre>
    <div class="ft">
      <button class="btn gh sm" onclick="closeModal()">关闭</button>
    </div>
  </div>
</div>

"""

JS = r"""
const DATA = __DATA__;
const THEME_KEY = 'fo-theme';
const DEC_KEY = 'fo-dec2-' + DATA.root.replace(/[\/:*?"<>|]/g,'_');

/* 决策：key = nodePath::idx → {mode, target, decided}
   mode: keep | move | new                                        */
let dec = {};

/* ══════ 工具 ══════ */
const esc = s => String(s ?? '').replace(/[&<>"]/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const tail = p => {
  if(p === '__keep__') return '保持原位';
  const z = (DATA.zones || []).find(x => x.path === p);
  if(z) return z.label;
  return String(p).replace(/[\/]+$/,'').split(/[\/]/).pop() || p;
};
const num = n => n == null ? '—' : (n >= 1000 ? (n/1000).toFixed(1)+'k' : String(n));
const gk = (p, i) => p + '::' + i;

/* ══════ 主题 ══════ */
function initTheme(){
  let t = null;
  try{ t = localStorage.getItem(THEME_KEY); }catch(e){}
  if(t === 'dark' || t === 'light') document.documentElement.dataset.theme = t;
  syncThemeBtn();
  document.getElementById('themeBtn').onclick = () => {
    const isDark = getComputedStyle(document.body)
      .getPropertyValue('--bg').trim().startsWith('#0')
      || document.documentElement.dataset.theme === 'dark';
    const next = isDark ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try{ localStorage.setItem(THEME_KEY, next); }catch(e){}
    syncThemeBtn();
  };
}
function syncThemeBtn(){
  const t = document.documentElement.dataset.theme;
  const dark = t === 'dark' ||
    (!t && window.matchMedia('(prefers-color-scheme: dark)').matches);
  const b = document.getElementById('themeBtn');
  b.textContent = dark ? '☾' : '☀';
  b.title = dark ? '当前深色 · 点击切换浅色' : '当前浅色 · 点击切换深色';
}

/* ══════ 建议算法 ══════ */
/* 跨类关键词 → 该去哪个已有区（按区名匹配） */
const CROSS = [
  { keys:['番剧','动画','动漫'], to:['影视','Video','番'] },
  { keys:['电影','影片','剧集','短剧','MV','录屏'], to:['影视','Video'] },
  { keys:['ASMR','音声'], to:['ASMR'] },
  { keys:['galgame','成人'], to:['H_Library'] },
  { keys:['安装包','破解','插件'], to:['软件安装包'] },
  { keys:['课程','教程','教材'], to:['文档','个人文档'] },
  { keys:['镜像','刷机','装机','驱动'], to:['系统镜像'] },
  { keys:['模型','工作流','词库','LoRA','embedding','ComfyUI','训练'], to:['AI工具'] },
  { keys:['游戏','Minecraft'], to:['游戏'] },
  { keys:['备份','还原点'], to:['backup'] },
];

/* 这些关键词暗示"该新建一个分类" */
const NEW_HINT = [
  { keys:['刷机','ROM','TWRP'], name:'安卓刷机' },
  { keys:['电子书','教材','PDF','小说'], name:'电子书' },
  { keys:['字体','字库'], name:'字体' },
  { keys:['驱动'], name:'驱动' },
  { keys:['壁纸'], name:'壁纸' },
];

/* 建议由 core/decisions.py 预先算好，前端只读 */

/* 从组名提取一个短分类名 */
function groupIdiom(name){
  const s = name.split(/[（(\/·,，]/)[0].trim();
  return s.length > 12 ? s.slice(0, 12) : (s || '新分类');
}

/* ══════ 初始化 ══════ */
function initDec(){
  DATA.nodes.forEach(n => {
    if(!n.groups) return;
    n.groups.forEach((g, i) => {
      const k = gk(n.path, i);
      if(dec[k]) return;
      const sg = g.suggest || {mode:'keep'};
      dec[k] = {
        mode: sg.mode,
        target: sg.target || '',
        name: sg.newName || '',
        parent: sg.newParent || '',
        decided: false,
        attn: !!sg.needsCheck,
      };
    });
  });
}
function looksCross(name){
  return ['番','音声','ASMR','galgame','安装包','镜像','课程','电影','MV',
          '刷机','电子书','字体','驱动'].some(k => name.indexOf(k) >= 0);
}

/* ══════ 统计 ══════ */
function stats(){
  let total=0, keep=0, move=0, newd=0, attn=0, manual=0;
  DATA.nodes.forEach(n => {
    if(!n.groups) return;
    n.groups.forEach((g,i) => {
      const d = dec[gk(n.path,i)];
      if(!d) return;
      total++;
      if(d.mode === 'keep') keep++;
      else if(d.mode === 'move') move++;
      else newd++;
      if(d.attn && !d.decided) attn++;
      if(d.decided) manual++;
    });
  });
  return {total, keep, move, newd, attn, manual};
}

/* ══════ 渲染核心 ══════ */
function renderCore(){
  const s = stats();
  const nEl = document.getElementById('coreNum');
  const need = s.attn + s.manual;
  nEl.textContent = need;
  nEl.classList.toggle('zero', need === 0);

  document.getElementById('coreLbl').textContent =
    need === 0 ? '方案已就绪' : '组需要你看一眼';

  let parts = [`一共 <b>${s.total}</b> 组。`];
  if(s.keep) parts.push(`<b>${s.keep}</b> 组保持原位`);
  if(s.move) parts.push(`<b>${s.move}</b> 组移到已有目录`);
  if(s.newd) parts.push(`<b>${s.newd}</b> 组建议新建目录`);
  let sub = parts.join('，') + '。';

  if(need > 0){
    sub += `<br>下面标了橙色的 <b>${s.attn}</b> 组，我拿不准该放哪——` +
           `尤其「新建目录」的，请确认名字对不对。`;
  }
  document.getElementById('coreSub').innerHTML = sub;

  document.getElementById('coreCta').innerHTML =
    `<button class="btn" onclick="acceptAll()">全部采纳</button>` +
    `<button class="btn gh" onclick="showList()">查看清单</button>`;
}

/* ══════ 渲染列表 ══════ */
function renderList(){
  const main = document.getElementById('main');
  const zones = DATA.zones || [];
  const nodes = DATA.nodes.filter(n => n.groups && n.groups.length);
  if(!nodes.length){ main.innerHTML = ''; return; }

  main.innerHTML = nodes.map(n => {
    const attn = n.groups.filter((g,i) => {
      const d = dec[gk(n.path,i)];
      return d && d.attn && !d.decided;
    }).length;

    let h = `<div class="node${attn?' attn':''}" data-p="${esc(n.path)}">
      <div class="node-hd" onclick="this.parentElement.classList.toggle('open')">
        <span class="dot"></span>
        <span class="t"><b>${esc(n.name)}</b><span>${n.groups.length} 组 · ${
          num(n.total_units || n.groups.reduce((s,g)=>s+g.n,0))} 单元</span></span>
        ${attn ? `<span class="tag warn">${attn} 组待确认</span>`
               : `<span class="tag ok">已安排</span>`}
        <span class="chev">›</span>
      </div><div class="node-bd">`;

    n.groups.forEach((g,i) => {
      const k = gk(n.path,i);
      const d = dec[k];
      h += `<div class="grp${d.attn && !d.decided ? ' attn' : ''}" data-k="${esc(k)}">
        <div class="bd">
          <div class="nm">${esc(g.name)}${
            g.relationText ? `<span class="rel">${esc(g.relationText)}</span>` : ''}</div>
          <div class="meta">${g.n} 个单元${
            g.why ? ' · ' + esc(g.why.slice(0, 64)) : ''}</div>
        </div>
        <div class="tgt" id="tgt-${esc(k).replace(/[^a-zA-Z0-9]/g,'_')}">
          ${targetHtml(k, d, zones)}
        </div>
      </div>`;
    });
    return h + '</div></div>';
  }).join('');

  bindTargets(main);
}

function targetHtml(k, d, zones){
  if(!d) return '';
  const cls = d.mode === 'keep' ? 'keep' : d.mode === 'move' ? 'move'
    : (d.attn && !d.decided ? 'need' : 'new');

  let opts = `<option value="__keep__"${d.mode==='keep'?' selected':''}>保持原位</option>`;

  // 按一级分组，用 optgroup
  const byTop = {};
  zones.forEach(z => {
    const top = z.label.split('/')[0];
    if(!byTop[top]) byTop[top] = [];
    byTop[top].push(z);
  });
  Object.keys(byTop).sort().forEach(top => {
    const list = byTop[top];
    // 一级自己也是一个选项
    const topZone = list.find(x => x.depth === 1);
    if(topZone){
      const on = d.mode === 'move' && d.target === topZone.path;
      opts += `<option value="${esc(topZone.path)}"${on?' selected':''}>` +
              `${esc(top)}</option>`;
    }
    // 子级放 optgroup
    const subs = list.filter(x => x.depth > 1);
    if(subs.length){
      opts += `<optgroup label="${esc(top)} 里的子目录">`;
      subs.forEach(z => {
        const on = d.mode === 'move' && d.target === z.path;
        const ind = '　'.repeat(z.depth - 2);
        opts += `<option value="${esc(z.path)}"${on?' selected':''}>` +
                `${ind}${esc(z.label.split('/').slice(1).join('/'))}</option>`;
      });
      opts += '</optgroup>';
    }
  });
  opts += `<option value="__new__"${d.mode==='new'?' selected':''}>＋ 新建目录…</option>`;

  let html = `<div class="row">
    <select class="z ${cls}" data-k="${esc(k)}">${opts}</select>
    <button class="fx" title="回到建议值" onclick="resetOne('${esc(k).replace(/'/g,"\'")}')">↺</button>
  </div>`;

  if(d.mode === 'new'){
    html += `<input class="newdir" data-nk="${esc(k)}"
      value="${esc(d.name || '')}" placeholder="新目录名">`;
  }
  return html;
}

function bindTargets(root){
  root.querySelectorAll('select.z').forEach(el => {
    el.onchange = () => {
      const k = el.dataset.k;
      const d = dec[k];
      if(el.value === '__new__'){
        d.mode = 'new';
        d.name = d.name || groupIdiom('');
        d.attn = true;
      } else if(el.value === '__keep__'){
        d.mode = 'keep'; d.target = ''; d.attn = false;
      } else {
        d.mode = 'move'; d.target = el.value; d.attn = false;
      }
      d.decided = true;
      save(); repaint();
    };
  });
  root.querySelectorAll('input.newdir').forEach(el => {
    el.oninput = () => {
      const k = el.dataset.nk;
      if(dec[k]){ dec[k].name = el.value; dec[k].decided = true; save(); }
    };
    el.onblur = () => { repaint(); };
    el.onkeydown = ev => { if(ev.key === 'Enter'){ ev.preventDefault(); el.blur(); } };
  });
}

function resetOne(k){
  const n = DATA.nodes.find(x => k.indexOf(x.path + '::') === 0);
  if(!n) return;
  const idx = +k.split('::').pop();
  const g = n.groups[idx];
  const s = suggest(g.name, n.name, DATA.zones || []);
  dec[k] = {
    mode: s.mode, target: s.target || '', name: s.name || '',
    decided: false,
    attn: s.mode === 'new' || (s.mode === 'keep' && looksCross(g.name)),
  };
  save(); repaint();
}

function repaint(){
  renderCore();
  renderList();
  renderStatus();
}

/* ══════ 状态栏 ══════ */
function renderStatus(){
  const s = stats();
  document.getElementById('stA').innerHTML = `共 <b>${s.total}</b> 组`;
  document.getElementById('stB').innerHTML =
    `原位 <b>${s.keep}</b> · 移动 <b>${s.move}</b> · 新建 <b>${s.newd}</b>`;
  document.getElementById('stC').innerHTML =
    s.manual ? `手动调整 <b>${s.manual}</b> 组` : '全部为建议值';
}

/* ══════ 操作 ══════ */
function acceptAll(){
  Object.keys(dec).forEach(k => {
    dec[k].attn = false;
    dec[k].decided = true;
  });
  save(); repaint();
}

function showList(){
  const lines = [];
  const s = stats();
  DATA.nodes.forEach(n => {
    if(!n.groups) return;
    n.groups.forEach((g,i) => {
      const d = dec[gk(n.path,i)];
      if(!d) return;
      const to = d.mode === 'keep' ? '【保持原位】'
        : d.mode === 'move' ? d.target
        : '【新建】' + (d.name || '???');
      lines.push([n.path, g.name, to].join('	'));
    });
  });
  openModal(`整理清单　共 ${s.total} 组`, lines.join('
'));
}

/* ══════ 持久化 ══════ */
function save(){
  try{
    localStorage.setItem(DEC_KEY, JSON.stringify({dec, at:Date.now()}));
  }catch(e){}
}
function load(){
  try{
    const raw = localStorage.getItem(DEC_KEY);
    if(!raw) return false;
    const o = JSON.parse(raw);
    if(!o || !o.at || Date.now() - o.at > 60*864e5) return false;
    dec = o.dec || {};
    return true;
  }catch(e){ return false; }
}

/* ══════ 弹层 ══════ */
function openModal(t,b){
  document.getElementById('mTitle').textContent = t;
  document.getElementById('mBody').textContent = b;
  document.getElementById('mask').classList.add('on');
}
function closeModal(){ document.getElementById('mask').classList.remove('on'); }

/* ══════ 对外接口 ══════ */
window.foState = function(){
  const out = {root: DATA.root, stats: stats(), groups: []};
  DATA.nodes.forEach(n => {
    if(!n.groups) return;
    n.groups.forEach((g,i) => {
      const d = dec[gk(n.path,i)];
      if(!d) return;
      out.groups.push({
        nodePath: n.path, nodeName: n.name, groupName: g.name,
        unitCount: g.n,
        mode: d.mode,
        target: d.mode === 'move' ? d.target
              : d.mode === 'new' ? d.name : '__keep__',
        userDecided: !!d.decided,
        needsAttention: !!d.attn && !d.decided
      });
    });
  });
  return out;
};
window.foPending = () => window.foState().groups
  .filter(g => g.needsAttention);
window.foNewDirs = () => window.foState().groups
  .filter(g => g.mode === 'new' && g.target);
window.foAcceptAll = acceptAll;

/* ══════ 启动 ══════ */
initTheme();
load();
initDec();
repaint();

document.getElementById('mask').onclick = e => {
  if(e.target.id === 'mask') closeModal();
};
document.addEventListener('keydown', e => {
  if(e.key === 'Escape') closeModal();
});
"""


def render_html(root, data):
    return (
        '<!DOCTYPE html>\n<html lang="zh-CN" data-theme="ink"><head>'
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
