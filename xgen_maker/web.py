"""웹 UI — CLI 대신 브라우저에서 쿼리 치면 MAKER 루프가 돈다.

의존성 0(stdlib http.server + SSE). 채팅 입력창 + 실시간 진행 로그(journal 이벤트 스트리밍) + 결과.
저널 이벤트를 SSE로 흘려 CLI의 verbose 로그를 그대로 웹에서 본다.
"""
from __future__ import annotations

import json
import os
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .config import MakerConfig, resolve_default_repo
from .kg.graph import Graph


# 이벤트가 없는 동안 연결을 살려 두는 신호. SSE 주석이라 클라이언트는 무시한다.
_SSE_KEEPALIVE = b": keepalive\n\n"


def _sync_summary(result: dict, pulled: bool) -> dict:
    """최신화 결과를 화면이 쓰는 모양으로. 붙잡아 둔 저장소는 이유까지 넘긴다."""
    return {"ok": True, "pulled": pulled,
            "changed": result.get("changed", 0), "nodes": result.get("nodes", 0),
            "updated_repos": result.get("updated_repos", []),
            "not_on_latest": result.get("not_on_latest", []),
            "per_repo": [{"repo": s.get("repo"), "changed": s.get("changed", 0),
                          "action": s.get("action")} for s in result.get("sync", [])]}


def _apply_overlay_for(kg_path: str, graph: Graph) -> None:
    """사람이 단 메모·직접 연결을 그래프에 입힌다. 기동 로드와 자동 리로드가 같은 규칙을 쓴다."""
    from pathlib import Path
    from .kg.overlay import load_overlay, apply_overlay
    overlay = load_overlay(Path(kg_path).parent / "overlay.json")
    if overlay["node_overrides"] or overlay["custom_edges"]:
        apply_overlay(graph, overlay)


