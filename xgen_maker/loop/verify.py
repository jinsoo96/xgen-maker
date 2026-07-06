"""⑦ 로컬 검증(T4) — 스택 프로파일 제안 + Playwright 스냅샷 + 리소스 가드.

리소스 가드(RAM 16GB): 다른 docker 스택이 떠 있으면 추가 기동을 거부한다.
enable_verify=False(기본)면 전 과정을 스킵하고 사유를 보고한다.
"""
from __future__ import annotations

import shutil
import subprocess
import urllib.request
import urllib.error
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


def _shim_command(exe_name: str, args: list[str]) -> list[str] | None:
    """Windows .cmd/.ps1 심은 cmd /c 경유(CreateProcess 직접실행 불가)."""
    exe = shutil.which(exe_name)
    if not exe:
        return None
    if exe.lower().endswith((".cmd", ".bat", ".ps1")):
        base = exe[:-4] + ".cmd" if exe.lower().endswith(".ps1") else exe
        return ["cmd", "/c", base, *args]
    return [exe, *args]


def playwright_snapshot(url: str, out_png: Path, timeout: int = 180,
                        wait_ms: int = 3000) -> dict:
    command = _shim_command("npx", ["-y", "playwright", "screenshot",
                                    "--full-page", "--wait-for-timeout", str(wait_ms),
                                    url, str(out_png)])
    if command is None:
        return {"ok": False, "reason": "npx 미발견"}
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as error:
        return {"ok": False, "reason": str(error)}
    if result.returncode != 0 or not out_png.exists():
        return {"ok": False, "reason": (result.stderr or result.stdout or "")[-500:]}
    return {"ok": True, "snapshot": str(out_png), "bytes": out_png.stat().st_size}


def http_reachable(url: str, timeout: int = 8) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status < 500
    except urllib.error.HTTPError as error:
        return error.code < 500  # 4xx(로그인 리다이렉트 등)도 "서버 살아있음"
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def verify(enable: bool, repos_touched: list[str], session_dir: Path,
           preview_base: str = "") -> dict:
    """프리뷰 검증 — 이미 떠 있는 스택을 재사용한다(자동 기동은 RAM 가드로 안 함)."""
    profiles = suggest_profiles(repos_touched)
    if not enable:
        return {"skipped": True, "reason": "enable_verify=False (리소스 가드 기본값)",
                "suggested_profiles": profiles,
                "manual": f"수동 검증: xgen-stack.ps1 up -Profiles {','.join(profiles) or 'core'}"}
    report: dict = {"skipped": False, "profiles": profiles, "snapshots": []}
    if not preview_base:
        report.update({"skipped": True, "reason": "preview_base 미설정"})
        return report
    report["preview_url"] = preview_base
    report["preview_reachable"] = http_reachable(preview_base)
    if report["preview_reachable"]:
        snap = playwright_snapshot(preview_base, session_dir / "preview.png")
        report["snapshots"].append(snap)
    else:
        report["note"] = ("preview_base 미도달 — 스택 자동 기동은 RAM 가드로 수행하지 않음. "
                          f"수동: xgen-stack.ps1 up -Profiles {','.join(profiles) or 'core'}")
    return report
