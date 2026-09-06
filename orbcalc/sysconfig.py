# -*- coding: utf-8 -*-
"""
SysConfig — 全局系统配置 (区别于每任务的 TrajConfig)。

管理服务监听地址、端口、单实例等**系统级**参数, 持久化到 JSON 文件
, 提供 load/save 函数。

字段:
- host: 监听地址 (默认 127.0.0.1; 0.0.0.0 允许局域网访问, 需安全内网)
- port: 端口 (默认 8765; 配合固定端口单实例)
- single_instance: 端口占用时直接打开已有实例 (默认 True)
- open_browser: 启动后自动打开浏览器 (默认 True)
- show_lan_warning: host 为 0.0.0.0 时前端显示安全警告 (默认 True)
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, asdict

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
SYS_FILE = "orbitcalculator.sys.json"


def _base_dir():
    """可执行文件(冻结)/工作目录(开发)所在目录."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return os.path.dirname(sys.executable)
    return os.getcwd()


def sysconfig_path(preferred=None):
    """返回 SysConfig 文件路径: 显式 > 程序目录"""
    if preferred:
        return preferred
    return os.path.join(_base_dir(), SYS_FILE)


@dataclass
class SysConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    single_instance: bool = True
    open_browser: bool = True
    show_lan_warning: bool = True

    def validate(self):
        if not self.host:
            raise ValueError("host 不能为空")
        if not (0 <= self.port <= 65535):   # 0 = 随机空闲端口 (无单实例语义)
            raise ValueError(f"port 应在 0-65535, 实际 {self.port}")
        return True

    @property
    def is_lan(self):
        """监听非本机回环 → 局域网暴露."""
        return self.host in ("0.0.0.0", "::")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        known = {f.name for f in cls.__dataclass_fields__.values()}
        kw = {k: v for k, v in d.items() if k in known}
        return cls(**kw)

    def to_json(self, path=None, indent=2):
        txt = json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(txt)
        return txt

    @classmethod
    def from_json(cls, path=None, text=None):
        if text is None:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        return cls.from_dict(json.loads(text))

    def __str__(self):
        return (f"SysConfig(host={self.host}, port={self.port}, "
                f"single_instance={self.single_instance}, open_browser={self.open_browser})")


def default_sysconfig():
    return SysConfig()


def load_sysconfig(preferred=None):
    """从磁盘加载; 文件不存在回退默认; JSON 损坏回退默认 (不抛异常)."""
    path = sysconfig_path(preferred)
    try:
        return SysConfig.from_json(path=path)
    except Exception:
        return SysConfig()


def save_sysconfig(cfg, preferred=None):
    """保存到磁盘; 返回实际路径."""
    path = sysconfig_path(preferred)
    cfg.to_json(path=path)
    return path