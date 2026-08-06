"""GitHub OAuth Device Flow. 각 사용자가 자기 계정으로 로그인 → 자기 권한 레포만.

토큰은 서버 메모리(SESSIONS)에만. 클라이언트는 불투명 session_id만 가짐.
"""
from __future__ import annotations
import secrets
import httpx

from config import GITHUB_CLIENT_ID

SESSIONS: dict[str, dict] = {}   # session_id -> {token, login}
_H = {"Accept": "application/json"}


async def device_start() -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post("https://github.com/login/device/code", headers=_H,
                         data={"client_id": GITHUB_CLIENT_ID, "scope": "repo"})
        r.raise_for_status()
        j = r.json()
    # user_code, verification_uri, device_code, interval, expires_in
    return j


async def device_poll(device_code: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post("https://github.com/login/oauth/access_token", headers=_H,
                         data={"client_id": GITHUB_CLIENT_ID, "device_code": device_code,
                               "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
        j = r.json()
    if j.get("access_token"):
        token = j["access_token"]
        login = await _whoami(token)
        sid = secrets.token_urlsafe(24)
        SESSIONS[sid] = {"token": token, "login": login}
        return {"status": "ok", "session": sid, "login": login}
    # authorization_pending / slow_down / expired_token / access_denied
    return {"status": j.get("error", "pending")}


async def _whoami(token: str) -> str:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get("https://api.github.com/user",
                        headers={"Authorization": f"Bearer {token}", **_H})
        return r.json().get("login", "?") if r.status_code == 200 else "?"


def token_for(session: str) -> str:
    return SESSIONS.get(session, {}).get("token", "")


def login_for(session: str) -> str | None:
    return SESSIONS.get(session, {}).get("login")


def logout(session: str):
    SESSIONS.pop(session, None)
