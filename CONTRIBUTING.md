# Contributing / 贡献指南

<!-- 本文档中英双语，两种语言内容等价。 -->
<!-- This guide is bilingual; both versions are equivalent. -->

感谢你对 PASTA 的关注！
Thanks for your interest in PASTA!

## Issues / 问题反馈

- 提交前请先搜索是否已有相同 Issue，并优先使用仓库自带模板。
  Search existing issues first, and use the provided issue templates.
- Bug 报告请附运行环境、复现步骤和日志（任务目录下的 `log.txt`）。
  For bugs, include environment, reproduction steps, and logs (`log.txt` in the job directory).

## Development setup / 开发环境

1. 安装 Python 3.13 / 3.14。Windows 上 `pykep`/`pygmo` 无官方 PyPI wheel，需先从
   [inertialobs/pykep-pygmo-win-wheels](https://github.com/inertialobs/pykep-pygmo-win-wheels/releases)
   下载对应 `.whl` 安装，或通过 conda 安装。
2. 安装依赖：
   ```bash
   pip install -r requirements.txt -r requirements-dev.txt
   ```
3. 启动：
   ```bash
   python main.py        # http://127.0.0.1:8765
   ```

1. Install Python 3.13 / 3.14. On Windows, install `pykep`/`pygmo` from the
   [prebuilt wheels](https://github.com/inertialobs/pykep-pygmo-win-wheels/releases) or via conda.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt -r requirements-dev.txt
   ```
3. Run:
   ```bash
   python main.py        # http://127.0.0.1:8765
   ```

## Tests / 测试

<!-- 标记定义见 pytest.ini：requires_pykep（需原生 pykep）、slow（慢速集成/优化冒烟）。 -->
<!-- Marker definitions live in pytest.ini: requires_pykep, slow. -->

- 纯逻辑测试（无需原生 `pykep`）：
  ```bash
  python -m pytest -m "not requires_pykep and not slow"
  ```
- 完整测试（需要 `pykep`）：
  ```bash
  python -m pytest -m "not slow"
  ```
- 前端/UI 测试依赖 Playwright，请先执行 `python -m playwright install chromium`。
- 提交 PR 前请确保相关测试通过；新增功能请补充测试。

- Pure-logic tests (no native `pykep` required):
  ```bash
  python -m pytest -m "not requires_pykep and not slow"
  ```
- Full suite (requires `pykep`):
  ```bash
  python -m pytest -m "not slow"
  ```
- Frontend/UI tests use Playwright; run `python -m playwright install chromium` first.
- Make sure relevant tests pass before opening a PR, and add tests for new features.

## Coding conventions / 编码规范

- Python 遵循 PEP 8，4 空格缩进，沿用现有模块组织方式。
- 前端保持既有静态资源与模板的风格。
- 保持改动聚焦、最小化；除非确有必要，不添加注释（与现有代码风格一致）。
- 配置默认值的唯一来源是计算库的默认值定义（字段集由默认值字典的键派生）。修改默认值时只需改该处，**请勿在别处重复写死数值或硬编码其文件位置**。

- Follow PEP 8 with 4-space indentation; mirror existing module structure.
- Keep the frontend consistent with the existing static assets and templates style.
- Keep changes focused and minimal; avoid comments unless necessary (match existing style).
- The single source of truth for config defaults is the defaults definition in the engine package (the field set is derived from the default keys). Change defaults there only — **never hardcode duplicate values or the file location elsewhere**.

## Commit messages / 提交信息

<!-- 与现有提交历史一致，采用 Conventional Commits。 -->
<!-- Match the existing history: Conventional Commits. -->

```text
fix(web): reject job ids escaping runs dir
test: add config editor tests and cross-platform CI
```

## Pull requests / 提交 PR

- 目标分支为 `dev`；请填写 PR 模板并说明验证方式。
- 如产生破坏性变更，请在描述中明确标注。

- Target the `dev` branch and fill in the PR template, including verification steps.
- Clearly call out any breaking changes in the description.

## License / 许可

提交贡献即表示你同意以 [GPL-3.0](LICENSE) 许可发布你的代码。
By contributing, you agree that your contributions are licensed under [GPL-3.0](LICENSE).
