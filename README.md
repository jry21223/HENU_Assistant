# 河大校园助手 · Langbot 插件版

将校园助手封装为 **Langbot 插件**，对外主要暴露统一 CLI 工具 `henu_cli`，并按 **QQ 账号隔离** 保存数据。

> 同能力的其他形态：[`mcp-server`](https://github.com/jry21223/HENU_Assistant/tree/mcp-server)、[`agent-skill`](https://github.com/jry21223/HENU_Assistant/tree/agent-skill)。

当前版本见 `manifest.yaml`（如 `2.0.2`）。

## 安装与运行

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env
.venv/bin/lbp run
```

构建插件包：

```bash
.venv/bin/lbp build
```

## CI 自动发布

GitHub Actions（`release-lbp.yaml`）：

- 推送 `v<version>` tag 时自动 `lbp build`
- 将 `dist/*.lbpkg` 上传到对应 GitHub Release

```bash
git tag v2.0.2
git push origin v2.0.2
```

## 常用 CLI

```text
help
account status
account set --student-id 20230001 --password 'secret'
schedule now
schedule day --date 2026-03-30
course status
course plan --excel ./courses.xlsx --class 25软工1 --like-early8 --compact-days --target-days 3
course filter --excel ./courses.xlsx --class 25软工1
course schema
course monitor config --config-json '{"targets":[{"course_id":"04500142","course_name":"数据结构","keywords":["25网工4"]}]}'
course monitor once
empty_classroom query --week 1 --day-of-week 1 --period 1 --building-text 十号楼
empty_classroom query --view occupancy --classroom-text 十号楼101
resource search 十号楼101
library current
library seats --location "<区域>" --date 2026-03-30 --time 08:00
library reserve --location "<区域>" --seat-no "<座位号>"
seminar rooms --date 2026-03-30 --start 14:00 --end 16:00 --members 4
seminar signin --auto-scan
```

## 数据隔离

按当前发消息的 QQ 身份选择存储目录：

| 场景 | 规则 |
| --- | --- |
| 群聊 | 优先 `sender_id` |
| 私聊 | 回退 `launcher_id` |
| 用户数据 | `data/users/<qq>/` |
| 共享配置 | `data/shared/` |

**用户目录**：账号配置、该 QQ 的 IDS CAS Cookie jar、业务 Cookie/Token、研讨室签到任务、选课监控配置、课表抓取结果。  

**共享目录**：公共校园配置与缓存（节次时间、校准状态、空教室公共参数等）。  
`shared:*` Storage key **不**保存密码、`CASTGC`、业务 Token 或个人 Cookie。

### 认证

- 优先 IDS；失败时仅自动一次 xk Kingo 降级；不识别验证码、不循环重试。  
- IDS 可供课表、选课、空教室、图书馆、研讨室、河宝复用。  
- Kingo 主要保证 xk 能力，不生成/覆盖 CAS Jar；其他服务仍需 IDS。  
- 账号初始化 / 登录检查 / 课表同步 / 系统状态通过 `auth` 返回模式、降级状态、错误码与能力警告。

## 目录结构

```text
manifest.yaml                 # 插件清单
main.py                       # 插件入口
components/cli_tools/         # 对 LLM 暴露的统一 CLI Tool
henu_plugin/service.py        # 工具分发与存储上下文
henu_plugin/cli.py            # CLI 解析与帮助
mcp_server.py                 # 兼容 MCP/Agent 的薄门面
henu_mcp/                     # 复用业务逻辑与路径切换
campus_core/                  # 底层校园集成
tests/                        # pytest
docs/                         # 设计/计划文档
```

说明：插件版**不会**启动后台研讨室自动签到线程；需要时调用 `seminar signin --auto-scan` 或指定 `record_id`。

## 智能选课

模块：`campus_core.smart_course_selector`（与 MCP / Agent Skill 共用逻辑）。

- 从 Excel 或清洗 JSON 读取选项；按班级筛选。  
- 偏好规划无冲突课表；输出 `henu.smart_course_selection.v1`。  
- `course status`：只读选课状态，不提交。  
- `course monitor`：只读余量监控，可飞书提醒；**不会**选课/退选。

```text
course plan --excel ./courses.xlsx --class 25软工1 --like-early8 --compact-days --target-days 3
course filter --excel ./courses.xlsx --class 25软工1
course schema
course status
course monitor once
```

## 测试

```bash
.venv/bin/pip install pytest   # 若环境中尚无
.venv/bin/pytest
```
