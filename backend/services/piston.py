import re

import httpx
import os

# Self-hosted Piston (works locally with Docker + --privileged)
PISTON_URL = os.environ.get("PISTON_URL", "http://localhost:2000/api/v2/execute")

# Wandbox — free public compiler service, no auth, no rate-limit issues
_WANDBOX_URL = "https://wandbox.org/api/compile.json"
_WANDBOX_COMPILER = {
    "python": "cpython-3.12.7",
    "node":   "nodejs-20.17.0",
    "java":   "openjdk-jdk-21+35",
    "gcc":    "gcc-head",       # gcc-head compiles C++ by default; gcc-13.2.0 defaults to C (.c)
}

# Extra Wandbox payload fields per language
_WANDBOX_EXTRA: dict[str, dict] = {
    "gcc": {"compiler-option-raw": "-std=c++17"},
}

_UNAVAILABLE = {
    "run": {
        "stdout": "",
        "stderr": "Code execution is temporarily unavailable. Please try again in a moment.",
        "code": -1,
    }
}


async def _piston(language: str, version: str, source: str, stdin: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                PISTON_URL,
                json={"language": language, "version": version,
                      "files": [{"content": source}], "stdin": stdin},
            )
            if resp.status_code == 401:
                return None
            resp.raise_for_status()
            return resp.json()
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError):
        return None


def _wandbox_source(language: str, source: str) -> str:
    # Wandbox always compiles Java as "prog.java" regardless of what the
    # candidate names their class. Java only requires the filename to match
    # a top-level class if that class is declared `public` — so strip the
    # `public` modifier from the top-level class holding main() (there can be
    # at most one public top-level class per file in valid Java anyway, and
    # only that one collides with the filename requirement).
    if language == "java":
        return re.sub(r"^public\s+class\b", "class", source, count=1, flags=re.MULTILINE)
    return source


async def _wandbox(language: str, source: str, stdin: str) -> dict | None:
    compiler = _WANDBOX_COMPILER.get(language)
    if not compiler:
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            payload = {"code": _wandbox_source(language, source), "compiler": compiler, "stdin": stdin}
            payload.update(_WANDBOX_EXTRA.get(language, {}))
            resp = await client.post(_WANDBOX_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
            stderr = data.get("program_error") or data.get("compiler_error") or ""
            return {
                "run": {
                    "stdout": data.get("program_output") or "",
                    "stderr": stderr,
                    "code": int(data.get("status", 0)),
                }
            }
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError):
        return None


async def run_code(language: str, version: str, source: str, stdin: str = "") -> dict:
    result = await _piston(language, version, source, stdin)
    if result:
        return result

    result = await _wandbox(language, source, stdin)
    if result:
        return result

    return _UNAVAILABLE
