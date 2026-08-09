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

def _is_routing_table(data) -> bool:
    """모듈→서비스 매핑처럼 생겼는가 — 파일 이름이 아니라 내용으로 판단한다."""
    if not isinstance(data, dict) or not isinstance(data.get("services"), dict):
        return False
    return any(isinstance(spec, dict) and ("modules" in spec or "host" in spec)
               for spec in data["services"].values())


def _walk_yaml(root: Path, skip: set):
    """yaml 파일을 훑되 무거운 디렉토리는 들어가지 않는다.

    rglob은 가지치기를 못 해서 node_modules 안까지 전부 걷고 나서야 거른다
    (프론트 저장소에서 33초). 들어가기 전에 잘라야 한다.
    """
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except (PermissionError, OSError):
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in skip and not entry.name.startswith("."):
                    stack.append(entry)
            elif entry.suffix in (".yaml", ".yml"):
                yield entry


def find_services_file(repo_root: str | Path) -> Path | None:
    """라우팅 표 파일을 찾는다.

    파일 이름을 목록으로 박으면(services.yaml, config/services.yml …) 그 목록에 없는
    이름으로 바뀌는 순간 조용히 못 찾는다. 이름 대신 구조로 판별한다.
    환경별 변형(services.docker.yaml 등)이 여럿이면 경로가 짧고 이른 것을 고른다.
    """
    if not HAS_YAML:
        return None
    from .build import SKIP_DIRS
    root = Path(repo_root)
    found: list[Path] = []
    for path in _walk_yaml(root, SKIP_DIRS):
        try:
            text = path.read_text(encoding="utf-8")
            if "services:" not in text:      # 파싱 전 값싼 거르기(찾는 구조의 최상위 키)
                continue
            if _is_routing_table(yaml.safe_load(text)):
                found.append(path)
        except (OSError, yaml.YAMLError, UnicodeDecodeError):
            continue
    if not found:
        return None
    # 환경 변형은 기본 이름에 접미사가 붙어 길어진다(services.yaml → services.docker.yaml).
    # 얕고 짧은 쪽이 기본형이다.
    return min(found, key=lambda p: (len(p.relative_to(root).parts), len(p.name), str(p)))


def _host_name(host: str) -> str:
    """`http://some-host:8000` → `some-host`. 포트·스킴 없이 이름만."""
    parsed = urlparse(host if "//" in host else f"//{host}")
    return (parsed.hostname or host).strip()


def extract_gateway_routes(graph: Graph, repo: str, repo_root: str | Path) -> int:
    """게이트웨이 레포에서 모듈→서비스 매핑을 읽어 gateway_route 노드를 만든다.

    인프라 저장소는 건드리지 않는다. 거기에도 배포용 사본(services.docker.yaml)이
    있어서 같은 라우팅표가 두 벌 생긴다 — 게다가 빌드(extract_infra)는 그걸 안 만들고
    sync만 만들어, 동기화 한 번에 그래프가 달라졌다. 빌드와 sync는 같은 그래프를
    내야 한다. 실측: 중복 라우트 27개가 들어가면 검색이 깎인다(R@10 0.845 → 0.834).
    정본은 관문 저장소의 config/services.yaml이다.
    """
    if not HAS_YAML:
        return 0
    node = graph.nodes.get(repo)
    if node is not None and (node.get("meta") or {}).get("plane") == "infra":
        return 0
    path = find_services_file(repo_root)
    if path is None:
        return 0
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return 0
    if not _is_routing_table(data):
        return 0

    rel = path.relative_to(Path(repo_root)).as_posix()
    base = str(data.get("base_path") or "").rstrip("/")
    # 이 설정 파일 자체가 좌표여야 한다. 라우트 노드만 만들면 "어느 모듈이 어디로
    # 가는지 적힌 그 파일을 고쳐라"라는 요청이 착지할 곳이 없다 — 실측: 이 파일을
    # 고친 MR 네 건이 검색에서 통째로 안 잡혔다. 다른 추출기는 모두 파일 노드를 만든다.
    # 내용(서비스·모듈 이름)을 요약에 담아, 무엇이 등록돼 있는지로도 찾을 수 있게 한다.
    file_id = f"{repo}:{rel}"
    services = [str(s) for s in data["services"] if isinstance(s, str)]
    modules: list[str] = []
    for spec in data["services"].values():
        if isinstance(spec, dict):
            modules += [str(m).strip().strip("/") for m in (spec.get("modules") or [])]
    graph.add_node(file_id, "file", Path(rel).name, repo, rel,
                   summary=("API 관문 라우팅 표 — 어느 모듈 요청이 어느 서비스로 가는지. "
                            f"서비스: {', '.join(services[:20])} · "
                            f"모듈: {', '.join(m for m in modules[:40] if m)}"),
                   summary_src="deterministic")

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
