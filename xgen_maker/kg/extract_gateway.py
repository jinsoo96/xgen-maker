"""API 게이트웨이 라우팅 테이블 추출 — 프론트 호출이 어느 백엔드로 가는지.

게이트웨이 코드 자체는 catch-all 프록시(`/:service/*tail`) 하나뿐이라, 코드만 읽어서는
"이 API가 어디로 가는가"를 답할 수 없다. 답은 설정 파일의 모듈→서비스 매핑에 있다.

  base_path: /api
  services:
    some-service:
      host: http://some-host:8000
      modules: [admin, auth, ...]        # /api/admin/** → some-host

이걸 `gateway_route` 노드로 만들고 두 방향으로 잇는다.
  api_call ──routes_via──> gateway_route ──handled_by──> (호스트명과 같은 이름의 repo 노드)

호스트명↔레포명 매칭은 이름이 같을 때만 한다. 조직·서비스 이름을 소스에 박지 않기 위해
매핑표를 두지 않고, 그래프에 이미 있는 repo 노드와 대조만 한다.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from .graph import Graph

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

# 환경별 변형(services.docker.yaml 등)보다 기본 파일을 우선한다
_CANDIDATES = ("config/services.yaml", "config/services.yml",
               "services.yaml", "services.yml")


def find_services_file(repo_root: str | Path) -> Path | None:
    root = Path(repo_root)
    for rel in _CANDIDATES:
        path = root / rel
        if path.is_file():
            return path
    return None


def _host_name(host: str) -> str:
    """`http://some-host:8000` → `some-host`. 포트·스킴 없이 이름만."""
    parsed = urlparse(host if "//" in host else f"//{host}")
    return (parsed.hostname or host).strip()


def extract_gateway_routes(graph: Graph, repo: str, repo_root: str | Path) -> int:
    """게이트웨이 레포에서 모듈→서비스 매핑을 읽어 gateway_route 노드를 만든다."""
    if not HAS_YAML:
        return 0
    path = find_services_file(repo_root)
    if path is None:
        return 0
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return 0
    if not isinstance(data, dict) or not isinstance(data.get("services"), dict):
        return 0

    rel = path.relative_to(Path(repo_root)).as_posix()
    base = str(data.get("base_path") or "").rstrip("/")
    created = 0
    for service, spec in data["services"].items():
        if not isinstance(spec, dict):
            continue
        host = _host_name(str(spec.get("host") or ""))
        for module in spec.get("modules") or []:
            module = str(module).strip().strip("/")
            if not module:
                continue
            prefix = f"{base}/{module}"
            node_id = f"{repo}:gwroute:{prefix}"
            graph.add_node(node_id, "gateway_route", prefix, repo, rel,
                           service=service, host=host, module=module, prefix=prefix)
            graph.add_edge(f"{repo}:{rel}", node_id, "contains") \
                if f"{repo}:{rel}" in graph.nodes else graph.add_edge(repo, node_id, "contains")
            created += 1
    return created


def link_gateway_routes(graph: Graph) -> dict:
    """gateway_route를 양쪽으로 잇는다 — 뒤로는 백엔드 레포, 앞으로는 프론트 호출.

    호출 매칭은 가장 긴 접두사 하나만 고른다(`/api/a`와 `/api/a/b`가 둘 다 있을 때
    구체적인 쪽이 이긴다). 못 찾으면 잇지 않는다 — 틀린 연결이 없는 것보다 나쁘다.
    """
    routes = graph.nodes_by_kind("gateway_route")
    if not routes:
        return {"serves": 0, "calls": 0}

    repo_ids = {n["id"] for n in graph.nodes_by_kind("repo")}
    serves = 0
    for route in routes:
        host = route["meta"].get("host", "")
        if host and host in repo_ids:              # 컨테이너명과 레포명이 같을 때만
            graph.add_edge(route["id"], host, "handled_by")
            serves += 1

    ordered = sorted(routes, key=lambda n: len(n["meta"].get("prefix", "")), reverse=True)
    calls = 0
    for call in graph.nodes_by_kind("api_call"):
        norm = call["meta"].get("norm_path", "")
        if not norm:
            continue
        for route in ordered:
            prefix = route["meta"].get("prefix", "")
            if prefix and (norm == prefix or norm.startswith(prefix + "/")):
                graph.add_edge(call["id"], route["id"], "routes_via")
                calls += 1
                break
    return {"serves": serves, "calls": calls}
