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
 header{padding:10px 18px;border-bottom:1px solid #1f2937;display:flex;align-items:center;gap:16px}
 header b{font-size:16px;color:#fbbf24} header .info{color:#9ca3af;font-size:12px}
 header .mode{margin-left:auto;font-size:12px;color:#9ca3af}
 nav{display:flex;gap:2px;padding:0 18px;border-bottom:1px solid #1f2937;background:#0f172a}
 nav button{padding:9px 16px;background:none;border:none;border-bottom:2px solid transparent;color:#9ca3af;cursor:pointer;font-size:13px}
 nav button.on{color:#fbbf24;border-bottom-color:#fbbf24}
 .tab{flex:1;overflow-y:auto;padding:14px 18px;display:none} .tab.on{display:block}
 #log{font-family:Consolas,monospace;font-size:13px;min-height:200px}
 .ev{padding:3px 0;border-bottom:1px solid #111827;white-space:pre-wrap;word-break:break-all}
 .ok{color:#34d399} .fail{color:#f87171} .info{color:#9ca3af} .step{color:#60a5fa;font-weight:600}
 .result{margin-top:10px;padding:10px;background:#1f2937;border-radius:8px;border-left:3px solid #fbbf24}
 form{display:flex;gap:8px;padding:14px 18px;border-top:1px solid #1f2937;background:#0f172a}
 input[type=text]{flex:1;padding:10px 12px;background:#1f2937;border:1px solid #374151;color:#e5e7eb;border-radius:8px;font-size:14px}
 select,button.act{padding:10px 14px;background:#374151;border:none;color:#e5e7eb;border-radius:8px;cursor:pointer}
 button.act{background:#2563eb;font-weight:600} button.act:disabled{opacity:.5}
 table{width:100%;border-collapse:collapse;font-size:13px} th,td{text-align:left;padding:6px 10px;border-bottom:1px solid #1f2937}
 th{color:#9ca3af;font-weight:600} tr:hover td{background:#111827}
 .badge{padding:1px 7px;border-radius:4px;font-size:11px} .merged{background:#065f46} .opened{background:#1e40af}
 .closed{background:#4b5563} .Synced{background:#065f46} .OutOfSync{background:#78350f} .Healthy{color:#34d399}
 a{color:#60a5fa} h3{color:#fbbf24;margin:14px 0 8px;font-size:14px} .muted{color:#6b7280}
</style></head><body>
<header><b>⚒ XGEN MAKER</b><span class="info">CLI(maker run) = 이 대시보드. 같은 엔진·같은 로그·같은 결과.</span>
 <span class="mode" id="mode"></span></header>
<nav>
 <button class="on" data-t="run">실행</button>
 <button data-t="history">작업 이력</button>
 <button data-t="mrs">MR</button>
 <button data-t="deploy">배포 상태</button>
</nav>
<div class="tab on" id="tab-run">
 <div id="log"><div class="ev info">쿼리를 입력하면 MAKER 루프가 돕니다. 진행 로그가 실시간으로 흐르고, 결과가 아래에 뜹니다.</div></div>
</div>
<div class="tab" id="tab-history"><div class="muted">불러오는 중…</div></div>
<div class="tab" id="tab-mrs"><div class="muted">불러오는 중…</div></div>
<div class="tab" id="tab-deploy"><div class="muted">불러오는 중…</div></div>
<form id="f"><input type="text" id="q" placeholder="예: 온톨로지 그래프 재빌드 후 안 바뀌는 버그 고쳐줘" autofocus>
 <select id="m"><option value="plan">plan (분석만)</option><option value="observe">observe (브랜치+MR초안)</option><option value="act">act (push+MR)</option></select>
 <button class="act" id="go">실행</button></form>
<script>
const log=document.getElementById('log'), q=document.getElementById('q'), go=document.getElementById('go');
const esc=s=>String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function line(cls,txt){const d=document.createElement('div');d.className='ev '+cls;d.textContent=txt;log.appendChild(d);log.scrollTop=log.scrollHeight;}
fetch('/api/info').then(r=>r.json()).then(d=>{document.getElementById('mode').textContent=d.nodes.toLocaleString()+' 노드 · '+d.repos+' 레포';});
// 탭 전환
const loaded={};
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{
 document.querySelectorAll('nav button').forEach(x=>x.classList.remove('on'));
 document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));
 b.classList.add('on'); document.getElementById('tab-'+b.dataset.t).classList.add('on');
 if(b.dataset.t!=='run' && !loaded[b.dataset.t]){loaded[b.dataset.t]=1; render(b.dataset.t);}
});
function render(t){
 const el=document.getElementById('tab-'+t);
 if(t==='history') fetch('/api/history').then(r=>r.json()).then(d=>{
  el.innerHTML='<h3>MAKER 작업 이력</h3><table><tr><th>결과</th><th>쿼리</th><th>브랜치</th><th>env</th><th>MR</th></tr>'+
   d.sessions.map(s=>`<tr><td><span class="badge ${s.outcome}">${esc(s.outcome)}</span></td><td>${esc(s.query).slice(0,60)}</td><td class="muted">${esc(s.branch)}</td><td>${esc(s.env)}</td><td>${s.mr?`<a href="${esc(s.mr)}" target=_blank>MR</a>`:''}</td></tr>`).join('')+'</table>';});
 if(t==='mrs') fetch('/api/mrs').then(r=>r.json()).then(d=>{
  const row=m=>`<tr><td>!${m.iid}</td><td><span class="badge ${m.state}">${m.state}</span></td><td>${esc(m.source)}→${esc(m.target)}</td><td>${esc(m.title).slice(0,50)}</td><td><a href="${esc(m.url)}" target=_blank>열기</a></td></tr>`;
  el.innerHTML='<h3>MAKER가 만든 MR</h3><table><tr><th>#</th><th>상태</th><th>브랜치</th><th>제목</th><th></th></tr>'+(d.maker.map(row).join('')||'<tr><td colspan=5 class=muted>없음</td></tr>')+'</table>'+
   '<h3>내 MR (전체)</h3><table><tr><th>#</th><th>상태</th><th>브랜치</th><th>제목</th><th></th></tr>'+d.mine.map(row).join('')+'</table>';});
 if(t==='deploy') fetch('/api/status').then(r=>r.json()).then(d=>{
  let h='<h3>릴리즈 사다리 (develop→stg→main)</h3><table><tr><th>브랜치</th><th>환경</th><th>URL</th><th>Jenkins</th></tr>'+
   d.ladder.map(s=>`<tr><td>${esc(s.branch)}</td><td>${esc(s.env)}</td><td><a href="${esc(s.url)}" target=_blank>${esc(s.url)}</a></td><td class=muted>${esc(s.jenkins)}</td></tr>`).join('')+'</table>';
  h+='<h3>Jenkins</h3>'+(d.jenkins?('<table><tr><th>job</th><th>env</th></tr>'+d.jenkins.map(j=>`<tr><td>${esc(j.name)}</td><td>${esc(j.env)}</td></tr>`).join('')+'</table>'):'<div class=muted>.env에 XGEN_MAKER_JENKINS_* 없음</div>');
  h+='<h3>ArgoCD 배포 상태 <span class=muted>(read-only — MAKER는 배포 안 함)</span></h3>'+(d.argocd?('<table><tr><th>app</th><th>sync</th><th>health</th></tr>'+d.argocd.map(a=>`<tr><td>${esc(a.name)}</td><td><span class="badge ${esc(a.sync)}">${esc(a.sync)}</span></td><td class="${esc(a.health)}">${esc(a.health)}</td></tr>`).join('')+'</table>'):'<div class=muted>.env에 XGEN_MAKER_ARGOCD_* 없음</div>');
  el.innerHTML=h;});
}
// 실행 (SSE)
document.getElementById('f').addEventListener('submit',e=>{
 e.preventDefault(); const query=q.value.trim(); if(!query)return;
 document.querySelector('nav button[data-t=run]').click();
 go.disabled=true; line('step','▶ '+query); q.value='';
 const es=new EventSource('/api/run?q='+encodeURIComponent(query)+'&mode='+document.getElementById('m').value);
 es.onmessage=ev=>{
  const e=JSON.parse(ev.data);
  if(e.type==='event'){const mark={ok:'✓',pass:'✓',fail:'✗',empty:'·',skipped:'·',observe:'◇',act:'◆'}[e.status]||'▸';
   line(e.status==='fail'?'fail':'ok', mark+' '+e.step.padEnd(14)+' '+e.status+(e.detail?'  '+e.detail:''));}
  else if(e.type==='result'){const r=e.report; let html='<b>결과: '+esc(r.outcome)+'</b>';
   if(r.branch)html+='<br>브랜치: '+esc(r.branch); if(r.iterations)html+=' · 수렴 '+r.iterations+'회';
   if(r.mr_draft)html+='<br>MR초안: '+esc(r.mr_draft); if(r.mr&&r.mr.url)html+='<br>MR: <a href="'+esc(r.mr.url)+'" target=_blank>'+esc(r.mr.url)+'</a>';
   if(r.answer)html+='<br>'+esc(r.answer).replace(/\\n/g,'<br>');
   const d=document.createElement('div');d.className='result';d.innerHTML=html;log.appendChild(d);log.scrollTop=log.scrollHeight;
   go.disabled=false; es.close(); loaded['history']=0;}  // 이력 갱신 유도
  else if(e.type==='error'){line('fail','✗ '+esc(e.message)); go.disabled=false; es.close();}
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
        elif parsed.path == "/api/history":
            from .loop.history import read_sessions
            self._json({"sessions": read_sessions(self.config.worklogs_dir, 30)})
        elif parsed.path == "/api/mrs":
            from .loop.gitlab_observe import my_mrs, maker_mrs
            self._json({"mine": my_mrs(self.config, "all", 15),
                        "maker": maker_mrs(self.config, 15)})
        elif parsed.path == "/api/status":
            from .loop import jenkins, argocd
            from .loop.release import ladder
            self._json({"ladder": ladder(self.config),
                        "jenkins": jenkins.list_jobs() if jenkins.available() else None,
                        "argocd": argocd.list_apps() if argocd.available() else None})
        elif parsed.path == "/api/release":
            from .loop.release import release_view
            repo = parse_qs(parsed.query).get("repo", ["xgen-core"])[0]
            self._json(release_view(self.graph, repo, self.config.target_branch, self.config))
        elif parsed.path == "/api/branches":
            from .loop.gitlab_observe import branches
            repo = parse_qs(parsed.query).get("repo", ["xgen-frontend-features"])[0]
            self._json(branches(self.config, repo))
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
