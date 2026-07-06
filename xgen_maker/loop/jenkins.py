"""Jenkins CI 클라이언트 (read-only) — 빌드 플레인 인지.

빌드 플로우: 코드 머지 → Jenkins 빌드(이미지) → ArgoCD 수동 sync → k3s.
자격은 env(XGEN_MAKER_JENKINS_URL/USER/TOKEN) 또는 .env. 없으면 전부 skip.
읽기 전용 — job 목록·최근 빌드 상태만. 빌드 트리거는 배포 live 인터록 밖(별도 승인).
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request
import urllib.error

# Jenkins job → env 매핑. 하드코딩 없음(공개 안전) — job명 토큰(dev/stage/prd)으로 추론 +
# XGEN_MAKER_JENKINS_JOBMAP='{"job명":"env"}' env로 오버라이드.
import json as _json
import os as _os


def _job_map() -> dict:
    raw = _os.environ.get("XGEN_MAKER_JENKINS_JOBMAP", "")
    if raw:
        try:
            return _json.loads(raw)
        except _json.JSONDecodeError:
            return {}
    return {}


def _creds() -> tuple[str, str, str] | None:
    url = os.environ.get("XGEN_MAKER_JENKINS_URL", "")
    user = os.environ.get("XGEN_MAKER_JENKINS_USER", "")
    token = os.environ.get("XGEN_MAKER_JENKINS_TOKEN", "")
    if url and user and token:
        return url.rstrip("/"), user, token
    return None


def _get(path: str, timeout: int = 25) -> dict | None:
    creds = _creds()
    if creds is None:
        return None
    url, user, token = creds
    auth = base64.b64encode(f"{user}:{token}".encode()).decode()
    request = urllib.request.Request(url + path,
                                     headers={"Authorization": f"Basic {auth}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None


def _env_of(name: str) -> str:
    """job명 → env. env 오버라이드 맵 우선, 없으면 job명 토큰(stage/prd/dev)으로 추론."""
    jobmap = _job_map()
    if name in jobmap:
        return jobmap[name]
    for key, env in jobmap.items():
        if key in name:
            return env
    low = name.lower()
    for tok, env in (("stage", "stg"), ("stg", "stg"), ("prd", "prd"),
                     ("prod", "prd"), ("dev", "dev")):
        if tok in low:
            return env
    return ""


def list_jobs() -> list[dict]:
    data = _get("/api/json?tree=jobs[name,color,url]")
    if data is None:
        return []
    return [{"name": j.get("name"), "color": j.get("color"),
             "env": _env_of(j.get("name", ""))}
            for j in data.get("jobs", [])]


def job_for_env(env: str) -> str | None:
    for name, mapped in _job_map().items():
        if mapped == env:
            return name
    return None


def available() -> bool:
    return _creds() is not None
