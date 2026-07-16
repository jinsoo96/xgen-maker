"""배포 트리거 — dev 기준 설계, 실발사 금지 인터록 내장.

모드 3단: off(기본) / dry_run(보낼 요청을 계획으로만 기록, 전송 0) / live.
   live는 이중 인터록 — config.deploy_mode=="live" **그리고** 환경변수
   XGEN_MAKER_DEPLOY_LIVE=="1" 둘 다일 때만 실제 전송. 하나라도 빠지면 거부 기록.
방식: MR 머지 후 target_branch 기준 GitLab 파이프라인 트리거.

레포→Helm 앱 매핑은 config.deploy_app_map(.env/config 주입)로만 온다 — 조직의
서비스명·인프라 구조를 소스에 담지 않는다(public 안전). 매핑 없으면 레포명=앱명 폴백.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

from ..config import MakerConfig


def _find_helm() -> str | None:
    exe = shutil.which("helm")
    if exe:
        return exe
    # portable 설치 경로 폴백
    for cand in (Path(__file__).resolve().parents[2] / ".tools" / "windows-amd64" / "helm.exe",
                 Path(__file__).resolve().parents[2] / ".tools" / "helm.exe"):
        if cand.is_file():
            return str(cand)
    return None


def app_for_repo(repo: str, config: MakerConfig | None = None) -> str | None:
    """레포→Helm 앱명. config.deploy_app_map 우선, 없으면 레포명 자체(identity)."""
    mapping = getattr(config, "deploy_app_map", None) or {}
    return mapping.get(repo, repo if repo else None)


def deploy_render_test(config: MakerConfig, repo: str) -> dict:
    """T1 — 상사님 'tmp 복사 후 테스트'의 안전한 K8s 등가물(클러스터 불필요).

    Helm 차트를 tmp에 복사 → `helm template`로 매니페스트 렌더 → YAML 파싱 검증.
    깨진 values/템플릿/이미지태그 오류를 배포 전(=MR 전)에 잡는다. 통과해야 자신 있게 MR.
    """
    app = app_for_repo(repo, config)
    if app is None:
        return {"name": "deploy_render", "status": "skipped", "reason": f"'{repo}' → Helm 앱 매핑 없음"}
    infra = Path(getattr(config, "infra_path", "") or "")
    chart = infra / "k3s" / "helm-chart"
    values = chart / "values" / f"{app}.yaml"
    if not chart.is_dir() or not values.is_file():
        return {"name": "deploy_render", "status": "skipped",
                "reason": f"차트/values 없음 ({chart}, {values.name}) — infra_path 확인"}
    helm = _find_helm()
    if helm is None:
        return {"name": "deploy_render", "status": "skipped", "reason": "helm 미설치 — 렌더 검증 불가"}

    with tempfile.TemporaryDirectory(prefix="maker-deploy-") as tmp:
        tmp_chart = Path(tmp) / "helm-chart"
        shutil.copytree(chart, tmp_chart)  # tmp에 통째 복사(격리) — 상사님 방식
        out = Path(tmp) / "rendered.yaml"
        try:
            result = subprocess.run(
                [helm, "template", app, str(tmp_chart), "-f",
                 str(tmp_chart / "values" / f"{app}.yaml")],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=120)
        except (subprocess.TimeoutExpired, OSError) as error:
            return {"name": "deploy_render", "status": "skipped", "reason": f"helm 실행불가: {error}"}
        if result.returncode != 0:
            return {"name": "deploy_render", "status": "failed", "app": app,
                    "error": (result.stderr or result.stdout or "")[-800:]}
        rendered = result.stdout or ""
        out.write_text(rendered, encoding="utf-8")
        # 렌더 결과 YAML 파싱·kind 집계 (스키마 없이도 구조 검증)
        kinds: dict[str, int] = {}
        bad = []
        for doc in rendered.split("\n---\n"):
            doc = doc.strip()
            if not doc:
                continue
            kind = next((ln.split(":", 1)[1].strip()
                         for ln in doc.splitlines() if ln.startswith("kind:")), None)
            has_api = any(ln.startswith("apiVersion:") for ln in doc.splitlines())
            if kind and has_api:
                kinds[kind] = kinds.get(kind, 0) + 1
            elif kind or has_api:
                bad.append(kind or "?")
        if bad:
            return {"name": "deploy_render", "status": "failed", "app": app,
                    "error": f"불완전 매니페스트(kind/apiVersion 누락): {bad}"}
        return {"name": "deploy_render", "status": "passed", "app": app, "kinds": kinds,
                "manifests": sum(kinds.values()), "helm": helm.split("\\")[-1]}


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
