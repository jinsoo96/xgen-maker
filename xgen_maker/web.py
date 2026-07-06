"""웹 UI — CLI 대신 브라우저에서 쿼리 치면 MAKER 루프가 돈다.

의존성 0(stdlib http.server + SSE). 채팅 입력창 + 실시간 진행 로그(journal 이벤트 스트리밍) + 결과.
저널 이벤트를 SSE로 흘려 CLI의 verbose 로그를 그대로 웹에서 본다.
"""
from __future__ import annotations

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .config import MakerConfig
from .kg.graph import Graph


_PAGE = """<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<title>XGEN MAKER</title><style>
 *{box-sizing:border-box} body{margin:0;font:14px/1.5 'Segoe UI',sans-serif;background:#0b1220;color:#e5e7eb;height:100vh;display:flex;flex-direction:column}
 header{padding:12px 18px;border-bottom:1px solid #1f2937;display:flex;align-items:center;gap:12px}
 header b{font-size:16px;color:#fbbf24} header .mode{margin-left:auto;font-size:12px;color:#9ca3af}
 #log{flex:1;overflow-y:auto;padding:14px 18px;font-family:Consolas,monospace;font-size:13px}
 .ev{padding:3px 0;border-bottom:1px solid #111827;white-space:pre-wrap;word-break:break-all}
 .ok{color:#34d399} .fail{color:#f87171} .info{color:#9ca3af} .step{color:#60a5fa;font-weight:600}
 .result{margin-top:10px;padding:10px;background:#1f2937;border-radius:8px;border-left:3px solid #fbbf24}
 form{display:flex;gap:8px;padding:14px 18px;border-top:1px solid #1f2937;background:#0f172a}
 input[type=text]{flex:1;padding:10px 12px;background:#1f2937;border:1px solid #374151;color:#e5e7eb;border-radius:8px;font-size:14px}
 select,button{padding:10px 14px;background:#374151;border:none;color:#e5e7eb;border-radius:8px;cursor:pointer}
 button{background:#2563eb;font-weight:600} button:disabled{opacity:.5}
 a{color:#60a5fa}
</style></head><body>
<header><b>⚒ XGEN MAKER</b><span class="info">쿼리 하나로 개발 자동화 — 결과는 MR 준비까지</span>
 <span class="mode" id="mode"></span></header>
<div id="log"><div class="ev info">쿼리를 입력하면 MAKER 루프가 돕니다. 진행 로그가 실시간으로 흐릅니다.</div></div>
<form id="f"><input type="text" id="q" placeholder="예: 온톨로지 그래프 재빌드 후 안 바뀌는 버그 고쳐줘" autofocus>
 <select id="m"><option value="plan">plan (분석만)</option><option value="observe">observe (브랜치+MR초안)</option><option value="act">act (push+MR)</option></select>
 <button id="go">실행</button></form>
<script>
const log=document.getElementById('log'), q=document.getElementById('q'), go=document.getElementById('go');
function line(cls,txt){const d=document.createElement('div');d.className='ev '+cls;d.textContent=txt;log.appendChild(d);log.scrollTop=log.scrollHeight;return d;}
fetch('/api/info').then(r=>r.json()).then(d=>{document.getElementById('mode').textContent=d.nodes.toLocaleString()+' 노드 · '+d.repos+' 레포';});
document.getElementById('f').addEventListener('submit',e=>{
 e.preventDefault(); const query=q.value.trim(); if(!query)return;
 go.disabled=true; line('step','▶ '+query); q.value='';
 const es=new EventSource('/api/run?q='+encodeURIComponent(query)+'&mode='+document.getElementById('m').value);
 es.onmessage=ev=>{
  const e=JSON.parse(ev.data);
  if(e.type==='event'){const mark={ok:'✓',pass:'✓',fail:'✗',empty:'·',skipped:'·',observe:'◇',act:'◆'}[e.status]||'▸';
   line(e.status==='fail'?'fail':'ok', mark+' '+e.step.padEnd(14)+' '+e.status+(e.detail?'  '+e.detail:''));}
  else if(e.type==='result'){const r=e.report; let html='<b>결과: '+r.outcome+'</b>';
   if(r.branch)html+='<br>브랜치: '+r.branch; if(r.iterations)html+=' · 수렴 '+r.iterations+'회';
   if(r.mr_draft)html+='<br>MR초안: '+r.mr_draft; if(r.mr&&r.mr.url)html+='<br>MR: <a href="'+r.mr.url+'" target=_blank>'+r.mr.url+'</a>';
   if(r.answer)html+='<br>'+r.answer.replace(/</g,'&lt;').replace(/\\n/g,'<br>');
   const d=document.createElement('div');d.className='result';d.innerHTML=html;log.appendChild(d);log.scrollTop=log.scrollHeight;
   go.disabled=false; es.close();}
  else if(e.type==='error'){line('fail','✗ '+e.message); go.disabled=false; es.close();}
 };
 es.onerror=()=>{go.disabled=false; es.close();};
});
</script></body></html>"""