_PAGE = """<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<title>XGEN MAKER</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>%E2%9A%92</text></svg>">
<style>
 /* 디자인 시스템(토큰 구조·hero wash·soft glow·muted 철학)은 CocoRoF/Geny 방식을 따르되,
    팔레트는 XGEN MAKER 고유 — "청사진 + 단조(forge)": 스틸 잉크 베이스 · azure-cyan primary ·
    타이틀/hero에만 은은한 ember(구리) 꼬리. primary(~195°)를 success/warning/danger와 멀리 둬 의미색 혼동 없음. */
 :root{--radius:10px;--radius-lg:16px;--t-fast:120ms ease;--t:200ms ease;
  --primary:#3aa8c9;--primary-hover:#2f8ea9;--primary-subtle:rgba(58,168,201,.13);
  --ember:#d99a63;
  --success:#5cc98a;--warning:#e0a95c;--danger:#e07070;
  --bg:#0e161d;--bg2:#141f28;--bg3:#1c2a35;--card:#141f28;--hover:#1c2a35;
  --text:#e7eff3;--text2:#a0b3bd;--muted:#6d818d;--border:#22323d;--border2:#2f4350;
  --grad:linear-gradient(135deg,#2b8aa8 0%,#3aa8c9 55%,#57c2cf 100%);
  --grad-hover:linear-gradient(135deg,#24788f 0%,#3195b3 55%,#49aeba 100%);
  --grad-text:linear-gradient(118deg,#3aa8c9 0%,#5cc3cf 48%,#d99a63 100%);
  --hero:radial-gradient(120% 130% at 82% -10%,rgba(58,168,201,.10) 0%,rgba(217,154,99,.03) 38%,transparent 66%);
  --glow:0 0 16px rgba(58,168,201,.15);--glow-soft:0 0 28px rgba(58,168,201,.07);
  --shadow-sm:0 1px 2px rgba(0,0,0,.5);--shadow:0 4px 16px rgba(0,0,0,.5);--shadow-lg:0 12px 32px rgba(0,0,0,.6);
  --link:#5cc3cf;
  --ok-bg:#1e4438;--ok-fg:#7fe0ac;--info-bg:#1e3a4a;--info-fg:#8fd0e6;--neutral-bg:#2b3d49;
  --warn-bg:#4a3720;--warn-fg:#e8c08a;--err-bg:#4a2626;--err-fg:#eda0a0}
 /* 라이트 — 흰색이 아니라 azure로 살짝 물들인 베이스(눈 안 아프게) */
 @media (prefers-color-scheme:light){:root{
  --primary:#2b8aa8;--primary-hover:#22738d;--primary-subtle:rgba(43,138,168,.08);
  --ember:#c07a3e;
  --success:#0f8f5f;--warning:#b5741f;--danger:#c94141;
  --bg:#eff5f7;--bg2:#f6fafb;--bg3:#e3edf1;--card:#f6fafb;--hover:#e8f1f4;
  --text:#16242c;--text2:#4d626d;--muted:#8399a4;--border:#dbe7ec;--border2:#c6d7de;
  --grad:linear-gradient(135deg,#3596b5 0%,#4fb2c6 55%,#7fd0d6 100%);
  --grad-hover:linear-gradient(135deg,#2c839f 0%,#439fb2 55%,#6dbcc3 100%);
  --grad-text:linear-gradient(118deg,#2b8aa8 0%,#3fa8bb 48%,#c07a3e 100%);
  --hero:radial-gradient(120% 130% at 82% -10%,rgba(58,168,201,.09) 0%,rgba(192,122,62,.028) 38%,transparent 66%);
  --glow:0 0 20px rgba(43,138,168,.10);--glow-soft:0 0 32px rgba(43,138,168,.05);
  --shadow-sm:0 1px 2px rgba(12,74,94,.05);--shadow:0 4px 14px rgba(12,74,94,.07);--shadow-lg:0 12px 30px rgba(12,74,94,.10);
  --link:#22738d;
  --ok-bg:#d6f5e3;--ok-fg:#0b5c3c;--info-bg:#d9edf6;--info-fg:#15556e;--neutral-bg:#e3edf1;
  --warn-bg:#fbeed3;--warn-fg:#7d4d13;--err-bg:#fbdede;--err-fg:#8f2b2b}}
 *{box-sizing:border-box} body{margin:0;font:14px/1.5 'Segoe UI',sans-serif;background:var(--bg);background-image:var(--hero);color:var(--text);height:100vh;display:flex;flex-direction:column}
 header{padding:12px 20px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:16px}
 header b{font-size:16px;font-weight:700;background:var(--grad-text);-webkit-background-clip:text;background-clip:text;color:transparent}
 header .info{color:var(--text2);font-size:12px} header .mode{margin-left:auto;font-size:12px;color:var(--muted)}
 #sync,#newsess,#pastsess{padding:6px 12px;background:var(--bg3);border:1px solid var(--border2);color:var(--text);border-radius:var(--radius);cursor:pointer;font-size:12px;transition:all var(--t-fast)}
 #sync:hover,#newsess:hover,#pastsess:hover{border-color:var(--primary);color:var(--primary)} #sync:disabled{opacity:.5} #sync.spin{color:var(--primary)}
 nav{display:flex;gap:4px;padding:0 20px;border-bottom:1px solid var(--border);background:var(--bg2)}
 nav button{padding:10px 16px;background:none;border:none;border-bottom:2px solid transparent;color:var(--text2);cursor:pointer;font-size:13px;transition:color var(--t-fast)}
 nav button:hover{color:var(--text)} nav button.on{color:var(--text);border-bottom-color:var(--primary)}
 .tab{flex:1;overflow-y:auto;padding:16px 20px;display:none} .tab.on{display:block}
 #runcols{display:flex;gap:16px;align-items:flex-start}
 #logcol{flex:1;min-width:0}
 #log{font-family:Consolas,monospace;font-size:13px;min-height:200px}
 #side{width:320px;flex:none;position:sticky;top:0;background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);padding:12px 14px}
 #side .side-h{font-size:12px;font-weight:700;color:var(--text2);letter-spacing:.3px;margin-bottom:8px}
 #gates .g{display:flex;align-items:center;gap:8px;padding:3px 0;font-size:12px;color:var(--muted)}
 #gates .g .dot{width:14px;text-align:center}
 #gates .g.done{color:var(--text)} #gates .g.done .dot{color:var(--success)}
 #gates .g.active{color:var(--primary);font-weight:600} #gates .g.active .dot{color:var(--primary);animation:pulse 1s ease-in-out infinite}
 @keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}
 #runstate{font-size:12px;font-weight:700;color:var(--primary);margin-bottom:8px;display:none;align-items:center;gap:6px}
 #runstate.on{display:flex} #runstate .spin{width:12px;height:12px;border:2px solid var(--border2);border-top-color:var(--primary);border-radius:50%;animation:spin .7s linear infinite}
 @keyframes spin{to{transform:rotate(360deg)}}
 #gates .g.fail{color:var(--danger)} #gates .g.fail .dot{color:var(--danger)}
 #gates .g.skip{color:var(--muted);opacity:.6} #gates .g.skip .dot{color:var(--muted);animation:none}
 #landing .lz{padding:6px 8px;border-radius:8px;background:var(--bg3);border:1px solid var(--border);margin-bottom:6px}
 #landing .lz .nm{font-size:12px;font-weight:600;color:var(--text)} #landing .lz .pt{font-size:11px;color:var(--muted);font-family:Consolas,monospace;word-break:break-all}
 #landing .lz .kd{font-size:10px;color:var(--primary)} #landing .lz[data-id]{cursor:pointer} #landing .lz[data-id]:hover{border-color:var(--primary)}
 .lrepos{font-size:11px;color:var(--muted);margin-bottom:7px;display:flex;flex-wrap:wrap;gap:4px;align-items:center}
 .rchip{display:inline-block;padding:2px 7px;border-radius:10px;background:rgba(224,137,74,.16);border:1px solid #e0894a;color:var(--text);cursor:pointer;font-size:11px}
 .rchip:hover{background:#e0894a;color:#fff}
 @media(max-width:820px){#runcols{flex-direction:column}#side{width:100%;position:static}}
 #gcrumb{display:flex;align-items:center;gap:6px;margin:6px 0 2px;font-size:13px}
 #gcrumb .cb{color:var(--primary);text-decoration:none;font-weight:600} #gcrumb .cb:hover{text-decoration:underline}
 #gcrumb .cbs{color:var(--muted)} #gcrumb .cbc{color:var(--text);font-weight:600}
 .setgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:10px;margin-top:8px}
 .setrow{display:flex;align-items:center;gap:10px;padding:9px 11px;border:1px solid var(--border);border-radius:9px;background:var(--bg2)}
 .setlbl{flex:1;font-size:12px;color:var(--text2)}
 .setrow input,.setrow select{padding:5px 8px;border:1px solid var(--border);border-radius:6px;background:var(--bg3);color:var(--text);font-size:12px}
 .tgl{padding:5px 12px;border:1px solid var(--border);border-radius:14px;background:var(--bg3);color:var(--muted);font-size:12px;font-weight:600;cursor:pointer}
 .tgl.on{background:var(--primary);border-color:var(--primary);color:#fff}
 .gsearch{display:flex;gap:8px;margin:8px 0 12px} .gsearch input{flex:1;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:var(--bg3);color:var(--text)}
 .gsearch button{padding:8px 16px;border:0;border-radius:8px;background:var(--primary);color:#fff;font-weight:600;cursor:pointer}
 #gwrap{position:relative;height:600px;border:1px solid var(--border);border-radius:12px;background:var(--bg2);overflow:hidden}
 #gsvg{width:100%;height:100%;display:block} #gsvg .gn:hover circle{filter:brightness(1.25)}
 #gtip{position:absolute;top:10px;left:10px;max-width:60%;padding:7px 10px;border-radius:8px;background:var(--bg3);border:1px solid var(--border);font-size:12px;pointer-events:none;display:none}
 #gedit{position:absolute;top:10px;right:10px;width:280px;padding:12px;border-radius:10px;background:var(--bg3);border:1px solid var(--primary);font-size:12px;display:none;box-shadow:0 4px 20px rgba(0,0,0,.25)}
 #gedit .geh{display:flex;align-items:center;gap:6px;margin-bottom:2px} #gedit .gex{margin-left:auto;cursor:pointer;color:var(--muted);font-weight:700}
 #gedit .gel{display:block;margin:8px 0 2px;color:var(--muted)} #gedit .gel textarea,#gedit .gel input{width:100%;margin-top:3px;padding:5px 7px;border:1px solid var(--border);border-radius:6px;background:var(--bg2);color:var(--text);font-size:12px;box-sizing:border-box;resize:vertical}
 #gedit .gelc{display:block;margin:9px 0;color:var(--text2);line-height:1.4} #gedit .gebtns{display:flex;align-items:center;gap:8px;margin-top:4px}
 #gedit .gebtns button{padding:6px 14px;border:0;border-radius:7px;background:var(--primary);color:#fff;font-weight:600;cursor:pointer}
 #gcode{display:none;margin-top:12px;border:1px solid var(--border);border-radius:10px;background:var(--bg2);overflow:hidden}
 #gcode .gch{display:flex;align-items:center;gap:8px;padding:8px 12px;background:var(--bg3);border-bottom:1px solid var(--border)} #gcode .gch .gcx{margin-left:8px;cursor:pointer;color:var(--muted);font-weight:700}
 #gcode .gcwork{margin-left:auto;padding:4px 10px;border:0;border-radius:6px;background:var(--primary);color:#fff;font-size:12px;font-weight:600;cursor:pointer}
 #gcode .gcs{padding:6px 12px;font-size:12px;color:var(--text2);border-bottom:1px solid var(--border)}
 #gcode .gcw{max-height:420px;overflow:auto} #gcode table.gct{border-collapse:collapse;width:100%;font-family:Consolas,monospace;font-size:12px}
 #gcode .gcn{text-align:right;color:var(--muted);padding:0 10px;user-select:none;white-space:nowrap;border-right:1px solid var(--border);width:1%}
 #gcode .gcl{padding:0 12px;white-space:pre;color:var(--text)}
 #gcode tr.gcf{background:var(--primary-subtle)} #gcode tr.gcf .gcn{color:var(--primary);font-weight:700}
 #glegend{display:flex;flex-wrap:wrap;gap:12px;margin-top:8px;font-size:12px;color:var(--text2)} #glegend .lg{display:inline-flex;align-items:center;gap:5px}
 #glegend .lg i{width:11px;height:11px;border-radius:50%;display:inline-block}
 #draftbox{display:none;position:fixed;right:18px;bottom:86px;width:min(560px,46vw);max-height:70vh;z-index:40;background:var(--bg2);border:1px solid var(--border);border-radius:12px;overflow:auto;box-shadow:0 8px 32px rgba(0,0,0,.28)}
 #draftbox .dfh{position:sticky;top:0;background:var(--bg2);display:flex;align-items:center;gap:8px;padding:10px 12px;border-bottom:1px solid var(--border)}
 #draftbox .dfx{margin-left:auto;cursor:pointer;color:var(--muted)} #draftbox .dfx:hover{color:var(--text)}
 #draftbox .dfcopy{padding:1px 9px;font-size:11px}
 #draftbox .dfbody{margin:0;padding:12px;white-space:pre-wrap;word-break:break-word;font-size:12px;line-height:1.6}
 /* 한국어는 어절 단위로 끊어야 읽힌다. break-word는 글자 하나씩 쪼개 세로로 늘어뜨린다. */
 td.cellwrap{white-space:normal;word-break:keep-all;overflow-wrap:anywhere;min-width:16em}
 .histlist table{table-layout:auto;width:100%}
 .brgrp{margin:10px 0 16px} .brgrp table{margin-top:4px}
 .brdel{padding:1px 8px;font-size:11px;line-height:1.7;opacity:.55;white-space:nowrap}
 .brdel:hover{opacity:1;color:var(--danger);border-color:var(--danger)}
 .modenote{font-size:12px;color:var(--muted);padding:0 6px;white-space:nowrap;cursor:help}
 .sessbar{display:flex;gap:10px;align-items:center;margin-bottom:10px}
 .sessbar input{flex:1;max-width:420px;padding:7px 11px;border:1px solid var(--border);border-radius:8px;background:var(--bg2);color:var(--text)}
 .sessdel{padding:1px 8px;font-size:11px;line-height:1.7;opacity:.5;white-space:nowrap} .sessdel:hover{opacity:1;color:var(--danger);border-color:var(--danger)}
 .histcols{display:flex;gap:16px;align-items:flex-start} .histlist{flex:1;min-width:0} #histdetail,#testdetail{width:380px;flex-shrink:0;background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:14px;position:sticky;top:12px;max-height:78vh;overflow:auto}
 .hrow,.brow{cursor:pointer} .hrow:hover,.brow:hover{background:var(--bg3)} .hrow.sel{background:var(--bg3);outline:2px solid var(--primary);outline-offset:-2px}
 .tl{display:flex;flex-direction:column;gap:2px} .tlr{display:grid;grid-template-columns:18px 96px 1fr;gap:6px;padding:3px 4px;border-bottom:1px solid var(--border);font-size:12px;align-items:baseline}
 .tlr .ti{font-weight:700;color:var(--muted)} .tlr.ok .ti{color:var(--success)} .tlr.fail .ti{color:var(--danger)} .tlr .ts{color:var(--primary);font-weight:600} .tlr .td{color:var(--text2);word-break:break-all;font-family:Consolas,monospace}
 pre.smd{background:var(--bg3);border:1px solid var(--border);border-radius:8px;padding:10px;font-size:11px;white-space:pre-wrap;word-break:break-all;max-height:220px;overflow:auto}
 button.danger{padding:7px 14px;border:1px solid var(--danger);border-radius:8px;background:transparent;color:var(--danger);font-weight:600;cursor:pointer} button.danger:hover{background:var(--danger);color:#fff}
 @media(max-width:820px){.histcols{flex-direction:column}#histdetail,#testdetail{width:100%;position:static}}
 .uiv{margin:10px 0 4px}
 /* .gsearch 안에만 두면 세션 상세의 '이어서 실행'·그래프의 '지금 동기화'가 스타일을 못 받는다 */
 button.ghost{padding:7px 14px;border-radius:8px;background:transparent;border:1px solid var(--primary);color:var(--primary);font-weight:600;cursor:pointer}
 button.ghost:hover{background:var(--primary);color:#fff}
 #uishots{display:flex;gap:12px;flex-wrap:wrap;margin-top:12px} #uishots figure{margin:0} #uishots img{max-width:320px;max-height:260px;border:1px solid var(--border);border-radius:8px;display:block} #uishots figcaption{font-size:12px;color:var(--muted);margin-top:4px}
 .gal{display:flex;gap:12px;flex-wrap:wrap;margin-top:8px} .gal figure{margin:0} .gal img{max-width:200px;max-height:150px;border:1px solid var(--border);border-radius:8px;display:block} .gal figcaption{font-size:11px;color:var(--muted);margin-top:3px}
 /* 축소된 캡처는 글씨가 안 읽힌다 — 눌러서 원본 크기로 */
 #uishots img,.gal img{cursor:zoom-in;transition:border-color var(--t-fast)} #uishots img:hover,.gal img:hover{border-color:var(--primary)}
 #lightbox{position:fixed;inset:0;background:rgba(6,12,18,.92);z-index:999;display:none;align-items:center;justify-content:center;flex-direction:column;gap:10px;padding:20px;cursor:zoom-out}
 #lightbox.on{display:flex} #lightbox img{max-width:96vw;max-height:86vh;object-fit:contain;border:1px solid var(--border);border-radius:8px;background:#fff}
 #lightbox .lbbar{color:var(--text);font-size:13px;display:flex;gap:14px;align-items:center}
 #lightbox .lbbar a{color:var(--primary);text-decoration:none} #lightbox .lbbar b{font-weight:600}
 .ev{padding:3px 0;border-bottom:1px solid var(--border);white-space:pre-wrap;word-break:break-all}
 /* 로그: 상태 글리프만 색 · 본문은 중립(가독성 + 톤 정돈) */
 .ev{color:var(--text2)} .ev .mk{display:inline-block;width:1.2em;font-weight:700;color:var(--muted)}
 .ev.ok .mk{color:var(--success)} .ev.fail{color:var(--danger)} .ev.fail .mk{color:var(--danger)}
 .ev.step{color:var(--text);font-weight:600} .ev.step .mk{color:var(--primary)} .ev.info{color:var(--muted)}
 .result{margin-top:12px;padding:12px 14px;background:var(--card);border-radius:var(--radius);border-left:3px solid var(--primary);box-shadow:var(--glow)}
 form{display:flex;gap:10px;padding:14px 20px;border-top:1px solid var(--border);background:var(--bg2)}
 input[type=text]{flex:1;padding:11px 14px;background:var(--bg3);border:1px solid var(--border2);color:var(--text);border-radius:var(--radius);font-size:14px;transition:border var(--t-fast)}
 input[type=text]:focus{outline:none;border-color:var(--primary);box-shadow:0 0 0 3px var(--primary-subtle)}
 select{padding:11px 12px;background:var(--bg3);border:1px solid var(--border2);color:var(--text);border-radius:var(--radius);cursor:pointer}
 button.act{padding:11px 20px;background:var(--grad);border:none;color:#fff;border-radius:var(--radius);cursor:pointer;font-weight:600;box-shadow:var(--glow);transition:transform var(--t-fast)}
 button.act:hover{transform:translateY(-1px);background:var(--grad-hover);box-shadow:var(--glow-soft)}
 button.act:disabled{opacity:.5;transform:none}
 table{width:100%;border-collapse:collapse;font-size:13px} th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--border)}
 th{color:var(--text2);font-weight:600} tr:hover td{background:var(--hover)}
 /* 배지는 항상 pill — 미정의 클래스(outcome 등)도 중립 배경을 갖는다 */
 /* 한글 라벨은 영문 코드보다 길다 — 줄바꿈되면 '답변 완/료'처럼 깨진다 */
 .badge{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:500;background:var(--neutral-bg);color:var(--text2);white-space:nowrap}
 th{white-space:nowrap}
 .badge.ok,.merged,.fix,.Synced,.badge.mr_prepared,.badge.committed_local,.badge.mr_created,.badge.answered{background:var(--ok-bg);color:var(--ok-fg)}
 .badge.fail,.pitfall,.badge.checks_failed,.badge.judge_failed,.badge.unauthorized,.badge.push_failed{background:var(--err-bg);color:var(--err-fg)}
 .opened,.convention,.badge.planned{background:var(--info-bg);color:var(--info-fg)}
 .closed,.note,.badge.muted{background:var(--neutral-bg);color:var(--text2)}
 .OutOfSync{background:var(--warn-bg);color:var(--warn-fg)} .Healthy{color:var(--success)}
 a{color:var(--link);text-decoration:none} a:hover{text-decoration:underline}
 h3{margin:16px 0 8px;font-size:14px;background:var(--grad-text);-webkit-background-clip:text;background-clip:text;color:transparent} .muted{color:var(--muted)}
 /* 제목 안 부제는 그라디언트 클립을 물려받아 제목과 구분이 안 된다 — 색을 되돌리고 띄운다 */
 h3 .muted,h4 .muted{-webkit-text-fill-color:var(--muted);color:var(--muted);font-weight:400;font-size:12px;margin-left:8px}
</style></head><body>
<header><b>⚒ XGEN MAKER</b>
 <span class="mode" id="mode"></span>
 <button id="newsess" title="현재 작업을 정리하고 새로 시작합니다">＋ 새 세션</button>
 <button id="pastsess" title="지난 작업을 찾아 이어서 하거나 정리합니다">🕘 세션 목록</button>
 <button id="sync" title="최신 코드로 동기화합니다">⟳ Sync</button></header>
<nav>
 <button class="on" data-t="run">실행</button>
 <button data-t="pipeline">파이프라인</button>
 <button data-t="graph">지식그래프</button>
 <button data-t="history">세션 목록</button>
 <button data-t="learn">학습</button>
 <button data-t="mrs">MR</button>
 <button data-t="branches">브랜치</button>
 <button data-t="tests">테스트</button>
 <button data-t="ui">화면 검증</button>
 <button data-t="deploy">배포 상태</button>
 <button data-t="login">로그인·점검</button>
 <button data-t="diag">진단</button>
</nav>
<div class="tab on" id="tab-run">
 <div id="runcols">
  <div id="logcol"><div id="log"><div class="ev info">작업할 내용을 입력하세요. 진행 상황이 실시간으로 표시됩니다.</div></div></div>
  <aside id="side">
   <div id="runstate"><span class="spin"></span><span id="runstate-t">실행 중…</span></div>
   <div class="side-h">진행 단계</div>
   <div id="gates"></div>
   <div class="side-h" style="margin-top:14px">🎯 관련 코드 <span class=muted id="landn"></span></div>
   <div id="landing"><div class=muted style="font-size:12px">작업을 시작하면 관련 코드를 찾아 표시합니다.</div></div>
  </aside>
 </div>
</div>
<div class="tab" id="tab-pipeline"><div class="muted">불러오고 있습니다</div></div>
<div class="tab" id="tab-graph"><div class="muted">불러오고 있습니다</div></div>
<div class="tab" id="tab-history"><div class="muted">불러오고 있습니다</div></div>
<div class="tab" id="tab-learn"><div class="muted">불러오고 있습니다</div></div>
<div class="tab" id="tab-mrs"><div class="muted">GitLab에서 병합 요청을 가져오고 있습니다</div></div>
<div class="tab" id="tab-branches"><div class="muted">GitLab에서 브랜치를 가져오고 있습니다</div></div>
<div class="tab" id="tab-tests"><div class="muted">불러오고 있습니다</div></div>
<div class="tab" id="tab-ui"><div class="muted">불러오고 있습니다</div></div>
<div class="tab" id="tab-deploy"><div class="muted">불러오고 있습니다</div></div>
<div class="tab" id="tab-login"><div class="muted">불러오고 있습니다</div></div>
<div class="tab" id="tab-diag"><div class="muted">불러오고 있습니다</div></div>
<div id="draftbox"></div>
<div id="lightbox"><div class="lbbar"><b id="lbcap"></b><a id="lbopen" href="#" target="_blank">새 탭에서 원본</a><span class="muted">아무 곳이나 누르면 닫힘 · Esc</span></div><img id="lbimg" alt=""></div>
<form id="f"><input type="text" id="q" placeholder="예: 로그인 오류를 수정해줘 / 결제 API에 입력 검증을 추가해줘" autofocus>
 <span class=modenote title="지식그래프에서 찾아 → 브랜치 → 고치고 → 검증 → 커밋 → MR 초안까지. 원격 푸시는 안 합니다.">브랜치 따서 MR 초안까지</span>
 <button class="act" id="go">실행</button><button type="button" id="stopbtn" class="danger" style="display:none">■ 중지</button></form>
<script>
const log=document.getElementById('log'), q=document.getElementById('q'), go=document.getElementById('go'), stopbtn=document.getElementById('stopbtn');
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// 속성/클래스에 넣는 값은 [a-z0-9_-]만 허용(뜻밖의 badge 클래스·주입 차단)
const cls=s=>String(s==null?'':s).replace(/[^a-zA-Z0-9_-]/g,'');
// 결과 코드는 내부 식별자다. 화면엔 사람이 읽는 말로 보여준다(코드는 상세에서 확인).
const OUTCOME={answered:'답변 완료',no_landing:'관련 코드 못 찾음',planned:'분석 완료',
 branch_failed:'브랜치 생성 실패',implement_failed:'수정 실패',checks_failed:'검증 실패',
 judge_failed:'품질 기준 미달',push_failed:'푸시 실패',unauthorized:'권한 없음',
 mr_prepared:'수정 완료(로컬)',committed_local:'수정 완료 — 내 PC에만',
 mr_created:'MR 생성됨',stopped:'중지됨'};
const outcomeLabel=s=>OUTCOME[s]||(s==null||s===''?'-':String(s));
function line(cls,txt,mark){const d=document.createElement('div');d.className='ev '+cls;
 if(mark){const s=document.createElement('span');s.className='mk';s.textContent=mark;d.appendChild(s);}
 d.appendChild(document.createTextNode(txt));log.appendChild(d);log.scrollTop=log.scrollHeight;}
function refreshInfo(){fetch('/api/info').then(r=>r.json()).then(d=>{if(!d||d.repo_names===undefined)return;window.__repos=d.repo_names||[];document.getElementById('mode').textContent=(d.nodes||0).toLocaleString()+' 노드 · '+(d.repos||0)+' 레포';}).catch(()=>{});}
refreshInfo();
// Sync 버튼 — 그래프 최신화(변경분만)
// 헤더의 세션 목록 버튼 — ＋새 세션만 있고 돌아갈 길이 없어 지난 작업이 안 보였다
document.getElementById('pastsess').onclick=()=>{
 document.querySelector('nav button[data-t=history]').click();
};
// 지난 세션 개수를 버튼에 표시해 '있다는 것' 자체가 보이게
function refreshSessCount(){
 jget('/api/history?limit=1000').then(d=>{
  const n=(d.sessions||[]).length, b=document.getElementById('pastsess');
  if(b)b.textContent='🕘 세션 목록'+(n?' '+n:'');
 }).catch(()=>{});
}
refreshSessCount();
// 새 세션 — 진행 중 실행 중단 + 로그·패널 초기화
document.getElementById('newsess').onclick=()=>{
 if(window.__es){try{window.__es.close()}catch(_){}window.__es=null;}
 document.getElementById('log').innerHTML='<div class="ev info">새 작업을 시작합니다. 작업할 내용을 입력하세요.</div>';
 document.getElementById('gates').innerHTML='';
 document.getElementById('landing').innerHTML='<div class=muted style="font-size:12px">작업을 시작하면 관련 코드를 찾아 표시합니다.</div>';
 document.getElementById('landn').textContent='';
 runstate(false); go.disabled=false; q.value=''; q.focus();
 document.querySelector('nav button[data-t=run]').click();
};
const syncBtn=document.getElementById('sync');
// 저장소를 여럿 도는 동안 아무 신호가 없으면 멈춘 것과 구분이 안 된다.
// 실행과 같은 방식으로 진행을 흘리고, 같은 방식으로 멈출 수 있게 한다.
let syncES=null, syncRun=null;
function syncIdle(label){
 syncBtn.textContent=label; syncBtn.classList.remove('spin'); syncBtn.disabled=false;
 syncBtn.onclick=startSync; syncES=null; syncRun=null;
 setTimeout(()=>{if(!syncES)syncBtn.textContent='⟳ Sync';},2500);
}
function stopSync(){
 if(!syncRun)return;
 syncBtn.textContent='중지하는 중';
 jget('/api/stop?id='+encodeURIComponent(syncRun)).catch(()=>{});
}
function startSync(){
 syncBtn.disabled=false; syncBtn.classList.add('spin'); syncBtn.textContent='■ 중지';
 syncBtn.title='동기화를 멈춥니다'; syncBtn.onclick=stopSync;
 line('info','동기화를 시작합니다','⟳');
 const es=new EventSource('/api/sync-stream?pull=1'); syncES=es;
 es.onmessage=ev=>{
  const e=JSON.parse(ev.data);
  if(e.type==='run_id'){syncRun=e.id;}
  else if(e.type==='event'){
   if(e.step==='pull'){syncBtn.textContent='■ 중지 ('+e.index+'/'+e.total+')';
    line('ok','받아오는 중 '+e.index+'/'+e.total+'  '+e.repo,'⟳');}
   else if(e.step==='sync'){syncBtn.textContent='■ 중지 (반영 중)'; line('ok',e.note,'⟳');}
  }
  else if(e.type==='result'){
   const d=e.report;
   const up=(d.updated_repos||[]);
   line(e.cancelled?'fail':'ok',
        (e.cancelled?'동기화 중지됨 · ':'동기화 완료 · ')
        +(up.length?up.length+'개 저장소 최신화('+up.join(', ')+') · ':'')
        +(d.changed>0?d.changed+'개 파일 반영':'변경 없음')
        +' · '+(d.nodes||0).toLocaleString()+'노드', e.cancelled?'■':'✓');
   (d.not_on_latest||[]).forEach(n=>line('info','그대로 둠: '+n.repo+' ('+n.branch+') — '+n.reason,'·'));
   refreshInfo(); loaded['graph']=0;
   es.close(); syncIdle(e.cancelled?'■ 중지됨':(d.changed>0?'✓ '+d.changed+'파일':'✓ 최신'));
  }
  else if(e.type==='error'){line('fail','동기화 실패: '+e.message,'✗'); es.close(); syncIdle('✗ 실패');}
 };
 es.onerror=()=>{es.close(); syncIdle('✗ 연결 끊김');};
}
syncBtn.onclick=startSync;
// 탭 전환
const loaded={};
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{
 document.querySelectorAll('nav button').forEach(x=>x.classList.remove('on'));
 document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));
 b.classList.add('on'); document.getElementById('tab-'+b.dataset.t).classList.add('on');
 if(location.hash.slice(1)!==b.dataset.t)history.replaceState(null,'','#'+b.dataset.t);  // 탭 딥링크(북마크·공유)
 if(b.dataset.t!=='run' && !loaded[b.dataset.t]){loaded[b.dataset.t]=1; render(b.dataset.t);}
});
// 캡처 확대 — 축소된 화면은 글씨가 안 읽힌다. 위임 방식이라 나중에 그려진 이미지도 동작.
const lb=document.getElementById('lightbox'),lbimg=document.getElementById('lbimg'),
      lbcap=document.getElementById('lbcap'),lbopen=document.getElementById('lbopen');
document.addEventListener('click',e=>{
 const img=e.target.closest('#uishots img, .gal img');
 if(img){lbimg.src=img.src;lbcap.textContent=img.closest('figure')?.querySelector('figcaption')?.textContent||'';
  lbopen.href=img.src;lb.classList.add('on');return;}
 if(e.target.closest('#lbopen'))return;      // 원본 링크는 닫지 않는다
 if(lb.classList.contains('on'))lb.classList.remove('on');
});
document.addEventListener('keydown',e=>{if(e.key==='Escape')lb.classList.remove('on');});
// 해시 → 탭. 최초 로드뿐 아니라 hashchange도 들어야 한다(같은 문서 내 해시 이동은
// 스크립트를 재실행하지 않으므로, 안 들으면 링크·뒤로가기로 탭이 안 바뀐다).
function openTabFromHash(){
 const h=location.hash.slice(1);
 const tb=h&&document.querySelector('nav button[data-t="'+h+'"]');
 if(tb&&!tb.classList.contains('on'))tb.click();
}
window.addEventListener('hashchange',openTabFromHash);
openTabFromHash();
function jget(url){return fetch(url).then(r=>{if(!r.ok)throw new Error('HTTP '+r.status);return r.json();});}
function tabErr(el){return e=>{el.innerHTML='<div class=muted>불러오지 못했습니다: '+esc(e&&e.message||e)+' — 설정을 확인하세요</div>';};}
const KCOLOR={repo:'#e0894a',function:'#3aa8c9',class:'#d99a63',file:'#7aa0b4',route:'#8bd5a0',endpoint:'#6fc2d8',api_call:'#c9a03a',gateway_route:'#c98bd5',feature:'#9db8c9',service:'#c98bd5',env:'#d5c98b',package:'#b0b0c0',helm_app:'#8fa8c0',helm_chart:'#8fa8c0',deploy_project:'#a0a0b8'};
// 내부 종류명을 그대로 띄우지 않는다 — 화면에 나가는 말은 사람 말로.
const KLABEL={repo:'저장소',file:'파일',class:'클래스',function:'함수',endpoint:'서버 API',api_call:'화면에서 호출',gateway_route:'API 관문 경로',route:'화면 주소',feature:'기능 그룹',service:'서비스',env:'환경',package:'패키지',helm_app:'배포 단위',helm_chart:'배포 차트',deploy_project:'배포 프로젝트',domain:'도메인'};
const klabel=k=>KLABEL[k]||k||'항목';
function drawGraph(data,svg,tip,onPick){
 const W=svg.clientWidth||900,H=svg.clientHeight||600;
 svg.setAttribute('viewBox','0 0 '+W+' '+H);
 if(!data.nodes||!data.nodes.length){svg.innerHTML='<text x="50%" y="50%" text-anchor=middle fill="#889" font-size=13>'+esc(data.reason||'결과가 없습니다')+'</text>';return;}
 // 노드가 많으면 레이아웃 공간을 넓게(밀도 유지). 노드 수에 비례.
 const N=data.nodes.length, SP=Math.max(1,Math.sqrt(N/120));
 const LW=W*SP, LH=H*SP;
 const isRepo=data.level==='repo';
 const nodes=data.nodes.map(n=>({...n,x:LW/2+(Math.random()-.5)*LW*.85,y:LH/2+(Math.random()-.5)*LH*.85,vx:0,vy:0,
  r:isRepo?(10+Math.min(22,Math.sqrt(n.count||1)/9)):((n.seed?9:4.5)+Math.min(7,(n.deg||1)*.5))}));
 const idx={};nodes.forEach((n,i)=>idx[n.id]=i);
 const links=data.edges.filter(e=>idx[e.src]!=null&&idx[e.dst]!=null).map(e=>({s:idx[e.src],t:idx[e.dst],kind:e.kind,weight:e.weight||1}));
 // 힘 시뮬레이션 — 노드 많으면 반복 줄여 렌더 지연 방지. 그리드 근사로 반발력 O(n·k).
 const iters=N>250?70:N>120?100:140, REP=2600*SP;
 const cell=90*SP;
 for(let it=0;it<iters;it++){const k=1-it/iters;
  // 공간 해싱(가까운 셀끼리만 반발) — n²회피
  const grid={};nodes.forEach((n,i)=>{const cx=Math.floor(n.x/cell),cy=Math.floor(n.y/cell);(grid[cx+','+cy]||(grid[cx+','+cy]=[])).push(i);});
  for(let i=0;i<nodes.length;i++){const a=nodes[i],cx=Math.floor(a.x/cell),cy=Math.floor(a.y/cell);
   for(let gx=cx-1;gx<=cx+1;gx++)for(let gy=cy-1;gy<=cy+1;gy++){const cellArr=grid[gx+','+gy];if(!cellArr)continue;
    for(const j of cellArr){if(j<=i)continue;const b=nodes[j];let dx=a.x-b.x,dy=a.y-b.y,d2=dx*dx+dy*dy+.01,d=Math.sqrt(d2),f=REP/d2,ux=dx/d,uy=dy/d;a.vx+=ux*f;a.vy+=uy*f;b.vx-=ux*f;b.vy-=uy*f;}}}
  links.forEach(l=>{const a=nodes[l.s],b=nodes[l.t];let dx=b.x-a.x,dy=b.y-a.y,d=Math.sqrt(dx*dx+dy*dy)+.01,f=(d-70)*.03,ux=dx/d,uy=dy/d;a.vx+=ux*f;a.vy+=uy*f;b.vx-=ux*f;b.vy-=uy*f;});
  // 중심 인력 + 레이아웃 공간 안에 고정 — 안 그러면 연결 없는 노드가 무한히 튕겨나가
  // 경계상자가 폭발하고, 자동맞춤 줌이 한없이 축소돼 화면이 텅 빈 것처럼 보인다.
  nodes.forEach(n=>{n.vx+=(LW/2-n.x)*.006;n.vy+=(LH/2-n.y)*.006;n.x+=n.vx*k;n.y+=n.vy*k;n.vx*=.86;n.vy*=.86;
   n.x=Math.max(n.r+6,Math.min(LW-n.r-6,n.x));n.y=Math.max(n.r+6,Math.min(LH-n.r-6,n.y));});}
 // 실제 좌표범위에 맞춰 뷰박스 초기화(전체가 보이게)
 let minx=1e9,miny=1e9,maxx=-1e9,maxy=-1e9;nodes.forEach(n=>{minx=Math.min(minx,n.x);miny=Math.min(miny,n.y);maxx=Math.max(maxx,n.x);maxy=Math.max(maxy,n.y);});
 const pad=40; let view={x:minx-pad,y:miny-pad,w:(maxx-minx)+2*pad,h:(maxy-miny)+2*pad};
 const applyView=()=>svg.setAttribute('viewBox',`${view.x.toFixed(1)} ${view.y.toFixed(1)} ${view.w.toFixed(1)} ${view.h.toFixed(1)}`);
 const labelMin=isRepo?0:(N>200?6:N>90?3:0);  // 노드 많으면 허브만 라벨(클릭·호버로 나머지 확인)
 function redraw(){let s='';
  links.forEach(l=>{const w=isRepo?Math.max(1,Math.min(7,Math.sqrt(l.weight||1))):1;
   s+=`<line x1="${nodes[l.s].x.toFixed(1)}" y1="${nodes[l.s].y.toFixed(1)}" x2="${nodes[l.t].x.toFixed(1)}" y2="${nodes[l.t].y.toFixed(1)}" stroke="#7f95a6" stroke-width="${(view.w/W*0.9*w).toFixed(2)}" opacity="${isRepo?0.55:0.4}" />`;});
  const fs=(view.w/W*(isRepo?13:10)).toFixed(1);
  nodes.forEach((n,i)=>{const c=KCOLOR[n.kind]||'#8894a0';const stk=n.seed?` stroke="#fff" stroke-width="${(view.w/W*2).toFixed(2)}"`:(n.deprecated?` stroke="#d99a63" stroke-width="${(view.w/W*1.5).toFixed(2)}"`:'');
   const nm=isRepo?`${esc(n.name)} (${(n.count||0).toLocaleString()})`:esc(n.name);
   const lbl=(n.seed||isRepo||(n.deg||0)>=labelMin)?`<text x="${(n.r+3).toFixed(0)}" y="4" font-size="${fs}" fill="var(--text)"${isRepo?' font-weight="600"':''}>${nm}</text>`:'';
   s+=`<g class="gn" data-i="${i}" transform="translate(${n.x.toFixed(1)},${n.y.toFixed(1)})" style="cursor:${isRepo?'zoom-in':'grab'}"><circle r="${n.r.toFixed(1)}" fill="${c}"${stk}></circle>${lbl}</g>`;});
  svg.innerHTML=s;}
 applyView();redraw();
 let drag=null,downNode=null,moved=false,pan=null;
 const pt=ev=>{const r=svg.getBoundingClientRect();return{x:view.x+(ev.clientX-r.left)/r.width*view.w,y:view.y+(ev.clientY-r.top)/r.height*view.h};};
 svg.onwheel=ev=>{ev.preventDefault();const p=pt(ev);const f=ev.deltaY<0?0.85:1.18;const nw=view.w*f,nh=view.h*f;if(nw<40||nw>LW*6)return;view.x=p.x-(p.x-view.x)*f;view.y=p.y-(p.y-view.y)*f;view.w=nw;view.h=nh;applyView();};
 svg.onmousedown=ev=>{const g=ev.target.closest('.gn');if(g){downNode=nodes[+g.dataset.i];drag=downNode;moved=false;g.style.cursor='grabbing';}else{pan={x:ev.clientX,y:ev.clientY,vx:view.x,vy:view.y};}};
 svg.onmousemove=ev=>{const g=ev.target.closest('.gn');
  if(drag){const p=pt(ev);if(Math.abs(p.x-drag.x)>2||Math.abs(p.y-drag.y)>2)moved=true;drag.x=p.x;drag.y=p.y;redraw();return;}
  if(pan){const r=svg.getBoundingClientRect();view.x=pan.vx-(ev.clientX-pan.x)/r.width*view.w;view.y=pan.vy-(ev.clientY-pan.y)/r.height*view.h;applyView();return;}
  if(g&&tip){const n=nodes[+g.dataset.i];
   tip.innerHTML=isRepo
    ?`<b>${esc(n.name)}</b><br><span class=muted>코드 ${(n.count||0).toLocaleString()}개 · 다른 저장소와 ${n.deg||0}건 연결</span><br><span style="color:var(--primary)">눌러서 내부 살펴보기</span>`
    :`<b>${esc(n.name)}</b> <span class=muted>${esc(klabel(n.kind))} · 연결 ${n.deg||0}건</span><br><span class=muted>${esc(n.repo)} · ${esc(n.path)||'-'}</span>${n.deprecated?'<br><span style="color:var(--ember)">⚠ 검색 제외</span>':''}${n.note?'<br>📝 '+esc(n.note):''}<br><span style="color:var(--primary)">눌러서 코드 보기</span>`;
   tip.style.display='block';}
  else if(tip)tip.style.display='none';};
 svg.onmouseup=ev=>{if(downNode&&!moved&&onPick)onPick(downNode);drag=null;downNode=null;pan=null;};
 svg.onmouseleave=()=>{drag=null;downNode=null;pan=null;};
}
// 노드 편집기 — 메모/착지회피(deprecated)/요약교체를 overlay에 저장(R8)
function openNodeEditor(n,ged){
 ged.style.display='block';
 ged.innerHTML=`<div class=geh><b>${esc(n.name)}</b> <span class=muted>${esc(klabel(n.kind))}</span><span class=gex>✕</span></div>`+
  `<div class=muted style="font-size:11px;word-break:break-all;margin-bottom:8px">${esc(n.repo)}:${esc(n.path)||'-'}</div>`+
  `<label class=gel>메모<textarea id=gnote rows=2 placeholder="예: 레거시 코드입니다. 신규 작업은 다른 모듈을 사용하세요">${esc(n.note||'')}</textarea></label>`+
  `<label class=gel>설명<input id=gsum value="${esc(n.summary||'')}" placeholder="직접 작성한 설명 — 검색에 사용됩니다"></label>`+
  `<label class=gelc><input type=checkbox id=gdep ${n.deprecated?'checked':''}> 검색에서 제외 — 자동으로 이 코드를 선택하지 않습니다</label>`+
  `<div class=gebtns><button id=gsave>저장</button><span id=gsaveout class=muted></span></div>`;
 ged.querySelector('.gex').onclick=()=>{ged.style.display='none';};
 document.getElementById('gsave').onclick=()=>{
  const p=new URLSearchParams({node:n.id});
  p.set('note',document.getElementById('gnote').value);
  p.set('summary',document.getElementById('gsum').value);
  p.set('deprecated',document.getElementById('gdep').checked?'1':'0');
  const out=document.getElementById('gsaveout');out.textContent='저장하고 있습니다';
  jget('/api/annotate?'+p.toString()).then(r=>{
   if(r.ok){out.innerHTML='<span style="color:var(--success)">✓ 저장했습니다</span>';
    n.note=document.getElementById('gnote').value;n.summary=document.getElementById('gsum').value;n.deprecated=document.getElementById('gdep').checked;}
   else out.innerHTML='<span style="color:var(--danger)">✗ '+esc(r.error||'실패')+'</span>';
  }).catch(e=>{out.textContent='실패: '+(e.message||e);});
 };
}
// 노드 → 실제 코드 내용(파일:라인 → def/class 본문). 클릭하면 그래프 아래에 코드가 뜬다.
function loadNodeCode(n,gcode){
 gcode.style.display='block';
 gcode.innerHTML='<div class=muted style="font-size:12px">코드를 불러오고 있습니다 '+esc(n.name)+'</div>';
 jget('/api/node-code?id='+encodeURIComponent(n.id)).then(r=>{
  if(!r.ok){gcode.innerHTML='<div class=gch><b>'+esc(n.name)+'</b> <span class=muted>'+esc(klabel(n.kind))+'</span></div><div class=muted style="font-size:12px">코드를 표시할 수 없습니다: '+esc(r.error)+'</div>';return;}
  const meta=(r.summary?`<div class=gcs>🧠 ${esc(r.summary)} ${r.summary_src==='human'?'<span class=muted>(사람 작성)</span>':''}</div>`:'')+(r.note?`<div class=gcs>📝 ${esc(r.note)}</div>`:'');
  // 줄번호 포함 코드
  const rows=r.code.split('\\n').map((ln,i)=>{const no=r.first_line+i,hit=r.focus_line&&no===r.focus_line;
   return `<tr${hit?' class=gcf':''}><td class=gcn>${no}</td><td class=gcl>${esc(ln)||' '}</td></tr>`;}).join('');
  const cut=r.truncated?` <span style="color:var(--warning)">· 너무 커서 ${r.shown_lines}줄까지만 표시</span>`:'';
  gcode.innerHTML=`<div class=gch><b>${esc(r.name)}</b> <span class=muted>${esc(klabel(r.kind))} · ${esc(r.repo)}:${esc(r.path)}:${r.focus_line||r.first_line} · 총 ${r.total_lines}줄${cut}</span><button class=gcwork data-nm="${esc(r.name)}" data-pt="${esc(r.repo)}:${esc(r.path)}">▶ 이 코드로 작업</button><span class=gcx>✕</span></div>${meta}<div class=gcw><table class=gct>${rows}</table></div>`;
  // 파일 전체를 싣되, 착지한 줄이 보이도록 그 자리로 옮겨 준다(맨 위에 두면 못 찾는다)
  const focus=gcode.querySelector('tr.gcf');
  if(focus)focus.scrollIntoView({block:'center'});
  gcode.querySelector('.gcx').onclick=()=>{gcode.style.display='none';};
  gcode.querySelector('.gcwork').onclick=(ev)=>{const b=ev.currentTarget;
   document.querySelector('nav button[data-t=run]').click();
   const qq=document.getElementById('q');qq.value=b.dataset.nm+' ('+b.dataset.pt+') 관련 수정: ';qq.focus();
   line('step','작업 대상: '+b.dataset.nm+' — 변경할 내용을 이어서 입력하세요','▶');};
 }).catch(e=>{gcode.innerHTML='<div class=muted>코드 불러오지 못했습니다: '+esc(e.message||e)+'</div>';});
}
function render(t){
 const el=document.getElementById('tab-'+t);
 if(t==='pipeline') jget('/api/pipeline').then(d=>{
  const SI={ok:'✓',pass:'✓',start:'◐',fail:'✗',skipped:'—',empty:'—',observe:'◇',act:'◆',blocked:'✗',warn:'!'};
  let h='<h3>파이프라인 <span class=muted>작업이 거치는 단계와 현재 설정입니다</span></h3>';
  h+='<div class=muted style="margin-bottom:10px">'+(d.last_query?('최근 작업: <b style="color:var(--text)">'+esc(d.last_query)+'</b> 기준으로 각 단계의 결과를 표시합니다'):'아직 코드를 변경한 작업이 없습니다. 현재 설정만 표시합니다.')+'</div>';
  h+='<table><tr><th>단계</th><th>설명</th><th>최근 결과</th><th>관련 설정</th></tr>'+
   d.stages.map(s=>{
    const off=(s.gate!=null&&(s.gate_value===false||s.gate_value===''||s.gate_value==null));
    const mark=s.last?`<span class="badge ${cls(s.last)}">${SI[s.last]||''} ${esc(s.last)}</span>`:'<span class=muted>-</span>';
    return `<tr${off?' style="opacity:.62"':''}><td><b>${esc(s.label)}</b><br><span class=muted style="font-size:11px">${esc(s.step)}</span></td>`+
     `<td class=muted style="font-size:12px">${esc(s.desc)}</td><td>${mark}</td>`+
     `<td class=muted style="font-size:11px">${s.gate?esc(s.gate)+' = <b>'+esc(String(s.gate_value))+'</b>':'-'}</td></tr>`;
   }).join('')+'</table>';
  h+='<h4 style="margin-top:18px">설정 <span class=muted>변경 사항은 현재 세션에만 적용됩니다</span></h4><div id=setgrid class=setgrid></div>';
  el.innerHTML=h;
  const sg=document.getElementById('setgrid');
  const render_settings=(cur)=>{
   sg.innerHTML=Object.keys(d.settable).map(k=>{
    const meta=d.settable[k], v=cur[k], t=meta.type;
    let ctrl;
    if(t==='bool') ctrl=`<button class="tgl ${v?'on':''}" data-k="${k}" data-v="${v?'0':'1'}">${v?'켜짐':'꺼짐'}</button>`;
    else if(t.startsWith('choice:')) ctrl=`<select data-k="${k}">`+t.split(':')[1].split(',').map(o=>`<option${o===v?' selected':''}>${o}</option>`).join('')+'</select>';
    else ctrl=`<input data-k="${k}" value="${esc(String(v==null?'':v))}" size=14>`;
    return `<div class=setrow><span class=setlbl>${esc(meta.label)}<br><span class=muted style="font-size:10px">${k}</span></span>${ctrl}</div>`;
   }).join('')+'<div id=setout class=muted style="font-size:12px;grid-column:1/-1"></div>';
   const save=(k,val)=>{const o=document.getElementById('setout');o.textContent='적용하고 있습니다';
    jget('/api/setting?key='+encodeURIComponent(k)+'&value='+encodeURIComponent(val)).then(r=>{
     if(r.ok){cur[k]=r.value;o.innerHTML='<span style="color:var(--success)">✓ '+esc(k)+' = '+esc(String(r.value))+'</span> — '+esc(r.note);render_settings(cur);loaded['pipeline']=0;}
     else o.innerHTML='<span style="color:var(--danger)">✗ '+esc(r.error)+'</span>';
    }).catch(e=>{o.textContent='실패: '+(e.message||e);});};
   sg.querySelectorAll('.tgl').forEach(b=>b.onclick=()=>save(b.dataset.k,b.dataset.v));
   sg.querySelectorAll('select[data-k]').forEach(s=>s.onchange=()=>save(s.dataset.k,s.value));
   sg.querySelectorAll('input[data-k]').forEach(i=>i.onchange=()=>save(i.dataset.k,i.value));
  };
  render_settings(Object.assign({},d.settings));
  }).catch(tabErr(el));
 if(t==='graph') jget('/api/graph').then(d=>{
  let h='<h3>코드 지식그래프 <span class=muted>저장소 → 내부 구조 → 코드 순으로 살펴봅니다</span></h3>';
  h+='<div id=ghealth class=muted style="font-size:12px;margin-bottom:6px">상태를 확인하고 있습니다</div>';
  h+='<div id=gcrumb></div>';
  h+='<div class=gsearch><input id=gq placeholder="코드 검색 (예: config, router, 로그인 처리)" autocomplete=off><select id=gn title="표시 노드 수"><option value=80>80</option><option value=160 selected>160</option><option value=320>320</option></select><button id=gbtn>검색</button></div>';
  h+='<div id=gwrap><svg id=gsvg></svg><div id=gtip></div><div id=gedit></div></div>';
  h+='<div id=glegend>'+Object.keys(KLABEL).filter(k=>KCOLOR[k]).map(k=>`<span class=lg><i style="background:${KCOLOR[k]}"></i>${KLABEL[k]}</span>`).join('')+'<span class=muted style="margin-left:8px">저장소를 누르면 내부로 이동합니다 · 코드를 누르면 내용을 봅니다 · 휠로 확대·축소, 빈 곳을 끌어 이동</span></div>';
  h+='<div id=gcode></div>';
  h+=`<div class=muted style="margin:16px 0 12px">총 <b style="color:var(--text)">${(d.nodes||0).toLocaleString()}</b> 노드 · <b style="color:var(--text)">${(d.edges||0).toLocaleString()}</b> 엣지 · <code>${esc(d.kg_path)}</code></div>`;
  h+='<h4>저장소별 현황 <button id=gsync class="ghost" style="font-size:12px;padding:4px 10px;margin-left:6px">⟳ 지금 동기화</button></h4>'+
    '<div id=gsyncout class=muted style="font-size:12px;margin-bottom:6px"></div>'+
    '<table><tr><th>레포</th><th>노드</th><th>HEAD</th></tr>'+
    (d.repos||[]).map(r=>`<tr><td>${esc(r.repo)}</td><td>${(r.nodes||0).toLocaleString()}</td><td class=muted style="font-family:Consolas,monospace">${esc(r.head)||'-'}</td></tr>`).join('')+'</table>';
  h+='<h4>코드 유형별 분포</h4><table><tr><th>종류</th><th>개수</th></tr>'+
    (d.by_kind||[]).map(k=>`<tr><td>${esc(k[0])}</td><td>${(k[1]||0).toLocaleString()}</td></tr>`).join('')+'</table>';
  el.innerHTML=h;
  document.getElementById('gsync').onclick=()=>{const o=document.getElementById('gsyncout');
   o.innerHTML='<span class=spin style="display:inline-block;width:13px;height:13px;vertical-align:middle"></span> 최신 코드로 동기화하고 있습니다';
   jget('/api/sync').then(s=>{
    if(!s.ok){o.innerHTML='<span style="color:var(--danger)">✗ '+esc(s.error)+'</span>';return;}
    const pr=(s.per_repo||[]).map(r=>`${esc(r.repo||'?')}: ${r.action==='full_rebuild_needed'?'<span style="color:var(--ember)">다시 만들어야 합니다</span>':(r.changed||0)+'개'}`).join(' · ');
    o.innerHTML=`<span style="color:var(--success)">✓ 동기화 완료</span> — 총 ${s.changed}개 파일 반영 · ${s.nodes.toLocaleString()}노드<br>${pr}`;
    loaded['graph']=0;render('graph');
   }).catch(e=>{o.innerHTML='실패: '+esc(e.message||e);});};
  const svg=document.getElementById('gsvg'),tip=document.getElementById('gtip'),gq=document.getElementById('gq'),ged=document.getElementById('gedit');
  const gcode=document.getElementById('gcode');
  // 레포 노드면 그 레포 내부로 파고들고, 코드 노드면 코드+메모를 연다
  const onPick=n=>{if(n.kind==='repo'){window.__showRepo(n.id);return;}openNodeEditor(n,ged);loadNodeCode(n,gcode);};
  const draw=sd=>drawGraph(sd,svg,tip,onPick);
  const gn=document.getElementById('gn'),crumb=document.getElementById('gcrumb');
  const busy=(t)=>{svg.innerHTML='<text x="50%" y="50%" text-anchor=middle fill="#889" font-size=13>'+t+'</text>';};
  const setCrumb=(parts)=>{crumb.innerHTML=parts.map((p,i)=>i<parts.length-1
    ?`<a href="#" class=cb data-lv="${esc(p.lv||'')}">${esc(p.t)}</a><span class=cbs>›</span>`
    :`<span class=cbc>${esc(p.t)}</span>`).join('');
   crumb.querySelectorAll('.cb').forEach(a=>a.onclick=(ev)=>{ev.preventDefault();showRepos();});};
  // 1단계 — 레포 간 그래프(즉시)
  const showRepos=()=>{ged.style.display='none';gcode.style.display='none';busy('저장소 관계를 불러오고 있습니다');
   setCrumb([{t:'전체 저장소'}]);
   jget('/api/repo-graph').then(draw).catch(e=>draw({reason:'불러오지 못했습니다: '+(e.message||e)}));};
  // 2단계 — 한 레포 내부
  const showRepo=(repo)=>{ged.style.display='none';gcode.style.display='none';busy(repo+' 내부를 불러오고 있습니다');
   setCrumb([{t:'전체 저장소',lv:'repo'},{t:repo}]);
   jget('/api/subgraph?repo='+encodeURIComponent(repo)+'&n='+gn.value).then(draw).catch(e=>draw({reason:'불러오지 못했습니다: '+(e.message||e)}));};
  window.__showRepo=showRepo;
  // 검색 — 레포 무관 코드 검색
  const search=()=>{const q=gq.value.trim();if(!q){showRepos();return;}
   ged.style.display='none';busy('검색하고 있습니다');setCrumb([{t:'전체 저장소',lv:'repo'},{t:'검색: '+q}]);
   jget('/api/subgraph?n='+gn.value+'&q='+encodeURIComponent(q)).then(draw).catch(e=>draw({reason:'불러오지 못했습니다: '+(e.message||e)}));};
  document.getElementById('gbtn').onclick=search;gq.onkeydown=e=>{if(e.key==='Enter')search();};
  gn.onchange=()=>{const c=crumb.querySelector('.cbc')?.textContent||'';
   if(c.startsWith('검색: '))search(); else if(c&&c!=='전체 저장소')showRepo(c);};
  showRepos();  // 탭 열면 레포 간 그래프부터
  // 건강도 — '항시 최신인가 / 제대로 구축됐나'를 주장 대신 숫자로
  jget('/api/graph-health').then(hh=>{
   const gh=document.getElementById('ghealth');if(!gh)return;
   const stale=hh.stale_repos, acc=hh.accuracy||{};
   const badge=(ok,txt)=>`<span class="badge ${ok?'ok':'fail'}">${txt}</span>`;
   let s=badge(stale===0,stale===0?'최신':stale+'개 저장소 업데이트 필요')+' ';
   s+=badge(hh.dangling===0,'끊긴 연결 '+hh.dangling)+' ';
   if(acc.pct!=null)s+=badge(acc.pct>=90,'코드 위치 정확도 '+acc.pct+'%')+' ';
   s+=`<span class=muted>표본 ${acc.checked||0}개 · 미연결 ${hh.orphans}개 · ${(hh.nodes||0).toLocaleString()}노드/${(hh.edges||0).toLocaleString()}엣지</span>`;
   if(stale)s+=`<br><span class=muted>업데이트가 필요한 저장소: ${hh.freshness.filter(f=>f.stale).map(f=>esc(f.repo)).join(', ')} — 동기화하면 최신 상태가 됩니다</span>`;
   gh.innerHTML=s;
  }).catch(()=>{const gh=document.getElementById('ghealth');if(gh)gh.textContent='';});
  }).catch(tabErr(el));
 if(t==='history') renderSessions(el,'');
 if(t==='learn') jget('/api/learnings').then(d=>{
  let h='<h3>학습 기록 <span class=muted>이전 작업에서 얻은 내용을 다음 작업에 반영합니다</span></h3>';
  if(!d.learnings.length){h+='<div class=muted>아직 기록이 없습니다. 작업이 쌓이면 자동으로 정리됩니다.</div>';}
  else h+='<table><tr><th>종류</th><th>레포</th><th>영역</th><th>교훈</th></tr>'+
   d.learnings.map(e=>`<tr><td><span class="badge ${cls(e.kind)}">${esc(e.kind)}</span></td><td class=muted>${esc(e.repo)}</td><td class=muted>${esc(e.area)}</td><td>${esc(e.note)}</td></tr>`).join('')+'</table>';
  el.innerHTML=h;}).catch(tabErr(el));
 if(t==='mrs') jget('/api/mrs').then(d=>{
  const row=m=>`<tr><td class=muted style="white-space:nowrap">${esc(m.updated||'')}</td><td>!${esc(m.iid)}</td><td><span class="badge ${cls(m.state)}">${esc(m.state)}</span></td><td class=muted style="font-size:12px">${esc(m.project||'')}</td><td>${esc(m.source)}→${esc(m.target)}</td><td>${esc(m.title)}</td><td><a href="${esc(m.url)}" target=_blank>열기</a></td></tr>`;
  const trow=m=>`<tr><td class=muted style="white-space:nowrap">${esc(m.updated||'')}</td><td>!${esc(m.iid)}</td><td><span class="badge ${cls(m.state)}">${esc(m.state)}</span></td><td>${esc(m.author||'')}</td><td class=muted style="font-size:12px">${esc(m.project||'')}</td><td>${esc(m.source)}→${esc(m.target)}</td><td>${esc(m.title)}</td><td><a href="${esc(m.url)}" target=_blank>열기</a></td></tr>`;
  const head='<tr><th>날짜</th><th>#</th><th>상태</th><th>프로젝트</th><th>브랜치</th><th>제목</th><th></th></tr>';
  const thead='<tr><th>날짜</th><th>#</th><th>상태</th><th>작성자</th><th>프로젝트</th><th>브랜치</th><th>제목</th><th></th></tr>';
  el.innerHTML='<h3>MAKER가 만든 MR <span class=muted>작업 기록과 일치하는 항목입니다</span></h3><table>'+head+(d.maker.map(row).join('')||'<tr><td colspan=7 class=muted>아직 없습니다. 푸시 + MR 생성 모드로 실행하면 여기에 표시됩니다.</td></tr>')+'</table>'+
   '<h3>내 MR <span class=muted>최근 등록한 순서입니다</span></h3><table>'+head+(d.mine.map(row).join('')||'<tr><td colspan=7 class=muted>표시할 항목이 없습니다. 연결 설정을 확인하세요.</td></tr>')+'</table>'+
   '<h3>팀 전체 MR <span class=muted>팀원이 등록한 MR을 함께 표시합니다</span></h3><table>'+thead+((d.team||[]).map(trow).join('')||'<tr><td colspan=8 class=muted>표시할 항목이 없습니다. 접근 권한을 확인하세요.</td></tr>')+'</table>';}).catch(tabErr(el));
 if(t==='branches'){
  // window.__repos가 /api/info 미도착으로 아직 없을 수 있음 → 딱 한 번만 채우고 재렌더.
  // (빈 배열은 '레포 없음'의 정상 상태이므로 무한 refetch하지 않는다)
  if(window.__repos===undefined && !window.__reposTried){
   window.__reposTried=1;
   jget('/api/info').then(d=>{window.__repos=d.repo_names||[];loaded['branches']=0;render('branches');}).catch(tabErr(el));
   return;
  }
  const names=(window.__repos||[]);
  const sel='<label>레포 <select id="brepo">'+(names.length?names.map(n=>`<option>${esc(n)}</option>`).join(''):'<option value="">(연결된 저장소 없음)</option>')+'</select></label>';
  el.innerHTML='<h3>브랜치 · 릴리즈 <span class=muted>작업 브랜치와 배포 경로를 확인합니다</span></h3>'+sel+'<div id="bbody" class=muted style="margin-top:12px">불러오고 있습니다</div>'+
   '<h4 style="margin-top:18px">내 PC의 브랜치 <span class=muted>여기서 정리합니다 — 원격은 건드리지 않습니다</span></h4>'+
   '<div id="localbr" class=muted style="font-size:12px">불러오고 있습니다</div>'+
   '<h4 style="margin-top:16px">활동 검색 <span class=muted>커밋 메시지나 작성자로 찾습니다</span></h4>'+
   '<div class=gsearch><input id=actq placeholder="예: governance, 로그인, 작성자 이름" autocomplete=off><button id=actbtn>검색</button></div><div id="actbody" class=muted style="font-size:12px">검색어를 입력하거나, 비워 두고 검색하면 최근 커밋을 표시합니다.</div>';
  renderLocalBranches();
  const load=()=>{const repo=document.getElementById('brepo').value; const bb=document.getElementById('bbody');
   if(!repo){bb.innerHTML='<div class=muted>저장소 연결 설정이 필요합니다.</div>';return;}
   bb.textContent='불러오고 있습니다';
   Promise.all([fetch('/api/branches?repo='+encodeURIComponent(repo)).then(r=>r.json()),
                fetch('/api/release?repo='+encodeURIComponent(repo)).then(r=>r.json())]).then(([b,rel])=>{
    if(b.error){bb.innerHTML='<div class=muted>브랜치를 불러오지 못했습니다: '+esc(b.error)+' — 연결 설정을 확인하세요</div>';return;}
    const brow=x=>`<tr class=brow data-b="${esc(x.name)}" title="눌러서 이 브랜치의 활동 보기"><td>${esc(x.name)}</td><td class=muted>${esc(x.author||'')}</td><td class=muted>${esc((x.when||'').slice(0,10))}</td></tr>`;
    let h='<h4>작업 브랜치 <span class=muted style="font-weight:400">항목을 누르면 활동을 검색합니다</span></h4><table><tr><th>브랜치</th><th>작성자</th><th>날짜</th></tr>'+((b.work_recent||[]).map(brow).join('')||'<tr><td colspan=3 class=muted>없음</td></tr>')+'</table>';
    h+='<h4>보호 브랜치</h4><div class=muted>'+((b.protected||[]).map(esc).join(', ')||'-')+'</div>';
    if(rel && rel.lands_on_env){h+='<h4 style="margin-top:14px">배포 경로</h4><div>배포 환경: <b>'+esc(rel.lands_on_env)+'</b> · 남은 단계: '+esc((rel.promotion_remaining||[]).join(' → ')||'없음')+'</div>';}
    bb.innerHTML=h;
    bb.querySelectorAll('.brow').forEach(row=>row.onclick=()=>{document.getElementById('actq').value=row.dataset.b;searchAct();document.getElementById('actbody').scrollIntoView({block:'center'});});
   });};
  const searchAct=()=>{const repo=document.getElementById('brepo').value;const q=document.getElementById('actq').value.trim();const ab=document.getElementById('actbody');
   ab.textContent='검색하고 있습니다';
   fetch('/api/activity?repo='+encodeURIComponent(repo)+'&q='+encodeURIComponent(q)).then(r=>r.json()).then(a=>{
    if(a.error){ab.innerHTML='<div class=muted>불러오지 못했습니다: '+esc(a.error)+'</div>';return;}
    if(!a.commits.length){ab.innerHTML='<div class=muted>결과가 없습니다'+(q?' ("'+esc(q)+'")':'')+'</div>';return;}
    ab.innerHTML='<table><tr><th>날짜</th><th>작성자</th><th>커밋</th><th>메시지</th></tr>'+
     a.commits.map(c=>`<tr><td class=muted style="white-space:nowrap;font-size:11px">${esc(c.when)}</td><td class=muted>${esc(c.author)}</td><td class=muted style="font-family:Consolas,monospace">${c.url?`<a href="${esc(c.url)}" target=_blank>${esc(c.sha)}</a>`:esc(c.sha)}</td><td>${esc(c.title)}</td></tr>`).join('')+'</table>';
   }).catch(e=>{ab.innerHTML='실패: '+esc(e.message||e);});};
  document.getElementById('brepo').onchange=()=>{load();document.getElementById('actbody').textContent='검색어를 입력하거나, 비워 두고 검색하면 최근 커밋을 표시합니다.';};
  document.getElementById('actbtn').onclick=searchAct;
  document.getElementById('actq').onkeydown=e=>{if(e.key==='Enter')searchAct();};
  load();
  return;
 }
 if(t==='tests') jget('/api/tests').then(d=>{
  let h='<h3>테스트 기록 <span class=muted>코드 변경마다 실행된 검증 결과입니다</span></h3>';
  h+='<div class=muted style="margin-bottom:10px">진행 중인 검증은 <b>실행</b> 탭에서 확인할 수 있습니다. 항목을 선택하면 상세 내용을 표시합니다.</div>';
  if(!d.runs.length){h+='<div class=muted>아직 검증 기록이 없습니다. 코드를 변경하는 작업을 실행하면 결과가 기록됩니다.</div>';el.innerHTML=h;return;}
  h+='<div class="histcols"><div class="histlist"><table><tr><th>쿼리</th><th>반복</th><th>sandbox</th><th>checks</th><th>judge</th></tr>'+
   d.runs.map(r=>`<tr class="hrow" data-sid="${esc(r.session)}"><td class=cellwrap title="${esc(r.query)}">${esc(r.query)}</td><td>${r.iterations}</td><td><span class="badge ${cls(r.sandbox)}">${esc(r.sandbox)}</span></td><td><span class="badge ${cls(r.checks_status)}">${esc(r.checks_status)}</span></td><td><span class="badge ${cls(r.judge)}">${esc(r.judge)}</span>${r.judge_source?` <span class=muted style="font-size:11px">${r.judge_source==='heuristic'?'기본 평가':'AI 평가'}${r.judge_score!=null?' '+r.judge_score:''}</span>`:''}</td></tr>`).join('')+'</table></div>'+
   '<aside id="testdetail"><div class=muted>왼쪽에서 항목을 선택하면 상세 내용을 표시합니다.</div></aside></div>';
  el.innerHTML=h;
  const td=document.getElementById('testdetail');
  el.querySelectorAll('.hrow').forEach(row=>row.onclick=()=>{el.querySelectorAll('.hrow').forEach(x=>x.classList.remove('sel'));row.classList.add('sel');showSession(row.dataset.sid,td);});
  }).catch(tabErr(el));
 if(t==='ui') jget('/api/ui-status').then(d=>{
  const yn=v=>v?'<span class="badge ok">가능</span>':'<span class="badge fail">불가</span>';
  let h='<h3>화면 검증 <span class=muted>변경 전후 화면을 비교합니다</span></h3>';
  h+='<table><tr><th>프리뷰 주소</th><td>'+(d.preview_base?esc(d.preview_base)+' '+(d.reachable?'<span class="badge ok">도달</span>':'<span class="badge fail">미도달</span>'):'<span class=muted>설정되지 않음 — 아래에서 주소를 직접 입력해 확인할 수 있습니다</span>')+'</td></tr>'+
   '<tr><th>화면 비교</th><td>'+yn(d.pillow)+'</td></tr><tr><th>화면 캡처</th><td>'+yn(d.playwright)+'</td></tr></table>';
  h+='<div class="uiv"><div class=gsearch><input id=uiurl placeholder="확인할 화면 주소 (예: http://localhost:3100/)" autocomplete=off>'+
   '<button id=uisnap>캡처 후 비교</button><button id=uibase class="ghost">기준으로 저장</button></div>'+
   '<div id=uiout class=muted style="font-size:12px">URL을 넣고 <b>캡처 후 비교</b>: 기준 화면과의 차이를 표시합니다. 기준이 없다면 <b>기준으로 저장</b>으로 먼저 등록하세요.</div>'+
   '<div id=uishots></div></div>';
  h+='<h4 style="margin-top:18px">캡처한 화면 <span class=muted>기준·캡처·차이를 모두 모았습니다</span></h4>'+
   '<div class=sessbar><input id=shotq placeholder="화면 주소·작업·종류로 찾기 (예: ontology, diff)"><span id=shotn class=muted></span></div>'+
   '<div id=shotgal class=muted style="font-size:12px">불러오고 있습니다</div>';
  el.innerHTML=h;
  renderShots('');
  const sq=document.getElementById('shotq');
  if(sq)sq.onkeydown=e=>{if(e.key==='Enter')renderShots(sq.value.trim());};
  const uiurl=document.getElementById('uiurl'),uiout=document.getElementById('uiout'),uishots=document.getElementById('uishots');
  const run=(save)=>{const u=uiurl.value.trim();if(!u){uiout.textContent='주소를 입력하세요.';return;}
   uiout.innerHTML='<span class=spin style="display:inline-block;width:14px;height:14px;vertical-align:middle"></span> 화면을 캡처하고 있습니다';uishots.innerHTML='';
   jget('/api/ui-snap?url='+encodeURIComponent(u)+(save?'&baseline=1':'')).then(r=>{
    if(!r.ok){uiout.innerHTML='<span style="color:var(--danger)">✗ '+esc(r.error)+'</span>';return;}
    if(r.baseline_saved){uiout.innerHTML='<span style="color:var(--success)">✓ 기준으로 저장했습니다</span> — 이후 이 화면과 비교합니다.';uishots.innerHTML=`<figure><img src="${esc(r.snap_url)}?t=${Date.now()}"><figcaption>새 기준</figcaption></figure>`;loaded['ui']=0;return;}
    let msg='<span style="color:var(--success)">✓ 캡처 완료</span>';
    if(r.diff){if(r.diff.diff_url){msg+=` — 기준 대비 <b>${(r.diff.changed_ratio*100).toFixed(2)}%</b> 변경`;}else{msg+=` — 기준과 <b>${r.diff.status==='identical'?'동일':'변경 '+((r.diff.changed_ratio||0)*100).toFixed(2)+'%'}</b>`;}}
    else{msg+=' — 기준 화면이 없습니다. ‘기준으로 저장’을 눌러 등록하세요.';}
    uiout.innerHTML=msg;
    let sh='<figure><img src="'+esc(r.snap_url)+'?t='+Date.now()+'"><figcaption>현재 화면</figcaption></figure>';
    if(r.baseline_url)sh+='<figure><img src="'+esc(r.baseline_url)+'"><figcaption>기준</figcaption></figure>';
    if(r.diff&&r.diff.diff_url)sh+='<figure><img src="'+esc(r.diff.diff_url)+'?t='+Date.now()+'"><figcaption>차이</figcaption></figure>';
    uishots.innerHTML=sh;
   }).catch(e=>{uiout.innerHTML='실패: '+esc(e.message||e);});};
  document.getElementById('uisnap').onclick=()=>run(false);
  document.getElementById('uibase').onclick=()=>run(true);
  }).catch(tabErr(el));
 if(t==='login') jget('/api/auth').then(d=>{
  let h='<h3>연결 상태 <span class=muted>로그인은 한 번만 하면 유지됩니다</span></h3>';
  h+='<h4>저장된 연결</h4><table>'+
   `<tr><th>AI 제공자</th><td>${esc(d.provider)} <span class=muted>(${esc(d.model)})</span></td></tr>`+
   `<tr><th>API 키</th><td>${d.api_key_set?'<span class="badge ok">설정됨</span>':'<span class=muted>구독 로그인 — 키가 필요 없습니다</span>'}</td></tr>`+
   `<tr><th>GitLab</th><td>${esc(d.gitlab_host)}${d.gitlab_user?' · '+esc(d.gitlab_user):''} ${d.gitlab_token_set?'<span class="badge ok">연결됨</span>':'<span class="badge fail">연결 안 됨</span>'}</td></tr>`+
   `<tr><th>저장 위치</th><td class=muted style="font-family:Consolas,monospace">${esc(d.auth_file)} ${d.auth_file_exists?'':'<span class=muted>(기본값 사용)</span>'}</td></tr></table>`;
  h+='<div class=gsearch style="margin-top:12px"><button id=authchk>연결 확인</button><button id=docbtn class="ghost">전체 점검 실행</button></div>';
  h+='<div id=authout style="margin-top:10px"></div><pre id=docout class="smd" style="display:none;margin-top:10px"></pre>';
  el.innerHTML=h;
  document.getElementById('authchk').onclick=()=>{const o=document.getElementById('authout');
   o.innerHTML='<span class=spin style="display:inline-block;width:14px;height:14px;vertical-align:middle"></span> 연결을 확인하고 있습니다';
   jget('/api/auth-check').then(r=>{
    const cl=r.claude,gl=r.gitlab;
    o.innerHTML='<table><tr><th>Claude</th><td>'+(cl.authenticated?'<span class="badge ok">✓ 연결됨</span>':'<span class="badge fail">✗ 인증 안 됨</span> <span class=muted>'+esc(cl.reason)+'</span>')+'</td></tr>'+
     '<tr><th>GitLab</th><td>'+(gl.ok?'<span class="badge ok">✓ '+esc(gl.user)+'</span>':'<span class="badge fail">✗</span> <span class=muted>'+esc(gl.reason)+'</span>')+'</td></tr></table>';
   }).catch(e=>{o.innerHTML='실패: '+esc(e.message||e);});};
  document.getElementById('docbtn').onclick=()=>{const p=document.getElementById('docout');
   p.style.display='block';p.textContent='전체 점검을 실행하고 있습니다. 1~2분 정도 걸립니다.';
   jget('/api/doctor').then(r=>{p.textContent=r.output||'결과가 없습니다';}).catch(e=>{p.textContent='실패: '+(e.message||e);});};
  }).catch(tabErr(el));
 if(t==='diag') jget('/api/diagnostics').then(d=>{
  const yn=v=>v?'<span class="badge ok">OK</span>':'<span class="badge fail">아니오</span>';
  const c=d.sdk.contract||{}; const inst=d.sdk.installed||{};
  const miss=(c.missing||[]).length;
  let h='<h3>자가진단 <span class=muted>(SDK 계약·엔진 구동·경계 — read-only)</span></h3>';
  h+='<h4>SDK / 엔진</h4><table>'+
     `<tr><td>엔진 설치</td><td>${esc(JSON.stringify(inst))}</td></tr>`+
     `<tr><td>계약 심볼</td><td>${yn(c.ok)} 보유 ${(c.present||[]).length}개`+(miss?` · 누락 ${miss}: ${esc((c.missing||[]).join(', '))}`:'')+`</td></tr>`+
     `<tr><td>샌드박스 격리(run_sandboxed)</td><td>${yn(c.sandbox_ok)}</td></tr>`+
     `<tr><td>엔진 stage 등록(R3-A)</td><td>${yn(d.engine&&d.engine.ok)} ${esc((d.engine||{}).stage_id||(d.engine||{}).reason||'')}</td></tr>`+
     `<tr><td>엔진 구동 기계장치(R3-B)</td><td>${yn(d.engine_levelb)}</td></tr>`+
     `<tr><td>작업 커밋 저자 강제</td><td>${yn(d.git_author.email_set)} ${esc(d.git_author.name||'')}</td></tr>`+
     '</table>';
  const v=d.verification||{};
  h+='<h4>검증 게이트</h4><table>'+
     `<tr><td>샌드박스 격리(엔진)</td><td>${yn(v.sandbox_isolated)} ${v.sandbox_isolated?'':'— [harness] 미설치, 로컬검증으로 degrade'}</td></tr>`+
     `<tr><td>레거시 회귀 strict 모드</td><td>${v.strict_regression?'<span class="badge ok">ON</span> 못 돌린 회귀 테스트 차단':'<span class="badge muted">OFF</span> 미검증은 경고만(기본)'}</td></tr>`+
     '</table>';
  const cap=(d.catalog||{}).capabilities||{};
  h+='<h4>능력 카탈로그</h4><table>'+Object.keys(cap).map(k=>`<tr><td class=muted>${esc(k)}</td><td>${esc((cap[k]||[]).join(' · '))}</td></tr>`).join('')+'</table>';
  h+='<div class=muted style="margin-top:10px">경계: '+esc((d.catalog||{}).boundary||'')+'</div>';
  el.innerHTML=h;}).catch(tabErr(el));
 if(t==='deploy') jget('/api/status').then(d=>{
  let h='<h3>릴리즈 사다리 (develop→stg→main)</h3><table><tr><th>브랜치</th><th>환경</th><th>URL</th><th>Jenkins</th></tr>'+
   d.ladder.map(s=>`<tr><td>${esc(s.branch)}</td><td>${esc(s.env)}</td><td><a href="${esc(s.url)}" target=_blank>${esc(s.url)}</a></td><td class=muted>${esc(s.jenkins)}</td></tr>`).join('')+'</table>';
  h+='<h3>Jenkins</h3>'+(d.jenkins?('<table><tr><th>job</th><th>env</th></tr>'+d.jenkins.map(j=>`<tr><td>${esc(j.name)}</td><td>${esc(j.env)}</td></tr>`).join('')+'</table>'):'<div class=muted>.env에 XGEN_MAKER_JENKINS_* 없음</div>');
  h+='<h3>ArgoCD 배포 상태 <span class=muted>(read-only — MAKER는 배포 안 함)</span></h3>'+(d.argocd?('<table><tr><th>app</th><th>sync</th><th>health</th></tr>'+d.argocd.map(a=>`<tr><td>${esc(a.name)}</td><td><span class="badge ${cls(a.sync)}">${esc(a.sync)}</span></td><td class="${cls(a.health)}">${esc(a.health)}</td></tr>`).join('')+'</table>'):'<div class=muted>.env에 XGEN_MAKER_ARGOCD_* 없음</div>');
  el.innerHTML=h;}).catch(tabErr(el));
}
// 우측 진행 패널 — 단계 게이트 + 찾은 코드 위치
// 모드마다 실제로 도는 단계가 다르다. 안 도는 단계를 걸어두면 영영 '진행 중'으로 남아
// 멈춘 것처럼 보인다 — 분석만인데 '브랜치 생성'을 기다리게 하지 않는다.
const STEPKO={intent:'의도 분류',kg_search:'관련 코드 찾기',query_expand:'코드 용어 변환',
 impact:'영향 분석',chain:'연관 코드',legacy_check:'근거 코드',learnings:'과거 교훈',
 authorize:'권한 확인',fetch_latest:'최신 코드',worktree:'작업 공간',branch:'브랜치',
 implement:'구현',checks:'검증',judge:'품질 평가',iteration:'수렴',verify:'로컬 확인',
 ui_verify:'화면 검증',deploy_test:'배포 검증',release:'릴리즈 경로',commit:'커밋',
 push:'푸시',mr_create:'MR 생성',mr_ready:'MR 준비',plan_only:'분석 정리',answer:'답변',
 kg_refresh:'그래프 갱신',cost:'사용량',session_start:'시작',session_end:'종료'};
const GATES_ALL=[['intent','의도 분류'],['kg_search','지식그래프 착지'],['fetch_latest','최신 코드'],
 ['branch','브랜치 생성'],['implement','구현(에이전트)'],['checks','검증(테스트·회귀)'],
 ['judge','품질 게이트'],['mr_ready','MR 준비']];
const GATES_PLAN=[['intent','의도 분류'],['kg_search','관련 코드 찾기'],['plan_only answer','분석 정리']];
const GLABEL={}; GATES_ALL.concat(GATES_PLAN).forEach(([keys,label])=>
 keys.split(' ').forEach(kk=>{GLABEL[kk]=label;}));
function gatesFor(mode){return mode==='plan'?GATES_PLAN:GATES_ALL;}
function runstate(on,txt){const r=document.getElementById('runstate');r.classList.toggle('on',on);if(txt)document.getElementById('runstate-t').textContent=txt;}
function resetPanel(mode){
 document.getElementById('gates').innerHTML=gatesFor(mode).map(g=>`<div class="g" data-s="${g[0]}"><span class=dot>○</span>${g[1]}</div>`).join('');
 document.getElementById('landing').innerHTML='<div class=muted style="font-size:12px">지식그래프 검색하고 있습니다</div>';
 document.getElementById('landn').textContent='';
 runstate(true,'시작…');
}
function markGate(step,status){
 const el=[...document.querySelectorAll('#gates .g')]
  .find(g=>g.dataset.s.split(' ').includes(step));
 if(el){const dot=el.querySelector('.dot');
  if(status==='fail'){el.className='g fail';dot.textContent='✗';}
  else if(status==='start'){el.className='g active';dot.textContent='◐';}
  else{el.className='g done';dot.textContent='✓';
   let nx=el.nextElementSibling; if(nx&&!nx.classList.contains('done')&&!nx.classList.contains('fail')){nx.classList.add('active');nx.querySelector('.dot').textContent='◐';}}}
 // 상단 실행표시 텍스트 = 현재 단계(게이트에 없는 세부단계도 표시)
 const SUB={query_expand:'키워드 확장(LLM)',implement:'구현(에이전트)',checks:'검증',judge:'품질 평가',answer:'답변 정리'};
 if(status!=='fail'){const label=GLABEL[step]||SUB[step]||step; runstate(true, label+(status==='start'?' 중…':''));}
}
function showLanding(items){
 const el=document.getElementById('landing');
 if(!items||!items.length){el.innerHTML='<div class=muted style="font-size:12px">일치하는 코드 없음</div>';return;}
 document.getElementById('landn').textContent='('+items.length+')';
 // 레포 목록(중복 제거) — 답변에서 바로 레포별 그래프로
 const repos=[...new Set(items.map(n=>n.repo).filter(Boolean))];
 let h=repos.length?`<div class=lrepos>관련 레포: ${repos.map(r=>`<span class=rchip data-r="${esc(r)}" title="클릭 → 이 레포 그래프">${esc(r)}</span>`).join('')}</div>`:'';
 h+=items.map(n=>`<div class="lz" data-id="${esc(n.id||'')}" data-nm="${esc(n.name||'')}" title="클릭 → 이 코드 보기"><div class="kd">${esc(klabel(n.kind))} · 관련도 ${esc(n.score)}</div><div class="nm">${esc(n.name)}</div><div class="pt">${esc(n.repo)}:${esc(n.path)}${n.line?':'+esc(n.line):''}</div></div>`).join('');
 el.innerHTML=h;
 el.querySelectorAll('.lz').forEach(z=>{if(z.dataset.id)z.onclick=()=>jumpToNodeCode(z.dataset.id,z.dataset.nm);});
 el.querySelectorAll('.rchip').forEach(c=>c.onclick=()=>jumpToRepoGraph(c.dataset.r));
}
// 아무 데서나 노드 코드로 점프 — 지식그래프 탭 열고 그 노드의 실제 코드를 띄운다(연동성)
function jumpToNodeCode(id,name){
 document.querySelector('nav button[data-t=graph]').click();
 let tries=20;
 const go=()=>{const gc=document.getElementById('gcode');
  if(gc){loadNodeCode({id:id,name:name||id},gc);gc.scrollIntoView({block:'center'});}
  else if(--tries>0)setTimeout(go,200);};
 go();
}
// 답변/어디서든 → 그 레포의 내부 그래프로
function jumpToRepoGraph(repo){
 document.querySelector('nav button[data-t=graph]').click();
 let tries=20;
 const go=()=>{if(window.__showRepo){window.__showRepo(repo);document.getElementById('gwrap')?.scrollIntoView({block:'center'});}
  else if(--tries>0)setTimeout(go,200);};
 go();
}
const SICON={ok:'✓',start:'◐',fail:'✗',skipped:'—'};
// 내 PC의 브랜치 — 정리는 여기서. 무엇을 지워도 되는지 기록으로 판단한다.
function renderLocalBranches(){
 const box=document.getElementById('localbr'); if(!box)return;
 jget('/api/local-branches').then(d=>{
  const rows=d.branches||[];
  if(!rows.length){box.innerHTML='<span class=muted>로컬 브랜치가 없습니다.</span>';return;}
  const byRepo={}; rows.forEach(b=>{(byRepo[b.repo]=byRepo[b.repo]||[]).push(b);});
  let h='';
  Object.keys(byRepo).sort().forEach(repo=>{
   const list=byRepo[repo];
   h+='<div class=brgrp><b>'+esc(repo)+'</b> <span class=muted>'+list.length+'개</span><table>';
   list.forEach(b=>{
    const tags=[];
    if(b.current)tags.push('<span class="badge neutral">지금 사용 중</span>');
    if(b.protected)tags.push('<span class="badge neutral">보호</span>');
    if(b.by_maker)tags.push('<span class="badge ok">MAKER 생성</span>');
    if(b.merged)tags.push('<span class="badge ok">병합됨</span>');
    const lock=b.current||b.protected;
    h+='<tr><td>'+esc(b.name)+'</td><td class=muted>'+esc(b.when||'')+'</td><td>'+tags.join(' ')+'</td>'
     +'<td>'+(lock?'<span class=muted style="font-size:11px">지울 수 없음</span>'
        :'<button class="brdel ghost" data-repo="'+esc(b.repo)+'" data-name="'+esc(b.name)+'">지우기</button>')+'</td></tr>';
   });
   h+='</table></div>';
  });
  box.innerHTML=h;
  box.querySelectorAll('.brdel').forEach(btn=>btn.onclick=()=>deleteBranch(btn.dataset.repo,btn.dataset.name));
 }).catch(e=>{box.innerHTML='<span class=muted>브랜치를 불러오지 못했습니다: '+esc(e.message||e)+'</span>';});
}
function deleteBranch(repo,name){
 jget('/api/branch-delete?repo='+encodeURIComponent(repo)+'&name='+encodeURIComponent(name)).then(p=>{
  if(!p.ok){alert(p.error||'지울 수 없습니다');return;}
  let warn='';
  if(!p.by_maker)warn+='\\n\\n주의: MAKER가 만든 브랜치가 아닙니다. 직접 만드신 작업일 수 있습니다.';
  if(!p.merged)warn+='\\n주의: 아직 병합되지 않았습니다. 이 브랜치에만 있는 커밋은 사라집니다.';
  if(!confirm('['+repo+'] '+name+'\\n로컬에서만 지웁니다(원격은 그대로).'+warn+'\\n\\n계속할까요?'))return;
  jget('/api/branch-delete?confirm=1&repo='+encodeURIComponent(repo)+'&name='+encodeURIComponent(name)).then(r=>{
   if(!r.ok){alert(r.error||'삭제 실패');return;}
   line('info','브랜치 삭제: ['+repo+'] '+name+' — '+(r.note||''),'·');
   renderLocalBranches();
  }).catch(e=>alert('삭제 실패: '+(e.message||e)));
 }).catch(e=>alert('확인 실패: '+(e.message||e)));
}
// 캡처한 화면 — 장수나 세션 수로 자르지 않는다. 찾으려고 보는 화면이다.
const SHOTKIND={baseline:'기준',snapshot:'캡처',diff:'차이'};
function renderShots(q){
 const box=document.getElementById('shotgal'), num=document.getElementById('shotn');
 if(!box)return;
 jget('/api/ui-images'+(q?'?q='+encodeURIComponent(q):'')).then(d=>{
  const rows=d.images||[];
  if(num)num.textContent=rows.length+'장';
  if(!rows.length){box.innerHTML='<span class=muted>'+(q?'찾는 화면이 없습니다.':'아직 캡처한 화면이 없습니다. 위에서 주소를 넣고 캡처해 보세요.')+'</span>';return;}
  box.innerHTML='<div class=gal>'+rows.map(s=>
   `<figure><img src="${esc(s.url)}" loading=lazy alt="${esc(s.route)}">`
   +`<figcaption><b>${esc(SHOTKIND[s.kind]||s.kind)}</b> ${esc(s.route)}`
   +(s.session?` <span class=muted>${esc(s.session)}</span>`:'')
   +`<br><span class=muted>${esc(s.when)} · ${(s.bytes/1024).toFixed(0)}KB</span></figcaption></figure>`).join('')+'</div>';
 }).catch(e=>{box.innerHTML='<span class=muted>화면을 불러오지 못했습니다: '+esc(e.message||e)+'</span>';});
}
// 병합 요청 초안 — 경로만 알려주면 사람이 파일을 찾아 열어야 한다. 그 자리에서 보여준다.
function showDraft(sid){
 jget('/api/draft?id='+encodeURIComponent(sid)).then(r=>{
  const box=document.getElementById('draftbox');
  if(!r.ok){box.innerHTML='<div class=dfh><b>병합 요청 초안</b><span class=dfx>✕</span></div><div class=muted style="padding:12px">'+esc(r.error||'초안을 열 수 없습니다')+'</div>';}
  else{box.innerHTML='<div class=dfh><b>병합 요청 초안</b> <span class=muted>'+esc(sid)+'</span>'
   +'<button class="dfcopy ghost">복사</button><span class=dfx>✕</span></div>'
   +'<pre class=dfbody>'+esc(r.markdown)+'</pre>';}
  box.style.display='block';
  box.querySelector('.dfx').onclick=()=>{box.style.display='none';};
  const cp=box.querySelector('.dfcopy');
  if(cp)cp.onclick=()=>{navigator.clipboard.writeText(r.markdown).then(()=>{cp.textContent='복사됨';setTimeout(()=>cp.textContent='복사',1500);}).catch(()=>{cp.textContent='복사 실패';});};
 }).catch(e=>alert('초안을 불러오지 못했습니다: '+(e.message||e)));
}
// 세션 목록 — 찾기·이어하기·지우기. 세션이 쌓이면 목록만으로는 원하는 걸 못 찾는다.
function renderSessions(el,q){
 jget('/api/history?limit=200'+(q?'&q='+encodeURIComponent(q):'')).then(d=>{
  const rows=d.sessions||[];
  let h='<h3>세션 목록 <span class=muted>작업을 찾아 이어서 하거나 정리합니다</span></h3>';
  h+=`<div class=sessbar><input id=sessq placeholder="쿼리·브랜치로 찾기" value="${esc(q||'')}">`
    +`<span class=muted>${rows.length}개</span></div>`;
  if(!rows.length){
   h+='<div class=muted>'+(q?'찾는 작업이 없습니다.':'아직 작업 기록이 없습니다.')+'</div>';
   el.innerHTML=h;
   const box=document.getElementById('sessq');
   if(box){box.onkeydown=e=>{if(e.key==='Enter')renderSessions(el,box.value.trim());};}
   return;
  }
  h+='<div class="histcols"><div class="histlist"><table><tr><th>결과</th><th>쿼리</th><th>브랜치</th><th>MR</th><th></th></tr>'
   +rows.map(s=>`<tr class="hrow" data-sid="${esc(s.session)}"><td><span class="badge ${cls(s.outcome)}">${esc(outcomeLabel(s.outcome))}</span></td><td class=cellwrap title="${esc(s.query)}">${esc(s.query)}</td><td class="muted">${esc(s.branch)}</td><td>${s.mr?`<a href="${esc(s.mr)}" target=_blank onclick="event.stopPropagation()">MR</a>`:''}</td>`
    +`<td><button class="sessdel ghost" data-sid="${esc(s.session)}" title="이 기록을 지웁니다">지우기</button></td></tr>`).join('')
   +'</table></div><aside id="histdetail"><div class=muted>왼쪽에서 작업을 선택하세요.</div></aside></div>';
  el.innerHTML=h;
  const box=document.getElementById('sessq');
  if(box){box.onkeydown=e=>{if(e.key==='Enter')renderSessions(el,box.value.trim());};}
  el.querySelectorAll('.hrow').forEach(r=>r.onclick=()=>{
   el.querySelectorAll('.hrow').forEach(x=>x.classList.remove('sel'));
   r.classList.add('sel'); showSession(r.dataset.sid);});
  el.querySelectorAll('.sessdel').forEach(b=>b.onclick=ev=>{
   ev.stopPropagation(); deleteSession(b.dataset.sid, el, q);});
 }).catch(tabErr(el));
}
// 지우기 — 무엇이 지워지는지 먼저 보여주고 확인받는다(되돌릴 수 없다)
function deleteSession(sid,el,q){
 jget('/api/session-delete?id='+encodeURIComponent(sid)).then(p=>{
  if(!p.ok){alert(p.error||'지울 수 없습니다');return;}
  const what=(p.query?'"'+p.query+'"':sid)
   +(p.branch?'\\n브랜치 '+p.branch+'는 그대로 남습니다(코드는 지우지 않습니다).':'');
  if(!confirm('이 기록을 지웁니다.\\n\\n'+what+'\\n\\n되돌릴 수 없습니다. 계속할까요?'))return;
  jget('/api/session-delete?confirm=1&id='+encodeURIComponent(sid)).then(r=>{
   if(!r.ok){alert(r.error||'삭제 실패');return;}
   if(r.note)alert(r.note);
   renderSessions(el,q); refreshSessCount();
  }).catch(e=>alert('삭제 실패: '+(e.message||e)));
 }).catch(e=>alert('확인 실패: '+(e.message||e)));
}
function showSession(sid,target){
 const d=target||document.getElementById('histdetail'); d.innerHTML='<div class=muted>불러오고 있습니다</div>';
 jget('/api/session?id='+encodeURIComponent(sid)).then(s=>{
  let h=`<h4>${esc(s.query)||'(쿼리 없음)'}</h4><div class=muted style="margin-bottom:10px">결과 <span class="badge ${cls(s.outcome)}">${esc(outcomeLabel(s.outcome))}</span>${s.mr?` · <a href="${esc(s.mr)}" target=_blank>MR</a>`:''}</div>`;
  if(s.query) h+=`<button class="resumebtn ghost" style="margin-bottom:10px" data-q="${esc(s.query)}">▶ 이 작업 이어서 실행</button>`;
  h+=`<button class="draftbtn ghost" style="margin:0 0 10px 6px" data-sid="${esc(sid)}">📄 병합 요청 초안</button>`;
  h+='<div class="side-h">진행 단계</div><div class="tl">'+s.steps.map(st=>`<div class="tlr ${st.status}"><span class="ti">${SICON[st.status]||'·'}</span><span class="ts">${esc(st.step)}</span><span class="td">${esc(st.summary)}</span></div>`).join('')+'</div>';
  if(s.summary_md) h+='<div class="side-h" style="margin-top:12px">SUMMARY.md</div><pre class="smd">'+esc(s.summary_md)+'</pre>';
  if(s.undoable) h+=`<div class="side-h" style="margin-top:12px">되돌리기</div><div class=muted style="font-size:12px;margin-bottom:6px">이 작업에서 만든 브랜치(${esc(s.branch)})를 삭제합니다. ${s.pushed?'원격에도 등록되어 있습니다':'로컬에만 있습니다'}.</div>`+
   `<label style="font-size:12px"><input type=checkbox class="undoremote" ${s.pushed?'':'disabled'}> 원격 브랜치도 함께 삭제</label><br><button class="undobtn danger" data-sid="${esc(s.session)}">↺ 되돌리기</button><div class="undoout muted" style="font-size:12px;margin-top:6px"></div>`;
  else h+='<div class=muted style="font-size:12px;margin-top:12px">되돌릴 변경 사항이 없는 작업입니다.</div>';
  d.innerHTML=h;
  // 이 패널(d) 안에서만 찾는다 — 작업이력·테스트 두 탭에 같이 렌더되므로
  // document 전역 조회를 쓰면 먼저 그려진 탭의 버튼만 잡혀 나중 탭 버튼이 죽는다.
  const ub=d.querySelector('.undobtn');
  if(ub) ub.onclick=()=>doUndo(ub.dataset.sid,d.querySelector('.undoremote').checked,d);
  const dbtn=d.querySelector('.draftbtn');
  if(dbtn) dbtn.onclick=()=>showDraft(dbtn.dataset.sid);
  const rb=d.querySelector('.resumebtn');
  if(rb) rb.onclick=()=>{document.querySelector('nav button[data-t=run]').click();
   const q=document.getElementById('q');q.value=rb.dataset.q;q.focus();
   line('step','이어서 실행: '+rb.dataset.q+' — 실행을 누르면 최신 코드로 다시 진행합니다','↻');};
 }).catch(e=>{d.innerHTML='<div class=muted>작업 내용을 불러오지 못했습니다: '+esc(e.message||e)+'</div>';});
}
function doUndo(sid,remote,panel){
 const out=(panel||document).querySelector('.undoout');
 if(!confirm('이 작업을 되돌리시겠습니까? 브랜치가 삭제됩니다'+(remote?' (원격 포함)':'')+'.')){return;}
 out.textContent='되돌리고 있습니다';
 jget('/api/undo?id='+encodeURIComponent(sid)+'&confirm=1'+(remote?'&remote=1':'')).then(r=>{
  if(r.ok){out.innerHTML='<span style="color:var(--success)">✓ 되돌림</span> — '+esc((r.steps||[]).join(' · '))+(r.mr_note?'<br>'+esc(r.mr_note):'');}
  else{out.innerHTML='<span style="color:var(--danger)">✗ 실패</span> — '+esc((r.errors||[]).join(' · '));}
 }).catch(e=>{out.textContent='실패: '+(e.message||e);});
}
// 실행 (SSE)
document.getElementById('f').addEventListener('submit',e=>{
 e.preventDefault(); const query=q.value.trim(); if(!query)return;
 document.querySelector('nav button[data-t=run]').click();
 go.disabled=true; stopbtn.style.display='inline-block'; line('step',query,'▶'); q.value='';
 const mode='observe'; resetPanel(mode);   // 모드 나누지 않는다 — 항상 MR 전단계까지
 window.__runid=null;
 if(window.__es){try{window.__es.close()}catch(_){}}
 const es=new EventSource('/api/run?q='+encodeURIComponent(query)+'&mode='+mode);
 window.__es=es;
 const done=()=>{runstate(false); go.disabled=false; stopbtn.style.display='none';
  // 끝났는데 ◐가 남아 있으면 계속 도는 것처럼 보인다. 안 돈 단계는 안 돌았다고 말한다.
  document.querySelectorAll('#gates .g.active').forEach(el=>{el.className='g skip';el.querySelector('.dot').textContent='–';el.title='이 모드에서는 실행되지 않는 단계입니다';});
  es.close(); window.__es=null; window.__runid=null;};
 es.onmessage=ev=>{
  const e=JSON.parse(ev.data);
  if(e.type==='run_id'){window.__runid=e.id;}
  else if(e.type==='event'){const mark={ok:'✓',pass:'✓',fail:'✗',empty:'·',skipped:'·',observe:'◇',act:'◆'}[e.status]||'▸';
   // 단계 이름도 사람 말로. 에이전트 활동은 이름 없이 내용만(줄줄이 이어지므로)
   const label=(e.step==='agent')?'':(STEPKO[e.step]||e.step)+'  ';
   line(e.status==='fail'?'fail':'ok', label+(e.detail||e.status), mark);
   markGate(e.step, e.status);
   if(e.landing)showLanding(e.landing);}
  else if(e.type==='result'){const r=e.report; let html='<b>결과: '+esc(outcomeLabel(r.outcome))+'</b>';
   if(r.landing&&r.landing.length)showLanding(r.landing);
   if(r.branch)html+='<br>'+(r.outcome==='plan_only'?'제안 브랜치명(아직 만들지 않음): ':'브랜치: ')+esc(r.branch); if(r.iterations)html+=' · 수렴 '+r.iterations+'회';
   if(r.mr_draft&&r.session_dir)html+='<br><button class="draftbtn ghost" data-sid="'+esc(r.session_dir.split(/[\\/]/).pop())+'">📄 병합 요청 초안 보기</button>';
   if(r.mr&&r.mr.url)html+='<br>MR: <a href="'+esc(r.mr.url)+'" target=_blank>'+esc(r.mr.url)+'</a>';
   if(r.answer)html+='<br>'+esc(r.answer).replace(/\\n/g,'<br>');
   const d=document.createElement('div');d.className='result';d.innerHTML=html;log.appendChild(d);log.scrollTop=log.scrollHeight;
   const db=d.querySelector('.draftbtn'); if(db)db.onclick=()=>showDraft(db.dataset.sid);
   done(); loaded['history']=0; loaded['tests']=0;}  // 이력·테스트 갱신 유도
  else if(e.type==='stopped'){line('fail',e.message||'중지됨','■'); done();}
  else if(e.type==='error'){line('fail',e.message,'✗'); done();}
 };
 // EventSource는 끊기면 스스로 다시 붙는다. 그런데 재연결은 새 GET이라 같은 작업이
 // 처음부터 또 돌아간다(한 번 눌렀는데 세션이 6개 생겼다). 결과를 받기 전에 끊겼다면
 // 다시 붙이지 않고 여기서 끝낸다 — 사용자가 다시 누를지는 사용자가 정한다.
 es.onerror=()=>{
  if(es.readyState!==EventSource.CLOSED)line('fail','연결이 끊겨 표시를 멈춥니다. 작업은 서버에서 계속될 수 있습니다 — 세션 목록에서 확인하세요.','✗');
  done();
 };
});
document.getElementById('stopbtn').onclick=()=>{
 if(!window.__runid){if(window.__es){window.__es.close();window.__es=null;}runstate(false);go.disabled=false;document.getElementById('stopbtn').style.display='none';return;}
 line('step','중지하고 있습니다','■');
 fetch('/api/stop?id='+window.__runid).then(r=>r.json()).catch(()=>{});
};
</script></body></html>"""


