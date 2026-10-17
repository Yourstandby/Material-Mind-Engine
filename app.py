"""前端入口：按 FRONTEND 环境变量选择 Chainlit（默认）或 Gradio。

用法: FRONTEND=gradio python app.py
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    gradio = os.environ.get("FRONTEND", "chainlit").lower() == "gradio"
    command = (
        [sys.executable, str(ROOT / "ui" / "gradio_app.py")]
        if gradio
        else [sys.executable, "-m", "chainlit", "run", str(ROOT / "ui" / "chainlit_app.py"), "-w"]
    )
    subprocess.run(command)


if __name__ == "__main__":
    main()
