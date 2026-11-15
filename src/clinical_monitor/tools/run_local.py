import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from sqlalchemy.engine import make_url

from clinical_monitor.core.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--dashboard-port", type=int, default=8501)
    parser.add_argument("--demo-rules", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    url = make_url(settings.database_url.get_secret_value())
    if url.host == "localhost":
        url = url.set(host="127.0.0.1")
    os.environ["CLINICAL_MONITOR_DATABASE_URL"] = url.render_as_string(hide_password=False)
    os.environ["CLINICAL_MONITOR_API_URL"] = f"http://127.0.0.1:{args.api_port}"
    os.environ["PGCONNECT_TIMEOUT"] = "5"
    os.environ["YOLO_CONFIG_DIR"] = str(Path("data/model-config").resolve())
    if args.demo_rules:
        os.environ["CLINICAL_MONITOR_VITAL_RANGES"] = '{"heart_rate":{"lower":10,"upper":20}}'
        os.environ["CLINICAL_MONITOR_VITAL_TEMPORAL_RULES"] = (
            '{"heart_rate":{"min_duration_seconds":10,"max_gap_seconds":5,"min_samples":3}}'
        )
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
    directory = Path("output/run")
    directory.mkdir(parents=True, exist_ok=True)
    commands = {
        "api": [
            "uvicorn",
            "clinical_monitor.api.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(args.api_port),
        ],
        "media": [
            "celery",
            "-A",
            "clinical_monitor.worker:celery_app",
            "worker",
            "-Q",
            "media",
            "--pool=solo",
            "--loglevel=INFO",
            "--hostname=media@%h",
        ],
        "clinical": [
            "celery",
            "-A",
            "clinical_monitor.worker:celery_app",
            "worker",
            "-Q",
            "clinical",
            "--pool=solo",
            "--loglevel=INFO",
            "--hostname=clinical@%h",
        ],
        "scheduler": [
            "celery",
            "-A",
            "clinical_monitor.worker:celery_app",
            "beat",
            "--loglevel=INFO",
            "--schedule",
            "output/run/celerybeat-schedule",
        ],
        "dashboard": [
            "streamlit",
            "run",
            "src/clinical_monitor/dashboard/app.py",
            "--server.address=127.0.0.1",
            f"--server.port={args.dashboard_port}",
        ],
    }
    children, logs = [], []
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        for name, arguments in commands.items():
            log = (directory / f"{name}.log").open("w", encoding="utf-8")
            logs.append(log)
            children.append(
                subprocess.Popen(
                    [sys.executable, "-m", *arguments],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=flags,
                )
            )
        print(f"API: http://127.0.0.1:{args.api_port}", flush=True)
        print(f"Dashboard: http://127.0.0.1:{args.dashboard_port}", flush=True)
        print("Logs: output/run. Press Ctrl+C to stop the application processes.", flush=True)
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        while all(child.poll() is None for child in children):
            time.sleep(1)
        raise RuntimeError("An application process stopped; inspect output/run logs")
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
