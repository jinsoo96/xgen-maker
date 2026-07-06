"""릴리즈 사다리 — develop → stg → main (= dev → stg → prd). 배포의 뼈대.

오너 방침: 이 3브랜치가 배포의 핵심. 코드는 로컬→develop→stg→main으로 승격.
main 직접 머지 금지. MAKER는 develop에 MR을 준비하고, 승격 경로(develop→stg→main)를 명시한다.
"""
from __future__ import annotations

# 기본 사다리 (config.release_stages로 오버라이드 가능)
DEFAULT_LADDER = [
    {"branch": "develop", "env": "dev", "role": "개발 통합"},
    {"branch": "stg", "env": "stg", "role": "스테이징 검증"},
    {"branch": "main", "env": "prd", "role": "운영 배포"},
]


def ladder(config=None) -> list[dict]:
    stages = getattr(config, "release_stages", None) if config else None
    return stages or DEFAULT_LADDER


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


def deploy_targets_by_env(graph, repo: str) -> dict[str, list[dict]]:
    """코드 레포 → 환경별 배포 대상 {env: [{project, domain, namespace}]}."""
    from ..kg.extract_infra import deploy_targets
    grouped: dict[str, list[dict]] = {}
    for t in deploy_targets(graph, repo):
        grouped.setdefault(t["env"], []).append(
            {"project": t["project"], "domain": t["domain"], "namespace": t["namespace"]})
    return grouped


def release_view(graph, repo: str, target_branch: str, config=None) -> dict:
    """MR/journal용 릴리즈 사다리 뷰. 이 변경이 사다리 어디에 놓이고 무엇이 남았나."""
    lad = ladder(config)
    by_env = deploy_targets_by_env(graph, repo) if graph is not None else {}
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
    lines = ["| 브랜치 | 환경 | 역할 | 배포 대상 | 현재 |",
             "|---|---|---|---|---|"]
    for s in view["ladder"]:
        targets = ", ".join(f"{t['project']}({t['domain']})" if t["domain"] else t["project"]
                            for t in s["targets"]) or "-"
        mark = "◀ 이 MR" if s["current"] else ""
        lines.append(f"| {s['branch']} | {s['env']} | {s['role']} | {targets} | {mark} |")
    path = " → ".join(view["promotion_remaining"])
    lines.append("")
    lines.append(f"**승격 경로(남음)**: {path}  ·  {view['note']}")
    return "\n".join(lines)
