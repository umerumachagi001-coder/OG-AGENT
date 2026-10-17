"""Check the Sarvam key without ever printing it.

    python scripts/check_sarvam.py

Reports where the key comes from (.env or Windows/system environment), whether it has quotes, spaces or
hidden characters, and asks Sarvam once (a 2-word translation) whether it accepts the key.
"""
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
NAME = "SARVAM_API_KEY"


def from_env_file():
    f = ROOT / ".env"
    if not f.exists():
        return None, 0
    raw = f.read_bytes()
    text = raw.decode("utf-8-sig", errors="replace")
    vals = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(NAME) and "=" in s and s.split("=", 1)[0].strip() == NAME:
            vals.append(s.split("=", 1)[1])
    return (vals[-1] if vals else None), len(vals)


def describe(label, v):
    problems = []
    if v != v.strip():
        problems.append("spaces before/after the key")
    core = v.strip()
    if core[:1] in "\"'" or core[-1:] in "\"'":
        problems.append("quotes around the key (remove them)")
    if "#" in core:
        problems.append("a # comment on the same line")
    if any(ord(c) > 126 or ord(c) < 33 for c in core.strip("\"'")):
        problems.append("hidden or non-ASCII characters (retype or re-copy it)")
    print(f"  {label}: {len(core)} characters" + (f"  PROBLEM: {'; '.join(problems)}" if problems else "  looks clean"))
    return core


def ask_sarvam(key):
    url = (os.environ.get("OEA_SARVAM_URL") or "https://api.sarvam.ai").rstrip("/")
    try:
        r = httpx.post(f"{url}/translate", timeout=20, headers={"api-subscription-key": key},
                       json={"input": "hello team", "source_language_code": "en-IN", "target_language_code": "hi-IN",
                             "model": "sarvam-translate:v1"})
    except httpx.HTTPError as e:
        print(f"  Could not reach Sarvam: {e}")
        return
    if r.status_code < 300:
        print("  Sarvam ACCEPTS this key. Voice will work after you restart: python scripts/dev.py")
    else:
        code = ""
        try:
            body = r.json()
            err = body.get("error") if isinstance(body, dict) else None
            code = err.get("code", "") if isinstance(err, dict) else (err or "")
        except ValueError:
            pass
        print(f"  Sarvam REJECTED this key: HTTP {r.status_code} {code}")
        if r.status_code in (401, 403):
            print("  -> Copy a fresh key from dashboard.sarvam.ai (API Keys) into .env and run this again.")
        elif r.status_code == 429:
            print("  -> Key is fine but rate-limited or out of credits; check your Sarvam dashboard.")


def main():
    file_val, count = from_env_file()
    sys_val = os.environ.get(NAME)
    print(f"{NAME}:")
    if count > 1:
        print(f"  PROBLEM: .env has {count} {NAME} lines; the last one is used. Delete the others.")
    if file_val is None or not file_val.strip():
        print("  .env: not set")
    else:
        describe(".env", file_val)
    if sys_val:
        print("  PROBLEM: Windows/system environment also has SARVAM_API_KEY, and the app uses THAT one, not .env.")
        describe("system", sys_val)
        if file_val and sys_val.strip() != file_val.strip():
            print("  The two values are different.")
        print("  Fix (PowerShell): [Environment]::SetEnvironmentVariable('SARVAM_API_KEY', $null, 'User'), then open a NEW terminal.")
    key = (sys_val or file_val or "").strip().strip("\"'")
    if not key:
        print("  No key found. Add SARVAM_API_KEY=yourkey to .env (no quotes).")
        return
    print("Asking Sarvam (one tiny translation)...")
    ask_sarvam(key)


if __name__ == "__main__":
    main()
