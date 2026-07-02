"""⑦ 로컬 검증(T4) — 스택 프로파일 제안 + Playwright 스냅샷 + 리소스 가드.

리소스 가드(RAM 16GB): 다른 docker 스택이 떠 있으면 추가 기동을 거부한다.
enable_verify=False(기본)면 전 과정을 스킵하고 사유를 보고한다.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

PROFILE_MAP = {"xgen-frontend": "frontend", "xgen-workflow": "workflow",
               "xgen-documents": "documents", "xgen-core": "core",
               "xgen-mcp-station": "mcp"}


def suggest_profiles(repos_touched: list[str]) -> list[str]:
    return sorted({PROFILE_MAP[r] for r in repos_touched if r in PROFILE_MAP})


def docker_guard(max_running: int = 0) -> dict:
    if not shutil.which("docker"):
        return {"ok": False, "reason": "docker 미발견"}
    try:
        result = subprocess.run(["docker", "ps", "-q"], capture_output=True,
                                text=True, timeout=15)
    except (subprocess.TimeoutExpired, OSError):
        return {"ok": False, "reason": "docker ps 실패"}
    running = len([line for line in result.stdout.splitlines() if line.strip()])
    if running > max_running:
        return {"ok": False, "reason": f"기존 컨테이너 {running}개 가동 중 — 추가 기동 거부(RAM 가드)",
                "running": running}
    return {"ok": True, "running": running}


def playwright_snapshot(url: str, out_png: Path, timeout: int = 120) -> dict:
    if not shutil.which("npx"):
        return {"ok": False, "reason": "npx 미발견"}
    try:
        result = subprocess.run(
            ["npx", "-y", "playwright", "screenshot", "--full-page", url, str(out_png)],
            capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as error:
        return {"ok": False, "reason": str(error)}
    if result.returncode != 0:
        return {"ok": False, "reason": (result.stderr or "")[-500:]}
    return {"ok": True, "snapshot": str(out_png)}


def verify(enable: bool, repos_touched: list[str], session_dir: Path,
           preview_base: str = "") -> dict:
    profiles = suggest_profiles(repos_touched)
    if not enable:
        return {"skipped": True, "reason": "enable_verify=False (리소스 가드 기본값)",
                "suggested_profiles": profiles,
                "manual": f"수동 검증: xgen-stack.ps1 up -Profiles {','.join(profiles) or 'core'}"}
    guard = docker_guard()
    if not guard["ok"]:
        return {"skipped": True, "reason": guard["reason"], "suggested_profiles": profiles}
    report: dict = {"skipped": False, "profiles": profiles, "snapshots": []}
    if preview_base:
        snap = playwright_snapshot(preview_base, session_dir / "preview.png")
        report["snapshots"].append(snap)
        report["preview_url"] = preview_base
    return report
