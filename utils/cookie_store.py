import json
import os


def _cookies_file() -> str:
    return os.getenv('COOKIES_FILE', 'cookies.json')


def save_cookies(session) -> None:
    cookies = {
        c.name: c.value
        for c in session.cookies
        if c.domain and '.goofish.com' in c.domain
    }
    with open(_cookies_file(), 'w', encoding='utf-8') as f:
        json.dump(cookies, f)


def load_cookies() -> dict | None:
    path = _cookies_file()
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def clear_cookies() -> None:
    path = _cookies_file()
    if os.path.exists(path):
        os.remove(path)
