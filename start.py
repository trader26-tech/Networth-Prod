"""
Production entry point — run uvicorn with the PORT Railway provides.

This avoids any shell-expansion ambiguity in Dockerfile CMD / Procfile /
nixpacks start commands. Reads PORT directly from os.environ and prints
the bind details so the deploy log shows exactly what's happening.
"""
import os
import sys

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")
    print(f"▶ start.py: binding uvicorn to {host}:{port}", flush=True)
    print(f"▶ start.py: env keys = {sorted(k for k in os.environ if k in ('PORT','RAILWAY_ENVIRONMENT','RAILWAY_PROJECT_NAME','RAILWAY_STATIC_URL','PYTHON_VERSION'))}", flush=True)

    # Import here so any boot-time errors surface in the same log stream as
    # uvicorn's startup messages, not as a silent crash before the first print.
    try:
        import uvicorn
    except Exception as e:
        print(f"✘ start.py: cannot import uvicorn — {e}", flush=True)
        sys.exit(1)

    uvicorn.run(
        "api.main:app",
        host=host,
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="*",
        # workers=1 implicit (we don't pass --workers); keeps boot fast + memory low
    )
