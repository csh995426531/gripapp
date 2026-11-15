from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from grip.service import GripService

# Device-first ops panel for NSPopover (~440×720). Industrial instrument look.
APP_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Grip</title>
<style>
:root{
  --bg:#12140f;
  --bg2:#1a1d16;
  --line:#2e3428;
  --text:#e7eadf;
  --muted:#9aa38a;
  --acc:#c4f042;
  --acc-dim:rgba(196,240,66,.14);
  --ok:#6dde8b;
  --bad:#ff6b5a;
  --warn:#e6b84d;
  --ink:#0c0d0a;
}
*{box-sizing:border-box}
html,body{height:100%;margin:0}
button,input,select,textarea{-webkit-appearance:none;appearance:none}
body{
  font:13.5px/1.45 "Avenir Next","Segoe UI",ui-sans-serif,system-ui,sans-serif;
  background:
    radial-gradient(900px 420px at 10% -10%, rgba(196,240,66,.07), transparent 55%),
    linear-gradient(180deg, #161911 0%, var(--bg) 40%, #0e100c 100%);
  color:var(--text);
  overflow:hidden;
}
button,input,select,textarea{font:inherit;color:inherit}
/* Never inherit label/heading uppercase into editable fields (WKWebView). */
input,textarea,select{text-transform:none;font-variant:normal;letter-spacing:normal}
button{cursor:pointer}
code,.mono{font-family:"SF Mono",Menlo,ui-monospace,monospace}

.app{display:flex;flex-direction:column;height:100vh;min-height:560px}
.top{
  display:flex;align-items:center;gap:8px;min-width:0;
  padding:9px 14px 7px;
  border-bottom:1px solid var(--line);
  background:rgba(18,20,15,.88);
  backdrop-filter:blur(10px);
}
.brand{
  font-family:"SF Mono",Menlo,monospace;
  font-weight:600;font-size:15px;letter-spacing:.08em;
  color:var(--acc);
}
.brand span{color:var(--muted);font-weight:400;letter-spacing:0;margin-left:6px;font-size:11px;opacity:.85}
.top-meta{
  margin-left:auto;color:var(--muted);font-size:10.5px;font-family:"SF Mono",Menlo,monospace;
  opacity:.72;letter-spacing:.01em;
  display:flex;align-items:center;gap:0;flex-wrap:wrap;justify-content:flex-end;
}
.top-meta .filt{
  border:0;background:transparent;padding:2px 3px;margin:0;cursor:pointer;
  color:inherit;font:inherit;letter-spacing:inherit;opacity:.9;
  border-radius:2px;transition:color .12s ease, background .12s ease, opacity .12s ease;
}
.top-meta .filt:hover{color:var(--acc);opacity:1}
.top-meta .filt.on{
  color:var(--acc);opacity:1;
  background:var(--acc-dim);
  box-shadow:inset 0 -1px 0 rgba(196,240,66,.45);
}
.top-meta .sep{opacity:.45;user-select:none;padding:0 1px}
.beat{
  font:10px/1.2 "SF Mono",Menlo,monospace;
  color:#7a8668;letter-spacing:.06em;
  font-variant-numeric:tabular-nums;
  opacity:.78;
  transition:color .2s ease, opacity .2s ease;
  flex:0 0 auto;white-space:nowrap;
}
.beat.warn{color:#d4b05a;opacity:.95}
.icon-btn,.btn{
  border:1px solid var(--line);
  background:var(--bg2);
  color:var(--text);
  border-radius:4px;
  padding:6px 10px;
  white-space:nowrap;
  transition:border-color .15s ease, background .15s ease, color .15s ease, opacity .15s ease;
}
/* English labels are wider than the 440px popover row. Keep the brand and
   actions on line 1; put the counters on their own line. */
html[lang="en"] .top{flex-wrap:wrap;row-gap:1px}
html[lang="en"] .brand,
html[lang="en"] .brand span{white-space:nowrap}
html[lang="en"] #btnClouds{margin-left:auto}
html[lang="en"] .top-meta{
  order:1;flex:1 0 100%;margin-left:0;
  justify-content:flex-end;flex-wrap:nowrap;
}
.icon-btn:hover,.btn:hover{border-color:#4a5340}
.btn.primary{background:var(--acc);border-color:var(--acc);color:var(--ink);font-weight:600}
input,textarea,select{-webkit-user-select:text;user-select:text;text-transform:none}
.btn.primary:hover{filter:brightness(1.05)}
.btn.ghost{background:transparent}
.btn.danger{border-color:rgba(255,107,90,.45);color:var(--bad)}
.btn:disabled{opacity:.45;cursor:not-allowed}
.btn.sm{padding:4px 8px;font-size:12px}
.icon-btn{
  padding:4px 7px;font-size:11px;color:#7e8872;
  background:transparent;border-color:transparent;opacity:.88;
}
.icon-btn:hover{color:var(--text);border-color:var(--line);background:var(--bg2);opacity:1}

.views{flex:1;overflow:auto;padding:10px 14px 16px}
/* Keep overlay scrollbar thin/transparent — no gray track on hover */
.views::-webkit-scrollbar{width:8px;height:8px}
.views::-webkit-scrollbar-track,
.views::-webkit-scrollbar-track:hover,
.views::-webkit-scrollbar-corner{background:transparent}
.views::-webkit-scrollbar-thumb{
  background:rgba(154,163,138,.28);
  border-radius:8px;
  border:2px solid transparent;
  background-clip:padding-box;
}
.views::-webkit-scrollbar-thumb:hover{
  background:rgba(154,163,138,.42);
  border:2px solid transparent;
  background-clip:padding-box;
}
.view.hidden{display:none!important}
.view:not(.hidden){animation:viewIn .16s ease}
@keyframes viewIn{from{opacity:.55}to{opacity:1}}

.toolbar{display:flex;gap:8px;align-items:center;margin-bottom:8px}
.toolbar h2{margin:0;font-size:12px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);flex:1}

.empty{
  border:1px dashed var(--line);border-radius:6px;padding:28px 16px;text-align:center;color:var(--muted);
}
.empty strong{display:block;color:var(--text);margin-bottom:6px;font-size:14px}

#view-list{display:flex;flex-direction:column;min-height:100%}
.list-shell{
  position:relative;
  flex:1;
  display:flex;flex-direction:column;
  border:1px solid var(--line);
  border-radius:6px;
  background:
    linear-gradient(180deg, rgba(255,255,255,.03), transparent 18%),
    rgba(0,0,0,.22);
  padding:8px 8px 0;
  min-height:220px;
}
.list-shell::after{
  content:"";
  pointer-events:none;
  position:absolute;left:1px;right:1px;bottom:1px;height:48px;
  border-radius:0 0 5px 5px;
  background:linear-gradient(180deg, transparent, rgba(12,14,10,.78));
}
.list{display:flex;flex-direction:column;gap:8px;position:relative;z-index:1}
.list.layout-compact{
  display:grid;grid-template-columns:1fr 1fr;gap:8px 10px;
}
.list.layout-compact .device{
  padding:12px 10px 11px;gap:4px 7px;min-width:0;
}
.list.layout-compact .device .name{font-size:13px;line-height:1.3}
.list.layout-compact .device .addr,
.list.layout-compact .mrow{display:none}
.list.layout-compact .dot{margin-top:6px}
.list.layout-compact .busy-tag,
.list.layout-compact .need-ssh-tag,
.list.layout-compact .cloud-tag{font-size:9.5px;padding:2px 5px}
.mcompact{
  margin-top:6px;
  font:10.5px/1.3 "SF Mono",Menlo,monospace;
  color:#8a947c;font-variant-numeric:tabular-nums;
  min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
  opacity:.92;
}
.list.layout-compact > .form.hint{grid-column:1 / -1}
.list-foot{
  position:relative;z-index:1;
  margin:auto 4px 8px;padding:0;
  font:10.5px/1.35 "SF Mono",Menlo,monospace;
  color:var(--muted);opacity:.7;text-align:center;
}
.device{
  display:grid;grid-template-columns:16px 8px minmax(0,1fr);gap:4px 8px;align-items:start;
  padding:12px 12px 11px;border:1px solid transparent;border-radius:6px;
  background:transparent;
  cursor:pointer;text-align:left;width:100%;
  /* minmax(0,1fr) + body min-width:0: without this, long OCI addr/disk pills
     refuse to shrink and blow out the menubar row. */
  transition:border-color .15s ease, background .15s ease, opacity .12s ease;
}
.device > span:last-child{min-width:0;overflow:hidden}
.device:hover{border-color:#4f5944;background:var(--acc-dim)}
.device.drag-over{border-color:rgba(196,240,66,.55);background:var(--acc-dim)}
.device.dragging{opacity:.45}
.drag-handle{
  flex:0 0 auto;width:16px;height:22px;margin:0;padding:0;
  display:inline-flex;align-items:center;justify-content:center;
  border:1px solid rgba(154,163,138,.28);border-radius:3px;
  background:rgba(154,163,138,.1);
  color:#c5d0b4;font:12px/1 "SF Mono",Menlo,monospace;letter-spacing:-.04em;
  cursor:grab;opacity:.9;user-select:none;-webkit-user-select:none;
  -webkit-user-drag:element;
}
.device:hover .drag-handle{
  border-color:rgba(196,240,66,.4);background:rgba(196,240,66,.12);color:var(--acc);opacity:1;
}
.drag-handle:hover{
  border-color:rgba(196,240,66,.65);background:rgba(196,240,66,.2);color:var(--acc);opacity:1;
}
.drag-handle:active{cursor:grabbing}
.rv-strip{
  margin:0 0 8px;padding:10px 12px;border:1px solid rgba(230,184,77,.35);
  border-radius:8px;background:rgba(230,184,77,.07);
  color:var(--text);font-size:12.5px;line-height:1.45;
}
.rv-strip.hidden{display:none}
.rv-strip .rv-actions{display:flex;flex-wrap:wrap;gap:6px;justify-content:flex-end;margin-top:8px}
.rv-strip .rv-note{color:var(--muted);margin-top:4px}
.list.layout-compact .device{grid-template-columns:15px 7px 1fr;gap:4px 6px;padding:12px 10px 11px}
.list.layout-compact .drag-handle{width:15px;height:20px;font-size:11px}
.dot{width:7px;height:7px;border-radius:50%;margin-top:6px;background:var(--muted);box-shadow:0 0 0 2px rgba(139,146,124,.12)}
.dot.ok{background:var(--ok);box-shadow:0 0 0 2px rgba(109,222,139,.15)}
.dot.bad{background:var(--bad);box-shadow:0 0 0 2px rgba(255,107,90,.15)}
.dot.warn{background:var(--warn);box-shadow:0 0 0 2px rgba(230,184,77,.15)}
.dot.busy{background:var(--warn);box-shadow:0 0 0 2px rgba(230,184,77,.18);animation:pulse 1.2s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.45}}
.device .title-row{display:flex;align-items:baseline;gap:6px;min-width:0;overflow:hidden}
.device .name{
  font-weight:650;font-size:14px;letter-spacing:-.01em;line-height:1.3;color:var(--text);
  flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
}
.device .addr{
  display:block;margin-top:3px;
  color:#7f8974;font-size:11px;line-height:1.35;
  font-family:"SF Mono",Menlo,monospace;
  min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
}
.device .busy-tag{flex:0 0 auto;font:9.5px/1.2 "SF Mono",Menlo,monospace;color:var(--warn);border:1px solid rgba(230,184,77,.4);border-radius:3px;padding:2px 5px;white-space:nowrap}
.device .need-ssh-tag,
.device .need-cred-tag{
  flex:0 0 auto;font:9.5px/1.2 "SF Mono",Menlo,monospace;color:var(--warn);
  border:1px solid rgba(230,184,77,.45);border-radius:3px;padding:2px 5px;white-space:nowrap;
  background:rgba(230,184,77,.08);
}
.device.need-ssh{opacity:.92}
.device .cloud-tag{
  flex:0 0 auto;font:9.5px/1.2 "SF Mono",Menlo,monospace;color:#8a947c;
  border:1px solid rgba(138,148,124,.35);border-radius:3px;padding:2px 5px;white-space:nowrap;
  background:rgba(138,148,124,.08);
}
.mrow{display:flex;flex-wrap:nowrap;gap:6px;margin-top:8px;overflow:hidden}
.mpill{
  display:inline-flex;align-items:center;gap:4px;
  padding:4px 7px;border-radius:4px;
  background:rgba(196,240,66,.035);border:1px solid rgba(196,240,66,.1);
  min-width:0;overflow:hidden;
}
.mpill .mlab{font:10px/1 "SF Mono",Menlo,monospace;letter-spacing:.04em;color:#828c74;flex:0 0 auto}
.mpill .mval{font:12px/1 "SF Mono",Menlo,monospace;color:#d7f28a;font-variant-numeric:tabular-nums;flex:0 0 auto}
.mpill .mrange{font:9.5px/1 "SF Mono",Menlo,monospace;color:#8a947c;font-variant-numeric:tabular-nums;opacity:.9}
.mpill svg{display:block;width:36px;height:12px;flex:0 0 auto;overflow:hidden}
/* List: equal-width pills, fixed spark — do NOT flex-grow the SVG (that
   stretched curves into invisibility / overlapped labels in the menubar). */
.mrow:not(.mrow-detail) .mpill{
  flex:1 1 0;min-width:0;justify-content:flex-start;
}
.mrow:not(.mrow-detail) .mpill .mval{
  /* Keep the percent visible. The spark shrinks first; long 盘IO text still ellipsizes. */
  flex:0 1 auto;min-width:2.4em;max-width:100%;text-align:left;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
}
.mrow:not(.mrow-detail) .mpill svg{
  width:36px;height:12px;flex:0 1 36px;min-width:16px;max-width:36px;
}
/* Keep last numbers when host is unreachable / sample is old — dim, don't blank */
.mpill.stale{
  opacity:.58;
  background:rgba(196,240,66,.02);
  border-color:rgba(196,240,66,.07);
}
.mpill.stale .mval{color:#9aa872}
.msec.stale .msv{opacity:.55;color:#7a8470}
/* Detail: 2×2 chart grid (CPU / mem / disk / net), larger sparks */
.mrow.mrow-detail{
  display:grid;grid-template-columns:1fr 1fr;gap:8px 10px;
  overflow:visible;margin-top:8px;
}
.mrow.mrow-detail .mpill{
  display:flex;flex-wrap:wrap;align-items:center;gap:6px;
  padding:6px 8px;min-width:0;width:auto;
  background:rgba(196,240,66,.05);border-color:rgba(196,240,66,.14);
}
.mrow.mrow-detail .mpill .mval{font-size:12.5px}
.mrow.mrow-detail .mpill .mrange{flex:1 1 100%;margin-top:1px}
/* Stretch sparks edge-to-edge: default xMidYMid meet centers short
   time-axis curves in the middle of the pill (looked worst on net spikes). */
.mrow.mrow-detail .mpill svg{width:100%;max-width:none;height:28px;flex:1 1 100%}
/* Secondary metrics under detail 指标 — quiet 2-col defs, below primary pills */
.msec{
  display:grid;grid-template-columns:1fr 1fr;column-gap:12px;row-gap:8px;
  margin-top:10px;padding-top:10px;border-top:1px solid rgba(46,52,40,.9);
}
.msec .msi{display:flex;flex-direction:column;gap:2px;min-width:0}
.msec .msk{
  font:9px/1.2 "SF Mono",Menlo,monospace;letter-spacing:.08em;
  color:#5c6452;white-space:nowrap;
}
.msec .msv{
  min-width:0;font:11px/1.3 "SF Mono",Menlo,monospace;
  color:#9aa38a;font-variant-numeric:tabular-nums;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
}

.back-row{
  display:flex;align-items:center;gap:8px;margin-bottom:8px;min-width:0;
}
.back-row .beat{margin-left:auto}
.back{
  display:inline-flex;align-items:center;gap:6px;border:0;background:transparent;
  color:var(--muted);padding:0;margin:0 0 8px;font-size:12px;
  transition:color .15s ease;
}
.back-row .back{margin-bottom:0}
.back:hover{color:var(--acc)}

.detail-head{
  display:flex;justify-content:space-between;gap:10px;align-items:flex-start;
  margin-bottom:6px;
}
.detail-head h1{margin:0;font-size:16px;letter-spacing:-.01em}
.detail-head .sub{margin-top:2px;color:var(--muted);font-family:"SF Mono",Menlo,monospace;font-size:11.5px}
.badge{
  font:10px/1 "SF Mono",Menlo,monospace;padding:5px 7px;border-radius:3px;
  border:1px solid var(--line);color:var(--muted);white-space:nowrap;
}
.badge.ok{color:var(--ok);border-color:rgba(109,222,139,.4);background:rgba(109,222,139,.08)}
.badge.bad{color:var(--bad);border-color:rgba(255,107,90,.4);background:rgba(255,107,90,.08)}
.badge.warn{color:var(--warn);border-color:rgba(230,184,77,.4);background:rgba(230,184,77,.08)}

.detail-meta{
  color:var(--muted);font:11px/1.35 "SF Mono",Menlo,monospace;
  margin:0 0 2px;padding-bottom:10px;border-bottom:1px solid var(--line);
}
.kv{margin:8px 0 0;display:none;grid-template-columns:1fr 1fr;gap:5px 12px;font-size:12px}
.kv.on{display:grid}
.kv .pair{
  display:grid;grid-template-columns:52px minmax(0,1fr);gap:2px 6px;
  align-items:baseline;min-width:0;
}
.kv .pair.wide{grid-column:1 / -1;grid-template-columns:52px minmax(0,1fr)}
.kv dt{color:var(--muted);margin:0;font-size:10.5px;letter-spacing:.02em}
.kv dd{
  margin:0;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
  font-family:"SF Mono",Menlo,ui-monospace,monospace;font-size:11px;opacity:.9;
}
.kv .pair.wide dd{white-space:normal;word-break:break-all;overflow:visible;text-overflow:clip}

.actions{display:flex;flex-wrap:wrap;gap:6px}

/* Soft section stack — hairline dividers; only 运行命令 stays as inset panel */
.sect{
  padding:11px 0 10px;
  margin:0;
  border:0;
  border-bottom:1px solid var(--line);
  background:transparent;
}
.sect:last-child{border-bottom:0}
.sect-h{display:flex;align-items:center;gap:8px;margin:0 0 8px}
.sect-h h3,.sect > h3{
  margin:0;font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;
  color:var(--muted);font-weight:600;
}
.sect-h .spacer{flex:1}
.metrics-at{
  font:10px/1.2 "SF Mono",Menlo,monospace;color:#7a8668;
  letter-spacing:.02em;font-variant-numeric:tabular-nums;
  opacity:.85;white-space:nowrap;margin-right:2px;
}
.metrics-at.empty{opacity:.45}

.panel{
  border:1px solid var(--line);border-radius:6px;background:rgba(0,0,0,.16);
  padding:9px 10px;margin:10px 0;
}
.panel-h{display:flex;align-items:center;gap:8px;margin:0 0 8px}
.panel-h h3,.panel h3{margin:0;font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);font-weight:600}
.panel-h .spacer{flex:1}
.panel.cmd{
  border-color:rgba(196,240,66,.22);
  background:linear-gradient(180deg, rgba(196,240,66,.05), rgba(0,0,0,.14));
  margin:12px 0;
}
.panel.cmd h3{color:#b7c88a}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
.chip{
  border:1px solid var(--line);background:transparent;border-radius:3px;
  padding:3px 8px;font-size:11px;color:var(--muted);font-family:"SF Mono",Menlo,monospace;
  transition:border-color .15s ease, color .15s ease, background .15s ease;
}
.chip:hover{border-color:var(--acc);color:var(--acc);background:var(--acc-dim)}
.cmd-row{display:flex;gap:6px}
.cmd-row input{
  flex:1;border:1px solid var(--line);background:var(--ink);border-radius:4px;
  padding:8px 10px;font-family:"SF Mono",Menlo,monospace;font-size:12px;
}
.cmd-row input:focus{outline:none;border-color:#5d6a48}
.out{
  margin-top:8px;min-height:64px;max-height:140px;overflow:auto;
  background:#0a0b08;border:1px solid var(--line);border-radius:4px;
  padding:8px 10px;font:11.5px/1.45 "SF Mono",Menlo,monospace;color:#cfd6c0;white-space:pre-wrap;
}
.out .err{color:var(--bad)}

.tasks{display:flex;flex-direction:column;gap:6px}
.task{
  border-left:2px solid #445;
  padding:7px 9px;background:rgba(255,255,255,.02);border-radius:0 4px 4px 0;
}
.task.success{border-left-color:var(--ok)}
.task.failed,.task.denied{border-left-color:var(--bad)}
.task.running,.task.queued,.task.orphaned,.task.probing{border-left-color:var(--warn)}
.task .t1{font-size:12px;font-weight:500;display:flex;align-items:baseline;gap:7px;min-width:0}
.task .tt{
  flex:0 0 auto;font:10.5px/1.2 "SF Mono",Menlo,monospace;
  color:#8a947c;font-variant-numeric:tabular-nums;letter-spacing:.02em;
}
.task .t1 .tbody{flex:1 1 auto;min-width:0}
.task .t2{
  font-size:11px;color:var(--muted);font-family:"SF Mono",Menlo,monospace;margin-top:2px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
}
.task .t3row{display:flex;align-items:baseline;gap:8px;margin-top:3px;min-width:0}
.task .t3{
  flex:1 1 auto;min-width:0;
  font:11px/1.35 "SF Mono",Menlo,monospace;color:#b7c29a;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
}
.task.open .t3{
  white-space:pre-wrap;overflow:visible;text-overflow:clip;word-break:break-word;
}
.task .tmore{
  flex:0 0 auto;border:0;background:transparent;padding:0;
  font:10.5px/1 "SF Mono",Menlo,monospace;color:#8a947c;cursor:pointer;white-space:nowrap;
}
.task .tmore:hover{color:var(--acc)}
.task{cursor:pointer}
.task:hover{background:rgba(255,255,255,.035)}

.form label{display:block;font-size:11px;color:var(--muted);margin:10px 0 4px;letter-spacing:.04em}
.form input,.form select{
  width:100%;border:1px solid var(--line);background:var(--ink);border-radius:4px;padding:8px 10px;
}
.form input:focus,.form select:focus{outline:none;border-color:#5d6a48}
.path-row{display:flex;gap:6px;align-items:center;margin:0 0 10px}
.path-row input{flex:1;min-width:0;margin:0}
.path-row .btn{flex:0 0 auto;white-space:nowrap}
.form .row{display:flex;gap:8px;margin-top:14px;flex-wrap:wrap}
.form .hint{color:var(--muted);font-size:11.5px;margin:0 0 4px}
.cloud-sub{color:var(--muted);font-size:11.5px;line-height:1.35;word-break:break-word}
#cloudList .device.cloud-row{
  cursor:pointer;
  /* No drag handle on cloud rows — only dot | body (avoids vertical-text collapse). */
  grid-template-columns:8px minmax(0,1fr);
}
#cloudList .cloud-handle{display:none}
#cloudList .cloud-row-body{min-width:0;overflow:hidden}
#cloudList .cloud-row-body .name,
#cloudList .cloud-row-body .addr{
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
}
.hint-banner{
  margin:8px 0 0;padding:10px 12px;border:1px solid var(--line);border-radius:4px;
  background:rgba(230,184,77,.08);color:var(--muted);font-size:12px;line-height:1.4;
}
.seg{display:flex;gap:0;border:1px solid var(--line);border-radius:4px;overflow:hidden;margin-bottom:8px}
.seg button{flex:1;border:0;border-right:1px solid var(--line);background:transparent;padding:7px;color:var(--muted);transition:background .15s ease, color .15s ease}
.seg button:last-child{border-right:0}
.seg button.on{background:var(--acc-dim);color:var(--acc);font-weight:600}

.toast{
  position:fixed;left:50%;bottom:14px;transform:translateX(-50%);
  background:#1f241a;border:1px solid var(--line);color:var(--text);
  padding:8px 12px;border-radius:4px;font-size:12px;display:none;max-width:90%;
  box-shadow:0 8px 24px rgba(0,0,0,.35);z-index:20;
}
/* WKWebView has no window.confirm — use in-panel modal instead. */
.confirm-mask{
  position:fixed;inset:0;z-index:40;display:flex;align-items:center;justify-content:center;
  background:rgba(0,0,0,.55);padding:18px;
}
.confirm-card{
  width:100%;max-width:320px;background:#161a12;border:1px solid var(--line);
  border-radius:6px;padding:14px 14px 12px;box-shadow:0 16px 40px rgba(0,0,0,.45);
}
.confirm-card h4{margin:0 0 8px;font-size:14px;font-weight:650;letter-spacing:-.01em}
.confirm-card p{margin:0;color:var(--muted);font-size:12.5px;line-height:1.45;white-space:pre-wrap}
.confirm-card .row{display:flex;justify-content:flex-end;gap:8px;margin-top:14px}
.confirm-card label{display:block;font-size:11px;color:var(--muted);margin:10px 0 4px;letter-spacing:.04em}
.confirm-card input{
  width:100%;border:1px solid var(--line);background:var(--ink);border-radius:4px;padding:8px 10px;
  color:var(--text);font:12px/1.3 "SF Mono",Menlo,monospace;
}
.confirm-card input:focus{outline:none;border-color:#5d6a48}
.hidden{display:none!important}

/* First-run permission gate */
.setup-gate{
  position:fixed;inset:0;z-index:50;display:flex;align-items:center;justify-content:center;
  background:
    radial-gradient(700px 320px at 20% -10%, rgba(196,240,66,.08), transparent 55%),
    rgba(8,10,6,.92);
  padding:18px;
}
.setup-card{
  width:100%;max-width:360px;background:#141810;border:1px solid var(--line);
  border-radius:8px;padding:16px 16px 14px;box-shadow:0 18px 48px rgba(0,0,0,.5);
}
.setup-card .lock{
  width:36px;height:36px;border-radius:8px;display:flex;align-items:center;justify-content:center;
  background:rgba(196,240,66,.1);border:1px solid rgba(196,240,66,.28);
  color:var(--acc);font:16px/1 "SF Mono",Menlo,monospace;margin-bottom:10px;
}
.setup-card h3{margin:0 0 6px;font-size:15px;letter-spacing:-.01em}
.setup-card .lead{margin:0 0 8px;color:var(--muted);font-size:12.5px;line-height:1.45}
.setup-steps{margin:0 0 12px;padding:0 0 0 1.15em;color:var(--muted);font-size:12px;line-height:1.5}
.setup-steps li{margin:0 0 2px}
.setup-item{
  display:grid;grid-template-columns:1fr auto;gap:6px 10px;align-items:center;
  padding:10px 0;border-top:1px solid var(--line);
}
.setup-item:first-of-type{border-top:0;padding-top:0}
.setup-item .st{font-weight:600;font-size:13px}
.setup-item .sb{color:var(--muted);font-size:11.5px;line-height:1.35;grid-column:1 / -1}
.setup-item .badge-mini{
  font:10px/1 "SF Mono",Menlo,monospace;padding:4px 6px;border-radius:3px;
  border:1px solid var(--line);color:var(--muted);white-space:nowrap;
}
.setup-item .badge-mini.ok{color:var(--ok);border-color:rgba(109,222,139,.4);background:rgba(109,222,139,.08)}
.setup-item .badge-mini.bad{color:var(--warn);border-color:rgba(230,184,77,.4);background:rgba(230,184,77,.08)}
.setup-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:12px}

/* aliases if any leftover block markup */
.block{border:1px solid var(--line);border-radius:6px;background:rgba(0,0,0,.16);padding:9px 10px;margin-bottom:8px}
.block-h{display:flex;align-items:center;gap:8px;margin:0 0 8px}
.block-h h3,.block > h3{margin:0;font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);font-weight:600}
.block-h .spacer{flex:1}
.block.cmd{
  border-color:rgba(196,240,66,.22);
  background:linear-gradient(180deg, rgba(196,240,66,.05), rgba(0,0,0,.14));
}
.block.cmd h3{color:#b7c88a}

</style>
</head>
<body>
<div class="setup-gate" id="setupGate" role="dialog" aria-modal="true" aria-labelledby="setupTitle">
  <div class="setup-card">
    <div class="lock" aria-hidden="true">锁</div>
    <h3 id="setupTitle">需要授权后才能使用</h3>
    <p class="lead">按下面两项授权即可进入。每项点「去授权」，在 macOS 弹窗里点允许。未签名安装包若钥匙串弹不出来，点左下角「改用文件保管」。</p>
    <ol class="setup-steps" aria-hidden="true">
      <li>钥匙串（或改用文件保管）— 用来存 SSH / 云密钥</li>
      <li>文件访问 — 允许读取本机 SSH 私钥（~/.ssh）</li>
    </ol>
    <div id="setupItems">
      <div class="setup-item">
        <div class="st">钥匙串</div><span class="badge-mini bad">待检查</span>
        <div class="sb">存放 SSH / 云密钥的主密钥。点「去授权」后在系统弹窗允许；弹不出则用「改用文件保管」。</div>
      </div>
      <div class="setup-item">
        <div class="st">文件访问</div><span class="badge-mini bad">待检查</span>
        <div class="sb">选取 ~/.ssh 下任意私钥文件，完成磁盘访问授权。</div>
      </div>
    </div>
    <div class="setup-actions">
      <button type="button" class="btn ghost sm" id="setupFileVault">改用文件保管</button>
      <button type="button" class="btn ghost sm" id="setupRecheck">重新检查</button>
      <button type="button" class="btn primary sm" id="setupContinue" disabled>进入 Grip</button>
    </div>
  </div>
</div>
<div class="confirm-mask hidden" id="confirmMask" role="dialog" aria-modal="true">
  <div class="confirm-card">
    <h4 id="confirmTitle">确认</h4>
    <p id="confirmBody"></p>
    <div class="row">
      <button type="button" class="btn ghost sm" id="confirmCancel">取消</button>
      <button type="button" class="btn danger sm" id="confirmOk">删除</button>
    </div>
  </div>
</div>
<div class="confirm-mask hidden" id="linkSshMask" role="dialog" aria-modal="true">
  <div class="confirm-card">
    <h4>开通 SSH</h4>
    <p id="linkSshHint">为云实例创建可执行 SSH 设备。WKWebView 不支持浏览器 prompt，请在此填写。</p>
    <label>SSH 用户</label>
    <input id="linkSshUser" placeholder="opc / ubuntu / root" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"/>
    <label>登录私钥</label>
    <div class="path-row" style="margin:0">
      <input id="linkSshKey" placeholder="选择本机私钥文件" readonly/>
      <button type="button" class="btn ghost sm" data-pick="linkSshKey">选择…</button>
    </div>
    <div class="row">
      <button type="button" class="btn ghost sm" id="linkSshCancel">取消</button>
      <button type="button" class="btn primary sm" id="linkSshOk">开通</button>
    </div>
  </div>
</div>
<div class="app">
  <header class="top">
    <div class="brand">GRIP<span>本机运维</span></div>
    <div class="top-meta" id="hdrMeta">—</div>
    <button type="button" class="icon-btn" id="btnClouds" title="云账号">云</button>
    <button type="button" class="btn primary sm" id="btnAdd">＋ 设备</button>
  </header>

  <div class="views">
    <section class="view" id="view-list">
      <div class="toolbar">
        <h2>设备</h2>
        <span class="beat" id="beatList" title="下次指标采样">10s</span>
        <button type="button" class="btn ghost sm" id="btnListLayout" title="切换列表布局">两列</button>
        <button type="button" class="btn ghost sm" id="btnExportAll" title="导出全部设备执行日志">导出日志</button>
      </div>
      <div id="rvStrip" class="rv-strip hidden"></div>
      <div class="list-shell" id="listShell">
        <div class="list" id="deviceList"></div>
        <p class="list-foot" id="listFoot">点选设备查看指标与操作</p>
      </div>
      <div class="empty" id="emptyList">
        <strong id="emptyTitle">加载中…</strong>
        <span id="emptyHint">正在读取本机设备列表</span>
        <div style="margin-top:12px"><button type="button" class="btn primary" id="btnEmptyAdd">添加设备</button></div>
      </div>
    </section>

    <section class="view hidden" id="view-detail">
      <div class="back-row">
        <button type="button" class="back" id="btnBack">← 全部设备</button>
        <span class="beat" id="beatDetail" title="下次指标采样">10s</span>
      </div>
      <div class="detail-head">
        <div>
          <h1 id="dName">—</h1>
          <div class="sub" id="dAddr">—</div>
        </div>
        <div class="badge" id="dStatus">—</div>
      </div>
      <div class="detail-meta" id="dMeta">—</div>
      <dl class="kv" id="dKv"></dl>

      <div class="sect" id="metricsPanel">
        <div class="sect-h">
          <h3>指标</h3>
          <span class="spacer"></span>
          <span class="metrics-at empty" id="dMetricsAt" title="最近一次成功采样时间">—</span>
          <button type="button" class="btn ghost sm" id="btnRefreshMetrics">刷新</button>
        </div>
        <div id="dMetrics">尚未采样</div>
      </div>

      <div class="sect" id="opsPanel">
        <h3>操作</h3>
        <div class="actions" id="dActions" style="margin-top:8px"></div>
      </div>

      <div class="panel cmd" id="cmdPanel">
        <h3>运行命令</h3>
        <div class="chips" id="presets" style="margin-top:8px"></div>
        <div class="cmd-row">
          <input id="cmdInput" placeholder="uptime" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"/>
          <button type="button" class="btn primary" id="btnRun">执行</button>
        </div>
        <div class="out" id="cmdOut">在这台设备上执行只读命令；写操作需确认。</div>
      </div>

      <div class="sect">
        <div class="sect-h">
          <h3>最近任务</h3>
          <span class="spacer"></span>
          <button type="button" class="btn ghost sm" id="btnExportDevice" title="导出本设备执行日志">导出日志</button>
        </div>
        <div class="tasks" id="dTasks" style="margin-top:8px"></div>
      </div>
    </section>

    <section class="view hidden" id="view-edit">
      <button type="button" class="back" id="btnBackEdit">← 返回</button>
      <div class="toolbar"><h2>编辑设备</h2></div>
      <p class="form hint">名称不可改。仅改备注性字段时，密码/私钥可留空保持原凭证；若改了主机/端口/用户，必须填新密码或私钥（否则会沿用上一台机器的密钥）。</p>
      <div class="form">
        <label>系统</label>
        <select id="editOs">
          <option value="linux">Linux</option>
          <option value="macos">macOS</option>
          <option value="windows">Windows</option>
          <option value="unix">Linux / macOS（未细分）</option>
        </select>
        <label>名称</label><input id="editName" disabled autocapitalize="off" autocorrect="off" spellcheck="false"/>
        <label>主机</label><input id="editHost" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"/>
        <label>用户名</label><input id="editUser" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"/>
        <label>端口</label><input id="editPort" type="number" value="22" inputmode="numeric"/>
        <label>认证</label>
        <div class="seg" id="editAuthSeg">
          <button type="button" class="on" data-auth="password">密码</button>
          <button type="button" data-auth="key">私钥</button>
        </div>
        <div id="editPwBox">
          <label>新密码（可选）</label><input id="editPassword" type="password" autocomplete="new-password"/>
        </div>
        <div id="editKeyBox" class="hidden">
          <label>新私钥文件（可选）</label>
          <div class="path-row">
            <input id="editKey" placeholder="选择新私钥（留空保持原凭证）" readonly/>
            <button type="button" class="btn ghost sm" data-pick="editKey">选择…</button>
          </div>
        </div>
        <div class="row">
          <button type="button" class="btn primary" id="btnSaveEdit">保存</button>
        </div>
      </div>
    </section>

    <section class="view hidden" id="view-add">
      <button type="button" class="back" id="btnBackAdd">← 取消</button>
      <div class="toolbar"><h2>添加设备</h2></div>
      <p class="form hint">局域网或云上 SSH 主机均可。密码/密钥只进本机保险库，不会给 AI。</p>
      <div class="form">
        <label>系统</label>
        <select id="sshOs">
          <option value="linux">Linux</option>
          <option value="macos">macOS</option>
          <option value="windows">Windows</option>
        </select>
        <label>名称</label><input id="sshName" placeholder="Mac-mini" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"/>
        <label>主机</label><input id="sshHost" placeholder="192.168.5.15 或公网 IP" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"/>
        <label>用户名</label><input id="sshUser" placeholder="ubuntu / opc / Administrator" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"/>
        <label>端口</label><input id="sshPort" type="number" value="22" inputmode="numeric"/>
        <p class="form hint" id="sshPortHint" style="margin-top:4px">Linux / macOS / Windows OpenSSH 默认端口均为 22，切系统会自动填入；可再改。</p>
        <label>认证</label>
        <div class="seg" id="authSeg">
          <button type="button" class="on" data-auth="password">密码</button>
          <button type="button" data-auth="key">私钥</button>
        </div>
        <div id="sshPwBox">
          <label>密码</label><input id="sshPassword" type="password" autocomplete="new-password"/>
        </div>
        <div id="sshKeyBox" class="hidden">
          <label>私钥文件</label>
          <div class="path-row">
            <input id="sshKey" placeholder="选择本机私钥文件" readonly/>
            <button type="button" class="btn ghost sm" data-pick="sshKey">选择…</button>
          </div>
        </div>
        <div class="row">
          <button type="button" class="btn primary" id="btnSaveSsh">保存并探测</button>
        </div>
      </div>
    </section>

    <section class="view hidden" id="view-clouds">
      <div id="cloudsListPane">
        <button type="button" class="back" id="btnBackClouds">← 设备</button>
        <div class="toolbar">
          <h2>云账号</h2>
          <button type="button" class="btn ghost sm" id="btnCloudAdd">添加</button>
          <button type="button" class="btn ghost sm" id="btnSync">同步资源</button>
        </div>
        <div class="list" id="cloudList"></div>
        <p class="form hint" style="margin-top:10px">点账号进入编辑（地域、密钥、实例 SSH）。保存后点「同步资源」拉取实例；账号设了实例 SSH 时会自动开通可执行设备。</p>
      </div>
      <div id="cloudsFormPane" class="hidden">
        <button type="button" class="back" id="btnBackCloudForm">← 云账号列表</button>
        <div class="toolbar">
          <h2 id="cloudFormTitle">添加云账号</h2>
        </div>
        <div class="panel">
          <div class="form">
            <input type="hidden" id="cloudEditId" value=""/>
            <label>厂商</label>
            <select id="cloudVendor">
              <option value="aliyun">阿里云</option>
              <option value="tencent">腾讯云</option>
              <option value="gcp">Google Cloud</option>
              <option value="oracle">Oracle</option>
            </select>
            <label>备注名</label><input id="cloudLabel" placeholder="main" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"/>
            <label>地域（可逗号分隔）</label><input id="cloudRegions" placeholder="ap-tokyo-1 或 cn-hangzhou" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"/>
            <div id="fields-aliyun">
              <label>AccessKey ID</label><input id="aliAk" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"/>
              <label>AccessKey Secret</label><input id="aliSk" type="password" placeholder="不更换请留空" autocomplete="new-password"/>
            </div>
            <div id="fields-tencent" class="hidden">
              <label>SecretId</label><input id="tcId" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"/>
              <label>SecretKey</label><input id="tcKey" type="password" placeholder="不更换请留空" autocomplete="new-password"/>
              <label>产品</label><select id="tcProduct"><option value="lighthouse">Lighthouse</option><option value="cvm">CVM</option></select>
            </div>
            <div id="fields-gcp" class="hidden">
              <label>项目 ID</label><input id="gcpProject" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"/>
              <label id="gcpSaEmailLab" class="hidden">服务账号</label>
              <input id="gcpSaEmail" class="hidden" readonly/>
              <label>服务账号 JSON</label>
              <div class="path-row">
                <input id="gcpSa" placeholder="不更换请留空" readonly/>
                <button type="button" class="btn ghost sm" data-pick="gcpSa">选择…</button>
              </div>
            </div>
            <div id="fields-oracle" class="hidden">
              <label>User OCID</label><input id="ociUser" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"/>
              <label>Tenancy OCID</label><input id="ociTenancy" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"/>
              <label>Fingerprint</label><input id="ociFp" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"/>
              <label>API 私钥</label>
              <div class="path-row">
                <input id="ociKey" placeholder="不更换请留空" readonly/>
                <button type="button" class="btn ghost sm" data-pick="ociKey">选择…</button>
              </div>
              <label>Compartment OCID</label><input id="ociComp" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"/>
            </div>
            <label>实例 SSH 用户（可选，同步后自动开通 SSH）</label>
            <input id="cloudSshUser" placeholder="opc / ubuntu / root" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"/>
            <label>实例登录私钥（可选，不是 API 密钥）</label>
            <div class="path-row">
              <input id="cloudSshKey" placeholder="不更换请留空" readonly/>
              <button type="button" class="btn ghost sm" data-pick="cloudSshKey">选择…</button>
            </div>
            <p class="form hint" id="cloudFormHint"></p>
            <div class="row">
              <button type="button" class="btn primary" id="btnSaveCloud">保存</button>
              <button type="button" class="btn danger sm hidden" id="btnDeleteCloudEdit">删除</button>
            </div>
          </div>
        </div>
      </div>
    </section>
  </div>
</div>
<div class="toast" id="toast"></div>
<script>
/*__GRIP_BOOT__*/
/*__GRIP_I18N__*/
let lang = (window.__GRIP_BOOT__ && window.__GRIP_BOOT__.lang)==="en" ? "en" : "zh";
document.documentElement.lang=lang==="en"?"en":"zh-Hans";
const EN_KEYS = Object.keys(EN).sort((a,b)=>b.length-a.length);
function trText(s){
  if(lang!=="en" || s==null) return s==null ? "" : String(s);
  const raw=String(s);
  if(EN[raw]) return EN[raw];
  let out=raw;
  for(const k of EN_KEYS){
    if(out.indexOf(k)!==-1) out=out.split(k).join(EN[k]);
  }
  return out;
}
const _textOrig=new WeakMap();
function localize(root){
  const scope=root||document.body;
  if(!scope) return;
  const walker=document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
  let n;
  while((n=walker.nextNode())){
    const parent=n.parentElement;
    if(!parent || parent.closest("script,style")) continue;
    if(parent.closest(".name, .addr, #dName, #dAddr, #editName, #sshName, #cloudLabel, .t2, .t3")) continue;
    if(!_textOrig.has(n)){
      if(!n.nodeValue || !n.nodeValue.trim()) continue;
      _textOrig.set(n, n.nodeValue);
    }
    const orig=_textOrig.get(n);
    const lead=orig.match(/^\s*/)[0];
    const tail=orig.match(/\s*$/)[0];
    n.nodeValue=lead+trText(orig.trim())+tail;
  }
  scope.querySelectorAll("[title],[placeholder],[aria-label]").forEach((el)=>{
    ["title","placeholder","aria-label"].forEach((attr)=>{
      const cur=el.getAttribute(attr);
      if(cur==null) return;
      const store="data-i18n-"+attr;
      if(!el.hasAttribute(store)) el.setAttribute(store, cur);
      el.setAttribute(attr, trText(el.getAttribute(store)));
    });
  });
  document.documentElement.lang=lang==="en"?"en":"zh-Hans";
  if(lang==="en"){
    // Rewrite the same text node. Replacing it would drop the Chinese original,
    // so switching back to 中文 could not restore 内存 / 15天 14时.
    scope.querySelectorAll(".mrow:not(.mrow-detail) .mlab").forEach((el)=>{
      const n=el.firstChild;
      if(!n || n.nodeType!==3) return;
      const shown=(n.nodeValue||"").trim();
      if(shown==="Memory") n.nodeValue="Mem";
      else if(shown==="Disk") n.nodeValue="Dsk";
    });
    scope.querySelectorAll(".msv").forEach((el)=>{
      const n=el.firstChild;
      if(!n || n.nodeType!==3) return;
      const orig=_textOrig.get(n);
      if(!orig) return;
      n.nodeValue=orig.trim()
        .replace(/天 /g,"d ")
        .replace(/时 /g,"h ")
        .replace(/时/g,"h")
        .replace(/分/g,"m");
    });
    const metaN=$("dMeta") && $("dMeta").firstChild;
    if(metaN && metaN.nodeType===3){
      const orig=_textOrig.get(metaN);
      if(orig) metaN.nodeValue=trText(orig.trim()).replace(/没有回应/g,"no reply");
    }
  }
}
async function setLang(next){
  lang=next==="en"?"en":"zh";
  try{ localStorage.setItem("grip.lang", lang); }catch(e){}
  try{
    await api("/api/lang",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({lang})});
  }catch(e){}
  localize(document.body);
}
const $ = (id)=>document.getElementById(id);
/* Hostnames / keys must stay literal — disable WKWebView autocapitalize. */
document.querySelectorAll("input,textarea").forEach((el)=>{
  if(el.type==="hidden"||el.type==="file"||el.type==="checkbox"||el.type==="radio") return;
  el.setAttribute("autocapitalize","off");
  el.setAttribute("autocorrect","off");
  if(el.type!=="password") el.setAttribute("spellcheck","false");
  if(!el.getAttribute("autocomplete")) el.setAttribute("autocomplete","off");
});
const PRESETS_UNIX = ["uptime","df -h","uname -a","who"];
const PRESETS_MAC = ["uptime","df -h","uname -a","sw_vers","who"];
const PRESETS_WIN = ["hostname","whoami","ipconfig","Get-Date","Get-PSDrive"];
function presetsFor(r){
  const target=r && isCloud(r) ? linkedSsh(r) : r;
  const os=String(labels(target||r||{}).os||"").toLowerCase();
  if(os==="windows" || os==="win" || os==="win32") return PRESETS_WIN;
  if(os==="macos" || os==="darwin") return PRESETS_MAC;
  return PRESETS_UNIX;
}
let board = (window.__GRIP_BOOT__ && window.__GRIP_BOOT__.board) || {resources:[], by_resource:{}, running_count:0, resource_health:{}};
let selected = null;
let sshAuth = "password";
let pollTimer = null;
let listSig = "";
let detailTaskSig = "";
let listFilter = null; // null | "running" | "reachable" | "unreachable"
let listLayout = (function(){
  try{
    const v=localStorage.getItem("grip.listLayout");
    return (v==="compact" || v==="comfortable") ? v : "comfortable";
  }catch(_e){ return "comfortable"; }
})();
let metricsInFlight = false;
const METRICS_INTERVAL_SEC = 10;
let beatLeft = METRICS_INTERVAL_SEC;
let beatTickTimer = null;

function toast(msg, ms){
  const t=$("toast"); t.textContent=msg; t.style.display="block";
  clearTimeout(toast._t); toast._t=setTimeout(()=>t.style.display="none", ms||2800);
}
function askConfirm(opts){
  // In-panel confirm: WKWebView does not implement window.confirm.
  const title=(opts&&opts.title)||"确认";
  const body=(opts&&opts.body)||"";
  const okLabel=(opts&&opts.okLabel)||"确定";
  const danger=!(opts&&opts.danger===false);
  return new Promise((resolve)=>{
    const mask=$("confirmMask");
    const okBtn=$("confirmOk");
    const cancelBtn=$("confirmCancel");
    $("confirmTitle").textContent=title;
    $("confirmBody").textContent=body;
    okBtn.textContent=okLabel;
    okBtn.className="btn sm "+(danger?"danger":"primary");
    mask.classList.remove("hidden");
    const done=(v)=>{
      mask.classList.add("hidden");
      okBtn.removeEventListener("click", onOk);
      cancelBtn.removeEventListener("click", onCancel);
      mask.removeEventListener("click", onMask);
      resolve(v);
    };
    const onOk=()=>done(true);
    const onCancel=()=>done(false);
    const onMask=(e)=>{ if(e.target===mask) done(false); };
    okBtn.addEventListener("click", onOk);
    cancelBtn.addEventListener("click", onCancel);
    mask.addEventListener("click", onMask);
    setTimeout(()=>okBtn.focus(), 0);
  });
}
function beatEls(){ return [$("beatList"),$("beatDetail")].filter(Boolean); }
function renderBeat(){
  const t=beatLeft+"s";
  const warn=beatLeft>0 && beatLeft<=3;
  beatEls().forEach(el=>{
    el.textContent=t;
    el.classList.toggle("warn", warn);
    el.classList.remove("err");
  });
}
function resetBeat(){
  beatLeft=METRICS_INTERVAL_SEC;
  renderBeat();
}
function metricsViewsActive(){
  return !$("view-list").classList.contains("hidden") || !$("view-detail").classList.contains("hidden");
}
function fireQuietMetrics(){
  resetBeat();
  sampleMetrics(selected || null, {quiet:true}).catch(()=>{});
}
function startBeatCountdown(){
  resetBeat();
  clearInterval(beatTickTimer);
  beatTickTimer=setInterval(()=>{
    if(!metricsViewsActive()) return;
    beatLeft-=1;
    if(beatLeft<=0){
      fireQuietMetrics();
    }else{
      renderBeat();
    }
  },1000);
}
async function api(path, opts){
  const r = await fetch(path, opts);
  const data = await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(data.error || r.statusText);
  return data;
}
function show(view){
  ["list","detail","add","edit","clouds"].forEach(v=>{
    $("view-"+v).classList.toggle("hidden", v!==view);
  });
  // Leaving detail/edit must drop selection — otherwise a quiet metrics tick
  // can call openDetail() and yank the user back to the last device.
  if(view!=="detail" && view!=="edit") selected=null;
}
function statusClass(s){
  s=(s||"").toLowerCase();
  if(["reachable","running","success","active"].includes(s)) return "ok";
  if(["unreachable","failed","error","denied"].includes(s)) return "bad";
  if(["orphaned","probing","queued","busy"].includes(s)) return "warn";
  return "warn";
}
function statusLabel(s, r){
  const k=String(s||"").toLowerCase();
  // Cloud lifecycle ≠ SSH reachability / task "running"
  if(r && isCloud(r)){
    const cm={
      running:"云端运行中", active:"云端运行中",
      stopped:"已关机", stopping:"关机中",
      terminated:"已终止", terminating:"终止中",
      provisioning:"创建中", starting:"启动中",
      unknown:"云状态未知",
    };
    if(cm[k]) return cm[k];
  }
  const m={
    reachable:"可达", unreachable:"不可达", success:"成功", failed:"失败",
    running:"运行中", queued:"排队", denied:"拒绝", unknown:"未知",
    orphaned:"待核对", probing:"探测中",
    error:"错误", busy:"忙碌", skipped:"已跳过"
  };
  return m[k] || (s || "未知");
}
function actionLabel(a){
  const m={
    run_command:"运行命令", probe_resources:"探测资源", sample_metrics:"采样指标",
    sync_cloud:"同步云", sync_all:"同步全部", probe:"探测"
  };
  return m[a] || a || "";
}
function actorLabel(a){
  const k=String(a||"unknown");
  if(k==="panel" || k==="human") return "面板";
  if(k==="mcp") return "MCP";
  if(k==="cli") return "CLI";
  if(k==="unknown") return "未知";
  if(k.startsWith("mcp:")) return "MCP · "+k.slice(4);
  return k;
}
function downloadAuditBlob(body, fname, mediaType){
  const blob=new Blob([body],{type:mediaType||"application/x-ndjson;charset=utf-8"});
  const url=URL.createObjectURL(blob);
  const a=document.createElement("a");
  a.href=url; a.download=fname; a.click();
  setTimeout(()=>URL.revokeObjectURL(url), 2000);
  toast("已下载 "+fname, 4000);
}
async function exportAudit(name){
  try{
    const r=await fetch("/api/audit/export",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({name: name||null, format:"jsonl"})
    });
    let j={};
    try{j=await r.json();}catch(_){}
    if(j.cancelled) return;
    if(j.ok && (j.display_path||j.path)){
      toast("已保存到 "+(j.display_path||j.path), 5500);
      return;
    }
    if(j.error){
      toast(String(j.error), 4500);
      return;
    }
    // Non-native / no save UI: browser download fallback
    if(j.native===false && j.body!=null){
      const fname=j.filename||("grip-audit-"+(name||"all")+".jsonl");
      downloadAuditBlob(j.body, fname, j.media_type);
      return;
    }
    if(!r.ok) throw new Error(j.error||"导出失败");
    throw new Error("导出失败");
  }catch(e){
    toast(String(e.message||e));
  }
}
function authLabel(a){
  if(a==="password") return "密码";
  if(a==="key") return "私钥";
  return a || "";
}
function kindLabel(k){
  if(!k) return "—";
  if(k==="ssh") return "SSH";
  if(k==="oracle_instance") return "Oracle";
  if(k==="gcp_instance") return "GCP";
  if(k==="aliyun_instance") return "阿里云";
  if(k==="tencent_instance") return "腾讯云";
  return k;
}
function isCloud(r){
  return !!(r && r.kind && r.kind!=="ssh");
}
function needsSshLink(r){
  return isCloud(r) && !labels(r).ssh_name;
}
function linkedSsh(r){
  if(!isCloud(r)) return null;
  const n=labels(r).ssh_name;
  if(!n) return null;
  return (board.resources||[]).find(x=>x.name===n) || null;
}
function sshHandshakeBroken(ssh){
  // TCP probe can mark the port "reachable" while the SSH session still fails
  // (wrong key, AutoDL gateway banner drop, MaxStartups, etc.). Commands then
  // all fail while the badge still said 可达 — treat that as broken.
  if(!ssh) return false;
  const tip=String(labels(ssh).metrics_detail||"");
  return /connection closed|permission denied|auth(entication)? fail|no supported auth|host key|timed out|timeout|connection reset|connection refused|protocol banner|error reading ssh|banner|unable to negotiate|handshake|kex_exchange/i.test(tip);
}
function displayStatus(r){
  // Cloud lifecycle (running) ≠ SSH reachability. Linked cloud rows use SSH
  // for green/red; keep「云端运行中」as prefix so vendor state stays visible.
  if(needsSshLink(r)){
    return {cls:"warn", lab:statusLabel(r.status,r)+" · 未开通 SSH"};
  }
  const ssh=linkedSsh(r);
  if(ssh){
    const cloudLab=statusLabel(r.status,r);
    const st=String(ssh.status||"").toLowerCase();
    if(st==="unreachable"){
      return {cls:"bad", lab:cloudLab+" · SSH不可达"};
    }
    if(sshHandshakeBroken(ssh)){
      // TCP open but SSH session dead → same severity as unreachable (red).
      return {cls:"bad", lab:cloudLab+" · SSH连不上"};
    }
    if(st==="reachable"){
      return {cls:"ok", lab:cloudLab+" · SSH可达"};
    }
    return {cls:statusClass(st), lab:cloudLab+" · "+statusLabel(ssh.status,ssh)};
  }
  // Plain SSH rows: TCP「可达」must not hide a dead handshake.
  if(!isCloud(r) && String(r.kind||"")==="ssh"){
    const st=String(r.status||"").toLowerCase();
    const tun=labels(r).tunnel;
    const tpath=tun && tun.enabled ? String(tun.path||"") : "";
    let pathLab="";
    if(tpath==="tunnel") pathLab=" · 直连";
    else if(tpath==="relay"){
      const err=String((tun && tun.last_error) || "").toLowerCase();
      // Keep badge short; full reason stays in meta for debugging.
      pathLab=(err.includes("privilege")||err.includes("not permitted")||err.includes("cancel")||err.includes("permission"))
        ? " · 中继(无直连权限)"
        : " · 中继";
    }else if(tpath==="lan" && tun && tun.enabled) pathLab=" · 局域网";
    if(st==="unreachable") return {cls:"bad", lab:"不可达"+pathLab};
    // Enrolled devices: the dot follows the channel heartbeat only.
    // A metrics SSH error stays in the metrics area and must not flip the dot.
    if(!(tun && tun.enabled) && sshHandshakeBroken(r)) return {cls:"bad", lab:"SSH连不上"+pathLab};
    if(st==="reachable") return {cls:"ok", lab:"可达"+pathLab};
  }
  return {cls:statusClass(r.status), lab:statusLabel(r.status,r)};
}
function isCloudSshTwin(r){
  // SSH row created by cloud sync — hide from list; show the Oracle/云 row instead.
  if(!r || r.kind!=="ssh") return false;
  const ref=labels(r).cloud_ref;
  if(!ref) return false;
  return (board.resources||[]).some(x=>x.name===ref);
}
function execName(r){
  // Commands/probe target the linked SSH device when viewing a cloud row.
  if(isCloud(r) && labels(r).ssh_name) return labels(r).ssh_name;
  return r && r.name;
}
function hasUsableMetrics(r){
  if(!r) return false;
  const m=metricsOf(r);
  if(m && (m.cpu_used_pct!=null || m.mem_used_pct!=null || m.load1!=null || m.net_rx_kbps!=null || m.disk_used_pct!=null)){
    return true;
  }
  return seriesOf(r).length > 0;
}
function metricsResource(r){
  // Prefer linked SSH host metrics when present; if SSH sampling is empty/broken,
  // fall back to the cloud row's vendor metrics so the panel is not blank.
  if(!isCloud(r)) return r;
  const ssh=linkedSsh(r);
  if(ssh && hasUsableMetrics(ssh)) return ssh;
  if(hasUsableMetrics(r)) return r;
  return ssh || r;
}
function cloudVendor(r){
  const k=(r&&r.kind)||"";
  if(k==="oracle_instance") return "oracle";
  if(k==="gcp_instance") return "gcp";
  if(k==="aliyun_instance") return "aliyun";
  if(k==="tencent_instance") return "tencent";
  return "";
}
function statusIsUp(r){
  const st=String(r.status||"").toLowerCase();
  if(["reachable","running","active"].includes(st)) return true;
  return false;
}
function probeDetailLabel(d){
  if(!d) return "";
  let s=String(d);
  s=s.replace(/^tcp ok\b/i, "探测正常");
  s=s.replace(/^tcp fail\b/i, "探测失败");
  s=s.replace(/^skip L2:\s*control-plane not running/i, "跳过探测：控制面未运行");
  return s;
}
function labels(r){ return r.labels || r.meta || {}; }
function addrOf(r){
  const L=labels(r);
  if(r.kind==="ssh") return `${L.user||"?"}@${L.host||"?"}:${L.port||22}`;
  const ip=L.public_ip || L.private_ip || L.ssh_host;
  const bits=[kindLabel(r.kind), L.region, ip, L.shape].filter(Boolean);
  return bits.join(" · ") || kindLabel(r.kind);
}
function metricsOf(r){
  const L=labels(r);
  return L.metrics || null;
}
function seriesOf(r){
  return (labels(r).metrics_series || []);
}
function formatUptime(sec){
  const s=Number(sec)||0;
  const d=Math.floor(s/86400), h=Math.floor((s%86400)/3600), mi=Math.floor((s%3600)/60);
  if(d) return d+"天 "+h+"时";
  if(h) return h+"时 "+mi+"分";
  return mi+"分";
}
function parseTs(t){
  const d=Date.parse(t);
  return Number.isFinite(d) ? d : 0;
}
function formatLocalClock(isoOrMs){
  const ms=typeof isoOrMs==="number" ? isoOrMs : parseTs(isoOrMs);
  if(!ms) return "";
  const d=new Date(ms), now=new Date();
  const pad=n=>String(n).padStart(2,"0");
  const hm=pad(d.getHours())+":"+pad(d.getMinutes());
  const sameDay=d.getFullYear()===now.getFullYear() && d.getMonth()===now.getMonth() && d.getDate()===now.getDate();
  if(sameDay) return hm;
  return pad(d.getMonth()+1)+"-"+pad(d.getDate())+" "+hm;
}
function taskWhen(t){
  return formatLocalClock(t.finished_at || t.created_at) || "—";
}
function seriesWindow(series, minutes){
  // Backend keeps ~30 min; match that window. If sampling stalled (timeouts),
  // the wall-clock window can go empty while older points still exist — fall
  // back to the freshest stored samples so sparks don't collapse / get wiped.
  const arr=series||[];
  const cut=Date.now() - minutes*60*1000;
  const win=arr.filter(p=>parseTs(p.t) >= cut);
  if(win.length >= 2) return win;
  return arr.slice(-180);
}
const METRICS_STALE_MS = 150000; // ~2.5 min — past a few missed 10s ticks
function metricsAtMs(r){
  // Last *successful* sample only. Never use metrics_attempt_at / failure times —
  // backend keeps metrics_at frozen on unreachable/failed attempts.
  const L=labels(r);
  const fromMeta=parseTs(L.metrics_at);
  if(fromMeta) return fromMeta;
  const series=seriesOf(r);
  return series.length ? parseTs(series[series.length-1].t) : 0;
}
function isMetricsStale(r){
  if(String(r.status||"").toLowerCase()==="unreachable") return true;
  const t=metricsAtMs(r);
  return t>0 && (Date.now()-t) > METRICS_STALE_MS;
}
function sparkScale(key, nums){
  // Hybrid Y scale: CPU% stays fixed 0–100 (absolute utilization; auto-zoom
  // made 76–78 look like a crash). Mem%/disk% often sit still for hours, so
  // they use adaptive min–max with padding + a floor window so tiny real
  // moves (e.g. 77→78) stay faintly visible without amplifying noise.
  // Net kbps: adaptive from 0 (rates are absolute, not percent).
  if(key==="cpu_used_pct"){
    return {min:0, max:100};
  }
  if(key==="mem_used_pct" || key==="disk_used_pct"){
    const lo=Math.min(...nums), hi=Math.max(...nums);
    if(lo===hi) return {min:lo, max:hi, flat:true};
    const span=hi-lo;
    // ~12% pad each side, then enforce a ≥5pp window so flats aren't empty
    // and 1pp wiggles don't fill the chart.
    const window=Math.max(span*1.24, 5);
    const mid=(lo+hi)/2;
    return {min:mid-window/2, max:mid+window/2};
  }
  if(key==="load1"){
    const hi=Math.max(...nums, 1);
    let max=1;
    while(max < hi * 1.15) max *= 2;
    return {min:0, max};
  }
  if(key==="net_rx_kbps" || key==="net_tx_kbps" || key==="net"
     || key==="disk_read_kbps" || key==="disk_write_kbps" || key==="disk_io"){
    const hi=Math.max(...nums, 0);
    if(hi<=0) return {min:0, max:1, flat:true};
    const max=hi*1.2 || 1;
    return {min:0, max};
  }
  let min=Math.min(...nums), max=Math.max(...nums);
  if(min===max){ min-=1; max+=1; }
  const pad=(max-min)*0.15 || 1;
  return {min:min-pad, max:max+pad};
}
function sparkEmptySVG(w, h){
  return `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" preserveAspectRatio="none" aria-hidden="true"></svg>`;
}
function sparkTimeBounds(win){
  // Shared wall-clock X for every spark: now-30m → now. Right edge = now;
  // curves grow right→left. Never stretch first→last sample to full width.
  const t1=Date.now();
  const t0=t1 - 30*60*1000;
  if((win||[]).some(p=>parseTs(p.t))) return {t0, t1};
  return null;
}
function sparkPlotScale(key, nums){
  // Flat series still need a Y scale — but must stay on the shared time axis
  // (do not draw a full-width placeholder line).
  const scale=sparkScale(key, nums);
  if(!scale.flat) return scale;
  if(key==="cpu_used_pct" || key==="mem_used_pct" || key==="disk_used_pct"){
    return {min:0, max:100};
  }
  const v=nums[0]||0;
  return {min:0, max:Math.max(v*1.2, 1)};
}
function sparkNum(p, key){
  // Distinguish missing/null from real 0 (Number(null)===0 would fake a point).
  if(p==null || p[key]==null || p[key]==="") return NaN;
  const v=Number(p[key]);
  return Number.isFinite(v) ? v : NaN;
}
function sparkFiniteNums(win, key){
  return (win||[]).map(p=>sparkNum(p, key)).filter(v=>Number.isFinite(v));
}
function sparkPtsOnAxis(win, key, bounds, w, h, pad, min, max){
  // One slot per series point on the shared time axis; null → gap (no stretch).
  const span=Math.max(max-min, 1e-6);
  const tSpan=Math.max(bounds.t1-bounds.t0, 1);
  return (win||[]).map(p=>{
    const t=parseTs(p.t);
    const v=sparkNum(p, key);
    if(!t || !Number.isFinite(v)) return null;
    const x=pad + ((t-bounds.t0)/tSpan)*(w-2*pad);
    const y=pad + (1-((v-min)/span))*(h-2*pad);
    return [x,y];
  });
}
function sparkSegs(pts){
  const segs=[];
  let cur=null;
  for(const p of pts){
    if(!p){ cur=null; continue; }
    if(!cur){ cur=[]; segs.push(cur); }
    cur.push(p);
  }
  return segs;
}
function sparkPathLine(pts){
  return pts.map((p,i)=>(i?"L":"M")+p[0].toFixed(1)+" "+p[1].toFixed(1)).join(" ");
}
function sparkPathArea(pts, h, pad){
  if(pts.length < 2) return "";
  return sparkPathLine(pts)+` L${pts[pts.length-1][0].toFixed(1)} ${h-pad} L${pts[0][0].toFixed(1)} ${h-pad} Z`;
}
function sparkLastPt(pts){
  for(let i=pts.length-1;i>=0;i--) if(pts[i]) return pts[i];
  return null;
}
function sparkSVG(win, color, key, size){
  // Shared wall-clock X. Flat values (mem stuck at 5%) still draw only over
  // sample times on the right — never a full-width placeholder line.
  const large=!!(size&&size.large);
  const w=large?96:28, h=large?26:9, pad=large?1.4:0.8;
  const stroke=large?1.4:1.1, dot=large?1.55:1.05;
  const uid="s"+Math.random().toString(36).slice(2,7);
  const bounds=sparkTimeBounds(win);
  const nums=sparkFiniteNums(win, key);
  if(!bounds || !nums.length) return sparkEmptySVG(w, h);
  const scale=sparkPlotScale(key, nums);
  const pts=sparkPtsOnAxis(win, key, bounds, w, h, pad, scale.min, scale.max);
  const segs=sparkSegs(pts).filter(s=>s.length>=2);
  const last=sparkLastPt(pts);
  if(!segs.length && !last) return sparkEmptySVG(w, h);
  let html=`<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" preserveAspectRatio="none" aria-hidden="true">
    <defs><linearGradient id="${uid}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="${color}" stop-opacity=".22"/>
      <stop offset="100%" stop-color="${color}" stop-opacity="0"/>
    </linearGradient></defs>`;
  for(const s of segs){
    html+=`<path d="${sparkPathArea(s, h, pad)}" fill="url(#${uid})"/>`;
    html+=`<path d="${sparkPathLine(s)}" fill="none" stroke="${color}" stroke-width="${stroke}" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>`;
  }
  if(last){
    html+=`<circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="${dot}" fill="${color}" vector-effect="non-scaling-stroke"/>`;
  }
  return html+"</svg>";
}
function sparkNetSVG(win, size){
  // Dual-line net spark on the same shared wall-clock axis as CPU/mem/disk.
  const large=!!(size&&size.large);
  const w=large?96:28, h=large?26:9, pad=large?1.4:0.8;
  const stroke=large?1.35:1.05, dot=large?1.45:1.0;
  const bounds=sparkTimeBounds(win);
  const rxNums=sparkFiniteNums(win, "net_rx_kbps");
  const txNums=sparkFiniteNums(win, "net_tx_kbps");
  const nums=rxNums.concat(txNums);
  if(!bounds || !nums.length) return sparkEmptySVG(w, h);
  const scale=sparkPlotScale("net", nums);
  const rxPts=sparkPtsOnAxis(win, "net_rx_kbps", bounds, w, h, pad, scale.min, scale.max);
  const txPts=sparkPtsOnAxis(win, "net_tx_kbps", bounds, w, h, pad, scale.min, scale.max);
  const rxSegs=sparkSegs(rxPts).filter(s=>s.length>=2);
  const txSegs=sparkSegs(txPts).filter(s=>s.length>=2);
  const last=sparkLastPt(rxPts) || sparkLastPt(txPts);
  if(!rxSegs.length && !txSegs.length && !last) return sparkEmptySVG(w, h);
  const uid="n"+Math.random().toString(36).slice(2,7);
  let html=`<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" preserveAspectRatio="none" aria-hidden="true"><defs>
    <linearGradient id="${uid}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#c8ff4a" stop-opacity=".18"/>
      <stop offset="100%" stop-color="#c8ff4a" stop-opacity="0"/>
    </linearGradient></defs>`;
  for(const s of rxSegs){
    html+=`<path d="${sparkPathArea(s, h, pad)}" fill="url(#${uid})"/>`;
    html+=`<path d="${sparkPathLine(s)}" fill="none" stroke="#c8ff4a" stroke-width="${stroke}" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>`;
  }
  for(const s of txSegs){
    html+=`<path d="${sparkPathLine(s)}" fill="none" stroke="#6ad6ff" stroke-width="${stroke}" stroke-opacity=".85" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>`;
  }
  if(last){
    html+=`<circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="${dot}" fill="#c8ff4a" vector-effect="non-scaling-stroke"/>`;
  }
  return html+"</svg>";
}
function sparkDiskIoSVG(win, size){
  // Dual-line disk throughput spark (read + write). Distinct colors from 网络.
  const large=!!(size&&size.large);
  const w=large?96:28, h=large?26:9, pad=large?1.4:0.8;
  const stroke=large?1.35:1.05, dot=large?1.45:1.0;
  const bounds=sparkTimeBounds(win);
  const rNums=sparkFiniteNums(win, "disk_read_kbps");
  const wNums=sparkFiniteNums(win, "disk_write_kbps");
  const nums=rNums.concat(wNums);
  if(!bounds || !nums.length) return sparkEmptySVG(w, h);
  const scale=sparkPlotScale("disk_io", nums);
  const rPts=sparkPtsOnAxis(win, "disk_read_kbps", bounds, w, h, pad, scale.min, scale.max);
  const wPts=sparkPtsOnAxis(win, "disk_write_kbps", bounds, w, h, pad, scale.min, scale.max);
  const rSegs=sparkSegs(rPts).filter(s=>s.length>=2);
  const wSegs=sparkSegs(wPts).filter(s=>s.length>=2);
  const last=sparkLastPt(wPts) || sparkLastPt(rPts);
  if(!rSegs.length && !wSegs.length && !last) return sparkEmptySVG(w, h);
  const uid="d"+Math.random().toString(36).slice(2,7);
  let html=`<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" preserveAspectRatio="none" aria-hidden="true"><defs>
    <linearGradient id="${uid}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#6ad6ff" stop-opacity=".18"/>
      <stop offset="100%" stop-color="#6ad6ff" stop-opacity="0"/>
    </linearGradient></defs>`;
  for(const s of rSegs){
    html+=`<path d="${sparkPathArea(s, h, pad)}" fill="url(#${uid})"/>`;
    html+=`<path d="${sparkPathLine(s)}" fill="none" stroke="#8ec8ff" stroke-width="${stroke}" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>`;
  }
  for(const s of wSegs){
    html+=`<path d="${sparkPathLine(s)}" fill="none" stroke="#6ad6ff" stroke-width="${stroke}" stroke-opacity=".95" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>`;
  }
  if(last){
    html+=`<circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="${dot}" fill="#6ad6ff" vector-effect="non-scaling-stroke"/>`;
  }
  return html+"</svg>";
}
function fmtMetric(key, v){
  if(v==null || !Number.isFinite(Number(v))) return "—";
  const n=Number(v);
  if(key==="uptime_sec") return formatUptime(n);
  if(key==="load1") return String(Math.round(n*100)/100);
  return Math.round(n)+"%";
}
function seriesRange(win, key){
  // Do not Number(null)→0 — that faked disk 0% ranges for missing keys.
  const nums=sparkFiniteNums(win, key);
  if(!nums.length) return null;
  return {min:Math.min(...nums), max:Math.max(...nums)};
}
function fmtKbps(v){
  if(v==null || !Number.isFinite(Number(v))) return "—";
  const n=Number(v);
  if(n>=1000) return String(Math.round(n));
  if(n>=10) return String(Math.round(n));
  return String(Math.round(n*10)/10);
}
function formatNetVal(m){
  if(m.net_rx_kbps==null && m.net_tx_kbps==null) return "—";
  return `↓${fmtKbps(m.net_rx_kbps??0)} ↑${fmtKbps(m.net_tx_kbps??0)} kbps`;
}
function formatNetRange(win){
  const rx=seriesRange(win, "net_rx_kbps");
  const tx=seriesRange(win, "net_tx_kbps");
  if(!rx && !tx) return "";
  const parts=[];
  if(rx) parts.push(`↓${fmtKbps(rx.min)}–${fmtKbps(rx.max)}`);
  if(tx) parts.push(`↑${fmtKbps(tx.min)}–${fmtKbps(tx.max)}`);
  return parts.join(" ")+" kbps";
}
function formatDiskIoRange(win){
  const rd=seriesRange(win, "disk_read_kbps");
  const wr=seriesRange(win, "disk_write_kbps");
  if(!rd && !wr) return "";
  const parts=[];
  if(rd) parts.push(`读${fmtKbps(rd.min)}–${fmtKbps(rd.max)}`);
  if(wr) parts.push(`写${fmtKbps(wr.min)}–${fmtKbps(wr.max)}`);
  return parts.join(" ")+" kbps";
}
function metricCellsHTML(r, opts){
  // List: CPU% + mem + disk pills. Detail: 2×2 charts incl. network (rx+tx).
  // Unreachable / old sample → keep last numbers, dim pills (no age copy).
  // Cloud with SSH link → use linked SSH metrics (unified host path).
  const showRange = !!(opts && opts.showRange);
  const largeSpark = !!(opts && opts.largeSpark);
  const src=metricsResource(r);
  const m=metricsOf(src)||{};
  const win=seriesWindow(seriesOf(src), 30);
  const stale=isMetricsStale(src);
  // Prefer real cpu_used_pct; fall back to old series that only had load1.
  const cpuKey = (m.cpu_used_pct!=null || win.some(p=>p.cpu_used_pct!=null)) ? "cpu_used_pct" : "load1";
  const cells=[
    {key:cpuKey, lab:"CPU", cur: cpuKey==="cpu_used_pct" ? m.cpu_used_pct : m.load1, color:"#c8ff4a"},
    {key:"mem_used_pct", lab:"内存", cur: m.mem_used_pct, color:"#5dffb0"},
  ];
  // Cloud vendor APIs often have no disk_used_pct — don't fake 0% or reuse the
  // 「磁盘」label with ↓↑ (that looked identical to 网络). Call it 盘IO.
  if(m.disk_used_pct!=null){
    cells.push({key:"disk_used_pct", lab:"磁盘", cur: m.disk_used_pct, color:"#6ad6ff"});
  }else if(m.disk_read_kbps!=null || m.disk_write_kbps!=null){
    cells.push({key:"disk_io", lab:"盘IO", cur:null, color:"#6ad6ff", diskIo:true});
  }else{
    cells.push({key:"disk_used_pct", lab:"磁盘", cur:null, color:"#6ad6ff"});
  }
  if(largeSpark){
    cells.push({key:"net", lab:"网络", cur:null, color:"#c8ff4a", net:true});
  }
  const pillCls="mpill"+(stale?" stale":"");
  const rowCls=largeSpark ? "mrow mrow-detail" : "mrow";
  return `<div class="${rowCls}">${cells.map(c=>{
    if(c.net){
      const val=formatNetVal(m);
      const rangeTxt=showRange ? formatNetRange(win) : "";
      const tip=rangeTxt
        ? `网络 当前 ${val} · 近30分钟 ${rangeTxt}`
        : `网络 当前 ${val}`;
      return `<span class="${pillCls}" title="${esc(tip)}">
        <span class="mlab">${c.lab}</span>
        <span class="mval">${esc(val)}</span>
        ${sparkNetSVG(win, {large:true})}
        ${rangeTxt?`<span class="mrange">${esc(rangeTxt)}</span>`:""}
      </span>`;
    }
    if(c.diskIo){
      // Do NOT reuse 网络's ↓↑ — that made 盘IO look like another net pill.
      // Cloud vendor path only has throughput (no disk_used_pct until SSH works).
      // List: spark first + short R/W so the curve is never clipped by long text.
      const val=largeSpark
        ? `读${fmtKbps(m.disk_read_kbps??0)} 写${fmtKbps(m.disk_write_kbps??0)} kbps`
        : `${fmtKbps(m.disk_read_kbps??0)}/${fmtKbps(m.disk_write_kbps??0)}`;
      const rangeTxt=showRange ? formatDiskIoRange(win) : "";
      const tip=rangeTxt
        ? `盘IO 当前 读${fmtKbps(m.disk_read_kbps??0)} 写${fmtKbps(m.disk_write_kbps??0)} kbps · 近30分钟 ${rangeTxt}`
        : `盘IO 读${fmtKbps(m.disk_read_kbps??0)} 写${fmtKbps(m.disk_write_kbps??0)} kbps（云侧吞吐，非占用%）`;
      if(largeSpark){
        return `<span class="${pillCls}" title="${esc(tip)}">
          <span class="mlab">${c.lab}</span>
          <span class="mval">${esc(val)}</span>
          ${sparkDiskIoSVG(win, {large:true})}
          ${rangeTxt?`<span class="mrange">${esc(rangeTxt)}</span>`:""}
        </span>`;
      }
      return `<span class="${pillCls}" title="${esc(tip)}">
        <span class="mlab">${c.lab}</span>
        ${sparkDiskIoSVG(win, {large:false})}
        <span class="mval">${esc(val)}</span>
      </span>`;
    }
    const val=fmtMetric(c.key, c.cur);
    const rg=seriesRange(win, c.key);
    const rangeTxt=(showRange && rg) ? (fmtMetric(c.key, rg.min)+"–"+fmtMetric(c.key, rg.max)) : "";
    const tip=rg
      ? `${c.lab} 当前 ${val} · 近30分钟 ${fmtMetric(c.key, rg.min)}–${fmtMetric(c.key, rg.max)}`
      : `${c.lab} 当前 ${val}`;
    return `<span class="${pillCls}" title="${esc(tip)}">
      <span class="mlab">${c.lab}</span>
      <span class="mval">${esc(val)}</span>
      ${sparkSVG(win, c.color, c.key, {large:largeSpark})}
      ${rangeTxt?`<span class="mrange">${esc(rangeTxt)}</span>`:""}
    </span>`;
  }).join("")}</div>`;
}
function formatMetricsBlockHTML(r){
  const src=metricsResource(r);
  const L=labels(src);
  const m=metricsOf(src);
  if(!m){
    const tip=L.metrics_detail ? ("采样失败: "+L.metrics_detail) : "尚未采样 — 点右上角刷新";
    return `<div class="form hint">${esc(tip)}</div>`;
  }
  // Text block: mem MB / disk free / load / uptime — network lives in the chart.
  const rows=[];
  if(m.mem_used_mb!=null || m.mem_total_mb!=null){
    rows.push(["内存", `${m.mem_used_mb??"?"} / ${m.mem_total_mb??"?"} MB`]);
  }
  if(m.disk_avail_gb!=null){
    rows.push(["磁盘剩余", `${m.disk_avail_gb} GB`]);
  }
  if(m.load1!=null){
    rows.push(["负载", fmtMetric("load1", m.load1)]);
  }
  if(m.uptime_sec!=null){
    rows.push(["已运行", formatUptime(m.uptime_sec)]);
  }
  if(m.disk_used_pct==null && (m.disk_read_kbps!=null || m.disk_write_kbps!=null)){
    rows.push(["盘IO吞吐", `读 ${fmtKbps(m.disk_read_kbps??0)} · 写 ${fmtKbps(m.disk_write_kbps??0)} kbps`]);
  }
  if(m.disk_iops_read || m.disk_iops_write){
    rows.push(["磁盘 IOPS", `↓${m.disk_iops_read??0} ↑${m.disk_iops_write??0}`]);
  }
  const stale=isMetricsStale(src);
  const extra=rows.length
    ? `<div class="msec${stale?" stale":""}">${rows.map(([k,v])=>`<div class="msi"><span class="msk">${esc(k)}</span><span class="msv">${esc(v)}</span></div>`).join("")}</div>`
    : "";
  let note="";
  if(isCloud(r)){
    const ssh=linkedSsh(r);
    const fromSsh=!!(ssh && src===ssh);
    if(fromSsh){
      const st=String(ssh.status||"").toLowerCase();
      if(st==="unreachable" || stale){
        note=`<div class="form hint">指标来自关联 SSH「${esc(ssh.name)}」· 当前采不到新点（每 ${METRICS_INTERVAL_SEC}s 自动重试）</div>`;
      }else{
        note=`<div class="form hint">指标来自关联 SSH「${esc(ssh.name)}」</div>`;
      }
    }else if(m.source){
      if(ssh && sshHandshakeBroken(ssh)){
        note=`<div class="form hint">云侧监控（SSH 连不上：${esc(String(labels(ssh).metrics_detail||"会话失败").slice(0,80))}；磁盘占用%需 SSH，下方「盘IO」是云侧吞吐）</div>`;
      }else if(ssh){
        note=`<div class="form hint">云侧监控（关联 SSH 暂无可用指标；「盘IO」是吞吐不是占用%）</div>`;
      }else{
        note=`<div class="form hint">云侧监控（未开通 SSH；「盘IO」是吞吐不是占用%）</div>`;
      }
    }
  }
  return note+metricCellsHTML(r,{showRange:true, largeSpark:true})+extra;
}
function osLabel(o){
  if(!o) return "";
  const k=String(o).toLowerCase();
  if(k==="unix") return "Linux / macOS";
  if(k==="macos" || k==="darwin") return "macOS";
  if(k==="linux") return "Linux";
  if(k==="windows" || k==="win" || k==="win32") return "Windows";
  return o;
}
function formatDetailMeta(r){
  const L=labels(r);
  if(isCloud(r)){
    return [kindLabel(r.kind), L.region, L.account ? ("账号 "+L.account) : null].filter(Boolean).join(" · ");
  }
  const bits=[
    kindLabel(r.kind),
    osLabel(L.os) || null,
    L.auth ? ("认证 "+authLabel(L.auth)) : null,
    probeDetailLabel(L.last_probe_detail) || null,
  ].filter(Boolean);
  return bits.join(" · ");
}
function fillCloudKv(r){
  const kv=$("dKv");
  if(!kv) return;
  if(!isCloud(r)){
    kv.classList.remove("on");
    kv.innerHTML="";
    return;
  }
  const L=labels(r);
  const has=v=>v!=null && String(v).trim()!=="";
  const cell=(k,v)=>`<div class="pair"><dt>${esc(k)}</dt><dd title="${esc(v)}">${esc(v)}</dd></div>`;
  const wide=(k,v)=>`<div class="pair wide"><dt>${esc(k)}</dt><dd title="${esc(v)}">${esc(v)}</dd></div>`;
  // Short fields share a row; long ids stay full-width.
  const groups=[
    [["厂商", kindLabel(r.kind)], ["地域", L.region]],
    [["公网", L.public_ip], ["内网", L.private_ip]],
    [["规格", L.shape], ["可用域", L.availability_domain]],
    [["账号", L.account], ["SSH", L.ssh_name || "未开通"]],
  ];
  const parts=[];
  for(const pair of groups){
    const alive=pair.filter(([,v])=>has(v));
    if(!alive.length) continue;
    if(alive.length===1) parts.push(cell(alive[0][0], alive[0][1]));
    else parts.push(cell(alive[0][0], alive[0][1])+cell(alive[1][0], alive[1][1]));
  }
  if(has(L.id)) parts.push(wide("实例", L.id));
  kv.innerHTML=parts.join("");
  kv.classList.toggle("on", parts.length>0);
}
function cloudMetricsHint(r){
  const ssh=linkedSsh(r);
  if(ssh){
    const tip=labels(ssh).metrics_detail;
    const st=String(ssh.status||"").toLowerCase();
    if(st==="unreachable"){
      return "关联 SSH「"+ssh.name+"」当前不可达 — 面板仍会每 "+METRICS_INTERVAL_SEC+"s 自动重试，网络恢复后指标会自己回来"+(tip?("（"+tip+"）"):"");
    }
    if(sshHandshakeBroken(ssh)){
      return "关联 SSH「"+ssh.name+"」端口可达但会话失败（"+(tip||"Connection closed")+"）— 命令无法执行；指标改走云侧监控。请检查实例防火墙/授权密钥。";
    }
    if(tip) return "关联 SSH「"+ssh.name+"」: "+tip+"（自动采样进行中）";
    return "已关联 SSH「"+ssh.name+"」，等待主机指标采样…";
  }
  const L=labels(r);
  if(L.metrics_detail) return String(L.metrics_detail);
  const v=cloudVendor(r);
  if(v==="oracle") return "未开通 SSH · 可点刷新拉取 OCI 云侧 CPU/内存/负载/网络（需 Cloud Agent）；开通 SSH 后改走主机指标。";
  if(v==="gcp") return "未开通 SSH · 可点刷新拉取 GCP Monitoring CPU/网络（内存需 Ops Agent）；开通 SSH 后改走主机指标。";
  if(v==="tencent") return "未开通 SSH · 可点刷新拉取腾讯云 CMS CPU/内存/网络；开通 SSH 后改走主机指标。";
  if(v==="aliyun") return "未开通 SSH · 可点刷新拉取 ECS 云侧 CPU/网络（此 API 无内存%）；开通 SSH 后改走主机指标。";
  return "未开通 SSH · 暂无主机指标。";
}
function renderHdrMeta(b){
  const el=$("hdrMeta");
  if(!el) return;
  const h=b.resource_health||{};
  const items=[
    {key:"running", lab:"运行", n:b.running_count||0},
    {key:"reachable", lab:"可达", n:h.reachable||0},
    {key:"unreachable", lab:"不可达", n:h.unreachable||0},
  ];
  el.innerHTML=items.map((it,i)=>{
    const on=listFilter===it.key ? " on" : "";
    const sep=i ? `<span class="sep"> · </span>` : "";
    return `${sep}<button type="button" class="filt${on}" data-filt="${it.key}" title="筛选：${esc(it.lab)}">${esc(it.lab)} ${it.n}</button>`;
  }).join("");
}
function matchesListFilter(r){
  if(isCloudSshTwin(r)) return false;
  if(!listFilter) return true;
  if(listFilter==="running"){
    return activeTasks(r.name).length>0 || activeTasks(execName(r)).length>0;
  }
  // 可达/不可达看可执行 SSH（云清单行看关联 SSH）
  const ssh = r.kind==="ssh" ? r : linkedSsh(r);
  if(!ssh) return false;
  const st=String(ssh.status||"").toLowerCase();
  if(listFilter==="reachable") return st==="reachable";
  if(listFilter==="unreachable") return st==="unreachable";
  return true;
}
function renderMetricsAt(r){
  const el=$("dMetricsAt");
  if(!el) return;
  const ms=r ? metricsAtMs(metricsResource(r)) : 0;
  if(!ms){
    el.textContent="—";
    el.classList.add("empty");
    el.title="尚未采样";
    return;
  }
  const clock=formatLocalClock(ms);
  el.textContent="采样 "+clock;
  el.classList.remove("empty");
  el.title="最近成功采样 "+clock;
}
function isLiveTask(t){
  const s=(t.status||"").toLowerCase();
  return s==="running" || s==="queued" || s==="probing" || s==="orphaned";
}
function activeTasks(name, b){
  b=b||board;
  const r=(b.resources||[]).find(x=>x.name===name);
  const names=new Set([name]);
  if(r && isCloud(r) && labels(r).ssh_name) names.add(labels(r).ssh_name);
  const fromBoard=(b.running||[]).filter(t=>names.has(t.resource_name) && isLiveTask(t));
  if(fromBoard.length) return fromBoard;
  let out=[];
  names.forEach(n=>{ out=out.concat(((b.by_resource&&b.by_resource[n])||[]).filter(isLiveTask)); });
  return out;
}
function liveLabel(t){
  if(!t) return "";
  const s=(t.status||"").toLowerCase();
  if(s==="queued") return "排队中";
  if(s==="probing") return "探测中";
  if(s==="orphaned") return "待核对";
  return "执行中";
}
function listFingerprint(b){
  const res=(b.resources||[]).map(r=>{
    const live=activeTasks(r.name,b).map(t=>t.id+":"+t.status+":"+(t.action||"")).join(",");
    const L=r.labels||{};
    const srcName=L.ssh_name||"";
    const src=(srcName && (b.resources||[]).find(x=>x.name===srcName)) || r;
    const SL=src.labels||src.meta||L;
    const m=SL.metrics||{};
    const series=SL.metrics_series||[];
    const last=series.length ? series[series.length-1].t : "";
    const ageTick=SL.metrics_at ? Math.floor((Date.now()-parseTs(SL.metrics_at))/5000) : 0;
    const ms=[m.cpu_used_pct,m.load1,m.mem_used_pct,m.disk_used_pct,m.uptime_sec,SL.metrics_at,series.length,last,ageTick].join(",");
    const sshSt=srcName ? ((b.resources||[]).find(x=>x.name===srcName)||{}).status : "";
    const tpath=(L.tunnel&&L.tunnel.path)||"";
    const offLan=((L.tunnel&&L.tunnel.direct_proven)?"d":"")+((L.tunnel&&L.tunnel.relay_proven)?"r":"");
    const setup=b.tunnel_setup||{};
    const pending=(setup.pending_lan||[]).join(",");
    return r.name+"|"+(r.status||"")+"|"+(L.ssh_name||"")+"|"+(sshSt||"")+"|"+tpath+"|"+offLan+"|"+live+"|"+ms+"|"+(setup.show_prompt?1:0)+"|"+(setup.offer?1:0)+"|"+(setup.show_retry?1:0)+"|"+pending;
  }).join(";");
  const h=b.resource_health||{};
  return res+"#"+ (b.running_count||0) +"#"+ (h.reachable||0) +"#"+ (h.unreachable||0);
}
function compactMetricsLine(r){
  // Two-column density: numbers only, no sparks.
  const src=metricsResource(r);
  const m=metricsOf(src)||{};
  const parts=[];
  if(m.cpu_used_pct!=null) parts.push(Math.round(Number(m.cpu_used_pct))+"%");
  else if(m.load1!=null) parts.push("L"+fmtMetric("load1", m.load1));
  if(m.mem_used_pct!=null) parts.push(Math.round(Number(m.mem_used_pct))+"%");
  if(m.disk_used_pct!=null) parts.push(Math.round(Number(m.disk_used_pct))+"%");
  else if(m.disk_read_kbps!=null || m.disk_write_kbps!=null){
    parts.push("盘"+fmtKbps(m.disk_read_kbps??0)+"/"+fmtKbps(m.disk_write_kbps??0));
  }
  if(!parts.length){
    if(needsSshLink(r)) return `<div class="mcompact">未开通 SSH</div>`;
    return `<div class="mcompact">—</div>`;
  }
  const tip="CPU / 内存 / 磁盘(或盘IO吞吐)";
  return `<div class="mcompact" title="${esc(tip)}">${esc(parts.join(" · "))}</div>`;
}
function syncListLayoutBtn(){
  const btn=$("btnListLayout");
  if(!btn) return;
  const compact=listLayout==="compact";
  btn.textContent=compact ? "单列" : "两列";
  btn.title=compact ? "切换为单列（带曲线）" : "切换为两列紧凑（无曲线）";
}
let rvBusy=false;
let rvBusyLabel="";
let rvLast=null;
const pathSeen={};
function offLanReady(r){
  if(!r || r.kind!=="ssh" || isCloud(r)) return false;
  const tun=labels(r).tunnel;
  if(!tun || !tun.enabled || !tun.node_id) return false;
  if(tun.relay_proven!==true && tun.direct_proven!==true) return false;
  const url=String(tun.rendezvous_url||"")
    || ((board.tunnel_setup&&board.tunnel_setup.active&&board.tunnel_setup.active.url)||"");
  if(!url) return false;
  const gaps=(board.tunnel_setup&&board.tunnel_setup.port_gaps)||[];
  return !gaps.length;
}
function offLanTag(r){
  if(!offLanReady(r)) return "";
  const tun=labels(r).tunnel||{};
  const direct=tun.direct_proven===true;
  const relay=tun.relay_proven===true;
  let text="支持多通道";
  let title="局域网之外还有汇合点通道。人在局域网里仍先走局域网 IP。";
  if(direct && !relay){
    text="支持直连";
    title="汇合点直连还活着。人在局域网里仍先走局域网 IP。";
  }else if(relay && !direct){
    text="支持中继";
    title="汇合点中继还活着。人在局域网里仍先走局域网 IP。";
  }
  return `<span class="cloud-tag" title="${esc(title)}">${esc(text)}</span>`;
}
function tunnelLeaveNote(r){
  if(!r || r.kind!=="ssh") return "";
  const tun=labels(r).tunnel;
  const path=tun && tun.enabled ? String(tun.path||"") : "";
  const prev=pathSeen[r.name];
  const key="grip-rv-note:"+r.name;
  let flag="";
  try{ flag=localStorage.getItem(key)||""; }catch(e){}
  if(prev==="lan" && (path==="tunnel" || path==="relay") && !flag){
    try{ localStorage.setItem(key, "show"); }catch(e){}
    flag="show";
  }
  if(path==="lan" && flag==="show"){
    try{ localStorage.setItem(key, "done"); }catch(e){}
    flag="done";
  }
  if(path) pathSeen[r.name]=path;
  if(flag==="show" && (path==="tunnel" || path==="relay")){
    return `<div class="addr">已改走隧道</div>`;
  }
  return "";
}
function panelWaitHint(err){
  const msg=String(err&&err.message||err||"");
  if(/load failed|failed to fetch|networkerror|aborted/i.test(msg)){
    return "面板没有等到接入结果。接入还在这台电脑上继续，稍等后再看这一条；如果停了，点重试。";
  }
  if(/[\u4e00-\u9fff]/.test(msg)) return msg;
  return "接入没有完成。点重试再来一次。";
}
function renderRvStrip(){
  const el=$("rvStrip");
  if(!el) return;
  const plan=board.tunnel_setup||{};
  const job=board.tunnel_job||{};
  if(job.running){
    el.classList.remove("hidden");
    el.innerHTML=esc(job.hint||rvBusyLabel||"正在接入局域网设备…");
    syncRvOfferBtn();
    return;
  }
  if(rvBusy){
    el.classList.remove("hidden");
    el.innerHTML=esc(rvBusyLabel||"正在安装汇合点，并接入局域网设备…");
  }else if(rvLast){
    el.classList.remove("hidden");
    const ports=(rvLast.port_gaps||[]).length ? "" : (rvLast.ports||[]).join("、");
    const retry=!rvLast.ok
      ? `<button type="button" class="btn primary sm" id="rvRetry" data-reenroll="1">${(rvLast.port_gaps||[]).length?"重试":"重新接入"}</button>`
      : "";
    el.innerHTML=`<div>${esc(rvLast.hint||"接入没有完成。点重试再来一次。")}</div>`
      +(ports?`<div class="rv-note">端口 ${esc(ports)}</div>`:"")
      +`<div class="rv-actions"><button type="button" class="btn ghost sm" id="rvDismiss">以后再说</button>${retry}</div>`;
  }else if(plan.show_ports){
    const host=plan.host_name||(plan.active&&plan.active.host_name)||"";
    el.classList.remove("hidden");
    el.innerHTML=`<div>${esc(plan.port_hint||"")}</div>`
      +`<div class="rv-actions"><button type="button" class="btn ghost sm" id="rvDismiss">以后再说</button><button type="button" class="btn primary sm" id="rvRetry" data-host="${esc(host)}" data-reenroll="1">重试</button></div>`;
  }else if(plan.show_retry){
    const host=(plan.active&&plan.active.host_name)||"";
    const pending=(plan.pending_lan||[]);
    if(!pending.length){
      el.classList.add("hidden");
      el.innerHTML="";
      syncRvOfferBtn();
      return;
    }
    const names=pending.join("、");
    const line=`汇合点已就绪。这些局域网设备还没接上：${esc(names)}。`;
    el.classList.remove("hidden");
    el.innerHTML=`<div>${line}</div>`
      +`<div class="rv-actions"><button type="button" class="btn ghost sm" id="rvDismiss">以后再说</button><button type="button" class="btn primary sm" id="rvRetry" data-host="${esc(host)}" data-reenroll="1">重新接入</button></div>`;
  }else if(plan.show_prompt){
    const picks=(plan.candidates||[]).map(c=>`<button type="button" class="btn primary sm" data-rv="${esc(c.name)}">${esc(c.name)}</button>`).join("");
    el.classList.remove("hidden");
    el.innerHTML=`<div>出门后家里的设备会断。选一台公网机器当汇合点，局域网设备会自动接上。</div>`
      +`<div class="rv-actions"><button type="button" class="btn ghost sm" id="rvDismiss">以后再说</button>${picks}</div>`;
  }else{
    el.classList.add("hidden");
    el.innerHTML="";
  }
  const dismiss=$("rvDismiss");
  if(dismiss) dismiss.addEventListener("click", ()=>dismissRvPrompt(true));
  const retry=$("rvRetry");
  if(retry) retry.addEventListener("click", ()=>{
    const again=(rvLast && rvLast.host_name) || retry.getAttribute("data-host");
    const reenroll=retry.getAttribute("data-reenroll")==="1";
    rvLast=null;
    if(again) chooseRendezvous(again, reenroll);
    else renderRvStrip();
  });
  el.querySelectorAll("[data-rv]").forEach(btn=>{
    btn.addEventListener("click", ()=>chooseRendezvous(btn.getAttribute("data-rv")));
  });
  syncRvOfferBtn();
}
function syncRvOfferBtn(){
  const plan=board.tunnel_setup||{};
  let btn=$("btnRvOffer");
  const gaps=(plan.port_gaps||[]).length;
  const stripOn=!!(plan.show_ports || plan.show_prompt || plan.show_retry || (rvLast && rvLast.ok===false));
  const want=!!(
    !stripOn
    && (plan.offer || gaps || (plan.pending_lan||[]).length)
  );
  if(!want){
    if(btn) btn.remove();
    return;
  }
  if(!btn){
    btn=document.createElement("button");
    btn.type="button";
    btn.id="btnRvOffer";
    btn.className="btn ghost sm";
    btn.textContent="出门也能连";
    btn.title="选择一台公网机器当汇合点";
    const layout=$("btnListLayout");
    if(layout && layout.parentNode) layout.parentNode.insertBefore(btn, layout);
    btn.addEventListener("click", ()=>dismissRvPrompt(false));
  }
}
async function dismissRvPrompt(dismissed){
  if(dismissed) rvLast=null;
  await api("/api/tunnel/setup/dismiss",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({dismissed:!!dismissed})});
  if(!dismissed) rvLast=null;
  board=await api("/api/board");
  renderList(true);
}
async function chooseRendezvous(name, reenroll){
  if(!name || rvBusy) return;
  const plan=board.tunnel_setup||{};
  const reuse=!!(plan.active && plan.active.host_name===name);
  const gaps=(plan.port_gaps||[]).length;
  rvBusyLabel=gaps
    ? "正在确认 443，并接入局域网设备…"
    : (reuse
      ? "汇合点已在运行，正在重新接入局域网设备…"
      : "正在安装汇合点，并接入局域网设备…");
  rvBusy=true;
  rvLast=null;
  renderRvStrip();
  try{
    await api("/api/tunnel/setup",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name, reenroll:!!reenroll})});
  }catch(err){
    rvBusy=false;
    rvLast={ok:false, hint:panelWaitHint(err)};
    renderList(true);
    return;
  }
  rvBusy=false;
  for(let i=0;i<180;i++){
    try{ board=await api("/api/board"); }
    catch(e){
      rvLast={ok:false, hint:"面板暂时读不到接入进度。稍等几秒再看这一条。"};
      renderList(true);
      return;
    }
    const job=board.tunnel_job||{};
    if(job.running){
      renderList(true);
      await new Promise(r=>setTimeout(r, 1000));
      continue;
    }
    rvLast=job.result||null;
    renderList(true);
    if(rvLast && rvLast.ok){
      const shown=rvLast;
      setTimeout(()=>{ if(rvLast===shown){ rvLast=null; renderList(true); } }, 4000);
    }
    return;
  }
  rvLast={ok:false, hint:"接入还没结束。稍等再看这一条；如果一直停着，点重试。"};
  renderList(true);
}
function renderList(force){
  renderRvStrip();
  const sig=listFingerprint(board)+"|"+(listFilter||"")+"|"+listLayout;
  if(!force && sig===listSig){
    renderHdrMeta(board);
    return;
  }
  listSig=sig;
  const list=$("deviceList"), empty=$("emptyList");
  const all=(board.resources||[]).filter(r=>!isCloudSshTwin(r));
  const res=all.filter(matchesListFilter);
  list.innerHTML="";
  const compact=listLayout==="compact";
  list.classList.toggle("layout-compact", compact);
  syncListLayoutBtn();
  const shell=$("listShell");
  const foot=$("listFoot");
  if(foot){
    foot.textContent=compact
      ? "两列总览 · 左侧⋮拖动排序 · 点选进详情"
      : "左侧⋮拖动排序 · 点选查看指标与操作";
  }
  if(!all.length){
    empty.classList.remove("hidden");
    if(shell) shell.classList.add("hidden");
    $("emptyTitle").textContent="还没有设备";
    $("emptyHint").textContent="先加一台 SSH 设备（局域网或云服务器）";
  }else{
    empty.classList.add("hidden");
    if(shell) shell.classList.remove("hidden");
    if(!res.length){
      const lab=listFilter==="running"?"运行中":(listFilter==="reachable"?"可达":"不可达");
      list.innerHTML=`<div class="form hint">无「${esc(lab)}」设备 — 再点筛选清除</div>`;
    }
  }
  res.forEach((r)=>{
    const live=activeTasks(r.name);
    const top=live[0];
    const busy=!!top;
    const needSsh=needsSshLink(r);
    const ds=displayStatus(r);
    const btn=document.createElement("button");
    btn.type="button";
    btn.className="device"+(busy?" busy":"")+(needSsh?" need-ssh":"");
    btn.dataset.name=r.name;
    const sc=busy ? "busy" : ds.cls;
    let tag="";
    if(busy){
      tag=`<span class="busy-tag">${esc(liveLabel(top))}${live.length>1?" +"+(live.length-1):""}</span>`;
    }else if(isCloud(r)){
      // Cloud-synced origin matters more than an "SSH linked" badge.
      const cloudLab=kindLabel(r.kind);
      tag=`<span class="cloud-tag" title="云账号同步 · ${esc(cloudLab)}">${esc(cloudLab)}</span>`;
      if(needSsh && !compact){
        tag+=`<span class="need-ssh-tag" title="云清单已同步，尚未开通可执行 SSH">未开通 SSH</span>`;
      }
      const sshTwin=linkedSsh(r);
      if(sshTwin && labels(sshTwin).credentials_ok===false){
        tag+=`<span class="need-cred-tag" title="关联 SSH 凭证丢失，请编辑 SSH 设备重填">缺凭证</span>`;
      }
    }else if(!isCloud(r) && labels(r).credentials_ok===false){
      tag=`<span class="need-cred-tag" title="保险库里没有这台设备的密码/私钥 — 点进去编辑并重填">缺凭证</span>`;
    }
    tag=offLanTag(r)+tag;
    const tip=ds.lab;
    const body=compact
      ? `<div class="title-row"><span class="name">${esc(r.name)}</span>${tag}</div>
         ${compactMetricsLine(r)}`
      : `<div class="title-row"><span class="name">${esc(r.name)}</span>${tag}</div>
         <div class="addr">${esc(addrOf(r))}</div>
         ${tunnelLeaveNote(r)}
         ${metricCellsHTML(r,{showRange:false})}`;
    btn.innerHTML=`<span class="drag-handle" draggable="true" title="拖动排序" aria-label="拖动排序">⋮⋮</span>
      <span class="dot ${sc}" title="${esc(tip)}"></span>
      <span>${body}</span>`;
    btn.addEventListener("click",(e)=>{
      if(listDragMoved) return;
      if(e.target && e.target.closest && e.target.closest(".drag-handle")) return;
      openDetail(r.name);
    });
    list.appendChild(btn);
  });
  bindListDrag(list);
  renderHdrMeta(board);
  localize(document.body);
}
let listDragName=null;
let listDragEl=null;
let listDragMoved=false;
function panelVisibleNames(){
  return (board.resources||[]).filter(r=>!isCloudSshTwin(r)).map(r=>r.name);
}
function mergeVisibleOrder(fullNames, visibleOrdered){
  const vis=new Set(visibleOrdered);
  const q=visibleOrdered.slice();
  return fullNames.map(n=> vis.has(n) ? q.shift() : n).filter(n=>n!=null);
}
async function persistListOrderFromDom(listEl){
  const visible=[...listEl.querySelectorAll(".device")].map(el=>el.dataset.name).filter(Boolean);
  if(!visible.length) return;
  const full=panelVisibleNames();
  const names=listFilter ? mergeVisibleOrder(full, visible) : visible.slice();
  // Keep any brand-new names board already has but DOM missed.
  const seen=new Set(names);
  for(const n of full){ if(!seen.has(n)) names.push(n); }
  try{
    await api("/api/resources/reorder",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({names})
    });
    await refresh();
  }catch(e){ toast(String(e.message||e)); }
}
function bindListDrag(listEl){
  if(!listEl || listEl._gripDragBound) return;
  listEl._gripDragBound=true;
  listEl.addEventListener("dragstart",(e)=>{
    const handle=e.target && e.target.closest && e.target.closest(".drag-handle");
    if(!handle || !listEl.contains(handle)) return;
    const card=handle.closest(".device");
    if(!card) return;
    listDragName=card.dataset.name||null;
    listDragEl=card;
    listDragMoved=false;
    card.classList.add("dragging");
    try{
      e.dataTransfer.effectAllowed="move";
      e.dataTransfer.setData("text/plain", listDragName||"");
    }catch(_e){}
  });
  listEl.addEventListener("dragend",()=>{
    listEl.querySelectorAll(".device.dragging,.device.drag-over").forEach(el=>{
      el.classList.remove("dragging","drag-over");
    });
    const moved=listDragMoved;
    listDragName=null;
    listDragEl=null;
    // Allow click suppression only for this gesture; clear next tick.
    setTimeout(()=>{ listDragMoved=false; }, 0);
    if(moved) persistListOrderFromDom(listEl);
  });
  listEl.addEventListener("dragover",(e)=>{
    if(!listDragName) return;
    e.preventDefault();
    try{ e.dataTransfer.dropEffect="move"; }catch(_e){}
    const card=e.target && e.target.closest && e.target.closest(".device");
    if(!card || !listEl.contains(card)) return;
    listEl.querySelectorAll(".device.drag-over").forEach(el=>{
      if(el!==card) el.classList.remove("drag-over");
    });
    if(card.dataset.name!==listDragName) card.classList.add("drag-over");
  });
  listEl.addEventListener("dragleave",(e)=>{
    const card=e.target && e.target.closest && e.target.closest(".device");
    if(card) card.classList.remove("drag-over");
  });
  listEl.addEventListener("drop",(e)=>{
    e.preventDefault();
    const toCard=e.target && e.target.closest && e.target.closest(".device");
    const fromCard=listDragEl;
    if(!toCard || !listEl.contains(toCard) || !fromCard || !listDragName) return;
    const toName=toCard.dataset.name;
    if(!toName || toName===listDragName) return;
    const cards=[...listEl.querySelectorAll(".device")];
    const fromIdx=cards.indexOf(fromCard);
    const toIdx=cards.indexOf(toCard);
    if(fromIdx<0 || toIdx<0 || fromIdx===toIdx) return;
    if(fromIdx<toIdx) toCard.after(fromCard);
    else toCard.before(fromCard);
    listDragMoved=true;
    listEl.querySelectorAll(".device.drag-over").forEach(el=>el.classList.remove("drag-over"));
  });
}
function esc(s){
  return String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
}
function openDetail(name){
  selected=name;
  const r=(board.resources||[]).find(x=>x.name===name);
  if(!r){toast("设备不存在"); show("list"); return;}
  const ds=displayStatus(r);
  $("dName").textContent=r.name;
  $("dAddr").textContent=addrOf(r);
  const badge=$("dStatus");
  badge.textContent=ds.lab;
  badge.className="badge "+ds.cls;
  $("dMeta").textContent=formatDetailMeta(r);
  fillCloudKv(r);
  const cloud=isCloud(r);
  const linked=linkedSsh(r);
  const canExec=!!linked || (!cloud && r.kind==="ssh");
  const cmdPanel=$("cmdPanel"), opsPanel=$("opsPanel"), metricsPanel=$("metricsPanel");
  if(cmdPanel) cmdPanel.classList.toggle("hidden", cloud && !linked);
  if(opsPanel) opsPanel.classList.toggle("hidden", false);
  if(cloud){
    const src=metricsResource(r);
    const has=hasUsableMetrics(src);
    // Always render the chart block when we have any sample; otherwise keep
    // the hint. Never leave the 指标 panel completely empty after a sync.
    $("dMetrics").innerHTML = has
      ? formatMetricsBlockHTML(r)
      : `<div class="hint-banner">${esc(cloudMetricsHint(r))}</div>`+formatMetricsBlockHTML(r);
  }else{
    $("dMetrics").innerHTML=formatMetricsBlockHTML(r);
  }
  renderMetricsAt(r);
  const actions=$("dActions"); actions.innerHTML="";
  const mk=(label,cls,fn)=>{const b=document.createElement("button");b.type="button";b.className="btn sm "+(cls||"");b.textContent=label;b.addEventListener("click",fn);actions.appendChild(b);};
  if(cloud){
    if(linked){
      mk("探测","primary",()=>probeOne(linked.name));
      mk("刷新指标","ghost",()=>sampleMetrics(name));
      mk("复制 SSH","ghost",()=>copySsh(linked));
      mk("删除","danger",()=>delRes(name));
    }else{
      mk("开通 SSH","primary",()=>linkCloudSsh(name));
      mk("刷新指标","ghost",()=>sampleMetrics(name));
      mk("删除","danger",()=>delRes(name));
    }
  }else{
    mk("探测","primary",()=>probeOne(name));
    mk("编辑","ghost",()=>openEdit(name));
    mk("复制 SSH","ghost",()=>copySsh(r));
    mk("删除","danger",()=>delRes(name));
  }
  const presets=$("presets");
  if(presets){
    presets.innerHTML="";
    if(canExec){
      presetsFor(linked || r).forEach(cmd=>{
        const c=document.createElement("button"); c.type="button"; c.className="chip"; c.textContent=cmd;
        c.addEventListener("click",()=>{$("cmdInput").value=cmd; runCmd();});
        presets.appendChild(c);
      });
    }
  }
  if(canExec) syncRunControls(linked || r);
  detailTaskSig="";
  refresh._metaSig="";
  refresh._mSig="";
  renderTasks(name, true);
  show("detail");
}
function hostRunnable(r){
  if(!r) return false;
  const target=isCloud(r) ? linkedSsh(r) : r;
  if(!target) return false;
  if(labels(target).credentials_ok===false) return false;
  if(sshHandshakeBroken(target)) return false;
  return (target.status||"").toLowerCase()!=="unreachable";
}
function runBlockReason(r){
  if(!r) return "未选择设备";
  const target=isCloud(r) ? linkedSsh(r) : r;
  if(isCloud(r) && !target) return "尚未开通 SSH，无法在云清单行上执行命令";
  if(target && labels(target).credentials_ok===false){
    return "凭证丢失（保险库重置后密码/私钥未重填）— 请点「编辑」重新保存密码或私钥后再执行";
  }
  if(target && sshHandshakeBroken(target)){
    const tip=String(labels(target).metrics_detail||"会话失败").trim();
    return "SSH 连不上（"+tip.slice(0,120)+"）— 端口探测成功但会话被关掉，命令都会失败。请检查实例防火墙 / 授权密钥；指标可继续看云侧。";
  }
  if(target && (target.status||"").toLowerCase()==="unreachable"){
    return "设备不可达 — 请先探测/恢复网络后再执行命令（不会进入执行中）。";
  }
  return "";
}
function syncRunControls(r){
  const ok=hostRunnable(r);
  const reason=runBlockReason(r);
  const btn=$("btnRun");
  const input=$("cmdInput");
  if(btn){ btn.disabled=!ok; btn.title=ok?"":reason; }
  if(input){ input.disabled=!ok; }
  document.querySelectorAll("#presets .chip").forEach(c=>{ c.disabled=!ok; c.style.opacity=ok?"":"0.45"; });
  const out=$("cmdOut");
  if(out && !ok){
    out.textContent=reason || "当前无法执行命令";
  }else if(out && (!out.textContent || /不可达|凭证丢失|尚未开通 SSH|SSH 连不上|在这台设备上执行/.test(out.textContent))){
    out.textContent="在这台设备上执行只读命令；写操作需确认。";
  }
}
function tasksFingerprint(name){
  return ((board.by_resource&&board.by_resource[name])||[]).slice(0,12)
    .map(t=>t.id+"|"+t.status+"|"+(t.finished_at||t.created_at||"")+"|"+(t.summary||t.error||"").slice(0,40)).join(";");
}
function taskResultText(t){
  // Prefer stored stdout/stderr tail (summary); fall back to error.
  // Strip agent wire markers so the panel shows the real command output.
  let s=String(t.summary||t.error||"").trim();
  if(!s) return "";
  s=s.replace(/^__GRIP_(STARTED|FINISHED|OUT_B64)__[^\n]*\n?/gm,"").trim();
  return s;
}
function taskResultOneLine(result){
  // Collapsed preview: first line only (width ellipsis via CSS).
  const line=String(result||"").split(/\r?\n/).find(x=>x.trim()) || String(result||"");
  return line.trim();
}
function taskResultClipped(result){
  // True when collapsed view hides part of the output (extra lines, or
  // the one visible line overflows the pill width).
  const s=String(result||"");
  if(!s) return false;
  if(/\r?\n/.test(s.trim())) return true;
  return false; // width overflow checked after layout
}
function syncTaskExpandBtn(el, result){
  const t3=el.querySelector(".t3");
  const btn=el.querySelector(".tmore");
  if(!t3 || !btn || !result) return;
  if(el.classList.contains("open")){
    btn.classList.remove("hidden");
    btn.textContent="收起";
    return;
  }
  const widthClip=t3.scrollWidth > t3.clientWidth+1;
  const clipped=taskResultClipped(result) || widthClip;
  btn.classList.toggle("hidden", !clipped);
  btn.textContent="展开";
}
function renderTasks(name, force){
  const r=(board.resources||[]).find(x=>x.name===name);
  const names=[name];
  if(r && isCloud(r) && labels(r).ssh_name) names.push(labels(r).ssh_name);
  const prevBox=$("dTasks");
  const openIds=new Set(
    prevBox
      ? Array.from(prevBox.querySelectorAll(".task.open")).map(el=>el.getAttribute("data-id")).filter(Boolean)
      : []
  );
  const sig=names.map(tasksFingerprint).join("#");
  if(!force && sig===detailTaskSig) return;
  detailTaskSig=sig;
  const box=$("dTasks"); box.innerHTML="";
  let tasks=[];
  names.forEach(n=>{ tasks=tasks.concat((board.by_resource&&board.by_resource[n])||[]); });
  tasks.sort((a,b)=>String(b.finished_at||b.created_at||"").localeCompare(String(a.finished_at||a.created_at||"")));
  if(!tasks.length){box.innerHTML='<div class="form hint">暂无任务</div>';return;}
  const pending=[];
  tasks.slice(0,12).forEach(t=>{
    const el=document.createElement("div");
    const opened=openIds.has(t.id);
    el.className="task "+(t.status||"")+(opened?" open":"");
    el.setAttribute("data-id", t.id||"");
    const who=actorLabel(t.actor);
    const cmd=(t.command||"").trim();
    const result=taskResultText(t);
    const when=taskWhen(t);
    const exit=(t.exit_code!=null && t.exit_code!=="") ? (" · exit "+t.exit_code) : "";
    let body=`<div class="t1"><span class="tt">${esc(when)}</span><span class="tbody">${esc(statusLabel(t.status))} · ${esc(actionLabel(t.action))} · ${esc(who)}${esc(exit)}</span></div>`;
    if(cmd) body+=`<div class="t2">$ ${esc(cmd.slice(0,120))}</div>`;
    if(result){
      const shown=opened ? result : taskResultOneLine(result);
      // Button starts hidden; shown only when output is actually clipped.
      body+=`<div class="t3row"><div class="t3">${esc(shown)}</div>
        <button type="button" class="tmore hidden">${opened?"收起":"展开"}</button></div>`;
    }else if(!cmd){
      body+=`<div class="t2">无输出</div>`;
    }
    el.innerHTML=body;
    el.addEventListener("click",(ev)=>{
      if(!result) return;
      const row=ev.target && ev.target.closest && ev.target.closest(".t3row");
      if(!row) return;
      const btn=el.querySelector(".tmore");
      // Only expand/collapse when clipped (button visible) or already open.
      if(!el.classList.contains("open") && (!btn || btn.classList.contains("hidden"))) return;
      ev.preventDefault();
      el.classList.toggle("open");
      const open=el.classList.contains("open");
      const t3=el.querySelector(".t3");
      if(t3) t3.textContent=open ? result : taskResultOneLine(result);
      syncTaskExpandBtn(el, result);
      const out=$("cmdOut");
      if(out && open){
        out.textContent=(cmd?("$ "+cmd+"\n\n"):"")+result;
      }
    });
    box.appendChild(el);
    if(result) pending.push([el, result]);
  });
  // After layout: reveal 展开 only when the one-line preview overflows.
  requestAnimationFrame(()=>{
    pending.forEach(([el, result])=>syncTaskExpandBtn(el, result));
  });
}
async function refresh(){
  try{
    board = await api("/api/board");
  }catch(e){
    if(window.__GRIP_BOOT__ && window.__GRIP_BOOT__.board){
      board = window.__GRIP_BOOT__.board;
    }else{
      $("hdrMeta").textContent = "接口错误";
      $("emptyTitle").textContent = "读不到设备列表";
      $("emptyHint").textContent = String(e.message||e);
      $("emptyList").classList.remove("hidden");
      throw e;
    }
  }
  renderList(false);
  if(!$("view-detail").classList.contains("hidden") && selected){
    const still=(board.resources||[]).some(r=>r.name===selected);
    if(still){
      const r=(board.resources||[]).find(x=>x.name===selected);
      const live=activeTasks(selected)[0];
      const ds=displayStatus(r);
      const sc=live ? "warn" : ds.cls;
      $("dStatus").textContent=live
        ? (liveLabel(live)+" · "+actionLabel(live.action))
        : ds.lab;
      $("dStatus").className="badge "+sc;
      $("dAddr").textContent=addrOf(r);
      const metaTxt=formatDetailMeta(r);
      if(refresh._metaSig!==metaTxt){
        refresh._metaSig=metaTxt;
        $("dMeta").textContent=metaTxt;
      }
      let mBlock;
      if(isCloud(r)){
        const src=metricsResource(r);
        const has=hasUsableMetrics(src);
        mBlock=has
          ? formatMetricsBlockHTML(r)
          : `<div class="hint-banner">${esc(cloudMetricsHint(r))}</div>`+formatMetricsBlockHTML(r);
      }else{
        mBlock=formatMetricsBlockHTML(r);
      }
      if(refresh._mSig!==mBlock){
        refresh._mSig=mBlock;
        $("dMetrics").innerHTML=mBlock;
      }
      renderMetricsAt(r);
      renderTasks(selected, false);
      syncRunControls(r);
    } else { selected=null; show("list"); }
  }
  localize(document.body);
}
async function probeOne(name){
  try{
    await api("/api/probe",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name})});
    await refresh();
    sampleMetrics(name, {quiet:true}).catch(()=>{});
  }catch(e){toast(String(e.message||e))}
}
async function sampleMetrics(name, opts){
  // Quiet by default for poll/probe; no success toasts — heartbeat shows cadence.
  const quiet=!!(opts&&opts.quiet);
  if(metricsInFlight) return;
  metricsInFlight=true;
  try{
    const body=name? {name} : {};
    const o=await api("/api/metrics",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    if(o.error) throw new Error(o.error);
    // Native tick may already hold the metrics lock — quiet skip.
    if(o.status==="busy"){ await refresh(); return; }
    if(o.status==="skipped"){
      if(!quiet) toast(o.detail || "该云暂不支持指标采样");
      await refresh();
      return;
    }
    const failed=(o.results||[]).filter(x=>!x.ok);
    if(name){
      const one=(o.results&&o.results[0])||{};
      if(!one.ok && !quiet){
        toast(one.skipped ? ("暂无指标: "+(one.detail||"")) : ("指标失败: "+(one.detail||"")));
      }
    }else if(failed.length && !quiet){
      toast("指标失败: "+failed.length+"/"+ (o.checked||0));
    }
    await refresh();
    // Only rebuild detail when user is still looking at it (never yank from
    // list/clouds). Quiet ticks already update metrics via refresh().
    if(!quiet && name && selected===name && !$("view-detail").classList.contains("hidden")){
      openDetail(name);
    }
  }catch(e){
    if(!quiet) toast(String(e.message||e));
  }
  finally{metricsInFlight=false}
}
async function copySsh(r){
  const L=labels(r);
  const cmd=`ssh -p ${L.port||22} ${L.user||""}@${L.host||""}`;
  try{await navigator.clipboard.writeText(cmd);toast("已复制: "+cmd);}
  catch{toast(cmd)}
}
async function delRes(name){
  const r=(board.resources||[]).find(x=>x.name===name);
  const cloud=r && isCloud(r);
  const twin=cloud ? labels(r).ssh_name : null;
  const body=cloud
    ? ("将删除「"+name+"」"+(twin?" 及关联 SSH「"+twin+"」":"")+"。\n下次同步若云端仍有这台，会再问你是否恢复。")
    : ("将删除设备「"+name+"」及其本地凭证。此操作不可撤销。");
  const ok=await askConfirm({title:"删除设备", body, okLabel:"删除"});
  if(!ok) return;
  try{
    const out=await api("/api/resources/delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name})});
    if(!out || out.ok===false) throw new Error((out&&out.error)||"删除失败");
    toast(cloud ? "已删除（同步时再确认是否恢复）" : "已删除");
    selected=null; show("list"); await refresh();
  }catch(e){ toast(String(e.message||e)); }
}
async function runCmd(){
  if(!selected) return;
  const cur=(board.resources||[]).find(x=>x.name===selected);
  const targetName=execName(cur) || selected;
  const target=(board.resources||[]).find(x=>x.name===targetName) || cur;
  if(!hostRunnable(cur)){
    toast("设备不可达，无法执行");
    syncRunControls(target);
    return;
  }
  const command=$("cmdInput").value.trim();
  if(!command){toast("输入命令");return;}
  const out=$("cmdOut");
  out.textContent="执行中…";
  $("btnRun").disabled=true;
  try{
    let confirm_write=false;
    if(/\b(rm|mv|dd|mkfs|shutdown|reboot|kill|chmod|chown|sudo)\b/i.test(command)){
      confirm_write=await askConfirm({
        title:"写操作确认",
        body:"这条命令可能修改系统。确认以写权限执行？\n\n$ "+command,
        okLabel:"确认执行",
        danger:true
      });
      if(!confirm_write){out.textContent="已取消"; syncRunControls(target); return;}
    }
    const started=await api("/api/run",{
      method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({name:targetName, command, confirm_write, background:true, actor:"panel"})
    });
    if(started.status==="denied"){out.innerHTML='<span class="err">拒绝：写操作需要确认</span>';return;}
    if(started.status==="failed"){
      const err=String(started.error||"");
      if(/secret_missing/i.test(err)){
        out.innerHTML='<span class="err">凭证丢失：请点「编辑」重新填写密码或私钥后保存</span>';
        await refresh();
        return;
      }
      if(/run_command_requires_ssh/i.test(err)){
        out.innerHTML='<span class="err">云清单不能直接执行命令 — 请先开通关联 SSH</span>';
        await refresh();
        return;
      }
      if(/不可达|unreachable/i.test(err)){
        out.innerHTML=`<span class="err">${esc(err||"主机不可达")}</span>`;
        await refresh();
        return;
      }
    }
    const id=started.task_id;
    out.textContent=`任务 ${id} · ${statusLabel(started.status)}`;
    for(let i=0;i<40;i++){
      await new Promise(r=>setTimeout(r,500));
      const g=await api("/api/task?id="+encodeURIComponent(id));
      const t=g.task||g;
      if(["success","failed","denied","orphaned"].includes(t.status)){
        const lines=[];
        lines.push(`${statusLabel(t.status)} · 退出码 ${t.exit_code??"—"}`);
        if(t.summary) lines.push(String(t.summary).slice(0,4000));
        if(t.error){
          const e=String(t.error);
          if(/secret_missing/i.test(e)) lines.push("错误: 凭证丢失 — 请编辑设备重填密码/私钥");
          else lines.push("错误: "+e);
        }
        out.textContent=lines.join("\n") || JSON.stringify(t,null,2);
        break;
      }
      out.textContent=`${statusLabel(t.status||"running")} · ${(t.summary||"等待会话…")}`;
    }
    await refresh();
  }catch(e){
    out.innerHTML=`<span class="err">${esc(e.message||e)}</span>`;
  }finally{
    const r2=(board.resources||[]).find(x=>x.name===selected);
    syncRunControls(r2 && isCloud(r2) ? (linkedSsh(r2)||r2) : r2);
  }
}

let editAuth="password";
let _editOrig={host:"",user:"",port:22};
function openEdit(name){
  const r=(board.resources||[]).find(x=>x.name===name);
  if(!r){toast("设备不存在"); return;}
  const L=labels(r);
  selected=name;
  $("editName").value=r.name;
  $("editHost").value=L.host||"";
  $("editUser").value=L.user||"";
  $("editPort").value=L.port||22;
  _editOrig={host:String(L.host||""), user:String(L.user||""), port:Number(L.port||22)};
  {
    const os=String(L.os||"unix").toLowerCase();
    $("editOs").value=["linux","macos","windows","unix"].includes(os) ? os : "unix";
    _editOsPrev=$("editOs").value;
  }
  editAuth=(L.auth==="key")?"key":"password";
  document.querySelectorAll("#editAuthSeg button").forEach(x=>{
    x.classList.toggle("on", x.dataset.auth===editAuth);
  });
  $("editPwBox").classList.toggle("hidden", editAuth!=="password");
  $("editKeyBox").classList.toggle("hidden", editAuth==="password");
  $("editPassword").value="";
  $("editKey").value="";
  show("edit");
}
function defaultSshPort(os){
  // OpenSSH on Linux / macOS / Windows all listen on 22 by default.
  const k=String(os||"").toLowerCase();
  if(k==="windows" || k==="win" || k==="win32") return 22;
  return 22;
}
function applyOsDefaultPort(osEl, portEl, prevOs){
  // Only overwrite when empty or still at the previous OS default (don't
  // clobber a custom port the user already typed).
  if(!osEl || !portEl) return;
  const next=defaultSshPort(osEl.value);
  const cur=Number(portEl.value);
  const prevDef=defaultSshPort(prevOs);
  if(!portEl.value || !Number.isFinite(cur) || cur===prevDef){
    portEl.value=String(next);
  }
}
let _sshOsPrev=$("sshOs") ? $("sshOs").value : "linux";
let _editOsPrev=$("editOs") ? $("editOs").value : "linux";
if($("sshOs")){
  $("sshOs").addEventListener("change",()=>{
    applyOsDefaultPort($("sshOs"), $("sshPort"), _sshOsPrev);
    _sshOsPrev=$("sshOs").value;
  });
}
if($("editOs")){
  $("editOs").addEventListener("change",()=>{
    applyOsDefaultPort($("editOs"), $("editPort"), _editOsPrev);
    _editOsPrev=$("editOs").value;
  });
}
function openAddDevice(){
  _sshOsPrev=$("sshOs").value || "linux";
  applyOsDefaultPort($("sshOs"), $("sshPort"), _sshOsPrev);
  show("add");
}
$("btnAdd").addEventListener("click",()=>openAddDevice());
$("btnEmptyAdd").addEventListener("click",()=>openAddDevice());
$("btnClouds").addEventListener("click",()=>{showCloudsList(); show("clouds");});
$("btnBack").addEventListener("click",()=>{selected=null; show("list"); refresh();});
$("btnBackAdd").addEventListener("click",()=>show("list"));
$("btnBackEdit").addEventListener("click",()=>show("detail"));
$("btnBackClouds").addEventListener("click",()=>show("list"));
$("btnBackCloudForm").addEventListener("click",()=>showCloudsList());
$("btnCloudAdd").addEventListener("click",()=>openCloudForm(null));
document.querySelectorAll("#editAuthSeg button").forEach(b=>{
  b.addEventListener("click",()=>{
    editAuth=b.dataset.auth;
    document.querySelectorAll("#editAuthSeg button").forEach(x=>x.classList.toggle("on",x===b));
    $("editPwBox").classList.toggle("hidden", editAuth!=="password");
    $("editKeyBox").classList.toggle("hidden", editAuth==="password");
  });
});
$("btnSaveEdit").addEventListener("click",async()=>{
  const body={
    name:$("editName").value.trim(),
    host:$("editHost").value.trim(),
    user:$("editUser").value.trim(),
    port:Number($("editPort").value||22),
    os:$("editOs").value,
  };
  const endpointChanged =
    body.host!==_editOrig.host ||
    body.user!==_editOrig.user ||
    Number(body.port)!==Number(_editOrig.port);
  const hasPw=editAuth==="password" && !!$("editPassword").value;
  const hasKey=editAuth==="key" && !!$("editKey").value.trim();
  if(endpointChanged && !hasPw && !hasKey){
    toast("主机/端口/用户已改 — 请填写新密码或私钥（留空会继续用上一台的凭证）");
    return;
  }
  if(hasPw) body.password=$("editPassword").value;
  if(hasKey) body.key_file=$("editKey").value.trim();
  try{
    await api("/api/resources/ssh/update",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    toast("已保存");
    await refresh();
    openDetail(body.name);
  }catch(e){
    const msg=String(e.message||e);
    if(/endpoint_changed_requires_credentials/i.test(msg)){
      toast("主机/端口/用户已改 — 请填写新密码或私钥");
    }else{
      toast(msg);
    }
  }
});
$("btnListLayout").addEventListener("click",()=>{
  listLayout = listLayout==="compact" ? "comfortable" : "compact";
  try{ localStorage.setItem("grip.listLayout", listLayout); }catch(_e){}
  renderList(true);
});
$("btnExportAll").addEventListener("click",()=>exportAudit(null));
$("btnExportDevice").addEventListener("click",()=>{
  if(!selected){toast("先选设备");return;}
  exportAudit(selected);
});
$("btnRefreshMetrics").addEventListener("click",()=>{
  if(selected){
    resetBeat();
    sampleMetrics(selected).catch(()=>{});
  }
});
$("hdrMeta").addEventListener("click",(e)=>{
  const btn=e.target.closest("[data-filt]");
  if(!btn || !$("hdrMeta").contains(btn)) return;
  const key=btn.getAttribute("data-filt");
  listFilter = listFilter===key ? null : key;
  renderList(true);
});
$("btnRun").addEventListener("click",runCmd);
$("cmdInput").addEventListener("keydown",e=>{if(e.key==="Enter") runCmd();});

document.querySelectorAll("#authSeg button").forEach(b=>{
  b.addEventListener("click",()=>{
    sshAuth=b.dataset.auth;
    document.querySelectorAll("#authSeg button").forEach(x=>x.classList.toggle("on",x===b));
    $("sshPwBox").classList.toggle("hidden", sshAuth!=="password");
    $("sshKeyBox").classList.toggle("hidden", sshAuth==="password");
  });
});

$("btnSaveSsh").addEventListener("click",async()=>{
  const body={
    name:$("sshName").value.trim(), host:$("sshHost").value.trim(), user:$("sshUser").value.trim(),
    port:Number($("sshPort").value||22), os:$("sshOs").value, auth:sshAuth,
    password:$("sshPassword").value, key_file:$("sshKey").value.trim()
  };
  try{
    await api("/api/resources/ssh",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    $("sshPassword").value="";
    toast("已保存，正在探测…");
    show("list");
    await refresh();
    if(body.name) await probeOne(body.name);
    openDetail(body.name);
  }catch(e){toast(String(e.message||e))}
});

$("cloudVendor").addEventListener("change",()=>syncCloudVendorFields());
function syncCloudVendorFields(){
  const v=$("cloudVendor").value;
  ["aliyun","tencent","gcp","oracle"].forEach(x=>{
    const el=$("fields-"+x); if(el) el.classList.toggle("hidden", x!==v);
  });
}
let cloudAccountsCache=[];
function cloudAccountSub(a){
  const meta=a.meta||{};
  const regions=Array.isArray(meta.regions) ? meta.regions.join(", ") : (meta.regions||meta.region||"");
  const bits=[
    regions ? (trText("地域")+" "+regions) : null,
    meta.ssh_user ? ("SSH "+meta.ssh_user+(meta.ssh_key_set?trText(" · 已设私钥"):"")) : trText("未设实例 SSH"),
  ].filter(Boolean);
  return bits.join(" · ") || a.id;
}
function showCloudsList(){
  $("cloudsListPane").classList.remove("hidden");
  $("cloudsFormPane").classList.add("hidden");
  refreshClouds();
}
function clearCloudForm(){
  $("cloudEditId").value="";
  $("cloudLabel").value="";
  $("cloudRegions").value="";
  $("aliAk").value=""; $("aliSk").value="";
  $("tcId").value=""; $("tcKey").value=""; $("tcProduct").value="lighthouse";
  $("gcpProject").value=""; $("gcpSa").value=""; $("gcpSa").title="";
  $("gcpSaEmail").value="";
  $("gcpSaEmail").classList.add("hidden");
  $("gcpSaEmailLab").classList.add("hidden");
  $("ociUser").value=""; $("ociTenancy").value=""; $("ociFp").value="";
  $("ociKey").value=""; $("ociKey").title=""; $("ociComp").value="";
  $("cloudSshUser").value=""; $("cloudSshKey").value=""; $("cloudSshKey").title="";
  $("cloudFormHint").textContent="";
}
function openCloudForm(account){
  clearCloudForm();
  $("cloudsListPane").classList.add("hidden");
  $("cloudsFormPane").classList.remove("hidden");
  const editing=!!account;
  $("cloudFormTitle").textContent=editing ? ("编辑 · "+(account.cloud||"")+" / "+(account.label||"")) : "添加云账号";
  $("cloudVendor").disabled=editing;
  $("cloudLabel").disabled=editing;
  $("btnDeleteCloudEdit").classList.toggle("hidden", !editing);
  if(editing){
    $("cloudEditId").value=account.id||"";
    $("cloudVendor").value=account.cloud||"aliyun";
    $("cloudLabel").value=account.label||"";
    const meta=account.meta||{};
    const form=account.form||{};
    const regions=Array.isArray(meta.regions) ? meta.regions.join(", ") : (meta.regions||meta.region||"");
    $("cloudRegions").value=regions||"";
    if(account.cloud==="aliyun"){
      $("aliAk").value=form.ak_id||"";
    }
    if(account.cloud==="tencent"){
      $("tcProduct").value=meta.product||"lighthouse";
      $("tcId").value=form.tc_id||"";
    }
    if(account.cloud==="gcp"){
      $("gcpProject").value=meta.project||form.project_id||"";
      if(form.client_email){
        $("gcpSaEmail").value=form.client_email;
        $("gcpSaEmail").classList.remove("hidden");
        $("gcpSaEmailLab").classList.remove("hidden");
      }
    }
    if(account.cloud==="oracle"){
      $("ociComp").value=meta.compartment_id||"";
      $("ociUser").value=form.user_ocid||meta.user_ocid||"";
      $("ociTenancy").value=form.tenancy_ocid||meta.tenancy_ocid||"";
      $("ociFp").value=form.fingerprint||meta.fingerprint||"";
    }
    $("cloudSshUser").value=meta.ssh_user||"";
    let hint=meta.ssh_key_set
      ? "普通字段直接改。仅「API 私钥 / AccessKey Secret / 服务账号 JSON / 实例登录私钥」留空表示不更换（实例登录私钥已保存）。"
      : "普通字段直接改。仅「API 私钥 / AccessKey Secret / 服务账号 JSON / 实例登录私钥」留空表示不更换。";
    if(account.need_rekey){
      hint=meta.ssh_key_set
        ? "凭证库里的密钥已失效或丢失 — 请重新填写标识字段并选择 API 私钥后再保存。"
        : "凭证库里的密钥已失效或丢失 — 请重新填写标识字段并选择 API 私钥后再保存。实例登录私钥如需开通 SSH 也请一并重选。";
    }else if(account.signing_set){
      hint+=" API 凭证已保存。";
    }
    $("cloudFormHint").textContent=hint;
  }else{
    $("cloudVendor").value="aliyun";
    $("cloudFormHint").textContent="新建需填写厂商凭证。实例 SSH 可选，填了同步后会自动开通。";
  }
  syncCloudVendorFields();
  localize($("cloudsFormPane"));
}
async function refreshClouds(){
  const clouds=await api("/api/clouds");
  cloudAccountsCache=clouds.accounts||[];
  const box=$("cloudList"); box.innerHTML="";
  cloudAccountsCache.forEach(a=>{
    const el=document.createElement("button");
    el.type="button"; el.className="device cloud-row";
    // Cloud rows: dot | body only (no drag handle — that column collapsed text).
    el.innerHTML=`<span class="dot warn"></span>
      <span class="cloud-row-body">
        <div class="name">${esc(a.cloud)} · ${esc(a.label||a.id)}</div>
        <div class="addr">${esc(cloudAccountSub(a))}</div>
      </span>`;
    el.addEventListener("click",()=>openCloudForm(a));
    box.appendChild(el);
  });
  if(!cloudAccountsCache.length) box.innerHTML='<div class="form hint">尚未配置云账号 — 点右上角「添加」</div>';
}
const SSH_KEY_PICK_IDS=new Set(["sshKey","editKey","cloudSshKey","linkSshKey"]);
async function pickPathInto(inputId){
  const el=$(inputId);
  if(!el) return;
  const isKey=SSH_KEY_PICK_IDS.has(inputId);
  const body={prompt: isKey ? "选择私钥" : "选择文件"};
  if(isKey) body.start_dir="~/.ssh";
  try{
    const o=await api("/api/pick-file",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    if(o.cancelled) return;
    if(o.error){ toast("无法打开文件选择器"); return; }
    if(o.path){ el.value=o.path; el.title=o.display_path||o.path; }
  }catch(e){ toast(String(e.message||e)); }
}
function defaultCloudSshUser(r){
  const v=cloudVendor(r);
  if(v==="oracle") return "opc";
  if(v==="aliyun" || v==="tencent" || v==="gcp") return "ubuntu";
  return "ubuntu";
}
function askLinkSsh(opts){
  // In-panel form: WKWebView has no window.prompt.
  const name=(opts&&opts.name)||"";
  const defUser=(opts&&opts.user)||"ubuntu";
  return new Promise((resolve)=>{
    const mask=$("linkSshMask");
    const userEl=$("linkSshUser");
    const keyEl=$("linkSshKey");
    const okBtn=$("linkSshOk");
    const cancelBtn=$("linkSshCancel");
    $("linkSshHint").textContent="为「"+name+"」创建可执行 SSH。选好用户和本机私钥后点开通。";
    userEl.value=defUser;
    keyEl.value="";
    keyEl.title="";
    mask.classList.remove("hidden");
    const done=(v)=>{
      mask.classList.add("hidden");
      okBtn.removeEventListener("click", onOk);
      cancelBtn.removeEventListener("click", onCancel);
      mask.removeEventListener("click", onMask);
      resolve(v);
    };
    const onOk=()=>{
      const user=userEl.value.trim();
      const key=keyEl.value.trim();
      if(!user){ toast("需要 SSH 用户"); userEl.focus(); return; }
      if(!key){ toast("需要选择私钥文件"); return; }
      done({user, key_file:key});
    };
    const onCancel=()=>done(null);
    const onMask=(e)=>{ if(e.target===mask) done(null); };
    okBtn.addEventListener("click", onOk);
    cancelBtn.addEventListener("click", onCancel);
    mask.addEventListener("click", onMask);
    setTimeout(()=>{ userEl.focus(); userEl.select(); }, 0);
  });
}
async function linkCloudSsh(name){
  const r=(board.resources||[]).find(x=>x.name===name);
  const form=await askLinkSsh({name, user:defaultCloudSshUser(r)});
  if(!form) return;
  try{
    const o=await api("/api/clouds/link-ssh",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name, ssh_user:form.user, key_file:form.key_file})});
    if(o.error){ toast(o.detail || o.error); return; }
    toast("已开通 SSH");
    await refresh();
    openDetail(name); // stay on cloud row (SSH twin stays hidden in list)
  }catch(e){toast(String(e.message||e))}
}
function buildCloudBody(){
  const v=$("cloudVendor").value;
  const body={cloud:v,label:$("cloudLabel").value.trim(),regions:$("cloudRegions").value.trim()};
  if(v==="aliyun"){body.access_key_id=$("aliAk").value;body.access_key_secret=$("aliSk").value}
  if(v==="tencent"){body.secret_id=$("tcId").value;body.secret_key=$("tcKey").value;body.product=$("tcProduct").value}
  if(v==="gcp"){body.project=$("gcpProject").value;body.sa_json=$("gcpSa").value}
  if(v==="oracle"){body.user_ocid=$("ociUser").value;body.tenancy_ocid=$("ociTenancy").value;body.fingerprint=$("ociFp").value;body.key_file=$("ociKey").value;body.compartment_id=$("ociComp").value}
  const su=$("cloudSshUser").value.trim();
  const sk=$("cloudSshKey").value.trim();
  if(su) body.ssh_user=su;
  if(sk) body.ssh_key_file=sk;
  return body;
}
$("btnSaveCloud").addEventListener("click",async()=>{
  const editId=$("cloudEditId").value.trim();
  const body=buildCloudBody();
  try{
    if(editId){
      body.id=editId;
      await api("/api/clouds/update",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
      toast("云账号已更新");
    }else{
      if(!body.label){ toast("需要备注名"); return; }
      await api("/api/clouds/add",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
      toast("云账号已保存");
    }
    showCloudsList();
  }catch(e){toast(String(e.message||e))}
});
$("btnDeleteCloudEdit").addEventListener("click",async()=>{
  const id=$("cloudEditId").value.trim();
  if(!id) return;
  const ok=await askConfirm({
    title:"删除云账号",
    body:"将删除账号「"+id+"」及其本地凭证。已同步的设备不会自动删掉。",
    okLabel:"删除"
  });
  if(!ok) return;
  try{
    await api("/api/clouds/delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id})});
    toast("已删除");
    showCloudsList();
  }catch(e){toast(String(e.message||e))}
});
$("btnSync").addEventListener("click",async()=>{
  try{
    const o=await api("/api/sync-all",{method:"POST"});
    const parts=(o.results||[]).map(r=>`${r.cloud} ${r.count||0} 台`);
    const err=(o.errors||[]).map(e=>`${e.cloud}: ${e.error}`).join("；");
    const ok=o.ok!=null ? o.ok : (o.results||[]).reduce((s,r)=>s+(r.count||0),0);
    const linked=o.ssh_linked!=null ? o.ssh_linked : (o.results||[]).reduce((s,r)=>s+(r.ssh_linked||0),0);
    const skipped=o.dismissed_present||[];
    await refresh();
    if(err) toast("同步部分失败 · "+err);
    else {
      let msg=ok ? ("已同步 "+ok+" 台 · "+parts.join(" · ")) : "同步完成，没有新实例";
      if(linked) msg+=" · 已开通 SSH "+linked+" 台";
      else if(ok) msg+="（无 SSH：请在账号填实例私钥，或在详情开通）";
      toast(msg);
    }
    if(skipped.length){
      const names=skipped.map(x=>x.name||x).filter(Boolean);
      const preview=names.slice(0,8).join("\n")+(names.length>8?"\n…":"");
      const restore=await askConfirm({
        title:"恢复已删除的云设备？",
        body:"云端仍有 "+names.length+" 台此前从本机删除的实例：\n"+preview+"\n\n要在这次把它们同步回设备列表吗？",
        okLabel:"恢复这 "+names.length+" 台",
        danger:false
      });
      if(restore){
        const r=await api("/api/resources/restore-dismissed",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({names})});
        await refresh();
        toast(r.ok===false ? (r.error||"恢复失败") : ("已恢复 "+(r.restored||names).length+" 台"));
      }
    }
  }catch(e){toast(String(e.message||e))}
});
document.addEventListener("click",(e)=>{
  const btn=e.target && e.target.closest && e.target.closest("[data-pick]");
  if(!btn) return;
  const id=btn.getAttribute("data-pick");
  if(id) pickPathInto(id);
});

listSig="";
async function refreshPermissions(){
  try{
    const st=await api("/api/permissions");
    renderSetupGate(st);
    return st;
  }catch(e){
    toast(String(e.message||e));
    return null;
  }
}
function renderSetupGate(st){
  const gate=$("setupGate");
  if(!gate) return;
  const ready=!!(st&&st.ready);
  gate.classList.toggle("hidden", ready);
  const cont=$("setupContinue");
  if(cont) cont.disabled=!ready;
  const box=$("setupItems");
  if(!box) return;
  const items=(st&&st.items)||[];
  if(!items.length){
    box.innerHTML='<div class="form hint">正在检查本机授权项…若一直空白，请点「重新检查」或「改用文件保管」。</div>';
    return;
  }
  box.innerHTML=items.map(it=>{
    const ok=!!it.ok;
    const badge=ok?'<span class="badge-mini ok">已授权</span>':'<span class="badge-mini bad">未授权</span>';
    const btn=ok
      ? `<button type="button" class="btn ghost sm" disabled>完成</button>`
      : `<button type="button" class="btn primary sm" data-perm="${esc(it.id)}">去授权</button>`;
    const tip=ok?"":(it.id==="keychain"
      ?" 未签名包若无系统弹窗 → 用下方「改用文件保管」。"
      :(it.id==="files"?" 点按钮后选 ~/.ssh 里任意一个私钥文件即可。":""));
    return `<div class="setup-item">
      <div class="st">${esc(it.title||it.id)}</div>${badge}
      <div class="sb">${esc(it.blurb||"")}${it.detail?(" · "+esc(it.detail)):""}${tip}</div>
      ${btn}
    </div>`;
  }).join("");
}
$("setupItems").addEventListener("click",async(e)=>{
  const btn=e.target&&e.target.closest&&e.target.closest("[data-perm]");
  if(!btn) return;
  const id=btn.getAttribute("data-perm");
  btn.disabled=true;
  btn.textContent="授权中…";
  try{
    // Keychain dialog can wait for user click — allow longer, then fall back tip.
    const ctrl=typeof AbortController!=="undefined"?new AbortController():null;
    const timer=ctrl?setTimeout(()=>ctrl.abort(),90000):null;
    let out;
    try{
      out=await api("/api/permissions/request",{
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({id}),
        signal:ctrl?ctrl.signal:undefined
      });
    }finally{
      if(timer) clearTimeout(timer);
    }
    const st=out.status||out;
    renderSetupGate(st);
    if(st&&st.ready){
      toast("权限已就绪");
      unlockAfterPermissions();
    }else if(!out.ok){
      const tip=(out.item&&out.item.detail)||"尚未完成授权";
      toast(id==="keychain"?(tip+"。也可点「改用文件保管」"):tip);
    }
  }catch(err){
    const msg=String(err.message||err);
    toast(id==="keychain"&&/abort/i.test(msg)
      ?"钥匙串授权超时，请点「改用文件保管」继续"
      :msg);
    refreshPermissions();
  }
});
$("setupRecheck").addEventListener("click",()=>refreshPermissions());
$("setupFileVault").addEventListener("click",async()=>{
  const btn=$("setupFileVault");
  if(btn){btn.disabled=true;btn.textContent="处理中…";}
  try{
    const out=await api("/api/permissions/request",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({id:"file_vault"})
    });
    const st=out.status||out;
    renderSetupGate(st);
    if(st&&st.ready){
      toast("已改用文件保险库");
      unlockAfterPermissions();
    }else{
      toast("文件保管已启用，请再完成其余授权");
    }
  }catch(err){
    toast(String(err.message||err));
  }finally{
    if(btn){btn.disabled=false;btn.textContent="改用文件保管";}
  }
});
let permissionsUnlocked=false;
function unlockAfterPermissions(){
  if(permissionsUnlocked) return;
  permissionsUnlocked=true;
  $("setupGate").classList.add("hidden");
  renderList(true);
  refresh().catch(()=>{});
  fireQuietMetrics();
  if(!pollTimer){
    pollTimer=setInterval(()=>{ if(metricsViewsActive()) refresh().catch(()=>{}); }, 3000);
  }
}
$("setupContinue").addEventListener("click",()=>unlockAfterPermissions());

(async function bootPermissions(){
  const bootPerm=window.__GRIP_BOOT__&&window.__GRIP_BOOT__.permissions;
  if(bootPerm) renderSetupGate(bootPerm);
  startBeatCountdown();
  // Always re-verify once (Keychain may have been revoked).
  const st=await refreshPermissions();
  if(st&&st.ready) unlockAfterPermissions();
})();
</script>
</body>
</html>
"""


def _embed_script_json(obj: Any) -> str:
    """Serialize JSON safe to embed inside a ``<script>`` element.

    Task summaries / command output can contain raw HTML (e.g. ``</script>``).
    Leaving those literals in the boot blob terminates the script early and the
    panel never boots — empty list, empty auth items, dead ``$`` helper.
    """
    return (
        json.dumps(obj, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _boot_board(board: dict[str, Any]) -> dict[str, Any]:
    """Lean board for first HTML paint; full history still comes from /api/board."""
    by_src = board.get("by_resource") if isinstance(board.get("by_resource"), dict) else {}
    by_lean: dict[str, list[dict[str, Any]]] = {}
    for name, tasks in by_src.items():
        stubs: list[dict[str, Any]] = []
        for t in (tasks or [])[:8]:
            if not isinstance(t, dict):
                continue
            stubs.append(
                {
                    "id": t.get("id"),
                    "status": t.get("status"),
                    "resource": t.get("resource") or name,
                    "created_at": t.get("created_at"),
                    "finished_at": t.get("finished_at"),
                    "summary": str(t.get("summary") or "")[:200],
                    "error": str(t.get("error") or "")[:200],
                    "command": str(t.get("command") or "")[:120],
                }
            )
        by_lean[str(name)] = stubs
    tasks_lean = [t for stubs in by_lean.values() for t in stubs][:40]
    return {
        "resources": board.get("resources") or [],
        "resource_health": board.get("resource_health") or {},
        "running_count": board.get("running_count") or 0,
        "running": board.get("running") or [],
        "security": board.get("security"),
        "model": board.get("model"),
        "by_resource": by_lean,
        "tasks": tasks_lean,
        "tunnel_setup": board.get("tunnel_setup") or {},
        "tunnel_job": board.get("tunnel_job") or {},
    }


def _json_response(handler: BaseHTTPRequestHandler, code: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _download_response(
    handler: BaseHTTPRequestHandler,
    *,
    body: str,
    media_type: str,
    filename: str,
) -> None:
    data = body.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", media_type)
    handler.send_header(
        "Content-Disposition", f'attachment; filename="{filename}"'
    )
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length) if length else b"{}"
    return json.loads(raw.decode("utf-8") or "{}")


def make_handler(svc: GripService):
    """HTTP handler bound to a GripService (used by browser UI and menubar popover)."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            if path in ("/", "/board", "/settings", "/index.html", "/devices"):
                # Embed live board so WKWebView shows devices even if fetch() fails.
                from grip.i18n import UI_EN, get_lang

                try:
                    svc.refresh_status()
                    boot = {"board": _boot_board(svc.board()), "lang": get_lang()}
                except Exception as exc:  # noqa: BLE001
                    boot = {
                        "board": {"resources": [], "error": str(exc)},
                        "lang": get_lang(),
                    }
                try:
                    from grip import permissions as perms

                    boot["permissions"] = perms.status()
                except Exception as exc:  # noqa: BLE001
                    boot["permissions"] = {
                        "ready": False,
                        "gate": True,
                        "error": str(exc),
                        "items": [],
                    }
                # Must escape </script> etc. — task HTML historically killed the page JS.
                boot_js = "window.__GRIP_BOOT__ = " + _embed_script_json(boot) + ";"
                i18n_js = "const EN = " + _embed_script_json(UI_EN) + ";"
                html = APP_HTML.replace("/*__GRIP_BOOT__*/", boot_js, 1)
                html = html.replace("/*__GRIP_I18N__*/", i18n_js, 1)
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/api/board":
                svc.refresh_status()
                return _json_response(self, 200, svc.board())
            if path == "/api/clouds":
                return _json_response(self, 200, svc.list_cloud_accounts())
            if path == "/api/task":
                qs = parse_qs(parsed.query or "")
                task_id = (qs.get("id") or [""])[0]
                return _json_response(self, 200, svc.get_task(task_id))
            if path == "/api/autostart":
                from grip import autostart

                return _json_response(self, 200, autostart.status())
            if path == "/api/lang":
                from grip.i18n import get_lang

                return _json_response(self, 200, {"lang": get_lang()})
            if path == "/api/permissions":
                from grip import permissions as perms

                return _json_response(self, 200, perms.status())
            if path == "/api/audit/export":
                qs = parse_qs(parsed.query or "")
                name = (qs.get("name") or [""])[0].strip() or None
                fmt = (qs.get("format") or ["jsonl"])[0].strip() or "jsonl"
                limit_raw = (qs.get("limit") or [""])[0].strip()
                limit = int(limit_raw) if limit_raw.isdigit() else None
                as_json = (qs.get("as") or [""])[0].strip().lower() == "json"
                out = svc.export_audit(name=name, fmt=fmt, limit=limit)
                if out.get("error"):
                    return _json_response(self, 400, out)
                if as_json:
                    return _json_response(self, 200, out)
                return _download_response(
                    self,
                    body=out.get("body") or "",
                    media_type=out.get("media_type") or "application/octet-stream",
                    filename=out.get("filename") or "grip-audit.jsonl",
                )
            return _json_response(self, 404, {"error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                data = _read_json(self)
            except json.JSONDecodeError:
                return _json_response(self, 400, {"error": "invalid_json"})
            try:
                if path == "/api/resources/ssh":
                    auth = data.get("auth") or "password"
                    name = (data.get("name") or "").strip()
                    host = (data.get("host") or "").strip()
                    user = (data.get("user") or "").strip()
                    port = int(data.get("port") or 22)
                    os_name = data.get("os") or "unix"
                    if auth == "password":
                        r = svc.resources.add_ssh(
                            name=name,
                            host=host,
                            user=user,
                            password=data.get("password") or "",
                            port=port,
                            os_name=os_name,
                        )
                    else:
                        r = svc.resources.add_ssh(
                            name=name,
                            host=host,
                            user=user,
                            key_file=Path(data.get("key_file") or ""),
                            port=port,
                            os_name=os_name,
                        )
                    # Best-effort: learn windows vs unix so L3 metrics pick the right script.
                    svc.maybe_detect_ssh_os(r.name)
                    r = svc.resources.get(r.name) or r
                    svc.refresh_status()
                    return _json_response(self, 200, r.summary())
                if path == "/api/resources/delete":
                    out = svc.delete_resource((data.get("name") or "").strip())
                    code = 200 if out.get("ok") else 404
                    return _json_response(self, code, out)
                if path == "/api/resources/restore-dismissed":
                    names = data.get("names") or []
                    if not isinstance(names, list):
                        return _json_response(self, 400, {"error": "names_required"})
                    out = svc.restore_dismissed([str(x) for x in names])
                    code = 200 if out.get("ok") else 400
                    return _json_response(self, code, out)
                if path == "/api/resources/reorder":
                    names = data.get("names") or []
                    if not isinstance(names, list):
                        return _json_response(self, 400, {"error": "names_required"})
                    out = svc.reorder_resources([str(x) for x in names])
                    return _json_response(self, 200, out)
                if path == "/api/clouds/add":
                    cloud = (data.get("cloud") or "").lower()
                    label = (data.get("label") or "").strip()
                    regions = [
                        x.strip()
                        for x in str(data.get("regions") or "").split(",")
                        if x.strip()
                    ]
                    if cloud == "aliyun":
                        meta = {
                            "regions": regions or ["cn-hangzhou"],
                            "ak_id": data.get("access_key_id") or "",
                        }
                        secret = {
                            "access_key_id": data.get("access_key_id"),
                            "access_key_secret": data.get("access_key_secret"),
                        }
                    elif cloud == "tencent":
                        meta = {
                            "regions": regions or ["ap-guangzhou"],
                            "product": data.get("product") or "lighthouse",
                            "tc_id": data.get("secret_id") or "",
                        }
                        secret = {
                            "secret_id": data.get("secret_id"),
                            "secret_key": data.get("secret_key"),
                        }
                    elif cloud == "gcp":
                        sa_path = Path(data.get("sa_json") or "").expanduser()
                        sa = json.loads(sa_path.read_text(encoding="utf-8"))
                        meta = {
                            "project": data.get("project") or sa.get("project_id"),
                            "client_email": sa.get("client_email") or "",
                            "project_id": sa.get("project_id") or "",
                        }
                        secret = {"service_account": sa}
                    elif cloud == "oracle":
                        key_path = Path(data.get("key_file") or "").expanduser()
                        if not str(data.get("key_file") or "").strip():
                            return _json_response(
                                self, 400, {"error": "请选择 Oracle API 私钥文件"}
                            )
                        if not key_path.is_file():
                            return _json_response(
                                self,
                                400,
                                {"error": f"私钥文件不存在: {key_path}"},
                            )
                        key = key_path.read_text(encoding="utf-8")
                        # OCIDs / fingerprint also live in meta so edit form
                        # still fills after a vault wipe (private key stays vault-only).
                        meta = {
                            "compartment_id": data.get("compartment_id"),
                            "regions": regions or ["ap-tokyo-1"],
                            "user_ocid": data.get("user_ocid") or "",
                            "tenancy_ocid": data.get("tenancy_ocid") or "",
                            "fingerprint": data.get("fingerprint") or "",
                        }
                        secret = {
                            "user_ocid": data.get("user_ocid"),
                            "tenancy_ocid": data.get("tenancy_ocid"),
                            "fingerprint": data.get("fingerprint"),
                            "private_key": key,
                            "compartment_id": data.get("compartment_id"),
                            "region": (regions or ["ap-tokyo-1"])[0],
                        }
                    else:
                        return _json_response(self, 400, {"error": "unknown_cloud"})
                    acc = svc.accounts.upsert(
                        cloud=cloud, label=label, meta=meta, secret_payload=secret
                    )
                    ssh_user = (data.get("ssh_user") or "").strip()
                    ssh_key = (data.get("ssh_key_file") or "").strip()
                    if ssh_user and ssh_key:
                        try:
                            acc = svc.accounts.set_instance_ssh_defaults(
                                acc.id,
                                ssh_user=ssh_user,
                                key_file=Path(ssh_key),
                            )
                        except (ValueError, FileNotFoundError, OSError) as exc:
                            return _json_response(
                                self,
                                400,
                                {"error": str(exc), "account": acc.public()},
                            )
                    return _json_response(self, 200, acc.public())
                if path == "/api/clouds/update":
                    acc_id = (data.get("id") or "").strip()
                    if not acc_id:
                        return _json_response(self, 400, {"error": "id required"})
                    acc = svc.accounts.get(acc_id)
                    if not acc:
                        return _json_response(self, 404, {"error": "account_not_found"})
                    cloud = acc.cloud
                    regions = [
                        x.strip()
                        for x in str(data.get("regions") or "").split(",")
                        if x.strip()
                    ]
                    meta_patch: dict = {}
                    if regions:
                        meta_patch["regions"] = regions
                    secret_patch: dict = {}
                    if cloud == "aliyun":
                        if data.get("access_key_id"):
                            secret_patch["access_key_id"] = data.get("access_key_id")
                            meta_patch["ak_id"] = data.get("access_key_id")
                        if data.get("access_key_secret"):
                            secret_patch["access_key_secret"] = data.get(
                                "access_key_secret"
                            )
                    elif cloud == "tencent":
                        if data.get("product"):
                            meta_patch["product"] = data.get("product")
                        if data.get("secret_id"):
                            secret_patch["secret_id"] = data.get("secret_id")
                            meta_patch["tc_id"] = data.get("secret_id")
                        if data.get("secret_key"):
                            secret_patch["secret_key"] = data.get("secret_key")
                    elif cloud == "gcp":
                        if data.get("project"):
                            meta_patch["project"] = data.get("project")
                        sa_path = (data.get("sa_json") or "").strip()
                        if sa_path:
                            sa = json.loads(
                                Path(sa_path).expanduser().read_text(encoding="utf-8")
                            )
                            secret_patch["service_account"] = sa
                            if not meta_patch.get("project"):
                                meta_patch["project"] = sa.get("project_id")
                            if sa.get("client_email"):
                                meta_patch["client_email"] = sa.get("client_email")
                            if sa.get("project_id"):
                                meta_patch["project_id"] = sa.get("project_id")
                    elif cloud == "oracle":
                        if data.get("compartment_id"):
                            meta_patch["compartment_id"] = data.get("compartment_id")
                            secret_patch["compartment_id"] = data.get(
                                "compartment_id"
                            )
                        if regions:
                            secret_patch["region"] = regions[0]
                        if data.get("user_ocid"):
                            secret_patch["user_ocid"] = data.get("user_ocid")
                            meta_patch["user_ocid"] = data.get("user_ocid")
                        if data.get("tenancy_ocid"):
                            secret_patch["tenancy_ocid"] = data.get("tenancy_ocid")
                            meta_patch["tenancy_ocid"] = data.get("tenancy_ocid")
                        if data.get("fingerprint"):
                            secret_patch["fingerprint"] = data.get("fingerprint")
                            meta_patch["fingerprint"] = data.get("fingerprint")
                        key_file = (data.get("key_file") or "").strip()
                        if key_file:
                            secret_patch["private_key"] = (
                                Path(key_file).expanduser().read_text(encoding="utf-8")
                            )
                        if not svc.vault.has(acc.secret_id):
                            need = []
                            for k in ("user_ocid", "tenancy_ocid", "fingerprint"):
                                if not (
                                    secret_patch.get(k) or (acc.meta or {}).get(k)
                                ):
                                    need.append(k)
                            if not secret_patch.get("private_key"):
                                need.append("API 私钥")
                            if need:
                                return _json_response(
                                    self,
                                    400,
                                    {
                                        "error": "凭证已丢失，请重新填写 "
                                        + "、".join(need)
                                    },
                                )
                    try:
                        acc = svc.accounts.update(
                            acc_id,
                            meta=meta_patch or None,
                            secret_payload=secret_patch or None,
                        )
                    except KeyError:
                        return _json_response(self, 404, {"error": "account_not_found"})
                    except (OSError, ValueError, json.JSONDecodeError) as exc:
                        return _json_response(self, 400, {"error": str(exc)})
                    ssh_user = (data.get("ssh_user") or "").strip()
                    ssh_key = (data.get("ssh_key_file") or "").strip()
                    if ssh_user and ssh_key:
                        try:
                            acc = svc.accounts.set_instance_ssh_defaults(
                                acc.id,
                                ssh_user=ssh_user,
                                key_file=Path(ssh_key),
                            )
                        except (ValueError, FileNotFoundError, OSError) as exc:
                            return _json_response(
                                self,
                                400,
                                {"error": str(exc), "account": acc.public()},
                            )
                    elif ssh_user and not ssh_key:
                        # Update user only when key already stored
                        cur_user, cur_key = svc.accounts.instance_ssh_defaults(acc)
                        if cur_key:
                            try:
                                acc = svc.accounts.set_instance_ssh_defaults(
                                    acc.id,
                                    ssh_user=ssh_user,
                                    key_material=cur_key,
                                )
                            except (ValueError, OSError) as exc:
                                return _json_response(self, 400, {"error": str(exc)})
                        else:
                            meta_u = dict(acc.meta)
                            meta_u["ssh_user"] = ssh_user
                            acc = svc.accounts.update(acc.id, meta=meta_u)
                    return _json_response(self, 200, acc.public())
                if path == "/api/clouds/ssh-defaults":
                    acc_id = (data.get("id") or "").strip()
                    out = svc.set_cloud_ssh_defaults(
                        acc_id,
                        ssh_user=(data.get("ssh_user") or "").strip(),
                        key_file=(data.get("key_file") or data.get("ssh_key_file") or "")
                        or None,
                    )
                    code = 400 if out.get("error") else 200
                    return _json_response(self, code, out)
                if path == "/api/clouds/link-ssh":
                    name = (data.get("name") or "").strip()
                    out = svc.link_cloud_ssh(
                        name,
                        ssh_user=(data.get("ssh_user") or "").strip() or None,
                        key_file=(data.get("key_file") or data.get("ssh_key_file") or "")
                        or None,
                    )
                    code = 400 if out.get("error") else 200
                    return _json_response(self, code, out)
                if path == "/api/clouds/delete":
                    ok = svc.accounts.delete((data.get("id") or "").strip())
                    return _json_response(self, 200 if ok else 404, {"ok": ok})
                if path == "/api/tunnel/setup":
                    name = (data.get("name") or "").strip()
                    if not name:
                        return _json_response(self, 400, {"error": "name_required"})
                    reenroll = data.get("reenroll", False)
                    if isinstance(reenroll, str):
                        reenroll = reenroll.lower() not in {"0", "false", "no"}
                    out = svc.start_tunnel_job(name, reenroll=bool(reenroll))
                    return _json_response(self, 200, out)
                if path == "/api/tunnel/setup/dismiss":
                    dismissed = data.get("dismissed", True)
                    if isinstance(dismissed, str):
                        dismissed = dismissed.lower() not in {"0", "false", "no"}
                    return _json_response(
                        self,
                        200,
                        svc.dismiss_rendezvous_prompt(dismissed=bool(dismissed)),
                    )
                if path == "/api/probe":
                    name = (data.get("name") or "").strip()
                    if name:
                        from grip.monitor import probe_one

                        resource = svc.resources.get(name)
                        if not resource:
                            return _json_response(
                                self, 404, {"error": "resource_not_found"}
                            )
                        pr = probe_one(resource)
                        svc.resources.apply_probe(
                            pr.name, pr.status, pr.detail, pr.checked_at
                        )
                        svc.refresh_status()
                        return _json_response(
                            self,
                            200,
                            {
                                "checked": 1,
                                "unreachable": 0 if pr.status == "reachable" else 1,
                                "results": [
                                    {
                                        "name": pr.name,
                                        "kind": pr.kind,
                                        "status": pr.status,
                                        "detail": pr.detail,
                                        "checked_at": pr.checked_at,
                                    }
                                ],
                            },
                        )
                    return _json_response(self, 200, svc.probe_resources())
                if path == "/api/run":
                    name = (data.get("name") or "").strip()
                    command = data.get("command") or ""
                    confirm_write = bool(data.get("confirm_write"))
                    background = data.get("background", True)
                    if isinstance(background, str):
                        background = background.lower() not in {"0", "false", "no"}
                    actor = (data.get("actor") or "panel").strip() or "panel"
                    out = svc.run_command(
                        name,
                        command,
                        confirm_write=confirm_write,
                        background=bool(background),
                        timeout_sec=int(data.get("timeout_sec") or 60),
                        actor=actor,
                    )
                    code = 200
                    if out.get("error") == "resource_not_found":
                        code = 404
                    return _json_response(self, code, out)
                if path == "/api/sync-all":
                    out = svc.sync_all()
                    svc.refresh_status()
                    return _json_response(self, 200, out)
                if path == "/api/pick-file":
                    from grip.native_pick import (
                        default_ssh_key_dir,
                        pick_file_with_panel,
                    )

                    prompt = (data.get("prompt") or "选择文件").strip() or "选择文件"
                    start_dir = (data.get("start_dir") or "").strip() or None
                    if start_dir in {"~/.ssh", "ssh", "ssh_key"}:
                        start_dir = default_ssh_key_dir()
                    return _json_response(
                        self,
                        200,
                        pick_file_with_panel(prompt=prompt, start_dir=start_dir),
                    )
                if path == "/api/metrics":
                    name = (data.get("name") or "").strip() or None
                    out = svc.sample_metrics(name=name)
                    code = 404 if out.get("error") == "resource_not_found" else 200
                    return _json_response(self, code, out)
                if path == "/api/resources/ssh/update":
                    name = (data.get("name") or "").strip()
                    if not name:
                        return _json_response(self, 400, {"error": "name_required"})
                    kw: dict[str, Any] = {}
                    if data.get("host") is not None:
                        kw["host"] = str(data.get("host") or "").strip()
                    if data.get("user") is not None:
                        kw["user"] = str(data.get("user") or "").strip()
                    if data.get("port") is not None and str(data.get("port")) != "":
                        kw["port"] = int(data.get("port") or 22)
                    if data.get("os") is not None:
                        kw["os_name"] = data.get("os") or "unix"
                    pw = data.get("password")
                    if pw:
                        kw["password"] = pw
                    key_file = (data.get("key_file") or "").strip()
                    if key_file:
                        kw["key_file"] = Path(key_file)
                    try:
                        r = svc.resources.update_ssh(name, **kw)
                    except KeyError:
                        return _json_response(self, 404, {"error": "resource_not_found"})
                    svc.refresh_status()
                    return _json_response(self, 200, r.summary())
                if path == "/api/autostart":
                    from grip import autostart

                    enabled = bool(data.get("enabled"))
                    out = autostart.enable() if enabled else autostart.disable()
                    return _json_response(self, 200, out)
                if path == "/api/lang":
                    from grip.i18n import set_lang

                    picked = set_lang(str(data.get("lang") or "zh"))
                    return _json_response(self, 200, {"lang": picked})
                if path == "/api/permissions/request":
                    from grip import permissions as perms

                    out = perms.request((data.get("id") or "").strip())
                    code = 400 if out.get("error") else 200
                    return _json_response(self, code, out)
                if path == "/api/audit/export":
                    from grip.native_save import save_export_with_panel

                    name = (data.get("name") or "").strip() or None
                    fmt = (data.get("format") or "jsonl").strip() or "jsonl"
                    limit_raw = data.get("limit")
                    limit = (
                        int(limit_raw)
                        if limit_raw is not None and str(limit_raw).isdigit()
                        else None
                    )
                    out = svc.export_audit(name=name, fmt=fmt, limit=limit)
                    if out.get("error"):
                        return _json_response(self, 400, out)
                    result = save_export_with_panel(
                        body=out.get("body") or "",
                        filename=out.get("filename") or "grip-audit.jsonl",
                        media_type=out.get("media_type")
                        or "application/x-ndjson; charset=utf-8",
                        prompt="选择保存位置",
                    )
                    if result.get("error"):
                        return _json_response(self, 400, result)
                    if result.get("ok"):
                        result["count"] = out.get("count")
                        result["format"] = out.get("format")
                    return _json_response(self, 200, result)
                return _json_response(self, 404, {"error": "not_found"})
            except Exception as exc:  # noqa: BLE001
                return _json_response(self, 400, {"error": str(exc)})

    return Handler


def pick_free_port(host: str = "127.0.0.1", preferred: int = 8765) -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, preferred))
            return preferred
        except OSError:
            s.bind((host, 0))
            return int(s.getsockname()[1])


def start_ui_server(
    host: str = "127.0.0.1",
    port: int | None = None,
    *,
    svc: GripService | None = None,
) -> tuple[ThreadingHTTPServer, str, int, GripService]:
    """Start local UI server in a daemon thread. Returns (httpd, url, port, svc)."""
    import socket

    service = svc or GripService()
    bind_port = pick_free_port(host, port or 8765)
    handler = make_handler(service)
    httpd = ThreadingHTTPServer((host, bind_port), handler)
    try:
        httpd.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except OSError:
        pass
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = f"http://{host}:{bind_port}/"
    return httpd, url, bind_port, service


def run_ui(host: str = "127.0.0.1", port: int = 8765, *, open_browser: bool = False) -> None:
    """Dev/fallback: run UI server in foreground. Default does NOT open Safari."""
    import socket

    svc = GripService()
    handler = make_handler(svc)
    httpd = ThreadingHTTPServer((host, port), handler)
    try:
        httpd.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except OSError:
        pass
    url = f"http://{host}:{port}/"
    print(f"Grip UI server {url}  (Ctrl+C to stop; prefer: grip start)")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    httpd.serve_forever()
