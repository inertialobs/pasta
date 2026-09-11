# -*- mode: python ; coding: utf-8 -*-
r"""
PyInstaller spec — 打包 PASTA (Parallel Astrodynamic Solver for Trajectory Analysis)
跨平台 (Windows / Linux), onedir 模式。

Run as:
    pip install pyinstaller
    pyinstaller --clean build.spec

Important Tips:
    - pykep\_vendor / pykep\lib : 仅 Windows wheel 有 (heyoka 纯 Python 绑定 + DLL),
                                  运行时由 pykep\__init__.py 注入 sys.path / add_dll_directory。
    - pykep\data                : SPICE 内核 (de440s.bsp), 所有平台都需要。
    - pygmo\lib                 : 仅 Windows wheel 有 (DLL)。
    - Linux: 共享库在 site-packages 的 pykep.libs / pygmo.libs (auditwheel 风格),
             由 PyInstaller 的 ELF 依赖分析自动收集, 无需手写进 datas。
    - VC runtime (仅 Windows): msvcp140.dll 等, 目标机装过 VC++ Redistributable 时可删。

路径全部从已安装包自动解析,不要手写绝对路径;
spec 必须在"装了 pykep 的 venv"里运行,否则 get_package_paths 找不到包。

输出目录与 exe 同级 (contents_directory='.'): webapp/、presets/ 随后由
main.py 启动时 chdir 到该目录, 资源与用户数据 (runs/、配置) 同根。
"""
import os
import sys

from PyInstaller.utils.hooks import get_package_paths, collect_submodules

datas = []
binaries = []

# ---- pykep 本体 ----
# 跨平台: 只收集所有平台都存在的 data (SPICE 内核) 与 tops (test json)。
# _vendor / lib 仅 Windows wheel 中存在; Linux 的共享库在 site-packages/pykep.libs
# (auditwheel 风格), 由 PyInstaller 的 ELF 依赖分析经 RPATH 自动收集, 不在此手写。
pykep_dir, _ = get_package_paths('pykep')
datas += [
    (os.path.join(pykep_dir, 'pykep/data'),    'pykep/data'),     # SPICE 内核
    (os.path.join(pykep_dir, 'pykep/trajopt/gym/tops/'),'pykep/trajopt/gym/tops/'),     # test json
]
if sys.platform == 'win32':
    datas += [
        (os.path.join(pykep_dir, 'pykep/_vendor'), 'pykep/_vendor'),  # 整目录递归,含 .py
        (os.path.join(pykep_dir, 'pykep/lib'),     'pykep/lib'),      # DLL
    ]

# ---- 让 pykep 测试套件在冻结环境可发现(unittest.discover 需要物理文件)----
# import glob as _glob
# _test_root = os.path.join(pykep_dir, 'pykep')
# for _f in _glob.glob(os.path.join(_test_root, 'test_*.py')):
#     datas.append((_f, 'pykep'))
# _ini = os.path.join(_test_root, '__init__.py')
# if os.path.exists(_ini):
#     datas.append((_ini, 'pykep'))

# ---- pygmo(如应用 import pygmo)----
# lib/ 仅 Windows wheel 有; Linux 的 pygmo.libs 由 ELF 依赖分析自动收集。
try:
    pygmo_dir, _ = get_package_paths('pygmo')
    if sys.platform == 'win32':
        datas += [(os.path.join(pygmo_dir, 'pygmo/lib'), 'pygmo/lib')]
except Exception:
    pass

# ---- 本应用资源 (webapp 静态/模板) ----
# PyInstaller spec 全局 SPECPATH = spec 文件所在目录 (= 项目根)。
# 若在非常规上下文 exec (罕见), 回退到当前工作目录。
_here = SPECPATH if 'SPECPATH' in globals() else os.getcwd()
datas += [
    (os.path.join(_here, 'webapp/templates'),     'webapp/templates'),
    (os.path.join(_here, 'webapp/static'),        'webapp/static'),
    (os.path.join(_here, 'presets'),              'presets'),
    (os.path.join(_here, 'licenses'),             'licenses'),
    (os.path.join(_here, 'LICENSE'),              '.'),
    (os.path.join(_here, 'ThirdPartyNotice.md'),  '.'),
]
# 运行时目录: 冻结版 EXE(contents_directory='.') 使所有依赖/资源与 exe 同级;
# main.py 启动时 chdir 到 exe 目录 -> 资源 (webapp/presets) 与用户数据同根且可写,
# runs/、用户预设、pasta.settings.json 均写在 exe 旁。

# ---- VC runtime (仅 Windows): 打进包内, 目标机无需安装 VC++ Redistributable ----
if sys.platform == 'win32':
    sys32 = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32')
    for _dll in ('msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll'):
        _p = os.path.join(sys32, _dll)
        if os.path.exists(_p):
            binaries.append((_p, '.'))
#datas = [i for i in datas if os.path.exists(i)]
a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=collect_submodules('pykep') + collect_submodules('orbcalc') + ['webapp'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,          # onedir 模式
    name='pasta',
    contents_directory='.',         # 旧版 onedir 布局: 依赖/资源与 exe 同级 (cwd 即运行根)
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                      # 关 UPX,避免压缩 DLL 引发加载问题
    console=(sys.platform != 'win32'),  # Windows: GUI 隐藏 cmd; Linux: 保留控制台日志
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='pasta',
)