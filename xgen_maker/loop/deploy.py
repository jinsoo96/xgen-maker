"""R20 배포 트리거 — dev 기준 설계, 실발사 금지 인터록 내장.

오너 방침(2026-07-03): "dev 기준으로 하되 진짜 해놓진 말고 아직은."
→ 모드 3단: off(기본) / dry_run(보낼 요청을 계획으로만 기록, 전송 0) / live.
   live는 이중 인터록 — config.deploy_mode=="live" **그리고** 환경변수
   XGEN_MAKER_DEPLOY_LIVE=="1" 둘 다일 때만 실제 전송. 하나라도 빠지면 거부 기록.
방식: MR 머지 후 target_branch(develop) 기준 GitLab 파이프라인 트리거(dev 배포 파이프라인).
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
import urllib.error

from ..config import MakerConfig


def plan_deploy(config: MakerConfig, repo: str, branch: str, mr_url: str = "") -> dict:
    """무엇을 보낼지 계획만 생성 — 전송 없음."""
    project = config.gitlab_projects.get(repo, "")
    encoded = urllib.parse.quote_plus(project) if project else "<project 미매핑>"
    return {
        "env": config.deploy_env,
        "method": "gitlab_pipeline",
        "request": {
            "http": "POST",
            "url": f"{config.gitlab_url}/api/v4/projects/{encoded}/pipeline",
            "body": {"ref": config.target_branch},
            "auth": "PRIVATE-TOKEN (env XGEN_MAKER_GITLAB_TOKEN)",
        },
        "precondition": f"MR 머지 후 {config.target_branch} 기준 — 머지 전 트리거 무의미",
        "source_branch": branch,
        "mr": mr_url,
    }


def trigger_deploy(config: MakerConfig, plan: dict) -> dict:
    """모드에 따라 실행. off/dry_run은 어떤 네트워크 요청도 보내지 않는다."""
    if config.deploy_mode == "off":
        return {"status": "off", "sent": False}
    if config.deploy_mode == "dry_run":
        return {"status": "dry_run", "sent": False, "plan": plan,
                "note": "실발사 금지 방침 — 보낼 요청을 기록만 함"}
    if config.deploy_mode == "live":
        if os.environ.get("XGEN_MAKER_DEPLOY_LIVE") != "1":
            return {"status": "refused", "sent": False,
                    "reason": "live 인터록 미해제 — XGEN_MAKER_DEPLOY_LIVE=1 필요(오너 승인 표시)"}
        request_spec = plan["request"]
        request = urllib.request.Request(
            request_spec["url"],
            data=json.dumps(request_spec["body"]).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "PRIVATE-TOKEN": config.gitlab_token},
            method="POST")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
            return {"status": "triggered", "sent": True,
                    "pipeline_url": data.get("web_url", ""), "id": data.get("id")}
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as error:
            return {"status": "error", "sent": False, "error": str(error)}
    return {"status": "off", "sent": False, "reason": f"알 수 없는 deploy_mode {config.deploy_mode}"}
