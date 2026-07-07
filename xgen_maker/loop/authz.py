"""인가 게이트 — 인가된 xgen 작업자만 실제 작업(act)이 되도록 강제.

public 저장소이므로 코드는 누구나 받지만, 실제 인프라 작업(push·MR)은:
  1) 유효한 GitLab 토큰
  2) 대상 프로젝트에 대한 Developer+ 멤버십
  3) placeholder가 아닌 실제 대상(gitlab_url·projects 매핑)
을 모두 요구한다. 자격/엔드포인트는 .env로만 주입되고(레포에 없음), 멤버십은
실 GitLab이 권위를 가진다. 게이트는 작업 시작 전 fail-fast로 미인가를 차단한다.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

DEVELOPER = 30  # GitLab access_level: 30=Developer, 40=Maintainer, 50=Owner
# 미설정/예시 대상 감지 — 실 인프라가 아니면 act 거부
_PLACEHOLDER = ("example.com", "your-company", "gitlab.example", "localhost", "")


def _api(url: str, path: str, token: str, timeout: int = 20):
    request = urllib.request.Request(url.rstrip("/") + "/api/v4" + path,
                                     headers={"PRIVATE-TOKEN": token})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def is_placeholder_target(gitlab_url: str) -> bool:
    u = (gitlab_url or "").strip().lower()
    return (not u) or any(p and p in u for p in _PLACEHOLDER)


def authorize(config, repo: str, min_level: int = DEVELOPER,
              timeout: int = 20) -> dict:
    """act(실 push/MR) 전 인가 확인. 반환 {ok, user?, level?, project?, reason?}.

    거부 사유: 토큰 없음 · placeholder 대상 · 프로젝트 매핑 없음 ·
              토큰 무효 · 멤버 아님 · 접근레벨 부족.
    """
    token = config.gitlab_token
    if not token:
        return {"ok": False, "reason":
                "GitLab 토큰 없음 — 인가된 xgen 작업자만 act 가능(maker login)"}
    url = config.gitlab_url
    if is_placeholder_target(url):
        return {"ok": False, "reason":
                f"gitlab_url이 미설정/예시({url!r}) — 실 대상 아님, act 거부"}
    project = (config.gitlab_projects or {}).get(repo)
    if not project:
        return {"ok": False, "reason":
                f"'{repo}' → gitlab_projects 매핑 없음 — 대상 프로젝트 미지정"}

    # 1) 토큰 유효성 + 사용자 식별
    try:
        me = _api(url, "/user", token, timeout)
    except urllib.error.HTTPError as error:
        return {"ok": False, "reason": f"토큰 무효(HTTP {error.code})"}
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as error:
        return {"ok": False, "reason": f"GitLab 접속 실패: {str(error)[:60]}"}
    uid, uname = me.get("id"), me.get("username")

    # 2) 대상 프로젝트 멤버십(그룹 상속 포함) + 접근레벨
    enc = urllib.parse.quote(str(project), safe="")
    try:
        member = _api(url, f"/projects/{enc}/members/all/{uid}", token, timeout)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return {"ok": False, "user": uname, "project": project, "reason":
                    f"'{uname}'는 {project} 멤버 아님 — 인가된 작업자만 act 가능"}
        return {"ok": False, "user": uname, "project": project,
                "reason": f"멤버십 확인 실패(HTTP {error.code})"}
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as error:
        return {"ok": False, "user": uname, "project": project,
                "reason": f"멤버십 확인 실패: {str(error)[:60]}"}

    level = int(member.get("access_level", 0))
    if level < min_level:
        return {"ok": False, "user": uname, "project": project, "level": level,
                "reason": f"'{uname}' 접근레벨 {level} < Developer({min_level}) — 쓰기 권한 없음"}
    return {"ok": True, "user": uname, "project": project, "level": level}
