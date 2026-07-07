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

from .config import MakerConfig
from .kg.graph import Graph


_PAGE = """<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<title>XGEN MAKER</title><style>
 /* 디자인 토큰 — CocoRoF/Geny 다크 라벤더 팔레트 차용 (muted 그라디언트·soft glow, 눈 안 아프게) */
 :root{--radius:10px;--radius-lg:16px;--t-fast:120ms ease;--t:200ms ease;
  --primary:#8573b8;--primary-hover:#74639f;--primary-subtle:rgba(141,121,201,.13);
  --success:#4ade80;--warning:#fbbf24;--danger:#f47171;
  --bg:#1a1726;--bg2:#1f1b2d;--bg3:#28233a;--card:#1f1b2d;--hover:#28233a;
  --text:#efecf6;--text2:#aca6bf;--muted:#797292;--border:#2f2942;--border2:#3c3553;
  --grad:linear-gradient(135deg,#6f64a6 0%,#897ab4 100%);--grad-text:linear-gradient(118deg,#8b7cbe,#9b8dc8 55%,#ab9ed2);
  --hero:radial-gradient(120% 130% at 82% -10%,rgba(133,115,184,.09),rgba(133,115,184,.028) 36%,transparent 64%);
  --glow:0 0 16px rgba(133,115,184,.14);--shadow:0 4px 16px rgba(0,0,0,.5)}
 *{box-sizing:border-box} body{margin:0;font:14px/1.5 'Segoe UI',sans-serif;background:var(--bg);background-image:var(--hero);color:var(--text);height:100vh;display:flex;flex-direction:column}
 header{padding:12px 20px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:16px}
 header b{font-size:16px;font-weight:700;background:var(--grad-text);-webkit-background-clip:text;background-clip:text;color:transparent}
 header .info{color:var(--text2);font-size:12px} header .mode{margin-left:auto;font-size:12px;color:var(--muted)}
 #sync{padding:6px 12px;background:var(--bg3);border:1px solid var(--border2);color:var(--text);border-radius:var(--radius);cursor:pointer;font-size:12px;transition:all var(--t-fast)}
 #sync:hover{border-color:var(--primary);color:var(--primary)} #sync:disabled{opacity:.5} #sync.spin{color:var(--primary)}
 nav{display:flex;gap:4px;padding:0 20px;border-bottom:1px solid var(--border);background:var(--bg2)}
 nav button{padding:10px 16px;background:none;border:none;border-bottom:2px solid transparent;color:var(--text2);cursor:pointer;font-size:13px;transition:color var(--t-fast)}
 nav button:hover{color:var(--text)} nav button.on{color:var(--text);border-bottom-color:var(--primary)}
 .tab{flex:1;overflow-y:auto;padding:16px 20px;display:none} .tab.on{display:block}
 #log{font-family:Consolas,monospace;font-size:13px;min-height:200px}
 .ev{padding:3px 0;border-bottom:1px solid var(--border);white-space:pre-wrap;word-break:break-all}
 .ok{color:var(--success)} .fail{color:var(--danger)} .info{color:var(--text2)} .step{color:var(--primary);font-weight:600}
 .result{margin-top:12px;padding:12px 14px;background:var(--card);border-radius:var(--radius);border-left:3px solid var(--primary);box-shadow:var(--glow)}
 form{display:flex;gap:10px;padding:14px 20px;border-top:1px solid var(--border);background:var(--bg2)}
 input[type=text]{flex:1;padding:11px 14px;background:var(--bg3);border:1px solid var(--border2);color:var(--text);border-radius:var(--radius);font-size:14px;transition:border var(--t-fast)}
 input[type=text]:focus{outline:none;border-color:var(--primary);box-shadow:0 0 0 3px var(--primary-subtle)}
 select{padding:11px 12px;background:var(--bg3);border:1px solid var(--border2);color:var(--text);border-radius:var(--radius);cursor:pointer}
 button.act{padding:11px 20px;background:var(--grad);border:none;color:#fff;border-radius:var(--radius);cursor:pointer;font-weight:600;box-shadow:var(--glow);transition:transform var(--t-fast)}
 button.act:hover{transform:translateY(-1px)} button.act:disabled{opacity:.5;transform:none}
 table{width:100%;border-collapse:collapse;font-size:13px} th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--border)}
 th{color:var(--text2);font-weight:600} tr:hover td{background:var(--hover)}
 .badge{padding:2px 8px;border-radius:6px;font-size:11px;font-weight:500} .merged{background:#2f5741;color:#8ff0b8}
 .opened{background:#3a3564;color:#c9b8ff} .closed{background:#3c3553;color:var(--text2)}
 .Synced{background:#2f5741;color:#8ff0b8} .OutOfSync{background:#5c4326;color:#f0c88a} .Healthy{color:var(--success)}
 .pitfall{background:#5c2b2b;color:#f0a0a0} .fix{background:#2f5741;color:#8ff0b8} .convention{background:#3a3564;color:#c9b8ff} .note{background:#3c3553;color:var(--text2)}
 a{color:#a898da;text-decoration:none} a:hover{text-decoration:underline}
 h3{margin:16px 0 8px;font-size:14px;background:var(--grad-text);-webkit-background-clip:text;background-clip:text;color:transparent} .muted{color:var(--muted)}
</style></head><body>
<header><b>⚒ XGEN MAKER</b><span class="info">CLI(maker run) = 이 대시보드. 같은 엔진·같은 로그·같은 결과.</span>
 <span class="mode" id="mode"></span>
 <button id="sync" title="지식그래프를 최신 코드로 갱신(변경분만)">⟳ Sync</button></header>
<nav>
 <button class="on" data-t="run">실행</button>
 <button data-t="history">작업 이력</button>
 <button data-t="learn">학습</button>
 <button data-t="mrs">MR</button>
 <button data-t="branches">브랜치</button>
 <button data-t="deploy">배포 상태</button>
 <button data-t="diag">진단</button>
</nav>
<div class="tab on" id="tab-run">
 <div id="log"><div class="ev info">쿼리를 입력하면 MAKER 루프가 돕니다. 진행 로그가 실시간으로 흐르고, 결과가 아래에 뜹니다.</div></div>
</div>
<div class="tab" id="tab-history"><div class="muted">불러오는 중…</div></div>
<div class="tab" id="tab-learn"><div class="muted">불러오는 중…</div></div>
<div class="tab" id="tab-mrs"><div class="muted">불러오는 중…</div></div>
<div class="tab" id="tab-branches"><div class="muted">불러오는 중…</div></div>
<div class="tab" id="tab-deploy"><div class="muted">불러오는 중…</div></div>
<div class="tab" id="tab-diag"><div class="muted">불러오는 중…</div></div>
<form id="f"><input type="text" id="q" placeholder="예: 온톨로지 그래프 재빌드 후 안 바뀌는 버그 고쳐줘" autofocus>
 <select id="m"><option value="plan">plan (분석만)</option><option value="observe">observe (브랜치+MR초안)</option><option value="act">act (push+MR)</option></select>
 <button class="act" id="go">실행</button></form>
<script>
const log=document.getElementById('log'), q=document.getElementById('q'), go=document.getElementById('go');
const esc=s=>String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function line(cls,txt){const d=document.createElement('div');d.className='ev '+cls;d.textContent=txt;log.appendChild(d);log.scrollTop=log.scrollHeight;}
function refreshInfo(){fetch('/api/info').then(r=>r.json()).then(d=>{window.__repos=d.repo_names||[];document.getElementById('mode').textContent=d.nodes.toLocaleString()+' 노드 · '+d.repos+' 레포';});}
refreshInfo();
// Sync 버튼 — 그래프 최신화(변경분만)
const syncBtn=document.getElementById('sync');
syncBtn.onclick=()=>{
 syncBtn.disabled=true; syncBtn.classList.add('spin'); const old=syncBtn.textContent; syncBtn.textContent='⟳ 동기화중…';
 fetch('/api/sync').then(r=>r.json()).then(d=>{
  if(d.ok){ syncBtn.textContent=d.changed>0?('✓ '+d.changed+'파일 갱신'):'✓ 최신'; refreshInfo();
   line('info','⟳ Sync: '+(d.changed>0?d.changed+'개 파일 반영':'변경 없음(최신)')+' · '+d.nodes.toLocaleString()+'노드'); }
  else line('fail','✗ Sync 실패: '+d.error);
  setTimeout(()=>{syncBtn.textContent=old; syncBtn.classList.remove('spin'); syncBtn.disabled=false;},2500);
 }).catch(e=>{line('fail','✗ Sync 오류'); syncBtn.textContent=old; syncBtn.classList.remove('spin'); syncBtn.disabled=false;});
};
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
 if(t==='learn') fetch('/api/learnings').then(r=>r.json()).then(d=>{
  let h='<h3>작업 학습 메모리 <span class=muted>(하네스가 다음 작업 시 참고 — 실수 방지)</span></h3>';
  if(!d.learnings.length){h+='<div class=muted>아직 없음. 작업이 쌓이면 실패/성공 교훈이 자동 기록됩니다.</div>';}
  else h+='<table><tr><th>종류</th><th>레포</th><th>영역</th><th>교훈</th></tr>'+
   d.learnings.map(e=>`<tr><td><span class="badge ${esc(e.kind)}">${esc(e.kind)}</span></td><td class=muted>${esc(e.repo)}</td><td class=muted>${esc(e.area)}</td><td>${esc(e.note)}</td></tr>`).join('')+'</table>';
  el.innerHTML=h;});
 if(t==='mrs') fetch('/api/mrs').then(r=>r.json()).then(d=>{
  const row=m=>`<tr><td>!${m.iid}</td><td><span class="badge ${m.state}">${m.state}</span></td><td>${esc(m.source)}→${esc(m.target)}</td><td>${esc(m.title).slice(0,50)}</td><td><a href="${esc(m.url)}" target=_blank>열기</a></td></tr>`;
  el.innerHTML='<h3>MAKER가 만든 MR</h3><table><tr><th>#</th><th>상태</th><th>브랜치</th><th>제목</th><th></th></tr>'+(d.maker.map(row).join('')||'<tr><td colspan=5 class=muted>없음</td></tr>')+'</table>'+
   '<h3>내 MR (전체)</h3><table><tr><th>#</th><th>상태</th><th>브랜치</th><th>제목</th><th></th></tr>'+d.mine.map(row).join('')+'</table>';});
 if(t==='branches'){
  const names=(window.__repos||[]);
  const sel='<label>레포 <select id="brepo">'+(names.length?names.map(n=>`<option>${esc(n)}</option>`).join(''):'<option value="">(config에 gitlab_projects 없음)</option>')+'</select></label>';
  el.innerHTML='<h3>브랜치 / 릴리즈 <span class=muted>(내가·MAKER가 만든 작업 브랜치 + 승격 경로)</span></h3>'+sel+'<div id="bbody" class=muted style="margin-top:12px">불러오는 중…</div>';
  const load=()=>{const repo=document.getElementById('brepo').value; const bb=document.getElementById('bbody');
   if(!repo){bb.innerHTML='<div class=muted>config에 gitlab_projects 매핑이 필요합니다.</div>';return;}
   bb.textContent='불러오는 중…';
   Promise.all([fetch('/api/branches?repo='+encodeURIComponent(repo)).then(r=>r.json()),
                fetch('/api/release?repo='+encodeURIComponent(repo)).then(r=>r.json())]).then(([b,rel])=>{
    if(b.error){bb.innerHTML='<div class=muted>브랜치 조회 실패: '+esc(b.error)+' (.env에 GitLab 토큰/프로젝트 매핑 필요)</div>';return;}
    const brow=x=>`<tr><td>${esc(x.name)}</td><td class=muted>${esc(x.author||'')}</td><td class=muted>${esc((x.when||'').slice(0,10))}</td></tr>`;
    let h='<h4>작업 브랜치(최근)</h4><table><tr><th>브랜치</th><th>작성자</th><th>날짜</th></tr>'+((b.work_recent||[]).map(brow).join('')||'<tr><td colspan=3 class=muted>없음</td></tr>')+'</table>';
    h+='<h4>보호 브랜치</h4><div class=muted>'+((b.protected||[]).map(esc).join(', ')||'-')+'</div>';
    if(rel && rel.lands_on_env){h+='<h4 style="margin-top:14px">릴리즈 승격</h4><div>대상 env: <b>'+esc(rel.lands_on_env)+'</b> · 남은 승격: '+esc((rel.promotion_remaining||[]).join(' → ')||'없음')+'</div>';}
    bb.innerHTML=h;
   });};
  document.getElementById('brepo').onchange=load; load();
  return;
 }
 if(t==='diag') fetch('/api/diagnostics').then(r=>r.json()).then(d=>{
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
  el.innerHTML=h;});
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
                                      "affected", "nodes", "sha", "draft", "url", "reason",
                                      "error", "promotion", "target", "count", "next_manual",
                                      "n", "phase", "files", "sandbox", "decision", "regression")},
                            ensure_ascii=False, default=str)[:180]
        self._q.put({"type": "event", "step": step, "status": status, "detail": detail})

    def close(self, outcome):
        return self._real.close(outcome)


def _run_query(config: MakerConfig, graph: Graph, query: str, q: queue.Queue) -> None:
    from .loop.pipeline import MakerLoop
    from .loop.journal import Journal
    try:
        # journal 팩토리 주입 — 전역 몽키패치 없이 이 요청만 SSE로 스트리밍(동시 요청 안전)
        def factory(worklogs_dir, qtext, verbose=False):
            return _SSEJournal(Journal(worklogs_dir, qtext, verbose=False), q)
        loop = MakerLoop(config, graph=graph, journal_factory=factory)
        report = loop.run(query)
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
            names = sorted((self.config.gitlab_projects or {}).keys()
                           or (self.config.repos or {}).keys()
                           or {n["repo"] for n in self.graph.nodes.values()})
            self._json({"nodes": len(self.graph.nodes),
                        "repos": len({n["repo"] for n in self.graph.nodes.values()}),
                        "repo_names": names})
        elif parsed.path == "/api/sync":
            # 그래프 최신화 — git 변경분만 재추출(CLI maker kg sync와 동일 로직)
            from .kg.sync import sync_all
            from .kg.enrich import enrich_deterministic
            try:
                results = sync_all(self.graph)
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
                self._json({"ok": True, "changed": total,
                            "nodes": len(self.graph.nodes),
                            "per_repo": [{"repo": r.get("repo"), "changed": r.get("changed", 0),
                                          "action": r.get("action")} for r in results]})
            except Exception as error:  # noqa: BLE001
                self._json({"ok": False, "error": str(error)[:200]})
        elif parsed.path == "/api/run":
            self._sse_run(parse_qs(parsed.query))
        elif parsed.path == "/api/history":
            from .loop.history import read_sessions
            self._json({"sessions": read_sessions(self.config.worklogs_dir, 30)})
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
            names = sorted((self.config.gitlab_projects or {}).keys())
            default = names[0] if names else ""
            repo = parse_qs(parsed.query).get("repo", [default])[0]
            self._json(branches(self.config, repo) if repo else {"error": "repo 미지정"})
        elif parsed.path == "/api/diagnostics":
            # 읽기전용 자가진단 — SDK 계약/드리프트 + 엔진 stage 등록 상태(로컬만, 네트워크 X)
            from .sdk_check import installed_versions, contract_probe, maker_catalog
            from .engine_stage import register, _load_engine
            from .loop.converge import HAS_HARNESS
            eng = _load_engine()
            self._json({
                "sdk": {"installed": installed_versions(), "contract": contract_probe()},
                "engine": register(),
                "engine_levelb": eng is not None and all(
                    hasattr(eng, n) for n in
                    ("EventEmitter", "InMemorySessionStore", "PipelineState", "save_session")),
                "catalog": maker_catalog(),
                "verification": {
                    "sandbox_isolated": bool(HAS_HARNESS),  # [harness] 있어야 진짜 격리
                    "strict_regression": bool(getattr(self.config, "strict_regression", False))},
                "git_author": {"name": self.config.git_author_name,
                               "email_set": bool(self.config.git_author_email)}})
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


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", ""}


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
