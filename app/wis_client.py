from __future__ import annotations

import os
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


_SESSION: requests.Session | None = None


def _apply_cookie_header(session: requests.Session, cookie_header: str) -> None:
    # Accept a normal browser cookie header:
    # key=value; key2=value2
    for piece in cookie_header.split(";"):
        if "=" not in piece:
            continue
        key, value = piece.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            session.cookies.set(key, value, domain=".whatifsports.com")


def _find_login_form_payload(html: str, username: str, password: str) -> tuple[str | None, dict]:
    soup = BeautifulSoup(html, "lxml")
    form = soup.find("form")
    if not form:
        return None, {}

    payload = {}
    user_field = None
    pass_field = None

    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        value = inp.get("value", "")
        payload[name] = value

        lname = name.lower()
        itype = (inp.get("type") or "").lower()

        if user_field is None and any(token in lname for token in ["user", "email", "login"]):
            user_field = name
        if pass_field is None and (itype == "password" or "pass" in lname):
            pass_field = name

    if user_field:
        payload[user_field] = username
    else:
        # legacy fallback names
        payload["username"] = username

    if pass_field:
        payload[pass_field] = password
    else:
        payload["password"] = password

    action = form.get("action")
    return action, payload


def get_wis_session(force_new: bool = False) -> requests.Session:
    global _SESSION

    if _SESSION is not None and not force_new:
        return _SESSION

    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.whatifsports.com/hd/",
    })

    cookie_header = os.getenv("WIS_COOKIE", "").strip()
    if cookie_header:
        _apply_cookie_header(session, cookie_header)
        _SESSION = session
        return session

    username = os.getenv("WIS_USERNAME", "").strip()
    password = os.getenv("WIS_PASSWORD", "").strip()

    if username and password:
        # WIS has changed login flows over time. This is a best-effort login that
        # first reads the form and posts back to its action. If that fails, the
        # user can use WIS_COOKIE instead.
        login_url = "https://www.whatifsports.com/shop/login.asp"
        login_get = session.get(login_url, timeout=30)
        login_get.raise_for_status()

        action, payload = _find_login_form_payload(login_get.text, username, password)
        post_url = urljoin(login_url, action) if action else login_url

        # Add a few common legacy fields in case the form parser misses them.
        payload.setdefault("UserName", username)
        payload.setdefault("Username", username)
        payload.setdefault("Email", username)
        payload.setdefault("Password", password)
        payload.setdefault("password", password)

        session.post(post_url, data=payload, timeout=30, allow_redirects=True)

    _SESSION = session
    return session


def wis_get(url: str) -> requests.Response:
    session = get_wis_session()
    response = session.get(url, timeout=45, allow_redirects=True)
    response.raise_for_status()
    return response


def wis_get_text(url: str) -> str:
    return wis_get(url).text


def wis_auth_status() -> dict:
    username = bool(os.getenv("WIS_USERNAME", "").strip())
    password = bool(os.getenv("WIS_PASSWORD", "").strip())
    cookie = bool(os.getenv("WIS_COOKIE", "").strip())

    return {
        "has_username": username,
        "has_password": password,
        "has_cookie": cookie,
        "mode": "cookie" if cookie else ("username/password" if username and password else "none"),
    }
