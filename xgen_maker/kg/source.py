"""그래프의 원본을 어디서 읽을지.

기본은 체크아웃된 워킹트리다. 그런데 사람은 대개 자기 작업 브랜치를 체크아웃해 두고,
그 브랜치는 팀의 통합 브랜치보다 한참 뒤처져 있다(측정해 보니 544커밋). 그 상태로
그래프를 만들면 착지 좌표가 몇 달 전 코드를 가리킨다 — 지도가 낡으면 나머지가 다
정확해도 엉뚱한 곳에 도착한다.

그렇다고 최신을 보겠다고 남의 워킹트리를 체크아웃할 수는 없다. 그래서 git 오브젝트에서
바로 읽는다. `git ls-tree`로 목록을, `git show`로 내용을 가져오면 워킹트리는 손대지
않으면서 원하는 커밋의 코드를 볼 수 있다.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


class WorktreeSource:
    """체크아웃된 파일을 그대로 읽는다(기본)."""

    ref = ""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def read_text(self, rel: str) -> str:
        return (self.root / rel).read_text(encoding="utf-8-sig", errors="ignore")

    def describe(self) -> str:
        return "작업 중인 파일"


class GitRefSource:
    """특정 커밋(ref)의 파일을 git에서 직접 읽는다. 워킹트리는 건드리지 않는다."""

    def __init__(self, root: str | Path, ref: str):
        self.root = Path(root)
        self.ref = ref

    def _git(self, *args: str) -> str:
        result = subprocess.run(["git", "-C", str(self.root), *args],
                                capture_output=True, timeout=120)
        if result.returncode != 0:
            raise OSError(result.stderr.decode("utf-8", "ignore").strip()[:200])
        return result.stdout.decode("utf-8-sig", "ignore")

    def list_files(self) -> list[str]:
        return [line for line in self._git("ls-tree", "-r", "--name-only", self.ref).splitlines()
                if line.strip()]

    def read_text(self, rel: str) -> str:
        return self._git("show", f"{self.ref}:{rel}")

    def describe(self) -> str:
        return f"{self.ref} 기준"


def resolve_ref(root: str | Path, preferred: str) -> str:
    """그래프를 만들 기준 커밋을 고른다.

    origin/<통합브랜치>를 우선한다. 그게 없는 저장소(기본 브랜치 이름이 다른 경우)는
    origin/HEAD로, 그것도 없으면 빈 문자열 — 호출자가 워킹트리로 돌아간다.
    """
    root = Path(root)
    for candidate in (f"origin/{preferred}" if preferred else "", "origin/HEAD"):
        if not candidate:
            continue
        result = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify",
                                 "--quiet", f"{candidate}^{{commit}}"],
                                capture_output=True, timeout=30)
        if result.returncode == 0:
            return candidate
    return ""


def open_source(root: str | Path, ref: str | None):
    """ref가 있으면 그 커밋에서, 없으면 워킹트리에서 읽는 소스를 준다.

    ref를 줬는데 열 수 없으면(원격을 한 번도 받지 않은 저장소 등) 워킹트리로 돌아간다.
    그래프가 통째로 비는 것보다는 낡더라도 있는 편이 낫다.
    """
    if not ref:
        return WorktreeSource(root)
    source = GitRefSource(root, ref)
    try:
        source.list_files()
    except (OSError, subprocess.SubprocessError):
        return WorktreeSource(root)
    return source
