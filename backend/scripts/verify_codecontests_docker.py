"""
Step 2 of 2 — MUST be run inside an isolated, network-disabled container
(it compiles and executes untrusted C++ reference solutions from the
CodeContests dataset). See README in this directory / the docker command
this script expects to be run with.

For each candidate problem, compiles its C++ reference solution with g++ and
runs it against every stdin/stdout test case as a real subprocess (matching
exactly how the live app will later run candidate code via Piston). Only
problems where every test case passes are kept — the reference solution and
all verification-only fields are stripped before writing the final output,
so nothing executable ships to candidates.

Usage (run from backend/, with Docker Desktop running):
    docker run --rm --network none \\
      -v "$(pwd)/scripts/verify_codecontests_docker.py:/work/verify.py:ro" \\
      -v "$(pwd)/data/codecontests_to_verify.json:/work/in.json:ro" \\
      -v "$(pwd)/data_out:/work/out" \\
      -w //work gcc:13 \\
      python3 verify.py --input in.json --output out/codecontests_verified.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import os


def _normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def _verify_one(cpp_source: str, tests: list[dict], timeout: float = 5.0) -> bool:
    with tempfile.TemporaryDirectory() as d:
        src_path = os.path.join(d, "sol.cpp")
        bin_path = os.path.join(d, "sol")
        with open(src_path, "w", encoding="utf-8") as f:
            f.write(cpp_source)

        compile_proc = subprocess.run(
            ["g++", "-O2", "-std=c++17", "-o", bin_path, src_path],
            capture_output=True, timeout=30,
        )
        if compile_proc.returncode != 0:
            return False

        for tc in tests:
            try:
                run_proc = subprocess.run(
                    [bin_path], input=tc["stdin"], capture_output=True,
                    text=True, timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                return False
            if _normalize(run_proc.stdout) != _normalize(tc["stdout"]):
                return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        candidates = json.load(f)

    verified = []
    for problem in candidates:
        ok = _verify_one(problem["_cpp_solution"], problem["tests"])
        if ok:
            clean = {k: v for k, v in problem.items() if not k.startswith("_")}
            clean["track"] = "technical"
            clean["languages"] = ["python", "node", "java", "cpp"]
            clean["function_name"] = None  # stdin/stdout style — no function signature
            verified.append(clean)
        print(f"{'PASS' if ok else 'FAIL'}: {problem['id']}")

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(verified, f, indent=2)

    print(f"\nVerified {len(verified)}/{len(candidates)} problems.")
    print(f"Written to {args.output}")


if __name__ == "__main__":
    main()