class _SSEJournal:
    """journal.event를 가로채 SSE 큐로 흘리는 래퍼."""
    def __init__(self, real, q: queue.Queue):
        self._real = real
        self._q = q
        self.dir = real.dir
        self.slug = real.slug

    def event(self, step, status, **data):
        self._real.event(step, status, **data)
        detail = json.dumps({k: v for k, v in data.items()
                             if k in ("hits", "branch", "score", "env", "keywords",
                                      "affected", "nodes", "sha", "draft", "url", "reason")},
                            ensure_ascii=False, default=str)[:180]
        self._q.put({"type": "event", "step": step, "status": status, "detail": detail})

    def close(self, outcome):
        return self._real.close(outcome)


def _run_query(config: MakerConfig, graph: Graph, query: str, q: queue.Queue) -> None:
    from .loop.pipeline import MakerLoop
    from .loop import pipeline as pl
    try:
        loop = MakerLoop(config, graph=graph)
        orig_journal = pl.Journal

        def wrapped(worklogs_dir, qtext, verbose=False):
            return _SSEJournal(orig_journal(worklogs_dir, qtext, verbose=False), q)
        pl.Journal = wrapped
        try:
            report = loop.run(query)
        finally:
            pl.Journal = orig_journal
        q.put({"type": "result", "report": report})
    except Exception as error:  # noqa: BLE001
        q.put({"type": "error", "message": str(error)[:300]})
    finally:
        q.put(None)


class MakerWebHandler(BaseHTTPRequestHandler):
    config: MakerConfig = None  # type: ignore[assignment]
    graph: Graph = None  # type: ignore[assignment]

    def log_message(self, *a):  # 조용히
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._html(_PAGE)
        elif parsed.path == "/api/info":
            self._json({"nodes": len(self.graph.nodes),
                        "repos": len({n["repo"] for n in self.graph.nodes.values()})})
        elif parsed.path == "/api/run":
            self._sse_run(parse_qs(parsed.query))
        else:
            self.send_error(404)

    def _html(self, body: str):
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _sse_run(self, params):
        query = (params.get("q", [""])[0]).strip()
        mode = params.get("mode", ["plan"])[0]
        if not query:
            self.send_error(400)
            return
        # 모드별 config 복제
        cfg = MakerConfig(**{f.name: getattr(self.config, f.name)
                             for f in self.config.__dataclass_fields__.values()})  # type: ignore[attr-defined]
        cfg.verbose = False
        if mode == "plan":
            cfg.allow_write = False
        else:
            cfg.allow_write = True
            cfg.mode = mode
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        q: queue.Queue = queue.Queue()
        threading.Thread(target=_run_query, args=(cfg, self.graph, query, q),
                         daemon=True).start()
        while True:
            item = q.get()
            if item is None:
                break
            try:
                self.wfile.write(f"data: {json.dumps(item, ensure_ascii=False, default=str)}\n\n"
                                 .encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                break


def serve(config_path: str | None, host: str = "127.0.0.1", port: int = 8760) -> None:
    config = MakerConfig.from_file(config_path) if config_path else MakerConfig()
    graph = Graph.load(config.kg_path)
    from .kg.overlay import load_overlay, apply_overlay
    from pathlib import Path
    overlay = load_overlay(Path(config.kg_path).parent / "overlay.json")
    if overlay["node_overrides"] or overlay["custom_edges"]:
        apply_overlay(graph, overlay)
    MakerWebHandler.config = config
    MakerWebHandler.graph = graph
    server = ThreadingHTTPServer((host, port), MakerWebHandler)
    print(f"⚒ XGEN MAKER 웹 UI → http://{host}:{port}")
    print(f"  KG {len(graph.nodes):,} 노드 로드됨. 브라우저에서 쿼리를 치세요. (Ctrl+C 종료)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료.")
        server.shutdown()
