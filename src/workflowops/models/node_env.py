"""Node child-only network settings; read existing Windows loopback proxy without mutation."""

import os
from collections.abc import Mapping
from urllib.parse import urlsplit


def local_proxy(server: str) -> str | None:
    """Use only the configured HTTPS/default HTTP loopback proxy, without credentials."""
    entries: dict[str, str] = {}
    for item in server.split(";"):
        if "=" in item:
            protocol, value = item.split("=", 1)
            entries[protocol.strip().lower()] = value.strip()
        else:
            entries["default"] = item.strip()
    value = entries.get("https", entries.get("default", ""))
    if not value or len(value) > 2048:
        return None
    try:
        url = urlsplit(value if "://" in value else "http://" + value)
        port = url.port
    except ValueError:
        return None
    if (
        url.scheme != "http"
        or url.hostname not in {"127.0.0.1", "localhost", "::1"}
        or port is None
        or not 1 <= port <= 65535
        or url.username is not None
        or url.password is not None
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
    ):
        return None
    return url.geturl().rstrip("/")


def windows_proxy() -> str | None:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            if not winreg.QueryValueEx(key, "ProxyEnable")[0]:
                return None
            server = winreg.QueryValueEx(key, "ProxyServer")[0]
        return local_proxy(server) if isinstance(server, str) else None
    except (ImportError, OSError):
        return None


def node_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if source is None else source)
    for key in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "PI_CODING_AGENT_DIR"):
        env.pop(key, None)
    if env.get("NODE_USE_ENV_PROXY") == "0":
        return env
    if env.get("HTTPS_PROXY") or env.get("HTTP_PROXY"):
        env.setdefault("NODE_USE_ENV_PROXY", "1")
        return env
    proxy = windows_proxy()
    if proxy:
        env.update(HTTP_PROXY=proxy, HTTPS_PROXY=proxy, NODE_USE_ENV_PROXY="1")
        existing = env.get("NO_PROXY", "")
        parts = [x.strip() for x in existing.split(",") if x.strip()]
        for host in ("localhost", "127.0.0.1", "::1"):
            if host not in parts:
                parts.append(host)
        env["NO_PROXY"] = ",".join(parts)
    return env
