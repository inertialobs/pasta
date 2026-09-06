# -*- coding: utf-8 -*-
"""
PASTA 统一日志设施。

约定:
    * 全局默认 logger "pasta"。
    * 格式: [YYYY-MM-DD HH:MM:SS][LEVEL] message
    * setup(stream=...) 绑定到某可写流; 未 setup 时默认 sys.stderr。
    * StreamHandler 每次 emit 自动 flush。
    * spawn worker: 不调用 setup, 默认 stderr (继承主进程的 log.txt 重定向)。

API:
    slog.inf/dbg/wrn/err("...")
    slog.setup(stream=..., debug=False)
    slog.set_debug(True/False)
"""
from __future__ import annotations

import logging
import sys

_FORMAT = "[%(asctime)s][%(levelname)-5s] %(message)s"
_DATE = "%Y-%m-%d %H:%M:%S"

_LG = logging.getLogger("pasta")
_LG.setLevel(logging.INFO)
_LG.propagate = False

_installed = False


def _mk_handler(stream):
    h = logging.StreamHandler(stream)  # emit 后自动 flush → 实时
    h.setFormatter(logging.Formatter(_FORMAT, _DATE))
    return h


def _install_default():
    global _installed
    _LG.handlers = [_mk_handler(sys.stderr)]
    _installed = True


def _ensure():
    if not _installed:
        _install_default()


def setup(stream=None, debug=False):
    """绑定输出流: 优先 stream, 否则 sys.stderr。debug=True 时级别 DEBUG。"""
    global _installed
    if stream is None:
        stream = sys.stderr
    _LG.handlers = [_mk_handler(stream)]
    _installed = True
    _LG.setLevel(logging.DEBUG if debug else logging.INFO)


def set_debug(on):
    _LG.setLevel(logging.DEBUG if on else logging.INFO)


def inf(msg, *a):
    _ensure()
    _LG.info(msg, *a)


def dbg(msg, *a):
    _ensure()
    _LG.debug(msg, *a)


def wrn(msg, *a):
    _ensure()
    _LG.warning(msg, *a)


def err(msg, *a):
    _ensure()
    _LG.error(msg, *a)
