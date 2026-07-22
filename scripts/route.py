"""Cloudflare 터널에 하위도메인 하나 붙이기/떼기.

이 서버(SERVER_JS)에서 돌리는 앱을 도메인으로 여는 도구. 포트만 정하면
ingress 규칙 + DNS CNAME을 한 번에 잡는다. 방화벽·포트개방 필요 없음.

    python scripts/route.py add jinxus 7000       # https://jinxus.js-96.com → localhost:7000
    python scripts/route.py list                  # 지금 붙어 있는 것들
    python scripts/route.py rm jinxus             # 떼기

자격증명은 환경변수 우선, 없으면 D:\\.claude\\CREDENTIALS.local.md에서 읽는다
(레포에 비밀을 넣지 않기 위해 — 이 파일은 공개 저장소에 올라간다).
    CF_API_EMAIL / CF_API_KEY / CF_TUNNEL_ID / CF_ZONE
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://api.cloudflare.com/client/v4"
CRED = Path(r"D:\.claude\CREDENTIALS.local.md")
DEFAULT_ZONE = "js-96.com"


def _from_creds() -> dict:
    """공개 레포에 비밀을 두지 않으려고 로컬 자격증명 파일에서 읽는다."""
    if not CRED.is_file():
        return {}
    text = CRED.read_text(encoding="utf-8", errors="ignore")
    out = {}
    m = re.search(r"X-Auth-Email:\s*([^\s\"]+)", text)
    if m:
        out["email"] = m.group(1)
    m = re.search(r"\*\*API 키\*\*:\s*`([^`]+)`", text)
    if m:
        out["key"] = m.group(1)
    m = re.search(r"\*\*터널 토큰\*\*:\s*`([^`]+)`", text)
    if m:  # 터널 토큰(base64 JSON)에서 터널 id 추출
        import base64
        try:
            out["tunnel"] = json.loads(base64.b64decode(m.group(1) + "==")).get("t")
        except (ValueError, json.JSONDecodeError):
            pass
    return out


def creds() -> tuple[str, str, str, str]:
    c = _from_creds()
    email = os.environ.get("CF_API_EMAIL") or c.get("email", "")
    key = os.environ.get("CF_API_KEY") or c.get("key", "")
    tunnel = os.environ.get("CF_TUNNEL_ID") or c.get("tunnel", "")
    zone = os.environ.get("CF_ZONE") or DEFAULT_ZONE
    if not (email and key and tunnel):
        sys.exit("자격증명을 찾지 못했습니다. CF_API_EMAIL/CF_API_KEY/CF_TUNNEL_ID를 설정하세요.")
    return email, key, tunnel, zone


def api(method: str, path: str, body=None):
    email, key, _, _ = creds()
    req = urllib.request.Request(
        BASE + path, method=method,
        headers={"X-Auth-Email": email, "X-Auth-Key": key,
                 "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode())


def account_id() -> str:
    r = api("GET", "/accounts?per_page=1")
    return r["result"][0]["id"]


def zone_id(zone: str) -> str:
    r = api("GET", f"/zones?name={zone}")
    if not r.get("result"):
        sys.exit(f"도메인 '{zone}'을 찾지 못했습니다.")
    return r["result"][0]["id"]


def get_ingress(acc: str, tun: str) -> list:
    r = api("GET", f"/accounts/{acc}/cfd_tunnel/{tun}/configurations")
    cfg = (r.get("result") or {}).get("config") or {}
    return [i for i in (cfg.get("ingress") or []) if i.get("hostname")]


def put_ingress(acc: str, tun: str, rules: list) -> bool:
    # 마지막 catch-all은 필수 — 없으면 Cloudflare가 설정을 거부한다
    body = {"config": {"ingress": rules + [{"service": "http_status:404"}]}}
    r = api("PUT", f"/accounts/{acc}/cfd_tunnel/{tun}/configurations", body)
    if not r.get("success"):
        print("  실패:", [e.get("message") for e in (r.get("errors") or [])])
    return bool(r.get("success"))


def cmd_list():
    _, _, tun, zone = creds()
    acc = account_id()
    rules = get_ingress(acc, tun)
    if not rules:
        print("붙어 있는 도메인이 없습니다.")
        return
    print(f"{'도메인':36} → 서비스")
    for i in rules:
        print(f"  https://{i['hostname']:28} → {i['service']}")


def cmd_add(name: str, port: str, scheme: str = "http"):
    _, _, tun, zone = creds()
    acc = account_id()
    host = f"{name}.{zone}"
    service = f"{scheme}://localhost:{port}"

    rules = [i for i in get_ingress(acc, tun) if i["hostname"] != host]
    rules.append({"hostname": host, "service": service})
    if not put_ingress(acc, tun, rules):
        sys.exit(1)
    print(f"경로 설정: {host} → {service}")

    zid = zone_id(zone)
    target = f"{tun}.cfargotunnel.com"
    body = {"type": "CNAME", "name": name, "content": target, "proxied": True,
            "comment": f"tunnel → localhost:{port}"}
    cur = api("GET", f"/zones/{zid}/dns_records?name={host}")
    existing = cur.get("result") or []
    if existing:
        r = api("PATCH", f"/zones/{zid}/dns_records/{existing[0]['id']}", body)
    else:
        r = api("POST", f"/zones/{zid}/dns_records", body)
    if not r.get("success"):
        sys.exit(f"DNS 실패: {[e.get('message') for e in (r.get('errors') or [])]}")
    print(f"DNS 연결: {host} → {target}")
    print(f"\n완료 → https://{host}")
    print("  ⚠ 인증이 필요한 앱이면 Zero Trust → Access에서 정책을 거세요.")


def cmd_rm(name: str):
    _, _, tun, zone = creds()
    acc = account_id()
    host = f"{name}.{zone}"
    rules = [i for i in get_ingress(acc, tun) if i["hostname"] != host]
    put_ingress(acc, tun, rules)
    zid = zone_id(zone)
    cur = api("GET", f"/zones/{zid}/dns_records?name={host}")
    for rec in cur.get("result") or []:
        api("DELETE", f"/zones/{zid}/dns_records/{rec['id']}")
    print(f"제거: {host}")


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    cmd = args[0]
    if cmd == "list":
        cmd_list()
    elif cmd == "add" and len(args) >= 3:
        cmd_add(args[1], args[2], args[3] if len(args) > 3 else "http")
    elif cmd == "rm" and len(args) >= 2:
        cmd_rm(args[1])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
