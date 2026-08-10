"""머지된 MR을 더 모은다 — 벤치마크를 넓히기 위해.

지금 표본은 294건이고, 남은 실패 25건은 대부분 "제목이 파일을 결정하지 않는" 것들이다.
그런 것들을 하나씩 특수 처리하면 과적합이다. 표본을 넓혀서, 지금 고른 상수들이
정말 일반화되는지부터 확인하는 편이 낫다.

제목과 그 MR이 바꾼 파일만 가져온다. 본문·diff는 쓰지 않는다 — 실제 사용 상황은
사람이 한 문장으로 요청하는 것이고, 본문까지 주면 맞히기 쉬워질 뿐 좋아지지 않는다.
"""
import json
import os
import pathlib
import sys
import time
import urllib.parse
import urllib.request

BASE = os.environ.get("XGEN_BENCH_GITLAB", "").rstrip("/")
TOKEN = os.environ.get("XGEN_BENCH_TOKEN", "")
GROUP = os.environ.get("XGEN_BENCH_GROUP", "")
OUT = pathlib.Path(__file__).resolve().parent.parent / "bench" / "mr_cases_v2.json"
# 파일이 이만큼 넘게 바뀐 MR은 릴리즈·대량 이동이라 제목이 파일을 가리키지 않는다.
_MAX_FILES = 12


def api(path: str, **params):
    url = f"{BASE}/api/v4/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"PRIVATE-TOKEN": TOKEN})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read())
        except Exception:                       # noqa: BLE001 — 일시 오류는 재시도
            if attempt == 2:
                return None
            time.sleep(2 * (attempt + 1))
    return None


def main() -> None:
    if not (BASE and TOKEN and GROUP):
        raise SystemExit("XGEN_BENCH_GITLAB / XGEN_BENCH_TOKEN / XGEN_BENCH_GROUP 필요")
    projects, page = [], 1
    while True:
        got = api(f"groups/{urllib.parse.quote_plus(GROUP)}/projects",
                  per_page=100, page=page, archived="false")
        if not got:
            break
        projects.extend(got)
        if len(got) < 100:
            break
        page += 1
    print(f"프로젝트 {len(projects)}개", flush=True)

    cases, seen = [], set()
    if OUT.exists():
        cases = json.loads(OUT.read_text(encoding="utf-8"))
        seen = {(c["repo"], c["iid"]) for c in cases}
        print(f"이어서 — 이미 {len(cases)}건", flush=True)

    for project in projects:
        pid, name = project["id"], project["path"]
        page, kept = 1, 0
        while True:
            mrs = api(f"projects/{pid}/merge_requests", state="merged",
                      per_page=100, page=page, order_by="updated_at")
            if not mrs:
                break
            for mr in mrs:
                key = (name, mr["iid"])
                if key in seen:
                    continue
                changes = api(f"projects/{pid}/merge_requests/{mr['iid']}/changes")
                if not changes:
                    continue
                files = sorted({c["new_path"] for c in changes.get("changes", [])
                                if c.get("new_path")})
                if not files or len(files) > _MAX_FILES:
                    continue
                cases.append({"repo": name, "iid": mr["iid"], "title": mr["title"],
                              "merged_at": mr.get("merged_at", ""), "files": files})
                seen.add(key)
                kept += 1
            if len(mrs) < 100:
                break
            page += 1
            OUT.write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  {name}: +{kept} (누적 {len(cases)})", flush=True)
        OUT.write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")

    OUT.write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"완료 — {len(cases)}건 → {OUT}")


if __name__ == "__main__":
    main()
