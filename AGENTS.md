# Agent guidance

## Agent skills

### Issue tracker

Use GitHub Issues in jry21223/HENU_Assistant. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the five default triage labels. See `docs/agents/triage-labels.md`.

### Domain docs

Single context: root `GLOSSARY.md` and `docs/adr/`. See `docs/agents/domain.md`.

## 工作入口

- 本目录是 Git 根目录；此交付形态为 `langbot-plugin`，依赖 LangBot 宿主。
- 开始前核对当前分支和未提交改动。`main`、`mcp-server`、`agent-skill` 是不同交付形态，跨分支移植需明确核对接口。
- 普通交流与新增说明使用中文。输出、预览和临时产物统一放在本仓库 `output/` 下。
- 版本以 `manifest.yaml` 为准；README 中的版本示例可能滞后。

## 代码导航

- `main.py`、`manifest.yaml`：插件入口、组件和发布元数据。
- `components/cli_tools/`、`components/event_listener/`：工具入口、身份和敏感命令拦截、QQ 事件处理。
- `henu_plugin/`：业务服务、个人 Storage、确认流程、卡片与图片展示。
- `campus_core/`：校园接口与领域逻辑；`henu_mcp/`：共享运行时和工具实现。
- `ops/`：宿主适配、运维和预览脚本；`tests/`：回归测试；`docs/`：设计、契约与验收记录。
- 修改 CLI 时先读 `README.md` 的命令方言：插件用空格子命令和连字符参数，其他交付形态可能不同。

## 环境与验证

`.python-version` 与 CI 使用 Python 3.13。先确认解释器和命令可用，使用项目虚拟环境；下列 `python` 指该环境的解释器。

```bash
python -m pip install -r requirements.txt
python -m pip install lbp
```

`lbp` 是单独安装的开发/打包 CLI。调试运行 `lbp run`，需要已配置的 LangBot runtime；安装细节见 `README.md`。
按改动范围运行测试。完整 CI 检查见 `.github/workflows/release-lbp.yaml`：

```bash
python -m compileall -q components henu_plugin henu_mcp campus_core main.py mcp_server.py
python -m pytest -q
lbp build
```

改动空教室状态与渲染时，优先验证：

```bash
python -m pytest -q tests/test_classroom_states.py tests/test_classroom_minglun.py tests/test_classroom_directory.py
python ops/render-classroom-preview.py
```

示例输出位于 `output/classroom-preview/`，使用合成数据。检查 PNG 尺寸、颜色、文字和文件大小；本地生成成功不等于真实查询或 QQ 送达成功。涉及查询、房号或 QQ 入口时，再运行对应的 `test_classroom_*`、`test_empty_classroom_render_chain.py`、`test_qq_classroom_scope.py`。

## 空教室维护契约

修改前读 `docs/classroom-two-state-minglun-20261028.md`；调整文字或字体时读 `henu_plugin/assets/README.md`。

- 数据入口：`campus_core/empty_classroom/client.py` 解析上游；`query.py` 查询并生成证据；`henu_mcp/tools/empty_classroom.py` 转出共享工具入口。
- 图片入口：`henu_plugin/classroom_map.py` 的 `render_classroom_map()` / `build_classroom_image()`；照片平面图和目录展示在同目录 `classroom_floorplans.py`；资源在 `henu_plugin/assets/classroom-plans.json`。
- 标绿需要 `verified()` 通过、`freshness.roomStates.complete == true`、证据与查询的学期/周/星期/节次/校区/楼栋完全一致，以及有效的 `free`、`occupied` 与结果数量。仅有 `rooms` 不能证明当前空闲。
- 绿色表示“课程未占用”；灰色包含“课程占用或未核验”。缺失、过期、部分结果、跨范围数据不能推断为空闲。全天五个大节分别核验。
- 平面图只使用已核对位置；目录视图只表示名称。金明 `LAYOUTS` 是限定楼栋的回退；未知楼栋不能编造位置。
- 明伦图使用 `floors[].cells`、`roomKey` 和 `stateKey`。编号不确定的房间保持灰色；`605-2`、`605-3` 独立，未标号区域不推断编号。学校目录身份不能替代课表状态证据。
- 守卫限制 PNG 原始字节不超过 `2 * 1024 * 1024`；Base64 编码另有体积开销。保留异常返回 `None` 与 QQ 文字回退。
- 内置字体是字符子集；新增文案需核验字形，Pillow 可能静默缺字。保留字体许可；演示状态和示例日期只放预览输出，不写入发布资源。

## 数据与发布边界

- 维护按 QQ 发送者隔离的身份、Storage、展示资产及确认流程；敏感命令在进入模型前处理。
- 使用合成 fixture 验证；凭据、Cookie、Token、个人缓存和真实账号数据不写入示例、日志或 Git。
- 学校写操作沿用现有独立确认和结果未知保护；本地成功、上游接受、实际送达分别报告。
- 改宿主适配或发布流程时先读相关 `ops/` 文档。构建包位于 `dist/`；发版 tag 必须匹配 `manifest.yaml`，构建验证本身不包含发布。
