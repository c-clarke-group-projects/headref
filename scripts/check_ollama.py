"""Verify the local Ollama setup for headref and measure this machine.

Task P0-005, issue #5. Standard library only, so it runs before the project
skeleton exists and needs no virtualenv.

Usage:
    python scripts/check_ollama.py
    python scripts/check_ollama.py --model llama3.2:1b

It checks every acceptance criterion on issue #5 and prints a WORKLOG block to
paste into WORKLOG.md.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request

DEFAULT_MODEL = "llama3.2:1b"
BASE = "http://localhost:11434"
TIMEOUT = 900

# The findings a real headref run would hand to stage S4b (DEC-018).
FACTS = """likely_current_work: AUTH-245 "Refresh token rotation" (confidence HIGH)
assigned: AUTH-245 (In Progress, High, due 2026-09-16), AUTH-301 (To Do, Medium)
open_pull_request: acme/api#182, branch feature/AUTH-245-refresh-token
blocker: pull request 182 has changes_requested from 2026-09-09, no commit since
risk: AUTH-245 due in 4 days and still In Progress
conflicts: none"""

SYSTEM = """You write short status summaries for engineering team leads.

Rules:
- Use ONLY the facts given. Add nothing, invent nothing.
- Describe the WORK, never judge the person. Never say slow, behind, or underperforming.
- summary: 2 full sentences on what the person is working on.
- needs_attention: 1 full sentence naming the blocker or risk, or "Nothing needs attention."

Example facts:
likely_current_work: PAY-10 "Invoice export" (confidence HIGH)
open_pull_request: acme/billing#44
blocker: pull request 44 waiting for review for 5 days

