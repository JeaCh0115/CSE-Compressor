"""用户设置持久化（%APPDATA%\\CSE Compressor\\settings.json）。

界面与桌面外壳共用；写入采用"读-改-写"合并，避免覆盖其他键。
"""

import json
import os
import threading

APP_DIR_NAME = "CSE Compressor"

DEFAULTS = {
    "theme": "dark",            # dark | light | system
    "format": "cse",            # 默认归档格式
    "method": "auto",           # 默认压缩方式
    "solid": True,
    "dedup": True,
    "remember_window": True,
    "window": {"x": None, "y": None, "w": 1200, "h": 820, "maximized": False},
    "confirm_exit": False,
}


def config_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_DIR_NAME)


def settings_path() -> str:
    return os.path.join(config_dir(), "settings.json")


_lock = threading.Lock()


def load() -> dict:
    data = json.loads(json.dumps(DEFAULTS))  # 深拷贝
    try:
        with open(settings_path(), "r", encoding="utf-8") as f:
            user = json.load(f)
        if isinstance(user, dict):
            for k, v in user.items():
                if k in data and isinstance(data[k], dict) and isinstance(v, dict):
                    data[k].update(v)
                else:
                    data[k] = v
    except (OSError, ValueError):
        pass
    return data


def save(patch: dict) -> dict:
    """合并保存，返回保存后的完整设置。"""
    with _lock:
        current = load()
        for k, v in (patch or {}).items():
            if k in current and isinstance(current[k], dict) and isinstance(v, dict):
                current[k].update(v)
            else:
                current[k] = v
        os.makedirs(config_dir(), exist_ok=True)
        tmp = settings_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=2)
        os.replace(tmp, settings_path())
        return current


def reset() -> dict:
    """恢复默认：写回默认值（不删文件，避免把窗口位置/偏好一并丢失）。"""
    with _lock:
        data = json.loads(json.dumps(DEFAULTS))
        os.makedirs(config_dir(), exist_ok=True)
        tmp = settings_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, settings_path())
        return data
