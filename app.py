"""前端入口：启动 Web 服务（FastAPI + SSE）。

用法: python app.py
      python app.py --port 8123
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="8000")
    parser.add_argument("--reload", action="store_true", help="代码改动自动重载")
    args = parser.parse_args()

    command = [
        sys.executable, "-m", "uvicorn", "server.main:app",
        "--host", "127.0.0.1", "--port", args.port,
    ]
    if args.reload:
        command.append("--reload")

    print(f"Material Mind Engine -> http://127.0.0.1:{args.port}")
    subprocess.run(command, cwd=ROOT)


if __name__ == "__main__":
    main()
