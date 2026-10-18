"""配置读取：直接用系统环境变量，不强制 .env。

优先级（先到先得，前面覆盖后面）：
    1. 当前进程已有的环境变量（shell / 启动器传入）
    2. 项目根目录 .env（可选，override=False 不会覆盖系统变量）
    3. Windows 用户/系统环境变量（注册表兜底 —— 解决"刚改完环境变量但终端没重启"）

用法:
    from core.config import settings, status
    cfg = settings()        # {"provider","api_key","base_url","model"}
    status()                # 给前端看的状态（密钥已脱敏）
"""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")  # 文件不存在时静默跳过；不覆盖已有环境变量

WANTED = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "LLM_MODEL",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "EMBEDDING_API_KEY",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIM",
    "CHROMA_PATH",
)

# 已知服务商的默认端点与模型
PROVIDER_DEFAULTS = {
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
}


def _import_windows_env() -> None:
    """Windows 下把未注入进程的用户/系统环境变量读进来（只读白名单）。"""
    if os.name != "nt":
        return
    try:
        import winreg
    except ImportError:
        return

    sources = (
        (winreg.HKEY_CURRENT_USER, r"Environment"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
    )
    for hive, path in sources:
        try:
            with winreg.OpenKey(hive, path) as key:
                index = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    index += 1
                    if name in WANTED and not os.environ.get(name):
                        os.environ[name] = str(value)
        except OSError:
            continue


_import_windows_env()


def env(name: str, default: str = "") -> str:
    return os.environ.get(name) or default


def settings() -> dict:
    deepseek_key = env("DEEPSEEK_API_KEY")
    openai_key = env("OPENAI_API_KEY")

    if openai_key:
        provider = "openai"
        api_key = openai_key
    elif deepseek_key:
        provider = "deepseek"
        api_key = deepseek_key
    else:
        provider = "openai"
        api_key = ""

    defaults = PROVIDER_DEFAULTS.get(provider, {})
    base_url = (
        env("OPENAI_BASE_URL")
        or env("DEEPSEEK_BASE_URL")
        or defaults.get("base_url", "")
    )
    model = env("LLM_MODEL") or defaults.get("model", "")

    return {
        "provider": provider,
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "chroma_path": env("CHROMA_PATH", "./chroma_db"),
    }


def mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * 6}{value[-4:]}"


def status() -> dict:
    """给 /api/health 与前端的状态，密钥脱敏。"""
    cfg = settings()
    missing = [k for k in ("api_key", "base_url", "model") if not cfg[k]]
    return {
        "provider": cfg["provider"],
        "model": cfg["model"],
        "base_url": cfg["base_url"],
        "api_key": mask(cfg["api_key"]),
        "ready": not missing,
        "missing": missing,
        "source": "环境变量",
    }
