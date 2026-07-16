"""릴리즈 사다리 — develop → stg → main (= dev → stg → prd). 배포의 뼈대.

오너 방침: 이 3브랜치가 배포의 핵심. 코드는 로컬→develop→stg→main으로 승격.
main 직접 머지 금지. MAKER는 develop에 MR을 준비하고, 승격 경로(develop→stg→main)를 명시한다.
"""
from __future__ import annotations

import os

# 기본 사다리 (config.release_stages로 오버라이드 가능). url/jenkins는 env로도 주입.
DEFAULT_LADDER = [
    {"branch": "develop", "env": "dev", "role": "개발 통합"},
    {"branch": "stg", "env": "stg", "role": "스테이징 검증"},
    {"branch": "main", "env": "prd", "role": "운영 배포"},
]

# 스테이지 URL·Jenkins job은 전부 .env(XGEN_MAKER_URL_<ENV> / XGEN_MAKER_JENKINS_<ENV>)로만 주입.
# 하드코딩 없음 — 공개 시 dev/stg 도메인 등 내부 정보 노출 방지.
def stage_url(env: str) -> str:
    return os.environ.get(f"XGEN_MAKER_URL_{env.upper()}", "")


def stage_jenkins(env: str) -> str:
    return os.environ.get(f"XGEN_MAKER_JENKINS_{env.upper()}", "")


def ladder(config=None) -> list[dict]:
    stages = getattr(config, "release_stages", None) if config else None
    base = stages or DEFAULT_LADDER
    # url·jenkins job을 각 스테이지에 채운다(명시값 우선)
    return [{**s, "url": s.get("url") or stage_url(s["env"]),
             "jenkins": s.get("jenkins") or stage_jenkins(s["env"])}
            for s in base]


def env_for_branch(branch: str, config=None) -> str | None:
    for stage in ladder(config):
        if stage["branch"] == branch:
            return stage["env"]
    return None


def promotion_path(from_branch: str, config=None) -> list[dict]:
    """from_branch부터 사다리 끝(main/prd)까지 남은 승격 단계."""
    lad = ladder(config)
    idx = next((i for i, s in enumerate(lad) if s["branch"] == from_branch), None)
    if idx is None:
        return lad
    return lad[idx:]


def deploy_targets_by_env(graph, repo: str, app_map=None) -> dict[str, list[dict]]:
    """코드 레포 → 환경별 배포 대상 {env: [{project, domain, namespace}]}."""
    from ..kg.extract_infra import deploy_targets
    grouped: dict[str, list[dict]] = {}
    for t in deploy_targets(graph, repo, app_map):
        grouped.setdefault(t["env"], []).append(
            {"project": t["project"], "domain": t["domain"], "namespace": t["namespace"]})
    return grouped


def release_view(graph, repo: str, target_branch: str, config=None) -> dict:
    """MR/journal용 릴리즈 사다리 뷰. 이 변경이 사다리 어디에 놓이고 무엇이 남았나."""
    lad = ladder(config)
    app_map = getattr(config, "deploy_app_map", None)
    by_env = deploy_targets_by_env(graph, repo, app_map) if graph is not None else {}
    stages = []
    for stage in lad:
        stages.append({**stage, "targets": by_env.get(stage["env"], []),
                       "current": stage["branch"] == target_branch})
    remaining = promotion_path(target_branch, config)
    return {"ladder": stages, "target_branch": target_branch,
            "lands_on_env": env_for_branch(target_branch, config),
            "promotion_remaining": [s["branch"] for s in remaining],
            "note": "main 직접 머지 금지 — develop→stg→main 순차 승격"}


def render_ladder_md(view: dict) -> str:
    lines = ["| 브랜치 | 환경 | URL | Jenkins | 배포 대상 | 현재 |",
             "|---|---|---|---|---|---|"]
    for s in view["ladder"]:
        targets = ", ".join(f"{t['project']}({t['domain']})" if t["domain"] else t["project"]
                            for t in s["targets"]) or "-"
        mark = "◀ 이 MR" if s["current"] else ""
        lines.append(f"| {s['branch']} | {s['env']} | {s.get('url','')} | "
                     f"{s.get('jenkins','')} | {targets} | {mark} |")
    path = " → ".join(view["promotion_remaining"])
    lines.append("")
    lines.append(f"**승격 경로(남음)**: {path}  ·  {view['note']}")
    lines.append("> MAKER는 MR 준비까지. 머지·빌드·ArgoCD sync·배포는 사용자 수동(로그는 `maker status`).")
    return "\n".join(lines)
