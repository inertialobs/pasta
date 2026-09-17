# -*- coding: utf-8 -*-
import copy
import json
import os
from pathlib import Path

from . import COMPUTE_DEFAULTS, SYS_DEFAULTS

CONFIG_FILE = Path("pasta.settings.json")

class Settings(dict):
    def __init__(self):
        super().__init__()
        dict.update(self, copy.deepcopy({**SYS_DEFAULTS, **COMPUTE_DEFAULTS}))

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(f"'Settings' has no attribute '{name}'")

    def __setattr__(self, name, value):
        if name in self:
            self[name]=value
        else: raise AttributeError(f"'Settings' has no attribute '{name}'")

    def load_file(self, path=None):
        '''Load from json file'''
        try:
            with open(path or os.path.abspath(CONFIG_FILE), "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            self.update(data)

    def save_file(self, path=None):
        '''save to json file'''
        with open(path or os.path.abspath(CONFIG_FILE), "w", encoding="utf-8") as f:
            json.dump(dict(self), f, indent=2, ensure_ascii=False)

    def validate(self) -> bool:
        '''validate settings'''
        if not self["host"]:
            raise ValueError("host 不能为空")
        if not (type(self["port"])==int and 0 <= self["port"] <= 65535):
            raise ValueError(f"port 应为 0-65535 的整数, 实际 {self['port']}")
        if not (type(self["jobs"])==int and 1 <= self["jobs"] <= 256):
            raise ValueError(f"线程数应为 1-256 的整数, 实际 {self['jobs']}")
        for k in ("scan_keep_pct", "refine_keep_pct"):
            if type(self[k]) != int or not (1 <= self[k] <= 100):
                raise ValueError(f"{k} 应为 1-100 的整数百分比, 实际 {self[k]!r}")
        if type(self["era_step_d"]) not in (int, float) or self["era_step_d"] < 1:
            raise ValueError("era_step_d 应 >= 1 天")
        cov = self["t0_coverage"]
        if (not isinstance(cov, list) or not cov or len(cov) < 5
                or any(type(i) not in (int, float) or not (0 < i <= 1) for i in cov)):
            raise ValueError(f"t0_coverage 应为5项非空列表, 每项 0<i<=1, 实际 {cov!r}")
        return True

    def update(self, d:dict):
        '''update config'''
        patch = {k: v for k, v in (d or {}).items() if k in self}
        # 先在候选副本上校验, 通过后再提交, 避免校验失败时把全局对象改脏
        cand = Settings()
        dict.update(cand, self)
        dict.update(cand, patch)
        cand.validate()
        dict.update(self, patch)

settings = Settings()