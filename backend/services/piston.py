import asyncio
import os
import sys
import tempfile

import httpx

from services import metrics
from services.logger import log
from services.retry import with_retry

# --- Judge0 ---------------------------------------------------------------
# Judge0 CE, called twice: first the public instance (no key, no signup,
# best-effort/no SLA), then — if configured — a RapidAPI-hosted Judge0
# instance as a more reliable second attempt before falling back locally.
# Both speak the same request/response shape, so one function serves both.
_JUDGE0_PUBLIC_URL = os.environ.get("JUDGE0_PUBLIC_URL", "https://ce.judge0.com")
_JUDGE0_RAPIDAPI_URL = os.environ.get("JUDGE0_RAPIDAPI_URL", "https://judge0-ce.p.rapidapi.com")
_JUDGE0_RAPIDAPI_KEY = os.environ.get("JUDGE0_RAPIDAPI_KEY")
_JUDGE0_RAPIDAPI_HOST = os.environ.get("JUDGE0_RAPIDAPI_HOST", "judge0-ce.p.rapidapi.com")

_JUDGE0_LANGUAGE_ID = {
    "python": 71,  # Python 3.8.1
    "node":   63,  # JavaScript (Node.js 12.14.0)
    "java":   62,  # Java (OpenJDK 13.0.1)
    "gcc":    54,  # C++ (GCC 9.2.0)
}

# Judge0 status id 13 = "Internal Error" — the judge itself failed to run the
# submission (infra problem on their end), as opposed to ids like 6 (Compile
# Error) or 11 (Runtime Error), which are legitimate results to show the
# candidate. Only 13 (and a missing/unparseable status) counts as "unavailable".
_JUDGE0_INFRA_ERROR_STATUS = 13

_OCI_MARKERS = ("OCI runtime", "crun: clone", "Resource temporarily unavailable")


def _is_oci_error(text: str) -> bool:
    return any(m in text for m in _OCI_MARKERS)


async def _judge0(base_url: str, headers: dict, language: str, source: str, stdin: str) -> dict | None:
    lang_id = _JUDGE0_LANGUAGE_ID.get(language)
    if lang_id is None:
        return None
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.post(
                f"{base_url}/submissions",
                params={"base64_encoded": "false", "wait": "true"},
                headers={"Content-Type": "application/json", **headers},
                json={"source_code": source, "language_id": lang_id, "stdin": stdin},
            )
            resp.raise_for_status()
            data = resp.json()
            status_id = data.get("status", {}).get("id")
            if status_id is None or status_id == _JUDGE0_INFRA_ERROR_STATUS:
                return None
            stderr = data.get("stderr") or data.get("compile_output") or ""
            if _is_oci_error(stderr):
                return None
            return {
                "run": {
                    "stdout": data.get("stdout") or "",
                    "stderr": stderr,
                    "code": 0 if status_id == 3 else 1,
                }
            }
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError):
        return None


async def _judge0_public(language: str, source: str, stdin: str) -> dict | None:
    return await _judge0(_JUDGE0_PUBLIC_URL, {}, language, source, stdin)


async def _judge0_rapidapi(language: str, source: str, stdin: str) -> dict | None:
    if not _JUDGE0_RAPIDAPI_KEY:
        return None
    headers = {"X-RapidAPI-Key": _JUDGE0_RAPIDAPI_KEY, "X-RapidAPI-Host": _JUDGE0_RAPIDAPI_HOST}
    return await _judge0(_JUDGE0_RAPIDAPI_URL, headers, language, source, stdin)


_UNAVAILABLE = {
    "run": {
        "stdout": "",
        "stderr": "Code execution is temporarily unavailable. Please try again in a moment.",
        "code": -1,
    }
}


async def _local_subprocess(language: str, source: str, stdin: str) -> dict | None:
    """Last-resort: run code directly in the backend container.
    Python is always available (the backend IS Python 3.12). Node and C++
    (gcc language id) work if node/g++ are installed in the container image."""
    tmp = None
    compiled_bin = None
    try:
        if language == "python":
            suffix, run_argv, compile_argv = ".py", [sys.executable], None
        elif language == "node":
            suffix, run_argv, compile_argv = ".js", ["node"], None
        elif language == "gcc":
            suffix = ".cpp"
            compiled_bin = tempfile.NamedTemporaryFile(suffix=".out", delete=False).name
            os.unlink(compiled_bin)  # let the compiler create it
            compile_argv = ["g++", "-O2", "-std=c++17", "-o", compiled_bin]
            run_argv = [compiled_bin]
        else:
            return None

        with tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False) as f:
            f.write(source)
            tmp = f.name

        if compile_argv:
            proc = await asyncio.create_subprocess_exec(
                *compile_argv, tmp,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, compile_stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return {"run": {"stdout": "", "stderr": "Compilation timed out (15s).", "code": 1}}
            if proc.returncode != 0:
                return {"run": {"stdout": "", "stderr": compile_stderr.decode(errors="replace"), "code": proc.returncode}}

        exec_args = run_argv if compile_argv else [*run_argv, tmp]
        proc = await asyncio.create_subprocess_exec(
            *exec_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=stdin.encode() if stdin else b""),
                timeout=15,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"run": {"stdout": "", "stderr": "Time limit exceeded (15s).", "code": 1}}
    except FileNotFoundError:
        return None
    except Exception:
        return None
    finally:
        for path in (tmp, compiled_bin):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    return {
        "run": {
            "stdout": stdout.decode(errors="replace"),
            "stderr": stderr.decode(errors="replace"),
            "code": proc.returncode,
        }
    }


async def run_code(language: str, version: str, source: str, stdin: str = "") -> dict:
    import time

    backends = [
        ("judge0_public", lambda: _judge0_public(language, source, stdin), 2, 0.5),
        ("judge0_rapidapi", lambda: _judge0_rapidapi(language, source, stdin), 2, 1.0),
    ]

    for name, call, attempts, base_delay in backends:
        start = time.monotonic()
        try:
            result = await with_retry(call, attempts=attempts, base_delay=base_delay, label=name)
        except Exception:
            result = None

        if result:
            elapsed = time.monotonic() - start
            log.info("piston.run", language=language, latency_ms=round(elapsed * 1000), backend=name)
            metrics.record_sandbox(language, name, ok=True, duration_seconds=elapsed)
            return result

        log.warning(f"piston.{name}_unavailable", language=language)

    sub_start = time.monotonic()
    try:
        result = await _local_subprocess(language, source, stdin)
    except Exception:
        result = None

    if result:
        elapsed = time.monotonic() - sub_start
        log.info("piston.run", language=language, latency_ms=round(elapsed * 1000), backend="subprocess")
        # The last-resort tier: candidate code running in the backend container
        # rather than an isolated sandbox. Worth alerting on, not just counting.
        metrics.record_sandbox(language, "subprocess", ok=True, duration_seconds=elapsed)
        return result

    log.error("piston.all_unavailable", language=language)
    metrics.record_sandbox(language, "none", ok=False, duration_seconds=0.0)
    return _UNAVAILABLE