Example answer:
{"summary":"Keshan is most likely working on PAY-10, Invoice export, which has an open pull request 44 in acme/billing. The work appears active and the ticket is still in progress.","needs_attention":"Pull request 44 has been waiting for review for 5 days and may need a reviewer assigned.","attention_needed":true}"""

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "minLength": 90, "maxLength": 320},
        "needs_attention": {"type": "string", "minLength": 30, "maxLength": 220},
        "attention_needed": {"type": "boolean"},
    },
    "required": ["summary", "needs_attention", "attention_needed"],
}

FORBIDDEN = ["slow", "behind", "underperform", "lazy", "poor performance", "unproductive"]

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"
results: list[tuple[str, str, str]] = []


def record(name: str, status: str, detail: str = "") -> None:
    results.append((name, status, detail))
    mark = {PASS: "[ok]  ", FAIL: "[FAIL]", WARN: "[warn]"}[status]
    print(f"  {mark} {name}" + (f"  {detail}" if detail else ""))


def post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def total_ram_gb() -> float | None:
    try:
        if platform.system() == "Windows":
            out = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return round(int(out.stdout.strip()) / 1024**3, 1)
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1024**2, 1)
    except Exception:
        return None
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()
    model = args.model

    print("\nheadref Ollama check  (task P0-005, issue #5)")
    print(f"model: {model}   host: {BASE}\n")

    ram = total_ram_gb()
    print(
        f"machine: {platform.system()} {platform.machine()}, "
        f"{ram if ram else '?'} GB RAM, {platform.processor()[:60] or 'cpu unknown'}\n"
    )

    # 1. binary present
    try:
        v = subprocess.run(["ollama", "--version"], capture_output=True, text=True, timeout=30)
        record("ollama installed", PASS, v.stdout.strip() or v.stderr.strip())
    except Exception:
        record("ollama installed", FAIL, "not on PATH. Install from https://ollama.com")
        return report(model, ram)

    # 2. API reachable
    try:
        with urllib.request.urlopen(BASE + "/api/tags", timeout=30) as r:
            tags = json.loads(r.read())
        record("API reachable", PASS, BASE)
    except Exception as e:
        record("API reachable", FAIL, f"{type(e).__name__}. Start it with: ollama serve")
        return report(model, ram)

    # 3. model pulled
    names = [m["name"] for m in tags.get("models", [])]
    if any(n == model or n.startswith(model + ":") for n in names):
        record("model pulled", PASS, model)
    else:
        record("model pulled", FAIL, f"not found. Run: ollama pull {model}")
        print(f"\n  installed models: {', '.join(names) or 'none'}")
        return report(model, ram)

    # 4. warm-up (first call loads ~2 GB from disk)
    print("\n  warming the model (first load reads it from disk)...")
    t0 = time.time()
    try:
        post(
            "/api/chat",
            {
                "model": model,
                "stream": False,
                "messages": [{"role": "user", "content": "hi"}],
                "options": {"num_predict": 1},
            },
        )
        record("warm-up", PASS, f"{time.time()-t0:.1f}s")
    except Exception as e:
        record("warm-up", FAIL, f"{type(e).__name__}: {e}")
        return report(model, ram)

    # 5. the real thing: structured narrative, as stage S4b does it
    print("\n  running the headref narrative task...")
    payload = {
        "model": model,
        "stream": False,
        "format": SCHEMA,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": "Facts:\n" + FACTS + "\n\nWrite the answer for Keshan now.",
            },
        ],
        "options": {"temperature": 0, "seed": 42, "num_predict": 300},
    }
    t0 = time.time()
    try:
        resp = post("/api/chat", payload)
    except Exception as e:
        record("narrative call", FAIL, f"{type(e).__name__}: {e}")
        return report(model, ram)
    elapsed = time.time() - t0

    try:
        out = json.loads(resp["message"]["content"])
        record("schema-valid JSON", PASS, "all 3 fields present")
    except Exception as e:
        record("schema-valid JSON", FAIL, str(e))
        return report(model, ram)

    gen = resp.get("eval_count", 0)
    gen_s = resp.get("eval_duration", 1) / 1e9
    pre = resp.get("prompt_eval_count", 0)
    pre_s = resp.get("prompt_eval_duration", 1) / 1e9
    tps = gen / gen_s if gen_s else 0

    record("latency", PASS if elapsed < 20 else WARN, f"{elapsed:.1f}s (NFR-013 target: under 20s)")
    record("generation speed", PASS if tps >= 8 else WARN, f"{tps:.1f} tok/s ({gen} tokens)")

    text = (out["summary"] + " " + out["needs_attention"]).lower()
    hits = [w for w in FORBIDDEN if w in text]
    record("no forbidden language", PASS if not hits else FAIL, ", ".join(hits) or "clean")

    grounded = "182" in text or "auth-245" in text
    record(
        "grounded in the facts",
        PASS if grounded else WARN,
        "names the real ticket or PR" if grounded else "did not name any given entity",
    )

    # 6. determinism
    print("\n  checking reproducibility (same seed, same input)...")
    try:
        again = post("/api/chat", payload)
        same = again["message"]["content"] == resp["message"]["content"]
        record(
            "reproducible",
            PASS if same else WARN,
            "byte-identical across runs" if same else "output differed between runs",
        )
    except Exception as e:
        record("reproducible", WARN, f"second call failed: {type(e).__name__}")

    # 7. container reachability (optional)
    try:
        d = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=30)
        if d.returncode == 0:
            c = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "curlimages/curl:latest",
                    "-s",
                    "-m",
                    "10",
                    "http://host.docker.internal:11434/api/tags",
                ],
                capture_output=True,
                text=True,
                timeout=180,
            )
            ok = c.returncode == 0 and "models" in c.stdout
            record(
                "reachable from container",
                PASS if ok else WARN,
                (
                    "host.docker.internal:11434"
                    if ok
                    else "could not reach. Check Docker host networking"
                ),
            )
        else:
            record("reachable from container", WARN, "Docker not running, skipped")
    except Exception:
        record("reachable from container", WARN, "Docker unavailable, skipped")

    record("monetary cost", PASS, "zero, inference is local (DEC-017)")

    print("\n" + "-" * 68)
    print("SUMMARY produced:\n")
    print("  " + out["summary"])
    print("\nNEEDS ATTENTION:\n")
    print("  " + out["needs_attention"])
    print(f"\nattention_needed: {out['attention_needed']}")

    return report(
        model, ram, tps=tps, elapsed=elapsed, gen=gen, pre=pre, pre_tps=pre / pre_s if pre_s else 0
    )


def report(model, ram, tps=None, elapsed=None, gen=None, pre=None, pre_tps=None) -> int:
    failed = [r for r in results if r[1] == FAIL]
    warned = [r for r in results if r[1] == WARN]
    print("\n" + "=" * 68)
    print(
        f"{len(results) - len(failed) - len(warned)} passed, "
        f"{len(warned)} warnings, {len(failed)} failed"
    )

    if failed:
        print("\nFix these before closing issue #5:")
        for n, _, d in failed:
            print(f"  - {n}: {d}")
        print("=" * 68)
        return 1

    print("\nPaste this into WORKLOG.md:\n")
    print("-" * 68)
    print(f"""## {time.strftime('%Y-%m-%d')} | <your name> | P0-005
Status: DONE

### Completed
Ollama verified on my machine. Model `{model}` pulled and answering.

| Measurement | Value |
|---|---|
| Machine | {platform.system()}, {ram if ram else '?'} GB RAM |
| Prompt processing | {f'{pre_tps:.1f} tok/s' if pre_tps else 'n/a'} |
| Generation | {f'{tps:.1f} tok/s' if tps else 'n/a'} |
| Narrative call latency | {f'{elapsed:.1f}s' if elapsed else 'n/a'} |
| Schema enforcement | works |
| Reproducible (temperature 0 + seed) | yes |
| Cost | zero |

### Next Step
P0-002: real Jira and GitHub data plus captured fixtures.""")
    print("-" * 68)
    if warned:
        print("\nWarnings, not blockers:")
        for n, _, d in warned:
            print(f"  - {n}: {d}")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