_RUN_MODES = ("plan", "observe", "act")  # 이 밖의 값은 거부(모르는 모드가 쓰기로 새지 않게)


def kind_label(node: dict) -> str:
    """노드 종류를 사람 말로(오류 메시지용). 화면 쪽 KLABEL과 같은 말을 쓴다."""
    return {"repo": "저장소 최상위", "feature": "기능 그룹", "domain": "도메인",
            "file": "파일", "class": "클래스", "function": "함수",
            "endpoint": "서버 API", "api_call": "화면에서 호출",
            "gateway_route": "API 관문 경로", "route": "화면 주소"}.get(
        node.get("kind", ""), node.get("kind", "") or "컨테이너")


def _is_link_local(url: str) -> bool:
    """링크로컬(169.254/16, fe80::) — 클라우드 메타데이터(169.254.169.254) 포함.

    IP 리터럴뿐 아니라 호스트명도 실제로 해소해서 판정한다. 이름만 걸러내면
    메타데이터를 가리키는 호스트명(공개 DNS에도 흔하다) 하나로 그냥 우회된다.
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse as _up
    host = (_up(url).hostname or "").strip("[]")
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_link_local
    except ValueError:
        pass
    try:  # 호스트명 → 해소된 주소 전부 검사(하나라도 링크로컬이면 거부)
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return False  # 해소 실패는 어차피 캡처도 실패한다
    for info in infos:
        try:
            if ipaddress.ip_address(info[4][0]).is_link_local:
                return True
        except ValueError:
            continue
    return False


class _Cancelled(Exception):
    """사용자가 실행 중지를 눌렀을 때 파이프라인을 협조적으로 끊는 신호."""


class _SSEJournal:
    """journal.event를 가로채 SSE 큐로 흘리는 래퍼. 매 단계에서 취소 신호를 확인한다."""
    def __init__(self, real, q: queue.Queue, cancel=None):
        self._real = real
        self._q = q
        self._cancel = cancel
        self.dir = real.dir
        self.slug = real.slug

    def cancelled(self) -> bool:
        # 오래 걸리는 단계(에이전트)가 실행 도중 폴링한다 — 단계 경계만으론
        # 중지를 눌러도 에이전트가 타임아웃까지 레포를 계속 고친다.
        return self._cancel is not None and self._cancel.is_set()

    def event(self, step, status, **data):
        # 협조적 취소 — 매 단계 경계에서 확인. 눌렸으면 파이프라인을 즉시 끊는다.
        if self.cancelled():
            raise _Cancelled()
        self._real.event(step, status, **data)
        # 화이트리스트로 거른 JSON을 그대로 흘리면 화면엔 `{}`나 조각만 남는다.
        # 무엇을 했는지 사람 말로 한 줄 만든다(loop/narrate.py).
        from .loop.narrate import describe
        item = {"type": "event", "step": step, "status": status,
                "detail": describe(step, status, data)}
        # 착지점(landing)은 우측 패널용으로 잘리지 않게 별도 전달
        if data.get("landing"):
            item["landing"] = data["landing"]
        self._q.put(item)

    def close(self, outcome):
        return self._real.close(outcome)


# 공유 Graph 접근 직렬화용 락. 단, 실행(run) 전체를 감싸면 분 단위로 락을 잡아
# /api/info·/api/sync가 그동안 얼어붙는다(회귀). 그래서 run은 락을 잡지 않고
# (파일 손상은 Graph.save의 원자적 교체가 방지), 명시적 mutator인 /api/sync만 직렬화하며,
# 읽기(/api/info)는 순회 중 mutation 시 RuntimeError를 회복재시도한다.
_GRAPH_LOCK = threading.Lock()


def _run_query(config: MakerConfig, graph: Graph, query: str, q: queue.Queue,
               cancel=None) -> None:
    from .loop.pipeline import MakerLoop
    from .loop.journal import Journal
    try:
        # journal 팩토리 주입 — 전역 몽키패치 없이 이 요청만 SSE로 스트리밍(동시 요청 안전)
        def factory(worklogs_dir, qtext, verbose=False):
            return _SSEJournal(Journal(worklogs_dir, qtext, verbose=False), q, cancel)
        loop = MakerLoop(config, graph=graph, journal_factory=factory)
        report = loop.run(query)  # 락 없이 — 대시보드 프리즈 방지(파일은 원자적 저장으로 안전)
        q.put({"type": "result", "report": report})
    except _Cancelled:
        q.put({"type": "stopped", "message": "작업을 중지했습니다"})
    except Exception as error:  # noqa: BLE001
        q.put({"type": "error", "message": str(error)[:300]})
    finally:
        q.put(None)


class MakerWebHandler(BaseHTTPRequestHandler):
    config: MakerConfig = None  # type: ignore[assignment]
    config_path: str | None = None
    graph: Graph = None  # type: ignore[assignment]
    _cancels: dict = {}  # run_id → threading.Event (실행 중지용)
    _adj: dict = None     # 인접 리스트 캐시(클래스 보관 — 핸들러는 요청마다 새 인스턴스)
    _adj_ver = None
    _adj_graph = None
    _kg_stamp = None      # 로드한 KG 파일의 (mtime, size)
    _cfg_stamp = None
    _kg_lock = threading.Lock()

    def log_message(self, *a):  # 조용히
        pass

    @classmethod
    def _reload_config_if_changed(cls) -> None:
        """설정 파일이 바뀌면 다시 읽는다 — 저장소를 추가하고 재시작을 기다리지 않게.

        진행 중인 실행은 자기가 받은 설정 객체를 계속 쓴다(중간에 규칙이 바뀌지 않는다).
        """
        path = cls.config_path
        if not path:
            return
        try:
            st = os.stat(path)
        except OSError:
            return
        stamp = (st.st_mtime_ns, st.st_size)
        if stamp == cls._cfg_stamp:
            return
        with cls._kg_lock:
            if stamp == cls._cfg_stamp:
                return
            first, cls._cfg_stamp = cls._cfg_stamp is None, stamp
            if first:                            # 기동 로드는 serve()가 이미 했다
                return
            try:
                cls.config = MakerConfig.from_file(path)
            except (OSError, ValueError, json.JSONDecodeError):
                pass                             # 편집 중인 파일 — 다음 요청에서 다시

    @classmethod
    def _reload_graph_if_changed(cls) -> None:
        """KG 파일이 밖에서 바뀌었으면 다시 읽는다(kg rebuild·CLI sync·스케줄러).

        안 하면 서버가 기동 시점 그래프를 계속 들고 있어, 낡은 좌표로 조용히 착지시킨다.
        저장은 원자적(os.replace)이라 반쯤 쓰인 파일을 읽을 일은 없다.
        """
        if cls.config is None:
            return
        try:
            st = os.stat(cls.config.kg_path)
        except OSError:
            return
        stamp = (st.st_mtime_ns, st.st_size)
        if stamp == cls._kg_stamp:
            return
        with cls._kg_lock:
            if stamp == cls._kg_stamp:          # 다른 스레드가 이미 갈아끼웠다
                return
            if cls._kg_stamp is None:           # 기동 직후 첫 기록 — 로드는 serve()가 했다
                cls._kg_stamp = stamp
                return
            try:
                fresh = Graph.load(cls.config.kg_path)
            except (OSError, ValueError, json.JSONDecodeError):
                return                          # 다음 요청에서 다시 시도
            _apply_overlay_for(cls.config.kg_path, fresh)
            cls.graph = fresh
            cls._kg_stamp = stamp
            cls._adj = cls._adj_ver = cls._adj_graph = None   # 인접 캐시는 옛 그래프 것

    def do_GET(self):
        # 어떤 핸들러가 예외를 던져도 스레드가 죽지 않게 500으로 감싼다(빈 응답/hang 방지)
        self._response_started = False
        self._reload_config_if_changed()
        self._reload_graph_if_changed()
        try:
            self._route()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:  # noqa: BLE001
            # 이미 응답(SSE 등)이 시작됐으면 두 번째 응답을 쏘지 않는다(HTTP 손상 방지)
            if not self._response_started:
                try:
                    self._json({"ok": False, "error": str(error)[:200]})
                except Exception:  # noqa: BLE001
                    pass

    @staticmethod
    def _graph_read(fn, fallback, tries: int = 6):
        """그래프 순회 읽기 공통 가드.

        sync/루프가 그래프를 제자리에서 바꾸는 동안 순회하면
        'dict changed size during iteration'이 난다. 락으로 직렬화하면 sync(수 초)
        내내 대시보드가 얼어붙으므로, 짧게 재시도하고 그래도 안 되면 fallback을 준다.
        (읽기 경로마다 try/except를 복붙하지 않도록 여기 한 곳에 모은다.)
        """
        import time
        for _ in range(tries):
            try:
                return fn()
            except RuntimeError:  # dict changed size during iteration
                time.sleep(0.02)
        return fallback() if callable(fallback) else fallback

    def _graph_info(self) -> dict:
        def read():
            snapshot = list(self.graph.nodes.values())
            names = sorted((self.config.gitlab_projects or {}).keys()
                           or (self.config.repos or {}).keys()
                           or {n["repo"] for n in snapshot})
            return {"nodes": len(snapshot),
                    "repos": len({n["repo"] for n in snapshot}),
                    "repo_names": names}
        return self._graph_read(read, lambda: {"nodes": len(self.graph.nodes),
                                               "repos": 0, "repo_names": []})

    def _graph_status(self) -> dict:
        # KG 상태 — 전체 통계 + 레포별 노드 수 + 종류 분포 + 마지막 동기화 head
        def read():
            nodes = list(self.graph.nodes.values())
            by_repo, by_kind = {}, {}
            for n in nodes:
                by_repo[n["repo"]] = by_repo.get(n["repo"], 0) + 1
                by_kind[n["kind"]] = by_kind.get(n["kind"], 0) + 1
            meta = self.graph.meta or {}
            heads = meta.get("repo_heads", {}) or {}
            repos = sorted(({s.get("repo") for s in meta.get("sources", [])}
                           | set(by_repo.keys())) - {None})
            return {
                "nodes": len(nodes), "edges": len(self.graph.edges),
                "kg_path": self.config.kg_path,
                "by_kind": sorted(by_kind.items(), key=lambda x: -x[1]),
                "repos": [{"repo": r, "nodes": by_repo.get(r, 0),
                           "head": (heads.get(r) or "")[:12]} for r in repos],
            }
        return self._graph_read(read, lambda: {"nodes": len(self.graph.nodes),
                                               "edges": 0, "by_kind": [], "repos": []})

    def _adjacency(self):
        # 인접 리스트 캐시 — 핸들러는 요청마다 새로 생기므로 반드시 클래스에 저장한다
        # (인스턴스에 두면 캐시가 매번 비어 16k 노드 인접을 요청마다 재구축한다).
        # 무효화 키: 엣지 수 + 노드 수(sync로 갱신되면 대개 둘 중 하나가 바뀐다).
        g = self.graph
        ver = (len(g.edges), len(g.nodes))
        cls = MakerWebHandler
        if cls._adj_ver != ver or cls._adj_graph is not g:
            def build():
                adj: dict = {}
                for e in list(g.edges):  # 스냅샷 — sync가 제자리 변경 중일 수 있다
                    adj.setdefault(e["src"], []).append(e["dst"])
                    adj.setdefault(e["dst"], []).append(e["src"])
                return adj
            adj = self._graph_read(build, None)
            if adj is None:
                return cls._adj or {}  # 계속 변경 중이면 직전 캐시로 버틴다
            cls._adj, cls._adj_ver, cls._adj_graph = adj, ver, g
        return cls._adj

    def _repo_graph(self) -> dict:
        # 1단계 — 레포 간 그래프(노드=레포, 엣지=레포를 넘나드는 연결 수). 몇 개뿐이라 즉시 뜬다.
        g = self.graph

        def read():
            counts: dict = {}
            for n in list(g.nodes.values()):  # 스냅샷 — sync 중 순회 크래시 방지
                counts[n["repo"]] = counts.get(n["repo"], 0) + 1
            pair: dict = {}
            for e in list(g.edges):
                s = g.nodes.get(e["src"])
                d = g.nodes.get(e["dst"])
                if not s or not d or s["repo"] == d["repo"]:
                    continue
                k = (s["repo"], d["repo"]) if s["repo"] < d["repo"] else (d["repo"], s["repo"])
                pair[k] = pair.get(k, 0) + 1
            deg: dict = {}
            for (a, b), w in pair.items():
                deg[a] = deg.get(a, 0) + w
                deg[b] = deg.get(b, 0) + w
            nodes = [{"id": r, "name": r, "kind": "repo", "repo": r, "path": "",
                      "deg": deg.get(r, 0), "count": c, "seed": False,
                      "note": "", "deprecated": False,
                      "summary": f"{c:,}개 코드 · 다른 저장소와 {deg.get(r, 0)}건 연결"}
                     for r, c in sorted(counts.items(), key=lambda kv: -kv[1])]
            edges = [{"src": a, "dst": b, "kind": "cross", "weight": w}
                     for (a, b), w in pair.items()]
            return {"nodes": nodes, "edges": edges, "level": "repo",
                    "total_nodes": len(g.nodes), "shown": len(nodes)}
        return self._graph_read(read, {"nodes": [], "edges": [], "level": "repo",
                                       "reason": "동기화 중입니다. 잠시 후 다시 시도하세요."})

    def _repo_subgraph(self, repo: str, max_nodes: int = 160) -> dict:
        # 2단계 — 한 레포 내부 그래프(연결 많은 순 상위 N + 그 사이 엣지). 레포 단위라 가볍다.
        g = self.graph
        max_nodes = max(20, min(max_nodes, 400))
        adj = self._adjacency()

        def read():
            # kind=repo(그 레포 자신)은 제외 — 이미 그 안에 들어와 있는데 컨테이너가
            # 같이 뜨면 클릭 시 같은 레포로 다시 드릴다운돼 제자리를 맴돈다.
            inside = [nid for nid, n in list(g.nodes.items())
                      if n["repo"] == repo and n.get("kind") != "repo"]
            if not inside:
                return {"nodes": [], "edges": [], "reason": f"'{repo}' 레포 항목을 찾을 수 없습니다"}
            inside.sort(key=lambda nid: -len(adj.get(nid, ())))
            keep = set(inside[:max_nodes])
            nodes = [{"id": nid, "name": n["name"], "kind": n["kind"],
                      "repo": n["repo"], "path": n.get("path", ""),
                      "seed": False, "deg": len(adj.get(nid, ())),
                      "note": n.get("meta", {}).get("note", ""),
                      "deprecated": bool(n.get("meta", {}).get("deprecated")),
                      "summary": n.get("meta", {}).get("summary", "")}
                     for nid in keep if (n := g.nodes.get(nid)) is not None]
            edges = [{"src": e["src"], "dst": e["dst"], "kind": e["kind"]}
                     for e in list(g.edges) if e["src"] in keep and e["dst"] in keep]
            return {"nodes": nodes, "edges": edges, "level": "node", "repo": repo,
                    "total_nodes": len(inside), "shown": len(nodes)}
        return self._graph_read(read, {"nodes": [], "edges": [], "level": "node",
                                       "repo": repo,
                                       "reason": "동기화 중입니다. 잠시 후 다시 시도하세요."})

    def _subgraph(self, query: str, max_nodes: int = 320) -> dict:
        # 쿼리로 착지 노드를 찾고, 그 주변을 다중 홉 BFS로 크게 펼쳐 시각화용 노드+엣지 반환.
        from .kg.search import search
        g = self.graph
        max_nodes = max(20, min(max_nodes, 700))  # 상한 가드(과도한 렌더 방지)
        adj = self._adjacency()

        def read():
            if query:
                hits = search(g, query, k=6)
                if not hits:
                    return {"nodes": [], "edges": [],
                            "reason": "일치하는 코드가 없습니다. 코드 이름으로 검색해 보세요 (예: config, router, Service)"}
                seeds = [h["id"] for h in hits]
            else:
                # 쿼리 없으면 '가장 많이 연결된 허브' 여럿을 씨앗으로 개요 그래프
                deg: dict = {}
                for e in list(g.edges):
                    deg[e["src"]] = deg.get(e["src"], 0) + 1
                    deg[e["dst"]] = deg.get(e["dst"], 0) + 1
                seeds = [nid for nid, _ in sorted(deg.items(), key=lambda kv: -kv[1])[:14]
                         if nid in g.nodes]
                if not seeds:
                    return {"nodes": [], "edges": [],
                            "reason": "지식그래프가 비어 있습니다. 동기화를 실행하세요."}
            # BFS 다중 홉 — 상한까지 넓게 확장(작은 그래프처럼 안 보이게)
            keep, frontier = set(seeds), list(seeds)
            while frontier and len(keep) < max_nodes:
                nxt = []
                for nid in frontier:
                    for nb in adj.get(nid, ()):
                        if nb not in keep:
                            keep.add(nb); nxt.append(nb)
                            if len(keep) >= max_nodes:
                                break
                    if len(keep) >= max_nodes:
                        break
                frontier = nxt
            nodes = [{"id": nid, "name": n["name"], "kind": n["kind"],
                      "repo": n["repo"], "path": n.get("path", ""),
                      "seed": nid in seeds, "deg": len(adj.get(nid, ())),
                      "note": n.get("meta", {}).get("note", ""),
                      "deprecated": bool(n.get("meta", {}).get("deprecated")),
                      "summary": n.get("meta", {}).get("summary", "")}
                     for nid in keep if (n := g.nodes.get(nid)) is not None]
            edges = [{"src": e["src"], "dst": e["dst"], "kind": e["kind"]}
                     for e in list(g.edges) if e["src"] in keep and e["dst"] in keep]
            return {"nodes": nodes, "edges": edges, "seed": seeds[0],
                    "total_nodes": len(g.nodes), "shown": len(nodes)}
        return self._graph_read(read, {"nodes": [], "edges": [],
                                       "reason": "동기화 중입니다. 잠시 후 다시 시도하세요."})

    def _node_code(self, node_id: str) -> dict:
        # 노드 → 실제 소스코드 블록 추출(파일:라인 → def/class 본문). AI/사람이 바로 이해하도록.
        from pathlib import Path
        n = self.graph.nodes.get(node_id)
        if not n:
            return {"ok": False, "error": "항목을 찾을 수 없습니다"}
        repo_path = self.config.repos.get(n.get("repo", ""))
        if not repo_path:
            return {"ok": False, "error": f"'{n.get('repo')}' 저장소 경로가 설정되지 않았습니다"}
        rel = n.get("path", "")
        if not rel:
            return {"ok": False, "error": "이 항목에는 파일 경로가 없습니다"}
        root = Path(repo_path).resolve()
        # repo/feature 노드의 path는 절대경로(저장소 최상위)다. Path의 '절대경로가 root를
        # 통째로 덮어쓰는' 성질에 기대면 조용히 root 밖을 가리킬 수 있으니 명시 처리한다.
        rel_p = Path(rel)
        full = rel_p.resolve() if rel_p.is_absolute() else (root / rel_p).resolve()
        # is_relative_to — startswith는 이름이 겹치는 형제 디렉토리(…/<repo>-backup)를 통과시킨다
        if not full.is_relative_to(root):
            return {"ok": False, "error": "허용되지 않은 경로입니다"}
        if full.is_dir():
            # 디렉토리(레포/피처 같은 컨테이너 노드) — 파일이 아닐 뿐 그래프는 정상이다.
            # 여기서 'Sync 필요'라고 하면 멀쩡한 그래프를 다시 돌리게 만드는 거짓 안내다.
            return {"ok": False,
                    "error": f"이 항목은 파일이 아니라 폴더입니다({kind_label(n)}) — "
                             "안쪽 파일 노드를 선택하세요"}
        # 그래프는 통합 브랜치 기준으로 만들어질 수 있다. 그러면 여기 있는 파일이
        # 사람의 체크아웃에는 없다 — 그건 "없는 파일"이 아니라 "아직 안 받은 파일"이다.
        # 그래프를 만든 그 커밋에서 읽어 보여준다(워킹트리는 건드리지 않는다).
        source_ref = next((s.get("ref") for s in self.graph.meta.get("sources", [])
                           if s.get("repo") == n.get("repo")), "")
        lines = None
        if full.is_file():
            try:
                lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError as e:
                return {"ok": False, "error": str(e)}
        elif source_ref:
            from .kg.source import GitRefSource
            try:
                lines = GitRefSource(root, source_ref).read_text(rel).splitlines()
            except OSError:
                lines = None
        if lines is None:
            return {"ok": False,
                    "error": ("파일을 찾을 수 없습니다 — 동기화가 필요합니다" if not source_ref
                              else f"이 파일은 {source_ref}에만 있습니다 — 아직 받지 않았습니다")}
        # 파일 전체를 준다. 보는 쪽에 스크롤이 있으므로 잘라 보낼 이유가 없고,
        # 60줄만 보여주면 3천 줄짜리 파일에서 "코드를 봤다"고 할 수 없다.
        # 브라우저가 감당 못 할 만큼 큰 파일만 자르고, 자를 때는 잘렸다고 말한다.
        kind = n.get("kind", "")
        code = "\n".join(lines)
        budget = self.config.code_view_budget_chars
        truncated = len(code) > budget
        if truncated:
            kept: list[str] = []
            used = 0
            for text in lines:
                if used + len(text) + 1 > budget:
                    break
                kept.append(text)
                used += len(text) + 1
            code, shown = "\n".join(kept), len(kept)
        else:
            shown = len(lines)
        meta = n.get("meta", {})
        return {"ok": True, "code": code, "first_line": 1,
                "focus_line": n.get("line") or 0,
                "shown_lines": shown, "truncated": truncated,
                "path": rel, "repo": n["repo"], "lang": full.suffix.lstrip("."),
                "name": n["name"], "kind": kind, "total_lines": len(lines),
                "summary": meta.get("summary", ""), "summary_src": meta.get("summary_src", ""),
                "note": meta.get("note", "")}

    # 설계된 파이프라인 전 단계 — (journal step, 라벨, 설명, 이 단계를 좌우하는 설정키)
    # "전체가 다 도는가"를 눈으로 확인할 수 있게 카탈로그를 코드에 명시한다.
    PIPELINE = [
        ("intent", "의도 분류", "요청 유형을 판별합니다", None),
        ("kg_search", "지식그래프 착지", "변경할 코드를 지식그래프에서 찾습니다", None),
        ("anchor", "지목한 곳 확인", "요청이 가리킨 화면·파일에서 관계를 타고 범위를 좁힙니다", None),
        ("query_expand", "키워드 확장", "한글 요청을 코드 용어로 변환합니다", "llm_enabled"),
        ("impact", "영향 분석", "변경 시 영향받는 코드를 찾습니다", None),
        ("chain", "워크플로우 체인", "연관된 코드를 함께 살펴봅니다", None),
        ("legacy_check", "레거시 확인", "기존 코드를 읽어 참고 자료로 전달합니다", None),
        ("learnings", "과거 학습", "이전 작업에서 얻은 내용을 반영합니다", None),
        ("fetch_latest", "최신 코드 동기화", "작업 전 최신 코드를 받아옵니다", "fetch_latest"),
        ("worktree", "별도 작업 공간 사용", "별도 작업 공간에서 진행합니다", "isolate_worktree"),
        ("authorize", "인가 검사", "저장소 접근 권한을 확인합니다", None),
        ("branch", "브랜치 생성", "규칙에 맞는 작업 브랜치를 만듭니다", None),
        ("implement", "구현(에이전트)", "AI가 코드를 수정합니다", None),
        ("checks", "검증(테스트)", "문법 검사와 테스트를 실행합니다", None),
        ("judge", "품질 게이트", "변경 품질을 평가합니다", "theta"),
        ("iteration", "수렴 반복", "통과할 때까지 다시 시도합니다", "max_iterations"),
        ("verify", "로컬 환경 확인", "로컬 환경에서 동작을 확인합니다", "enable_verify"),
        # 게이트는 코드와 정확히 일치해야 한다 — pipeline은 config.enable_ui_verify로
        # 분기하고 preview_base는 그 안에서 쓰는 전제조건이다(둘을 혼동하면 UI가 거짓말).
        ("ui_verify", "화면 검증", "바뀐 화면을 실제로 띄워 캡처하고, 깨졌는지 "
                                            "AI가 판정합니다 (프리뷰 주소가 있으면 자동)", "preview_base"),
        ("deploy_test", "배포 렌더 검증", "배포 설정을 점검합니다", None),
        ("release", "릴리즈 경로", "배포 대상과 경로를 확인합니다", None),
        ("commit", "커밋", "변경 내용을 기록합니다", "allow_write"),
        ("push", "푸시", "원격 저장소에 올립니다", "mode"),
        ("mr_create", "MR 생성", "병합 요청을 생성합니다", "mode"),
        ("mr_ready", "MR 준비", "병합 요청 초안을 준비합니다", None),
        ("kg_refresh", "그래프 갱신", "변경 내용을 지식그래프에 반영합니다", None),
        ("cost", "비용 집계", "사용량을 기록합니다", None),
    ]
    # 런타임에 바꿔도 되는 설정(화이트리스트). 배포 live는 env 인터록이 따로 있어 제외.
    SETTABLE = {
        "target_branch": ("대상 브랜치", "str"),
        "mode": ("모드", "choice:observe,act"),
        "fetch_latest": ("작업 전 최신 코드 받기", "bool"),
        "isolate_worktree": ("별도 작업 공간 사용", "bool"),
        "enable_verify": ("로컬 환경 확인", "bool"),
        "enable_ui_verify": ("화면 검증", "bool"),
        "ui_converge": ("화면 문제 시 다시 시도", "bool"),
        "strict_regression": ("테스트 미실행 시 중단", "bool"),
        "llm_enabled": ("AI 사용", "bool"),
        "max_iterations": ("최대 재시도 횟수", "int:1:10"),
        "theta": ("품질 기준 점수", "float:0:1"),
        "preview_base": ("프리뷰 주소", "str"),
    }

    def _graph_health(self) -> dict:
        """그래프가 '최신인지·제대로 구축됐는지'를 숫자로. 주장 대신 측정을 UI에 올린다."""
        import random
        from pathlib import Path
        from .kg.build import git_head

        def read():
            g = self.graph
            meta = g.meta or {}
            heads = meta.get("repo_heads", {}) or {}
            # 1) 신선도 — 기록된 HEAD vs 현재 git HEAD
            fresh = []
            for s in meta.get("sources", []):
                repo, root = s.get("repo"), s.get("root")
                if not repo or not root:
                    continue
                rec = (heads.get(repo) or "")[:12]
                cur = (git_head(root) or "")[:12]
                fresh.append({"repo": repo, "recorded": rec, "current": cur,
                              "stale": bool(cur and rec != cur), "no_head": not rec})
            # 2) 무결성
            ids = set(g.nodes)
            dangling = sum(1 for e in g.edges if e["src"] not in ids or e["dst"] not in ids)
            linked = set()
            for e in g.edges:
                linked.add(e["src"]); linked.add(e["dst"])
            # 3) 정확도 표본 — 심볼이 정말 그 파일 그 라인에 있나(빠르게 60개)
            syms = [n for n in g.nodes.values()
                    if n["kind"] in ("function", "class") and n.get("path") and n.get("line")]
            rnd = random.Random(7)
            sample = rnd.sample(syms, min(60, len(syms))) if syms else []
            exact = checked = 0
            for n in sample:
                root = self.config.repos.get(n["repo"])
                if not root:
                    continue
                f = Path(root) / n["path"]
                if not f.is_file():
                    continue
                try:
                    lines = f.read_text(encoding="utf-8-sig", errors="replace").splitlines()
                except OSError:
                    continue
                checked += 1
                ln, name = n["line"], n["name"].split(".")[-1]
                if 1 <= ln <= len(lines) and name in lines[ln - 1]:
                    exact += 1
            return {
                "nodes": len(g.nodes), "edges": len(g.edges),
                "freshness": fresh,
                "stale_repos": sum(1 for f in fresh if f["stale"]),
                "dangling": dangling, "orphans": len(ids - linked),
                "accuracy": {"checked": checked, "exact": exact,
                             "pct": round(exact / checked * 100, 1) if checked else None},
            }
        return self._graph_read(read, {"nodes": 0, "edges": 0, "freshness": [],
                                       "stale_repos": 0, "dangling": 0, "orphans": 0,
                                       "accuracy": {"checked": 0, "exact": 0, "pct": None},
                                       "reason": "동기화 중입니다. 잠시 후 다시 시도하세요."})

    def _pipeline_view(self) -> dict:
        from .loop.history import read_sessions, read_session_detail
        cfg = self.config
        # 최근 '실제로 코드를 만진' 세션의 단계별 결과를 붙여 보여준다
        last, steps = None, {}
        for s in read_sessions(cfg.worklogs_dir, 12):
            d = read_session_detail(cfg.worklogs_dir, s["session"])
            if d and any(st["step"] in ("implement", "branch") for st in d["steps"]):
                last = d
                for st in d["steps"]:
                    steps.setdefault(st["step"], st["status"])
                break
        stages = []
        for step, label, desc, gate in self.PIPELINE:
            gate_val = getattr(cfg, gate, None) if gate else None
            stages.append({"step": step, "label": label, "desc": desc,
                           "gate": gate, "gate_value": gate_val,
                           "last": steps.get(step, "")})
        settings = {k: getattr(cfg, k, None) for k in self.SETTABLE}
        return {"stages": stages, "settings": settings,
                "settable": {k: {"label": v[0], "type": v[1]} for k, v in self.SETTABLE.items()},
                "last_session": last["session"] if last else "",
                "last_query": last["query"] if last else ""}

    def _set_setting(self, qs: dict) -> dict:
        key = qs.get("key", [""])[0]
        raw = qs.get("value", [""])[0]
        if key not in self.SETTABLE:
            return {"ok": False, "error": f"변경할 수 없는 설정입니다: {key}"}
        kind = self.SETTABLE[key][1]
        try:
            if kind == "bool":
                val = raw.lower() in ("1", "true", "on", "yes")
            elif kind.startswith("int:"):
                lo, hi = (int(x) for x in kind.split(":")[1:])
                val = max(lo, min(hi, int(raw)))
            elif kind.startswith("float:"):
                lo, hi = (float(x) for x in kind.split(":")[1:])
                val = max(lo, min(hi, float(raw)))
            elif kind.startswith("choice:"):
                allowed = kind.split(":", 1)[1].split(",")
                if raw not in allowed:
                    return {"ok": False, "error": f"허용되는 값: {allowed}"}
                val = raw
            else:
                val = raw.strip()
        except ValueError:
            return {"ok": False, "error": "값 형식이 올바르지 않습니다"}
        setattr(self.config, key, val)
        # 런타임 변경임을 명시 — config 파일은 그대로다(재시작하면 되돌아간다)
        return {"ok": True, "key": key, "value": val, "note": "현재 세션에만 적용됩니다"}

    @staticmethod
    def _mask(value: str, keep: int = 3) -> str:
        """식별정보 마스킹 — 대시보드가 공개 도메인에 붙을 수 있어 원문을 그대로 내보내지 않는다.
        '연결됨 여부'와 앞 몇 글자면 어느 계정인지 분간하기엔 충분하다."""
        if not value:
            return ""
        if len(value) <= keep:
            return "•" * len(value)
        return value[:keep] + "•" * min(8, len(value) - keep)

    def _auth_info(self) -> dict:
        # 저장된 연결 설정(네트워크 호출 없음, 즉시). 식별정보는 마스킹해서 내보낸다.
        from urllib.parse import urlparse as _up
        from .auth import load_auth, AUTH_FILE
        a = load_auth()
        host = (_up(a.gitlab_url).hostname or a.gitlab_url) if a.gitlab_url else ""
        return {"provider": a.provider, "model": a.resolved_model(), "base": a.resolved_base(),
                # 주소는 호스트만, 그것도 마스킹 — 내부 GitLab 주소를 그대로 흘리지 않는다
                "gitlab_host": self._mask(host, 6),
                "gitlab_user": self._mask(a.gitlab_user, 3),
                "gitlab_token_set": bool(a.gitlab_token), "api_key_set": bool(a.api_key),
                # 전체 경로는 OS 사용자명이 드러나므로 파일명만
                "auth_file": AUTH_FILE.name, "auth_file_exists": AUTH_FILE.exists()}

    def _auth_check(self) -> dict:
        # 실제 로그인 지속 확인 — claude CLI 단발 호출 + GitLab 토큰 검증(느림, 버튼).
        from .auth import load_auth, claude_cli_status, gitlab_verify_token
        a = load_auth()
        out = {}
        if a.provider == "claude_cli":
            s = claude_cli_status()
            out["claude"] = {"authenticated": s["authenticated"],
                             "reason": (s.get("reason", "") or "")[:200]}
        else:
            out["claude"] = {"authenticated": bool(a.api_key), "reason": f"provider={a.provider}"}
        if a.gitlab_token:
            v = gitlab_verify_token(a.gitlab_url, a.gitlab_token)
            out["gitlab"] = {"ok": v["ok"], "user": self._mask(str(v.get("user", "")), 3),
                             "reason": (v.get("reason", "") or "")[:200]}
        else:
            out["gitlab"] = {"ok": False, "reason": "연결 안 됨 — 로그인이 필요합니다"}
        return out

    def _doctor(self) -> dict:
        # maker doctor를 하위 프로세스로 실행해 출력을 캡처(느림, 버튼).
        # redirect_stdout은 전역 sys.stdout을 바꿔, 스레딩 서버에선 doctor가 도는
        # 1~2분 동안 다른 요청 스레드의 print가 이 버퍼로 빨려 들어간다. 프로세스
        # 분리가 유일하게 안전하고, CLI가 실제로 찍는 출력과도 정확히 같다.
        import os
        import subprocess
        import sys
        cfg_path = getattr(self, "config_path", None)
        cmd = [sys.executable, "-m", "xgen_maker", "doctor"]
        if cfg_path:
            cmd += ["--config", str(cfg_path)]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=300, env=env,
                                 cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        except subprocess.TimeoutExpired:
            return {"ok": False, "output": "[진단 시간초과] 5분 안에 끝나지 않음"}
        except OSError as e:
            return {"ok": False, "output": f"[진단 실행불가] {e}"}
        out = (res.stdout or "") + (("\n" + res.stderr) if res.stderr.strip() else "")
        return {"ok": res.returncode == 0, "output": out or "결과가 없습니다"}

    def _ui_dirs(self):
        from pathlib import Path
        base = Path(self.config.kg_path).parent
        return base / "ui-baselines", base / "ui-snaps"

    @staticmethod
    def _ui_slug(url: str) -> str:
        # 사람이 읽을 부분 + URL 해시 — 해시가 없으면 a/b 와 a-b 가 같은 기준선을 덮어쓴다
        import re
        import hashlib
        s = re.sub(r"^https?://", "", url).strip("/")
        base = re.sub(r"[^a-zA-Z0-9]+", "_", s)[:60] or "root"
        return f"{base}_{hashlib.sha1(url.encode('utf-8')).hexdigest()[:8]}"

    @staticmethod
    def _ui_slug_legacy(url: str) -> str:
        # 해시 도입 이전 형식 — 이미 저장된 기준선이 조용히 고아가 되지 않게 읽기 폴백용
        import re
        s = re.sub(r"^https?://", "", url).strip("/")
        return re.sub(r"[^a-zA-Z0-9]+", "_", s)[:80] or "root"

    def _ui_baseline_path(self, baseline_dir, url: str, slug: str):
        """기준선 경로 — 새 형식 우선, 없으면 옛 형식(해시 없는 파일)도 인정."""
        new = baseline_dir / f"{slug}.png"
        if new.exists():
            return new
        legacy = baseline_dir / f"{self._ui_slug_legacy(url)}.png"
        return legacy if legacy.exists() else new

    def _ui_status(self) -> dict:
        from pathlib import Path
        from .loop.verify import http_reachable
        base = getattr(self.config, "preview_base", "")
        try:
            from PIL import Image  # noqa: F401
            pillow = True
        except ImportError:
            pillow = False
        import shutil
        playwright = bool(shutil.which("npx"))
        baseline_dir, _ = self._ui_dirs()
        baselines = []
        if baseline_dir.is_dir():
            for p in sorted(baseline_dir.glob("*.png")):
                baselines.append({"slug": p.stem, "url": f"/api/ui-image?f=kg/ui-baselines/{p.name}"})
        shots = self._ui_images()
        return {"preview_base": base, "reachable": http_reachable(base, timeout=5) if base else False,
                "pillow": pillow, "playwright": playwright,
                "baselines": baselines, "images": shots,
                # 예전 이름 — 화면이 갱신되기 전에도 깨지지 않게 남겨 둔다
                "recent": [s for s in shots if s["kind"] == "diff"]}

    def _ui_images(self, needle: str = "") -> list[dict]:
        """캡처된 화면 전부. 세션 수나 장수로 자르지 않는다 — 찾으려고 보는 화면이다.

        기준(baseline)·캡처(snapshot)·차이(diff)를 한 목록으로 모아, 어느 작업의
        어느 화면인지와 언제 찍었는지를 함께 준다. 그래야 검색이 가능하다.
        """
        from pathlib import Path
        import datetime
        found: list[dict] = []

        def add(path: Path, kind: str, route: str, session: str) -> None:
            try:
                stat = path.stat()
            except OSError:
                return
            rel = path.relative_to(Path(self.config.kg_path).parent.parent).as_posix()
            found.append({
                "kind": kind, "route": route, "session": session,
                "name": path.name, "bytes": stat.st_size,
                "when": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "url": f"/api/ui-image?f={rel}",
            })

        baseline_dir, snap_dir = self._ui_dirs()
        for directory, kind in ((baseline_dir, "baseline"), (snap_dir, "snapshot")):
            if directory.is_dir():
                for p in sorted(directory.glob("*.png")):
                    add(p, kind, p.stem, "")
        wl = Path(self.config.worklogs_dir)
        if wl.is_dir():
            for sd in sorted([p for p in wl.iterdir() if p.is_dir()], reverse=True):
                for p in sorted(sd.glob("*.png")):
                    stem = p.stem
                    kind = "diff" if stem.startswith("diff_") else "snapshot"
                    route = stem[5:] if kind == "diff" else stem
                    add(p, kind, route, sd.name)

        found.sort(key=lambda s: s["when"], reverse=True)
        if needle:
            low = needle.lower()
            found = [s for s in found
                     if low in f"{s['route']} {s['session']} {s['name']} {s['kind']}".lower()]
        return found

    def _ui_snap(self, qs: dict) -> dict:
        # 임의 URL 스냅샷 → (baseline=1이면 기준 저장) → 기준 있으면 픽셀 diff(R11·R23).
        from pathlib import Path
        from .loop.verify import playwright_snapshot, http_reachable
        from .loop.ui_verify import pixel_diff
        url = qs.get("url", [""])[0].strip()
        if not url:
            return {"ok": False, "error": "주소를 입력하세요"}
        if not url.startswith(("http://", "https://")):
            return {"ok": False, "error": "http 또는 https 주소만 사용할 수 있습니다"}
        # 임의 URL 캡처는 이 기능의 목적(로컬 스택 주소가 제각각)이라 allowlist를 못 건다.
        # 다만 링크로컬/클라우드 메타데이터는 화면검증에 쓸 일이 없고, 무인증 포트에
        # 닿은 사람에게 자격증명을 스크린샷으로 넘겨줄 수 있어 막는다.
        if _is_link_local(url):
            return {"ok": False, "error": "이 주소는 캡처할 수 없습니다"}
        if not http_reachable(url, timeout=6):
            return {"ok": False, "error": f"{url} 에 연결할 수 없습니다"}
        baseline_dir, snap_dir = self._ui_dirs()
        snap_dir.mkdir(parents=True, exist_ok=True)
        baseline_dir.mkdir(parents=True, exist_ok=True)
        slug = self._ui_slug(url)
        snap = snap_dir / f"{slug}.png"
        res = playwright_snapshot(url, snap, timeout=120, wait_ms=1500)
        if not res.get("ok"):
            return {"ok": False, "error": "캡처에 실패했습니다: " + res.get("reason", "")[-200:]}
        out = {"ok": True, "slug": slug,
               "snap_url": f"/api/ui-image?f=kg/ui-snaps/{slug}.png", "bytes": res.get("bytes")}
        # 읽을 땐 옛 형식(해시 없는 파일)도 인정 — 슬러그 형식이 바뀌어도 저장된 기준선이 안 죽게
        baseline = self._ui_baseline_path(baseline_dir, url, slug)
        if qs.get("baseline", ["0"])[0] == "1":
            import shutil
            baseline = baseline_dir / f"{slug}.png"  # 저장은 항상 새 형식으로
            shutil.copyfile(snap, baseline)
            out["baseline_saved"] = True
            out["baseline_url"] = f"/api/ui-image?f=kg/ui-baselines/{slug}.png"
        elif baseline.exists():
            d = pixel_diff(baseline, snap, snap_dir / f"diff_{slug}.png")
            if d.get("status") == "diff":
                out["diff"] = {"changed_ratio": d["changed_ratio"],
                               "diff_url": f"/api/ui-image?f=kg/ui-snaps/diff_{slug}.png"}
            else:
                out["diff"] = {"status": d.get("status"), "changed_ratio": d.get("changed_ratio", 0)}
            # 실제로 비교에 쓴 파일을 가리킨다(옛 형식 폴백일 수 있음)
            out["baseline_url"] = f"/api/ui-image?f=kg/ui-baselines/{baseline.name}"
        return out

    def _serve_image(self, relpath: str) -> None:
        # kg 디렉토리·worklogs 안의 PNG만 서빙(경로 탈출 차단).
        from pathlib import Path
        kg_root = Path(self.config.kg_path).parent.resolve()
        wl_root = Path(self.config.worklogs_dir).resolve()
        target = (kg_root.parent / relpath).resolve()
        # is_relative_to — startswith면 kg-secrets 같은 형제 디렉토리가 jail을 빠져나간다
        if not (target.is_relative_to(kg_root) or target.is_relative_to(wl_root)):
            self._json({"error": "허용되지 않은 경로"}, status=403); return
        if not target.is_file() or target.suffix.lower() != ".png":
            self._json({"error": "이미지 없음"}, status=404); return
        data = target.read_bytes()
        self._response_started = True
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _annotate(self, qs: dict) -> dict:
        # 그래프 노드 편집을 overlay에 영속 + 살아있는 그래프에 즉시 반영(R8).
        from pathlib import Path
        from .kg.overlay import annotate
        node = qs.get("node", [""])[0]
        if not node or node not in self.graph.nodes:
            return {"ok": False, "error": "항목을 찾을 수 없습니다"}
        kw = {}
        if "note" in qs:
            kw["note"] = qs["note"][0]
        if "summary" in qs:
            kw["summary"] = qs["summary"][0]
        if "deprecated" in qs:
            kw["deprecated"] = qs["deprecated"][0] == "1"
        if not kw:
            return {"ok": False, "error": "변경할 내용이 없습니다"}
        overlay_path = Path(self.config.kg_path).parent / "overlay.json"
        edits = annotate(overlay_path, node, **kw)  # overlay가 정본 — 이건 항상 남는다
        # 살아있는 그래프에 즉시 반영(재빌드/새로고침 없이 바로 보이게).
        # 위 membership 검사와 여기 사이에 sync가 노드를 지울 수 있으므로 다시 집는다
        # (없어져도 overlay엔 남아 다음 로드에서 재적용되니 편집은 유실되지 않는다).
        live = self.graph.nodes.get(node)
        if live is not None:
            meta = live.setdefault("meta", {})
            for k, v in kw.items():
                meta[k] = v
            if "summary" in kw:
                meta["summary_src"] = "human"
        return {"ok": True, "edits": edits, "overlay": str(overlay_path)}

    def _undo(self, qs: dict) -> dict:
        # 되돌리기 — MAKER가 만든 로컬 브랜치 삭제(가드: 보호/허용밖 브랜치 거부).
        # 원격 삭제는 remote=1 명시해야만(외부 반영). confirm=1 없으면 미리보기만.
        from .loop.rollback import action_from_session, undo
        sid = qs.get("id", [""])[0]
        act = action_from_session(self.config.worklogs_dir, sid)
        if not act:
            return {"ok": False, "errors": ["되돌릴 변경 사항이 없는 작업입니다"]}
        if qs.get("confirm", ["0"])[0] != "1":
            return {"ok": True, "preview": True, "action": act,
                    "note": "confirm=1로 실제 실행. 원격까지 지우려면 remote=1."}
        remote = qs.get("remote", ["0"])[0] == "1"
        return undo(self.config, act, delete_remote=remote)

    def _route(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._html(_PAGE)
        elif parsed.path == "/favicon.ico":
            self._response_started = True
            self.send_response(204)  # 인라인 파비콘 사용 — 별도 파일 없음(404 방지)
            self.end_headers()
        elif parsed.path == "/api/info":
            self._json(self._graph_info())
        elif parsed.path == "/api/graph":
            self._json(self._graph_status())
        elif parsed.path == "/api/repo-graph":
            self._json(self._repo_graph())
        elif parsed.path == "/api/subgraph":
            sq = parse_qs(parsed.query)
            q = sq.get("q", [""])[0].strip()
            repo = sq.get("repo", [""])[0].strip()
            try:
                n = int(sq.get("n", ["160"])[0])
            except ValueError:
                n = 160
            # repo 지정이면 그 레포 내부 그래프(드릴다운 2단계)
            self._json(self._repo_subgraph(repo, n) if repo else self._subgraph(q, n))
        elif parsed.path == "/api/annotate":
            self._json(self._annotate(parse_qs(parsed.query)))
        elif parsed.path == "/api/node-code":
            self._json(self._node_code(parse_qs(parsed.query).get("id", [""])[0]))
        elif parsed.path == "/api/ui-images":
            self._json({"images": self._ui_images(parse_qs(parsed.query).get("q", [""])[0].strip())})
        elif parsed.path == "/api/ui-status":
            self._json(self._ui_status())
        elif parsed.path == "/api/ui-snap":
            self._json(self._ui_snap(parse_qs(parsed.query)))
        elif parsed.path == "/api/ui-image":
            self._serve_image(parse_qs(parsed.query).get("f", [""])[0])
        elif parsed.path == "/api/graph-health":
            self._json(self._graph_health())
        elif parsed.path == "/api/pipeline":
            self._json(self._pipeline_view())
        elif parsed.path == "/api/setting":
            self._json(self._set_setting(parse_qs(parsed.query)))
        elif parsed.path == "/api/auth":
            self._json(self._auth_info())
        elif parsed.path == "/api/auth-check":
            self._json(self._auth_check())
        elif parsed.path == "/api/doctor":
            self._json(self._doctor())
        elif parsed.path == "/api/sync-stream":
            self._sse_sync(parse_qs(parsed.query))
        elif parsed.path == "/api/sync":
            # 그래프 최신화 — git 변경분만 재추출(CLI maker kg sync와 동일 로직)
            # pull=1이면 원격까지 안전 최신화(fetch + 조건 만족 시에만 fast-forward)
            from .kg.sync import sync_all
            from .kg.enrich import enrich_deterministic
            do_pull = parse_qs(parsed.query).get("pull", ["0"])[0] == "1"
            if do_pull:
                from .kg.refresh import refresh_all
                try:
                    with _GRAPH_LOCK:
                        r = refresh_all(self.config, graph=self.graph)
                        MakerWebHandler._adj_ver = None
                    self._json({"ok": True, "changed": r["changed"], "nodes": r["nodes"],
                                "pulled": True, "updated_repos": r["updated_repos"],
                                "not_on_latest": r["not_on_latest"],
                                "per_repo": [{"repo": s.get("repo"), "changed": s.get("changed", 0),
                                              "action": s.get("action")} for s in r["sync"]]})
                except Exception as error:  # noqa: BLE001
                    self._json({"ok": False, "error": str(error)[:200]})
                return
            try:
                with _GRAPH_LOCK:  # 실행 중인 루프의 그래프 갱신/저장과 겹치지 않게
                    results = sync_all(self.graph)
                    # sync는 그래프를 제자리에서 바꾼다 — 노드/엣지 수가 우연히 같아도
                    # 내용은 달라질 수 있으므로 인접 캐시를 무조건 버린다.
                    MakerWebHandler._adj_ver = None
                    total = sum(r.get("changed", 0) for r in results)
                    if total or any(r.get("action") for r in results):
                        enrich_deterministic(self.graph)
                        self.graph.save(self.config.kg_path)
                        # CLI kg sync와 동일 — 사람 편집(overlay)을 재적용해 유실 방지
                        from .kg.overlay import load_overlay, apply_overlay
                        from pathlib import Path as _Path
                        overlay = load_overlay(_Path(self.config.kg_path).parent / "overlay.json")
                        if overlay["node_overrides"] or overlay["custom_edges"]:
                            apply_overlay(self.graph, overlay)
                            self.graph.save(self.config.kg_path)
                    payload = {"ok": True, "changed": total,
                               "nodes": len(self.graph.nodes),
                               "per_repo": [{"repo": r.get("repo"), "changed": r.get("changed", 0),
                                             "action": r.get("action")} for r in results]}
                self._json(payload)
            except Exception as error:  # noqa: BLE001
                self._json({"ok": False, "error": str(error)[:200]})
        elif parsed.path == "/api/run":
            self._sse_run(parse_qs(parsed.query))
        elif parsed.path == "/api/stop":
            rid = parse_qs(parsed.query).get("id", [""])[0]
            ev = MakerWebHandler._cancels.get(rid)
            if ev:
                ev.set()
                self._json({"ok": True})
            else:
                self._json({"ok": False, "error": "실행 중인 작업이 없습니다"})
        elif parsed.path == "/api/history":
            from .loop.history import read_sessions
            qs = parse_qs(parsed.query)
            try:
                limit = int(qs.get("limit", ["30"])[0])
            except ValueError:
                limit = 30
            sessions = read_sessions(self.config.worklogs_dir, limit)
            needle = qs.get("q", [""])[0].strip().lower()
            if needle:      # 세션이 쌓이면 목록만으로는 못 찾는다
                sessions = [s for s in sessions
                            if needle in (s.get("query", "") + " " + s.get("branch", "")).lower()]
            self._json({"sessions": sessions})
        elif parsed.path == "/api/local-branches":
            from .loop.branches import list_local
            self._json({"branches": list_local(self.config)})
        elif parsed.path == "/api/branch-delete":
            # 지우면 되돌릴 수 없다. confirm=1 없이는 무엇을 지우는지만 알려준다.
            from .loop.branches import list_local, delete_local
            qs = parse_qs(parsed.query)
            repo, name = qs.get("repo", [""])[0], qs.get("name", [""])[0]
            row = next((b for b in list_local(self.config)
                        if b["repo"] == repo and b["name"] == name), None)
            if row is None:
                self._json({"ok": False, "error": "그런 브랜치가 없습니다"})
            elif qs.get("confirm", ["0"])[0] != "1":
                self._json({"ok": True, "preview": True, **row})
            else:
                self._json(delete_local(self.config, repo, name))
        elif parsed.path == "/api/draft":
            from .loop.history import session_path
            sid = parse_qs(parsed.query).get("id", [""])[0]
            target = session_path(self.config.worklogs_dir, sid)
            draft = (target / "MR-DRAFT.md") if target else None
            if draft is None or not draft.is_file():
                self._json({"ok": False, "error": "이 작업에는 초안이 없습니다"})
            else:
                self._json({"ok": True, "session": sid,
                            "markdown": draft.read_text(encoding="utf-8")})
        elif parsed.path == "/api/session-delete":
            # 지우는 건 되돌릴 수 없다. confirm=1 없으면 무엇이 지워지는지만 알려준다.
            from .loop.history import read_session_detail, session_path, delete_session
            qs = parse_qs(parsed.query)
            sid = qs.get("id", [""])[0]
            if session_path(self.config.worklogs_dir, sid) is None:
                self._json({"ok": False, "error": "세션을 찾을 수 없습니다"})
            elif qs.get("confirm", ["0"])[0] != "1":
                detail = read_session_detail(self.config.worklogs_dir, sid) or {}
                self._json({"ok": True, "preview": True, "session": sid,
                            "query": detail.get("query", ""), "branch": detail.get("branch", ""),
                            "note": "기록만 지웁니다 — 코드와 브랜치는 그대로 남습니다"})
            else:
                self._json(delete_session(self.config.worklogs_dir, sid))
        elif parsed.path == "/api/tests":
            from .loop.history import read_test_runs
            self._json({"runs": read_test_runs(self.config.worklogs_dir, 40)})
        elif parsed.path == "/api/activity":
            from .loop.gitlab_observe import activity
            qs = parse_qs(parsed.query)
            repo = qs.get("repo", [resolve_default_repo(self.config)])[0]
            self._json(activity(self.config, repo, qs.get("q", [""])[0], 30))
        elif parsed.path == "/api/session":
            from .loop.history import read_session_detail
            from .loop.rollback import action_from_session
            sid = parse_qs(parsed.query).get("id", [""])[0]
            detail = read_session_detail(self.config.worklogs_dir, sid)
            if detail is None:
                self._json({"error": "세션 없음"}, status=404)
            else:
                act = action_from_session(self.config.worklogs_dir, sid)
                detail["undoable"] = bool(act)
                detail["pushed"] = bool(act and act.get("pushed"))
                self._json(detail)
        elif parsed.path == "/api/undo":
            self._json(self._undo(parse_qs(parsed.query)))
        elif parsed.path == "/api/learnings":
            from .loop.learnings import _all
            from pathlib import Path as _P
            ld = _P(self.config.learnings_dir)
            entries = []
            if ld.is_dir():
                for f in ld.glob("*.jsonl"):
                    entries += _all(ld, f.stem)
            entries.sort(key=lambda e: e.get("ts", ""), reverse=True)
            self._json({"learnings": entries[:40]})
        elif parsed.path == "/api/mrs":
            from .loop.gitlab_observe import my_mrs, maker_mrs, team_mrs
            self._json({"mine": my_mrs(self.config, "all", 15),
                        "maker": maker_mrs(self.config, 15),
                        "team": team_mrs(self.config, "all", 30)})
        elif parsed.path == "/api/status":
            from .loop import jenkins, argocd
            from .loop.release import ladder
            self._json({"ladder": ladder(self.config),
                        "jenkins": jenkins.list_jobs() if jenkins.available() else None,
                        "argocd": argocd.list_apps() if argocd.available() else None})
        elif parsed.path == "/api/release":
            from .loop.release import release_view
            repo = parse_qs(parsed.query).get("repo", [resolve_default_repo(self.config)])[0]
            self._json(release_view(self.graph, repo, self.config.target_branch, self.config)
                       if repo else {"error": "repo 미지정"})
        elif parsed.path == "/api/branches":
            from .loop.gitlab_observe import branches
            repo = parse_qs(parsed.query).get("repo", [resolve_default_repo(self.config)])[0]
            self._json(branches(self.config, repo) if repo else {"error": "repo 미지정"})
        elif parsed.path == "/api/diagnostics":
            self._json(self._diagnostics())
        else:
            self.send_error(404)

    # 진단은 register()(레지스트리 변형)·contract_probe()(샌드박스 서브프로세스)를 타므로
    # GET마다 재실행하지 않고 클래스 레벨에서 1회 계산 후 캐시(설정 불변 가정).
    _diag_cache = None

    def _diagnostics(self) -> dict:
        if MakerWebHandler._diag_cache is not None:
            return MakerWebHandler._diag_cache
        from .sdk_check import installed_versions, contract_probe, maker_catalog
        from .engine_stage import register, _load_engine
        from .loop.converge import HAS_HARNESS
        eng = _load_engine()
        MakerWebHandler._diag_cache = {
            "sdk": {"installed": installed_versions(), "contract": contract_probe()},
            "engine": register(),
            "engine_levelb": eng is not None and all(
                hasattr(eng, n) for n in
                ("EventEmitter", "InMemorySessionStore", "PipelineState", "save_session")),
            "catalog": maker_catalog(),
            "verification": {
                "sandbox_isolated": bool(HAS_HARNESS),
                "strict_regression": bool(getattr(self.config, "strict_regression", False))},
            "git_author": {"name": self.config.git_author_name,
                           "email_set": bool(self.config.git_author_email)}}
        return MakerWebHandler._diag_cache

    def _html(self, body: str):
        data = body.encode("utf-8")
        self._response_started = True
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._response_started = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _sse_sync(self, params):
        """그래프 최신화를 진행 상황과 함께 흘린다. 실행과 같은 방식으로 중지도 받는다."""
        import uuid
        from .kg.refresh import refresh_all
        do_pull = params.get("pull", ["0"])[0] == "1"
        self._response_started = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        q: queue.Queue = queue.Queue()
        run_id = uuid.uuid4().hex[:12]
        cancel = threading.Event()
        MakerWebHandler._cancels[run_id] = cancel
        q.put({"type": "run_id", "id": run_id})

        def work():
            try:
                with _GRAPH_LOCK:      # 실행 중인 루프의 그래프 갱신과 겹치지 않게
                    result = refresh_all(
                        self.config, graph=self.graph,
                        on_progress=lambda step, info: q.put({"type": "event", "step": step, **info}),
                        should_cancel=cancel.is_set) if do_pull else self._sync_only()
                    MakerWebHandler._adj_ver = None
                q.put({"type": "result", "report": _sync_summary(result, do_pull),
                       "cancelled": cancel.is_set()})
            except Exception as error:  # noqa: BLE001
                q.put({"type": "error", "message": str(error)[:200]})
            finally:
                MakerWebHandler._cancels.pop(run_id, None)
                q.put(None)

        threading.Thread(target=work, daemon=True).start()
        try:
            while True:
                try:
                    # 오래 걸리는 단계(LLM 호출 등)에는 이벤트가 없다. 그동안 아무것도
                    # 안 보내면 연결이 끊기고, EventSource가 자동 재연결하면서 같은
                    # 작업을 처음부터 다시 돌린다(한 번 눌렀는데 세션이 6개 생겼다).
                    item = q.get(timeout=10)
                except queue.Empty:
                    self.wfile.write(_SSE_KEEPALIVE)
                    self.wfile.flush()
                    continue
                if item is None:
                    break
                try:
                    payload = json.dumps(item, ensure_ascii=False, default=str)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    cancel.set()
                    break
        finally:
            MakerWebHandler._cancels.pop(run_id, None)

    def _sync_only(self) -> dict:
        """원격을 받지 않고 로컬 변경만 그래프에 반영."""
        from .kg.sync import sync_all
        from .kg.enrich import enrich_deterministic
        results = sync_all(self.graph)
        changed = sum(r.get("changed", 0) for r in results)
        if changed or any(r.get("action") for r in results):
            enrich_deterministic(self.graph)
            self.graph.save(self.config.kg_path)
        return {"sync": results, "changed": changed, "nodes": len(self.graph.nodes),
                "pulls": [], "updated_repos": [], "not_on_latest": []}

    def _sse_run(self, params):
        query = (params.get("q", [""])[0]).strip()
        mode = params.get("mode", ["plan"])[0]
        if not query:
            self.send_error(400)
            return
        # 모드 화이트리스트 — 없으면 'plan' 정확일치만 읽기전용이고 오타·미지의 값이
        # 전부 allow_write=True로 새어 실제 레포에 브랜치·커밋이 나간다(fail-open).
        if mode not in _RUN_MODES:
            self.send_error(400, "unknown mode")
            return
        # 모드별 config 복제
        cfg = MakerConfig(**{f.name: getattr(self.config, f.name)
                             for f in self.config.__dataclass_fields__.values()})  # type: ignore[attr-defined]
        cfg.verbose = False
        cfg.mode = mode                      # 설정 파일 값이 남아 사유가 어긋나지 않게
        cfg.allow_write = mode != "plan"
        self._response_started = True  # 이후 예외는 do_GET에서 2차 응답 금지
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        q: queue.Queue = queue.Queue()
        import uuid
        run_id = uuid.uuid4().hex[:12]
        cancel = threading.Event()
        MakerWebHandler._cancels[run_id] = cancel
        q.put({"type": "run_id", "id": run_id})  # 클라이언트가 중지 시 쓸 id
        threading.Thread(target=_run_query, args=(cfg, self.graph, query, q, cancel),
                         daemon=True).start()
        try:
            while True:
                try:
                    # 오래 걸리는 단계(LLM 호출 등)에는 이벤트가 없다. 그동안 아무것도
                    # 안 보내면 연결이 끊기고, EventSource가 자동 재연결하면서 같은
                    # 작업을 처음부터 다시 돌린다(한 번 눌렀는데 세션이 6개 생겼다).
                    item = q.get(timeout=10)
                except queue.Empty:
                    self.wfile.write(_SSE_KEEPALIVE)
                    self.wfile.flush()
                    continue
                if item is None:
                    break
                try:
                    self.wfile.write(f"data: {json.dumps(item, ensure_ascii=False, default=str)}\n\n"
                                     .encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    cancel.set()  # 클라이언트 연결 끊김 → 루프도 중단 신호
                    break
        finally:
            MakerWebHandler._cancels.pop(run_id, None)


# ""(빈 호스트)는 ThreadingHTTPServer가 0.0.0.0으로 바인드하므로 loopback이 아니다.
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def serve(config_path: str | None, host: str = "127.0.0.1", port: int = 8760) -> None:
    # 무인증 노출 가드 — 이 대시보드는 인증이 없어 포트 접근자가 운영자 신원으로 act(push/MR) 가능.
    # 비-loopback 바인드는 신뢰망에서 명시 동의(env)해야만 허용.
    if host not in _LOOPBACK_HOSTS and os.environ.get("XGEN_MAKER_WEB_ALLOW_REMOTE") != "1":
        raise SystemExit(
            f"거부: 웹 UI를 비-loopback 호스트({host})로 여는 것은 무인증 노출입니다.\n"
            "  이 대시보드엔 인증이 없어, 포트에 닿는 누구나 운영자의 저장된 GitLab 신원으로\n"
            "  act(push/MR)를 일으킬 수 있습니다. 로컬 전용이면 --host 127.0.0.1(기본)을 쓰고,\n"
            "  원격이 꼭 필요하면 신뢰망에서만 XGEN_MAKER_WEB_ALLOW_REMOTE=1 로 명시 동의하세요.")
    config = MakerConfig.from_file(config_path) if config_path else MakerConfig()
    graph = Graph.load(config.kg_path)
    _apply_overlay_for(config.kg_path, graph)
    MakerWebHandler.config = config
    MakerWebHandler.config_path = config_path
    MakerWebHandler.graph = graph
    for attr, target in (("_kg_stamp", config.kg_path), ("_cfg_stamp", config_path)):
        try:
            st = os.stat(target)                       # 기동 시점을 기준으로 잡아둔다
            setattr(MakerWebHandler, attr, (st.st_mtime_ns, st.st_size))
        except (OSError, TypeError):
            pass
    server = ThreadingHTTPServer((host, port), MakerWebHandler)
    print(f"⚒ XGEN MAKER 웹 UI → http://{host}:{port}")
    print(f"  KG {len(graph.nodes):,} 노드 로드됨. 브라우저에서 쿼리를 치세요. (Ctrl+C 종료)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료.")
        server.shutdown()
