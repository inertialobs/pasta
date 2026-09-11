# -*- coding: utf-8 -*-
import os
import json
from pathlib import Path

# 相对路径, 在 load_file/save_file 调用时才解析 -> main.py 已 chdir 到运行根。
# (不要在此处用 Path.cwd() 锚定: 本模块在 main() 之前就被 import, 那时还未 chdir)
CONFIG_FILE = Path("pasta.settings.json")

# 计算字段 (任务的计算/并行参数; 轨迹字段见 orbcalc.config.TrajConfig)。
# 每任务快照写入 runs/<jid>/config.json, run_cli 据此执行。
COMPUTE_FIELDS = {"run_scan", "run_seed", "run_compress", "run_frontier",
                  "scan_keep", "refine_keep", "era_step_d", "jobs"}

class Settings(dict):
    def __init__(self):
        super().__init__()
        self['host']="127.0.0.1"
        self["port"]=8765
        # self.single_instance : deprecated, default as True
        self["open_browser"]=True
        # show_lan_warning : deprecated

        self["run_scan"] = True
        self["run_seed"] = True
        self["run_compress"] = True
        self["run_frontier"] = True
        self["scan_keep"] = 8
        self["refine_keep"] = 6
        self["era_step_d"] = None
        self["jobs"] = 4

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

    #is_lan deprecated, use Settings["host"]=="0.0.0.0" instead

    #to_dict deprecated, it is a dict

    def update(self, d:dict):
        '''update config'''
        patch = {k: v for k, v in (d or {}).items() if k in self}
        # 先在候选副本上校验, 通过后再提交, 避免校验失败时把全局对象改脏
        cand = Settings()
        dict.update(cand, self)
        dict.update(cand, patch)
        cand.validate()
        dict.update(self, patch)

    #__str__:use dict.__str__

    # the host still use sysLan 勾选框

settings = Settings()
# when need to find setting options, use :
# from orbcalc.settings import settings
# settings["host"]/settings.host
# 首次加载后显式调用validate并update