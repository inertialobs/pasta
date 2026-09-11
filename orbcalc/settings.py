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
        if ((type(self["scan_keep"]),type(self["refine_keep"]))!=(int,int)
             or self["scan_keep"] < 1 or self["refine_keep"] < 1):
            raise ValueError("scan_keep / refine_keep 应为正整数")
        if self["era_step_d"] is not None and (
                type(self["era_step_d"]) not in (int, float) or self["era_step_d"] < 1):
            raise ValueError("era_step_d 应为 null 或 >= 1 天")
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