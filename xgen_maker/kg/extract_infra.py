"""인프라 지식그래프 추출 — LLM이 배포 토폴로지를 '인지'하게.

xgen-infra(K3s+Helm+ArgoCD)를 파싱해 배포 평면을 KG에 넣는다:
- deploy_project (ArgoCD projects/*.yaml): 배포 대상. meta: namespace, 도메인{env}, site, 서비스목록
- helm_app (helm-chart/values/*.yaml): 배포 단위 서비스
- helm_chart: 단일 범용 차트
엣지:
- deploy_project --deploys--> helm_app  (환경별 서비스 목록)
- deploy_project --serves--> helm_app    (hasDomain=frontend → 도메인 주입)
- helm_app --values_of--> helm_chart
코드 연결(link_infra_to_code): helm_app <--deploys--> 동명 코드 레포 노드.
그래서 "xgen-core 고치면 어느 도메인/프로젝트로 배포되나"를 그래프가 답한다.
"""
from __future__ import annotations

from pathlib import Path

from .graph import Graph

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


def _safe_load(path: Path) -> dict | None:
    if not HAS_YAML:
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, yaml.YAMLError):
        return None


def extract_infra(infra_root: str | Path, repo: str = "xgen-infra") -> Graph:
    infra_root = Path(infra_root)
    graph = Graph()
    graph.add_node(repo, "repo", repo, repo, str(infra_root), plane="infra")
    chart_dir = infra_root / "k3s" / "helm-chart"
    values_dir = chart_dir / "values"
    argocd_dir = infra_root / "k3s" / "argocd" / "projects"

    chart_id = f"{repo}:chart:helm-chart"
    if chart_dir.is_dir():
        graph.add_node(chart_id, "helm_chart", "helm-chart", repo,
                       "k3s/helm-chart", plane="infra")
        graph.add_edge(repo, chart_id, "contains")

    # helm_app (values/*.yaml)
    apps: dict[str, str] = {}
    if values_dir.is_dir():
        for values_file in sorted(values_dir.glob("*.yaml")):
            app = values_file.stem
            data = _safe_load(values_file) or {}
            app_id = f"{repo}:app:{app}"
            graph.add_node(app_id, "helm_app", app, repo,
                           f"k3s/helm-chart/values/{app}.yaml", plane="infra",
                           service_name=(data.get("serviceName") or app))
            graph.add_edge(app_id, chart_id, "values_of")
            graph.add_edge(repo, app_id, "contains")
            apps[app] = app_id

    # deploy_project (argocd/projects/*.yaml)
    if argocd_dir.is_dir():
        for proj_file in sorted(argocd_dir.glob("*.yaml")):
            data = _safe_load(proj_file)
            if not data:
                continue
            project = data.get("project", {}) if isinstance(data.get("project"), dict) else {}
            # 식별자는 파일명(배포 타깃) — project.name은 여러 클러스터가 공유("xgen")하므로 meta로
            name = proj_file.stem
            namespace = project.get("namespace", "")
            dests = data.get("destinations", {}) if isinstance(data.get("destinations"), dict) else {}
            domains = {env: (d or {}).get("domain", "")
                       for env, d in dests.items() if isinstance(d, dict)}
            proj_id = f"{repo}:project:{name}"
            graph.add_node(proj_id, "deploy_project", name, repo,
                           f"k3s/argocd/projects/{proj_file.name}", plane="infra",
                           namespace=namespace, domains=domains,
                           site=data.get("site", ""),
                           project_name=project.get("name", name))
            graph.add_edge(repo, proj_id, "contains")

            # 환경별 서비스 수집 → (project,app)당 deploys 엣지 하나(envs 리스트)
            # add_edge가 (src,dst,kind) 중복제거하므로 env를 엣지 meta 리스트로 담는다.
            envs = data.get("environments", {}) if isinstance(data.get("environments"), dict) else {}
            app_envs: dict[str, set[str]] = {}
            app_domains: dict[str, dict[str, str]] = {}
            for env, env_data in envs.items():
                if not isinstance(env_data, dict):
                    continue
                for svc in env_data.get("services", []) or []:
                    if not isinstance(svc, dict) or not svc.get("name"):
                        continue
                    svc_name = svc["name"]
                    app_id = apps.get(svc_name) or f"{repo}:app:{svc_name}"
                    if svc_name not in apps:
                        graph.add_node(app_id, "helm_app", svc_name, repo, "",
                                       plane="infra", service_name=svc_name)
                        apps[svc_name] = app_id
                    app_envs.setdefault(app_id, set()).add(env)
                    if svc.get("hasDomain") and domains.get(env):
                        app_domains.setdefault(app_id, {})[env] = domains[env]
            for app_id, env_set in app_envs.items():
                graph.add_edge(proj_id, app_id, "deploys",
                               envs=sorted(env_set), domains=app_domains.get(app_id, {}))
                if app_domains.get(app_id):
                    graph.add_edge(proj_id, app_id, "serves",
                                   domains=app_domains[app_id])

    graph.meta = {"repo": repo, "root": str(infra_root), "plane": "infra",
                  "projects": len(graph.nodes_by_kind("deploy_project")),
                  "apps": len(graph.nodes_by_kind("helm_app"))}
    return graph


def deploy_targets(graph: Graph, repo: str) -> list[dict]:
    """코드 레포 → 배포 대상(프로젝트·환경·도메인). LLM/MR이 '이 변경이 어디 배포되나' 인지.

    반환 [{project, env, domain, namespace}]. KG에 인프라 평면이 없으면 [].
    """
    alias = {"xgen-frontend-app": "xgen-frontend", "xgen-frontend-lib": "xgen-frontend",
             "xgen-frontend-features": "xgen-frontend"}
    app_name = alias.get(repo, repo)
    app_ids = {n["id"] for n in graph.nodes_by_kind("helm_app") if n["name"] == app_name}
    if not app_ids:
        return []
    out = []
    for edge in graph.edges:
        if edge["kind"] != "deploys" or edge["dst"] not in app_ids:
            continue
        proj = graph.nodes.get(edge["src"])
        if proj is None or proj["kind"] != "deploy_project":
            continue
        edge_domains = edge["meta"].get("domains", {})
        proj_domains = proj["meta"].get("domains", {})
        for env in edge["meta"].get("envs", []):
            out.append({"project": proj["name"], "env": env,
                        "domain": edge_domains.get(env) or proj_domains.get(env, ""),
                        "namespace": proj["meta"].get("namespace", "")})
    return out


def link_infra_to_code(graph: Graph) -> int:
    """helm_app <--deploys--> 동명 코드 레포 노드. 반환 = 생성된 엣지 수.

    코드 레포 매핑: frontend-* 스코프는 xgen-frontend 앱 하나로 수렴.
    """
    alias = {"xgen-frontend-app": "xgen-frontend", "xgen-frontend-lib": "xgen-frontend",
             "xgen-frontend-features": "xgen-frontend"}
    apps = {n["name"]: n["id"] for n in graph.nodes_by_kind("helm_app")}
    code_repos = {n["name"] for n in graph.nodes_by_kind("repo")
                  if n["meta"].get("plane") != "infra"}
    created = 0
    for repo_name in code_repos:
        app_name = alias.get(repo_name, repo_name)
        app_id = apps.get(app_name)
        if app_id:
            graph.add_edge(app_id, repo_name, "deploys", code_repo=repo_name)
            created += 1
    return created
