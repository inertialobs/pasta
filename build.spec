# -*- mode: python ; coding: utf-8 -*-
r"""
PyInstaller spec — 打包 PASTA (Parallel Astrodynamic Solver for Trajectory Analysis) Windows 应用。

Run as:
    pip install pyinstaller
    pyinstaller pykep_test.spec

Important Tips:
    - pykep\_vendor : heyoka 纯 Python 绑定。pykep\__init__.py 在运行时把它注入 sys.path,
                      PyInstaller 静态分析看不到,必须整目录(含 .py)打进包。
    - pykep\lib     : 全部 DLL(kep3.dll、heyoka.dll、mkl_intel_thread.3.dll、libgcc 等),
                      运行时由 __init__.py 的 add_dll_directory 定位。
    - pykep\data    : SPICE 内核(de440s.bsp 等),udpla 需要。
    - pygmo\lib     : pygmo 的 DLL(mkl_rt.3.dll 等)。应用 import pygmo 时必需,否则可删。
    - VC runtime    : msvcp140.dll 等。目标机装过 VC++ Redistributable 时可整段删除。

路径全部从已安装包自动解析,不要手写绝对路径;
spec 必须在"装了 pykep 的 venv"里运行,否则 get_package_paths 找不到包。
"""
import os

from PyInstaller.utils.hooks import get_package_paths, collect_submodules

datas = []
binaries = []

# ---- pykep 本体 ----
pykep_dir, _ = get_package_paths('pykep')
datas += [
    (os.path.join(pykep_dir, 'pykep/_vendor'), 'pykep/_vendor'),  # 整目录递归,含 .py
    (os.path.join(pykep_dir, 'pykep/lib'),     'pykep/lib'),      # DLL
    (os.path.join(pykep_dir, 'pykep/data'),    'pykep/data'),     # SPICE 内核
    (os.path.join(pykep_dir, 'pykep/trajopt/gym/tops/'),'pykep/trajopt/gym/tops/'),     # test json
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
try:
    pygmo_dir, _ = get_package_paths('pygmo')
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
    (os.path.join(_here, 'licenses'),             'licenses'),
    (os.path.join(_here, 'LICENSE'),              '.'),
    (os.path.join(_here, 'ThirdPartyNotice.md'),  '.'),
]
# 运行时目录: 冻结版 exe 首次启动时自动创建 (JobManager/预设)

# ---- VC runtime: 打进 _internal 顶层, 目标机无需安装 VC++ Redistributable ----
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
    exclude_binaries=True,          # onedir 模式(和当前 dist\test 布局一致)
    name='pasta',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                      # 关 UPX,避免压缩 DLL 引发加载问题
    console=False,                  # GUI 模式: 隐藏 cmd 窗口 (日志走 log.txt; --cli 输出重定向)
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