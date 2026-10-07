"""DairyFlow backend entrypoint.

    python main.py              dev: auto-reload when backend .py source changes (venv/ and tests/ excluded)
    python main.py --no-reload  demo: one process, never restarts

Host/port from DAIRYFLOW_API_HOST / DAIRYFLOW_API_PORT (127.0.0.1:8600). The route cache and logs live in
../.cache/ (config.CACHE_DIR), outside the watched folder, so writing them can never trigger a reload.
"""
import argparse
import logging
import logging.handlers
import os
import sys

import config

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))


def setup_logging():
    os.makedirs(config.LOG_DIR, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s")
    file_handler = logging.handlers.RotatingFileHandler(
        os.path.join(config.LOG_DIR, "backend.log"), maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        root.addHandler(file_handler)
        root.addHandler(console)


def main():
    parser = argparse.ArgumentParser(description="DairyFlow backend")
    parser.add_argument("--no-reload", action="store_true", help="demo mode: no auto-reload")
    args = parser.parse_args()
    import uvicorn

    setup_logging()
    os.chdir(BACKEND_DIR)  # uvicorn always watches the working directory too
    logging.getLogger("dairyflow").info("Starting on http://%s:%s (%s), OSRM %s, cache %s", config.API_HOST,
                                        config.API_PORT, "no reload" if args.no_reload else "reload on *.py",
                                        config.OSRM_BASE_URL, config.CACHE_DIR)
    if args.no_reload:
        uvicorn.run("api:app", host=config.API_HOST, port=config.API_PORT, log_config=None)
    else:
        uvicorn.run("api:app", host=config.API_HOST, port=config.API_PORT, log_config=None, reload=True,
                    reload_dirs=[BACKEND_DIR], reload_includes=["*.py"],
                    reload_excludes=[os.path.join(BACKEND_DIR, d) for d in ("venv", "tests", "__pycache__")
                                     if os.path.isdir(os.path.join(BACKEND_DIR, d))])


if __name__ == "__main__":
    main()
else:  # the reload worker process (__mp_main__), or `uvicorn main:app`
    setup_logging()
    from api import app  # noqa: F401
