"""KG 대시보드 — 외부 의존 0의 단일 HTML 생성 (오프라인/폐쇄망 동작).

canvas 포스 레이아웃 + 검색 + 노드 상세 패널 + kind 범례.
노드가 cap을 넘으면 우선순위(route/endpoint/api_call/file 순, degree 내림차순)로 표시 제한.
"""
from __future__ import annotations

import json
from pathlib import Path

from .graph import Graph

_KIND_PRIORITY = {"repo": 0, "deploy_project": 0, "helm_app": 1, "helm_chart": 1,
                  "route": 1, "endpoint": 2, "api_call": 3,
                  "file": 4, "class": 5, "function": 6, "feature": 4}

_HTML = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<title>XGEN MAKER — Knowledge Graph</title>
<style>
 body{margin:0;font:13px/1.4 'Segoe UI',sans-serif;background:#111827;color:#e5e7eb;display:flex;height:100vh;overflow:hidden}
 #side{width:340px;min-width:340px;border-right:1px solid #374151;padding:12px;overflow-y:auto;background:#0b1220}
 #canvasWrap{flex:1;position:relative}
 canvas{display:block;cursor:grab}
 h1{font-size:15px;margin:0 0 8px}
 input{width:100%;box-sizing:border-box;padding:6px 8px;background:#1f2937;border:1px solid #374151;color:#e5e7eb;border-radius:6px}
 .hit,.nb{padding:4px 6px;border-radius:4px;cursor:pointer;margin:2px 0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .hit:hover,.nb:hover{background:#1f2937}
 .kind{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px}
 #detail{margin-top:10px;border-top:1px solid #374151;padding-top:8px;font-size:12px}
 #detail b{color:#fbbf24}
 .summary{color:#d1d5db;margin:6px 0;padding:6px 8px;background:#1f2937;border-radius:4px;line-height:1.5;white-space:pre-wrap}
 .src{font-size:10px;padding:1px 5px;border-radius:3px;margin-left:6px}
 .src-human{background:#7c3aed} .src-llm{background:#1e3a8a} .src-deterministic{background:#374151}
 .badge{font-size:10px;padding:1px 5px;border-radius:3px;background:#991b1b;margin-left:6px}
 .meta{color:#9ca3af;word-break:break-all}
 #legend{position:absolute;right:10px;top:10px;background:#0b1220cc;padding:8px 10px;border-radius:8px;font-size:12px}
 #stats{color:#9ca3af;font-size:11px;margin:6px 0}
</style></head><body>
<div id="side">
 <h1>XGEN MAKER · KG</h1>
 <div id="stats"></div>
 <input id="q" placeholder="검색 (이름/경로)…">
 <div id="hits"></div>
 <div id="detail">노드를 클릭하면 상세가 표시됩니다.</div>
</div>
<div id="canvasWrap"><canvas id="cv"></canvas><div id="legend"></div></div>
<script>
const GRAPH = __GRAPH_JSON__;
const COLORS = {repo:"#f472b6",route:"#34d399",endpoint:"#60a5fa",api_call:"#fbbf24",
                file:"#9ca3af",class:"#c084fc",function:"#6b7280",feature:"#34d399",
                deploy_project:"#ef4444",helm_app:"#f97316",helm_chart:"#facc15"};
const nodes = GRAPH.nodes, edges = GRAPH.edges;
const byId = {}; nodes.forEach(n=>byId[n.id]=n);
const deg = {}; edges.forEach(e=>{deg[e.src]=(deg[e.src]||0)+1;deg[e.dst]=(deg[e.dst]||0)+1;});
nodes.forEach((n,i)=>{n.x=Math.cos(i*2.4)*(120+8*Math.sqrt(i));n.y=Math.sin(i*2.4)*(120+8*Math.sqrt(i));n.vx=0;n.vy=0;n.r=4+Math.min(10,Math.sqrt(deg[n.id]||0));});
const adj = edges.map(e=>[byId[e.src],byId[e.dst],e.kind]).filter(p=>p[0]&&p[1]);

const cv=document.getElementById('cv'),ctx=cv.getContext('2d');
let W,H,tx=0,ty=0,scale=1,sel=null,hover=null,dragN=null,panning=false,px=0,py=0;
function resize(){W=cv.width=cv.parentElement.clientWidth;H=cv.height=cv.parentElement.clientHeight;}
window.addEventListener('resize',resize);resize();

let alpha=1;
function tick(){
 if(alpha>0.02){
  for(let i=0;i<nodes.length;i++){const a=nodes[i];
   for(let j=i+1;j<nodes.length;j++){const b=nodes[j];
    let dx=a.x-b.x,dy=a.y-b.y,d2=dx*dx+dy*dy;if(d2<1)d2=1;
    if(d2<40000){const f=900/d2*alpha;const d=Math.sqrt(d2);dx/=d;dy/=d;
     a.vx+=dx*f;a.vy+=dy*f;b.vx-=dx*f;b.vy-=dy*f;}}}
  adj.forEach(([a,b])=>{const dx=b.x-a.x,dy=b.y-a.y,d=Math.sqrt(dx*dx+dy*dy)||1;
   const f=(d-60)*0.02*alpha;a.vx+=dx/d*f;a.vy+=dy/d*f;b.vx-=dx/d*f;b.vy-=dy/d*f;});
  nodes.forEach(n=>{n.vx-=n.x*0.0015*alpha;n.vy-=n.y*0.0015*alpha;
   n.x+=n.vx;n.y+=n.vy;n.vx*=0.85;n.vy*=0.85;});
  alpha*=0.995;}
 draw();requestAnimationFrame(tick);}

function draw(){
 ctx.clearRect(0,0,W,H);ctx.save();
 ctx.translate(W/2+tx,H/2+ty);ctx.scale(scale,scale);
 ctx.lineWidth=0.6/scale;
 adj.forEach(([a,b,k])=>{
  ctx.strokeStyle=k==='resolves_to'?'#fbbf2466':(k==='route_of'?'#34d39955':'#4b556333');
  ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke();});
 nodes.forEach(n=>{
  ctx.fillStyle=COLORS[n.kind]||'#888';
  ctx.beginPath();ctx.arc(n.x,n.y,n.r/Math.sqrt(scale),0,7);ctx.fill();
  if(n===sel||n===hover){ctx.strokeStyle='#fff';ctx.lineWidth=1.5/scale;ctx.stroke();}});
 if(sel||hover){const n=sel||hover;ctx.fillStyle='#fff';ctx.font=`${12/scale}px sans-serif`;
  ctx.fillText(n.name,n.x+n.r+3,n.y+3);}
 ctx.restore();}

function pick(mx,my){const x=(mx-W/2-tx)/scale,y=(my-H/2-ty)/scale;
 let best=null,bd=100;nodes.forEach(n=>{const dx=n.x-x,dy=n.y-y,d=dx*dx+dy*dy;
  if(d<bd&&d<(n.r+4)*(n.r+4)){bd=d;best=n;}});return best;}
cv.addEventListener('mousedown',e=>{const n=pick(e.offsetX,e.offsetY);
 if(n){dragN=n;}else{panning=true;}px=e.offsetX;py=e.offsetY;});
window.addEventListener('mouseup',()=>{dragN=null;panning=false;});
cv.addEventListener('mousemove',e=>{
 if(dragN){dragN.x+=(e.offsetX-px)/scale;dragN.y+=(e.offsetY-py)/scale;dragN.vx=0;dragN.vy=0;alpha=Math.max(alpha,0.1);}
 else if(panning){tx+=e.offsetX-px;ty+=e.offsetY-py;}
 else{hover=pick(e.offsetX,e.offsetY);}
 px=e.offsetX;py=e.offsetY;});
cv.addEventListener('wheel',e=>{e.preventDefault();scale*=e.deltaY<0?1.15:0.87;scale=Math.max(0.05,Math.min(8,scale));});
cv.addEventListener('click',e=>{const n=pick(e.offsetX,e.offsetY);if(n)select(n);});

function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function select(n){sel=n;tx=-n.x*scale;ty=-n.y*scale;
 const nbs=[];adj.forEach(([a,b,k])=>{if(a===n)nbs.push(['→ '+k,b]);else if(b===n)nbs.push(['← '+k,a]);});
 const m=n.meta||{};
 const summary=m.summary?`<div class="summary">${esc(m.summary)}<span class="src src-${m.summary_src||'deterministic'}">${m.summary_src||'det'}</span></div>`:'';
 const dep=m.deprecated?`<span class="badge">deprecated</span>`:'';
 const note=m.note?`<div class="meta">📝 ${esc(m.note)}</div>`:'';
 const rest=Object.fromEntries(Object.entries(m).filter(([k])=>!['summary','summary_src','note','deprecated','doc'].includes(k)));
 document.getElementById('detail').innerHTML=
  `<b>${esc(n.name)}</b> <span class="meta">(${n.kind})</span>${dep}<br>`+
  `<div class="meta">${esc(n.repo)} · ${esc(n.path)}${n.line?':'+n.line:''}</div>`+
  summary+note+
  (Object.keys(rest).length?`<div class="meta">${esc(JSON.stringify(rest))}</div>`:'')+
  `<div style="margin-top:6px"><b>연결 ${nbs.length}</b></div>`+
  nbs.slice(0,40).map(([lab,m],i)=>`<div class="nb" data-nb="${nodes.indexOf(m)}"><span class="kind" style="background:${COLORS[m.kind]||'#888'}"></span>${esc(lab)} ${esc(m.name)}</div>`).join('');
 document.querySelectorAll('.nb').forEach(el=>el.onclick=()=>select(nodes[+el.dataset.nb]));}

document.getElementById('q').addEventListener('input',e=>{
 const q=e.target.value.toLowerCase();const hits=document.getElementById('hits');
 if(q.length<2){hits.innerHTML='';return;}
 const found=nodes.filter(n=>n.name.toLowerCase().includes(q)||n.path.toLowerCase().includes(q)).slice(0,30);
 hits.innerHTML=found.map((n,i)=>`<div class="hit" data-n="${nodes.indexOf(n)}"><span class="kind" style="background:${COLORS[n.kind]||'#888'}"></span>${esc(n.name)} <span class="meta">${esc(n.repo)}</span></div>`).join('');
 document.querySelectorAll('.hit').forEach(el=>el.onclick=()=>select(nodes[+el.dataset.n]));});

document.getElementById('stats').textContent=
 `${GRAPH.total_nodes} nodes (표시 ${nodes.length}) · ${GRAPH.total_edges} edges · repos: ${GRAPH.repos.join(', ')}`;
document.getElementById('legend').innerHTML=Object.entries(COLORS)
 .map(([k,c])=>`<div><span class="kind" style="background:${c}"></span>${k}</div>`).join('');
tick();
</script></body></html>
"""


def render_dashboard(graph: Graph, out_path: str | Path, max_nodes: int = 1200) -> Path:
    degree: dict[str, int] = {}
    for edge in graph.edges:
        degree[edge["src"]] = degree.get(edge["src"], 0) + 1
        degree[edge["dst"]] = degree.get(edge["dst"], 0) + 1

    ranked = sorted(graph.nodes.values(),
                    key=lambda n: (_KIND_PRIORITY.get(n["kind"], 9), -degree.get(n["id"], 0)))
    shown = ranked[:max_nodes]
    shown_ids = {n["id"] for n in shown}
    shown_edges = [e for e in graph.edges if e["src"] in shown_ids and e["dst"] in shown_ids]

    payload = {"nodes": shown, "edges": shown_edges,
               "total_nodes": len(graph.nodes), "total_edges": len(graph.edges),
               "repos": sorted({n["repo"] for n in graph.nodes.values()})}
    html = _HTML.replace("__GRAPH_JSON__", json.dumps(payload, ensure_ascii=False))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
