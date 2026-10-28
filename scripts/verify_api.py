"""Milestone 1: verify Meta Model API access with one small request.

Reads .env (never prints the key). Lists enabled models and checks
whether META_MODEL is among them.

Run:  python3 scripts/verify_api.py
"""
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env(path):
    env = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def main():
    env_path = os.path.join(ROOT, ".env")
    if not os.path.exists(env_path):
        print("missing .env; copy .env.example to .env and add your key")
        return 1
    env = load_env(env_path)
    key = env.get("MODEL_API_KEY", "")
    base = env.get("META_BASE_URL", "").rstrip("/")
    want = env.get("META_MODEL", "")
    if not key:
        print("MODEL_API_KEY is empty in .env")
        return 1
    if not base:
        print("META_BASE_URL is empty in .env")
        return 1

    req = urllib.request.Request(
        base + "/models",
        headers={"Authorization": "Bearer " + key},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode("utf-8", "replace")
            status = r.status
    except Exception as e:  # noqa: BLE001 - report any transport/API error
        print(f"request failed: {type(e).__name__}: {e}")
        return 1

    print(f"HTTP {status}")
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        print("non-JSON response (first 300 chars):")
        print(body[:300])
        return 1
    models = data.get("data", data) if isinstance(data, dict) else data
    ids = [m.get("id", m) if isinstance(m, dict) else m for m in models] if isinstance(models, list) else []
    print("enabled models:", ids if ids else "(unrecognized shape; first 300 chars) " + body[:300])
    if want:
        print(f"target {want}: {'ENABLED' if want in ids else 'NOT LISTED'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
