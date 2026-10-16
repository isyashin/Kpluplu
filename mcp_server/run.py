"""Запуск CONS++ MCP-сервера с авто-подъёмом Search API.

1. Проверяет CONSPLUS_API/api/health
2. Если API не отвечает — поднимает uvicorn в фоне и ждёт готовности
3. Запускает stdio MCP-сервер (mcp_server/server.py)
"""
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).parent.parent
API = os.environ.get("CONSPLUS_API", "http://127.0.0.1:8741")


def api_alive() -> bool:
    try:
        with urllib.request.urlopen(f"{API}/api/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def ensure_api() -> subprocess.Popen | None:
    if api_alive():
        return
    parsed = urlsplit(API)
    if parsed.hostname not in ("127.0.0.1", "localhost"):
        raise RuntimeError("Автозапуск API разрешён только для localhost")
    port = parsed.port or 8741
    with open(ROOT / "api_autostart.log", "ab") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "api.main:app",
             "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(ROOT), stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if api_alive():
            return process
        if process.poll() is not None:
            raise RuntimeError(f"CONS++ API завершился; подробности в {ROOT / 'api_autostart.log'}")
        time.sleep(1)
    raise RuntimeError("CONS++ API не поднялся за 120 секунд")


def main():
    ensure_api()
    # запускаем сам MCP-сервер (stdio)
    sys.path.insert(0, str(ROOT))
    from mcp_server.server import mcp
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
