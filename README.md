<p align="center">
  <img src="./assets/readme/hero.png" width="100%" alt="河南大学校园助手：本地课表、空教室、图书馆、研讨室与河宝查询，支持 MCP / Langbot / Agent Skill">
</p>

面向河南大学学生的**本地校园助手**。  
同一套能力，按使用场景拆成三条独立分支——**可运行代码在对应分支里，不在 `main`**。

| 场景 | 形态 | 分支 |
| --- | --- | --- |
| MCP 客户端 / 二次集成 | MCP Server | [`mcp-server`](https://github.com/jry21223/HENU_Assistant/tree/mcp-server) |
| QQ / Langbot 多用户 | Langbot 插件 | [`langbot-plugin`](https://github.com/jry21223/HENU_Assistant/tree/langbot-plugin) |
| Agent Skill / 本地 CLI | Agent Skill | [`agent-skill`](https://github.com/jry21223/HENU_Assistant/tree/agent-skill) |

想直接用部署版：QQ 群 **1031855485**

<details>
<summary>群二维码</summary>
<p align="center">
  <img src="./docs/group_qr.jpg" width="200" alt="QQ 群 1031855485 二维码">
</p>
</details>

### 命令方言（选形态前必读）

| 形态 | 命令风格 | 示例 |
| --- | --- | --- |
| MCP / Agent Skill | snake_case 工具/子命令 | `schedule_query`、`library_query --view locations`、`--student_id` |
| LangBot 插件 | 空格风格 CLI 字符串 | `schedule now`、`library locations`、`--student-id` |

三套能力同源，**命令字符串不通用**。

---

## 功能

| 模块 | 能力 |
| --- | --- |
| 账号 | 学号密码绑定、本地加密保存、系统状态 |
| 课表 | 同步；当前 / 日 / 周 / 全量查询 |
| 选课 | 状态查询、智能规划、只读余量监控 |
| 空教室 | 空闲 / 占用查询、资源映射 |
| 图书馆 | 区域、座位、预约、签到、取消 |
| 研讨室 | 筛选、成员组、预约、签到、取消 |
| 河宝 | 请假、签到、查寝、活动、信息收集 |
| 校准 | 节次时间源 |

<details>
<summary>半认真的「毒瘤名单」</summary>

- [x] 今日校园
- [x] 喜鹊
- [ ] 多彩校园 / 水满分
- [ ] 体适能

</details>

---

## 设计取舍

- **三形态共享能力**，不是三套互不相干的脚本（工程上各分支自带一份 `henu_mcp/` + `campus_core/`，改业务请同步）。  
- **登录可解释**：优先 IDS CAS；失败时只自动尝试 **一次** xk Kingo，不识别验证码、不循环重试。  
- **Kingo 有边界**：主要保课表 / 选课状态 / 空教室；图书馆、研讨室、河宝仍需 IDS。接口用 `auth.mode` / `degraded` / `warning` 标明状态。  
- **数据默认本地**；Langbot 版按 QQ 隔离。  
- **选课监控只读**：可提醒余量，不代替提交或退选。  
- LangBot 外部写操作（预约/签到/取消）为**两阶段确认**；账号绑定等敏感命令仅私聊直发。

---

## 快速开始

### Langbot

前置：已运行 [LangBot](https://docs.langbot.app) 与消息适配器。

**用户**：从 [Releases](https://github.com/jry21223/HENU_Assistant/releases) 安装 `*.lbpkg`，配置稳定的 `HENU_MASTER_KEY`，私聊绑定账号。

**开发**：

```bash
git clone -b langbot-plugin https://github.com/jry21223/HENU_Assistant.git
cd HENU_Assistant
chmod +x install.sh && ./install.sh
# 或：python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/pip install lbp
# cp -n .env.example .env  # 设置 HENU_MASTER_KEY=
.venv/bin/lbp run    # 或 .venv/bin/lbp build
```

### MCP

```bash
git clone -b mcp-server https://github.com/jry21223/HENU_Assistant.git henu-mcp
cd henu-mcp
python3 -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python3 mcp_server.py --transport stdio
```

本分支**没有** `henu_cli.py`。

### Agent Skill

```bash
git clone -b agent-skill https://github.com/jry21223/HENU_Assistant.git henu_campus_assistant
cd henu_campus_assistant
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python henu_cli.py system_status
```

可拷贝到 OpenClaw / Claude / Codex 等 skill 目录；执行时以**含 `henu_cli.py` 的目录**为准，详见该分支 `SKILL.md`。

各分支 README 有完整工具列表、安全约定与示例。

---

## 文档

- [校园相关 API 汇总](docs/school-api-summary.md)
- [选课接口记录](docs/course-selection-api.md)
- [空教室 API](docs/henu_empty_classroom_api_doc.md)

---

## 边界

- 需要河大学生账号，以及可访问校园系统的网络。  
- 账号 / Cookie / 缓存仅本地保存。  
- 仅供学习与个人使用，请遵守学校规定。  
- 接口可能变更，不保证长期稳定。  
- 正式认证、成绩、选课提交、财务等以官方渠道为准。  
- 请勿用于违法或违反院校 / 平台条款的用途。

## License

[MIT](LICENSE)
