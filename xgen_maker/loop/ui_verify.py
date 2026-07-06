"""UI/UX 검증 — 업계 두 패턴 합성 (실사례 조사 반영).

1) 결정론 비주얼 회귀 (BackstopJS/Playwright toHaveScreenshot 패턴):
   baseline 스냅샷 ↔ 현재 스냅샷 픽셀 diff(Pillow) → 변경률·diff 이미지.
2) 에이전트 비전 판정 (Visual Feedback Loop / VF-Coder 패턴):
   스크린샷 → 비전 LLM → {renders_ok, issues} → 수렴 루프 신호.

라우트 매핑: 변경 파일 → 그 파일에 (역)import로 닿는 Next.js route → URL.
스택은 develop 스냅샷이라 fix브랜치 화면은 P2(브랜치 마운트) — 현 스택 상태의
깨진 렌더/에러/공백은 지금도 잡는다.
"""
from __future__ import annotations

from pathlib import Path

from .. import llm
from .verify import playwright_snapshot, http_reachable


def affected_routes(graph, changed_files: list[str], repo: str,
                    max_routes: int = 3) -> list[dict]:
    """변경 파일에 import로 닿는 route 노드를 역추적. 반환 [{route, url_path}]."""
    changed_ids = {f"{repo}:{f}" for f in changed_files}
    # 역 import 인덱스: dst가 바뀌면 src가 영향
    rev: dict[str, set[str]] = {}
    for edge in graph.edges:
        if edge["kind"] in ("imports", "contains"):
            rev.setdefault(edge["dst"], set()).add(edge["src"])
        elif edge["kind"] == "same_package":  # 스코프 간 feature 연결(양방향)
            rev.setdefault(edge["dst"], set()).add(edge["src"])
    # 변경에서 BFS로 위로 타고 올라가며 route_of 페이지에 닿는지
    visited = set(changed_ids)
    frontier = list(changed_ids)
    reached_files = set(changed_ids)
    for _ in range(4):
        nxt = []
        for node in frontier:
            for parent in rev.get(node, ()):
                if parent not in visited:
                    visited.add(parent)
                    nxt.append(parent)
                    reached_files.add(parent)
        frontier = nxt
    routes = []
    for route in graph.nodes_by_kind("route"):
        page_ids = {e["dst"] for e in graph.edges
                    if e["kind"] == "route_of" and e["src"] == route["id"]}
        # route의 페이지 파일이나 그 페이지가 닿는 파일이 변경분과 겹치면 영향
        if page_ids & reached_files or any(p in visited for p in page_ids):
            routes.append({"route": route["name"], "id": route["id"]})
        if len(routes) >= max_routes:
            break
    return routes


def pixel_diff(baseline: Path, current: Path, out_diff: Path,
               threshold: int = 30) -> dict:
    """Pillow 픽셀 diff. 반환 {changed_ratio, diff_png}. 크기 다르면 리사이즈 비교."""
    try:
        from PIL import Image, ImageChops
    except ImportError:
        return {"status": "skipped", "reason": "Pillow 미설치"}
    if not baseline.exists() or not current.exists():
        return {"status": "skipped", "reason": "baseline/current 없음"}
    a = Image.open(baseline).convert("RGB")
    b = Image.open(current).convert("RGB")
    if a.size != b.size:
        b = b.resize(a.size)
    diff = ImageChops.difference(a, b)
    bbox = diff.getbbox()
    if bbox is None:
        return {"status": "identical", "changed_ratio": 0.0}
    # 임계 초과 픽셀 비율
    gray = diff.convert("L")
    hist = gray.histogram()
    changed = sum(hist[threshold:])
    total = a.size[0] * a.size[1]
    ratio = round(changed / total, 4)
    out_diff.parent.mkdir(parents=True, exist_ok=True)
    diff.save(out_diff)
    return {"status": "diff", "changed_ratio": ratio, "diff_png": str(out_diff),
            "bbox": bbox}


def _url(base: str, route_path: str) -> str:
    # 동적 세그먼트([id])는 스킵 대상 — 정적 라우트만 안전
    return base.rstrip("/") + (route_path if route_path != "/" else "")


def ui_verify(config, graph, changed_files: list[str], repo: str,
              session_dir: Path, vision: bool = True) -> dict:
    """영향 라우트별로 스냅샷 + (baseline 있으면)픽셀diff + (키 있으면)비전판정."""
    base = getattr(config, "preview_base", "")
    if not base:
        return {"skipped": True, "reason": "preview_base 미설정"}
    if not http_reachable(base, timeout=6):
        return {"skipped": True, "reason": f"{base} 미도달(스택 미기동)"}
    routes = affected_routes(graph, changed_files, repo)
    if not routes:
        return {"skipped": True, "reason": "영향 라우트 없음(비UI 변경)"}

    baseline_dir = Path(config.kg_path).parent / "ui-baselines"
    results = []
    for route in routes:
        rp = route["route"]
        if "[" in rp:  # 동적 라우트 스킵(파라미터 필요)
            continue
        slug = rp.strip("/").replace("/", "_") or "root"
        current = session_dir / f"ui_{slug}.png"
        snap = playwright_snapshot(_url(base, rp), current)
        entry = {"route": rp, "snapshot": snap}
        if snap.get("ok"):
            baseline = baseline_dir / f"{slug}.png"
            if baseline.exists():
                entry["pixel_diff"] = pixel_diff(baseline, current,
                                                 session_dir / f"diff_{slug}.png")
            if vision:
                v = llm.vision_judge(
                    str(current),
                    f"이 화면(라우트 {rp})이 정상 렌더되는가? 깨진 레이아웃·에러·공백·"
                    f"로딩멈춤이 있으면 issues에 적어라.")
                if v is not None:
                    entry["vision"] = v
        results.append(entry)

    problems = [r for r in results
                if (r.get("vision") and not r["vision"].get("renders_ok"))
                or (r.get("pixel_diff", {}).get("changed_ratio", 0) > 0.05)]
    return {"skipped": False, "routes": len(results), "results": results,
            "problems": len(problems)}
