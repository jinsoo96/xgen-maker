"""ArgoCD 클라이언트 (read-only) — 배포 상태/로그 관측만.

⚠️ MAKER는 배포(sync) 안 함 — 사용자 수동. 여기선 app 목록·sync/health 상태만 읽는다.
자격: env(XGEN_MAKER_ARGOCD_URL/USER/TOKEN 또는 USER+PASSWORD). 없으면 skip.
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error


def _base() -> str | None:
    url = os.environ.get("XGEN_MAKER_ARGOCD_URL", "")
    return url.rstrip("/") if url else None


def _session_token(base: str, timeout: int = 20) -> str | None:
    token = os.environ.get("XGEN_MAKER_ARGOCD_TOKEN", "")
    if token:
        return token
    user = os.environ.get("XGEN_MAKER_ARGOCD_USER", "")
    pw = os.environ.get("XGEN_MAKER_ARGOCD_PASSWORD", "")
    if not (user and pw):
        return None
    request = urllib.request.Request(
        base + "/api/v1/session",
        data=json.dumps({"username": user, "password": pw}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8")).get("token")
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None


def available() -> bool:
    return bool(_base()) and (bool(os.environ.get("XGEN_MAKER_ARGOCD_TOKEN"))
                              or bool(os.environ.get("XGEN_MAKER_ARGOCD_PASSWORD")))


def list_apps(timeout: int = 25) -> list[dict]:
    """앱별 sync/health 상태 (read-only). 반환 [{name, sync, health, revision}]."""
    base = _base()
    if base is None:
        return []
    token = _session_token(base, timeout)
    if token is None:
        return []
    request = urllib.request.Request(
        base + "/api/v1/applications",
        headers={"Cookie": f"argocd.token={token}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return []
    out = []
    for app in data.get("items", []) or []:
        meta = app.get("metadata", {})
        status = app.get("status", {})
        out.append({
            "name": meta.get("name"),
            "sync": (status.get("sync", {}) or {}).get("status"),
            "health": (status.get("health", {}) or {}).get("status"),
            "revision": (status.get("sync", {}) or {}).get("revision", "")[:8],
        })
    return out
