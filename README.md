# 河大校园助手（OpenClaw Skill）

为河南大学学生提供课表查询、图书馆预约、研讨室预约等能力，当前仓库包含多种接入形态，可按你的客户端和使用方式选择。

## 版本入口

### Langbot 插件版
**分支**: [`langbot-plugin`](https://github.com/jry21223/HENU_Assistant/tree/langbot-plugin)
- 适用于 Langbot 插件生态
- 已封装为 Langbot `Tool` 组件
- 支持按不同 QQ 账号隔离保存河大学号和密码
- 适合 Langbot 机器人直接接入校园能力

### MCP 服务器版
**分支**: [`mcp-server`](https://github.com/jry21223/HENU_Assistant/tree/mcp-server)
- 基于 Model Context Protocol (MCP)
- 适用于支持 MCP 协议的 AI 客户端
- 提供完整工具集和 API 接口
- 适合开发者和高级用户

### OpenClaw Skill 版
**分支**: [`openclaw-skill`](https://github.com/jry21223/HENU_Assistant/tree/openclaw-skill)
- 专为 OpenClaw 设计的 skill
- 支持自然语言交互
- 零配置，开箱即用
- 适合普通用户日常使用

## 如何选择

| 形态 | 分支 | 适用对象 | 特点 |
| --- | --- | --- | --- |
| Langbot 插件 | `langbot-plugin` | Langbot 用户 | 支持 Tool 调用，支持按 QQ 隔离账号配置 |
| MCP 服务器 | `mcp-server` | MCP 客户端 / 开发者 | 接口完整，适合集成 |
| OpenClaw Skill | `openclaw-skill` | OpenClaw 用户 | 对话式使用，部署简单 |

## 快速开始

### 使用 Langbot 插件版
```bash
git clone -b langbot-plugin https://github.com/jry21223/HENU_Assistant.git
cd HENU_Assistant
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/lbp build
```

### 使用 MCP 服务器版
```bash
git clone -b mcp-server https://github.com/jry21223/HENU_Assistant.git
cd HENU_Assistant
pip install -r requirements.txt
python mcp_server.py
```

### 使用 OpenClaw Skill 版
```bash
git clone -b openclaw-skill https://github.com/jry21223/HENU_Assistant.git
cp -r HENU_Assistant ~/.openclaw/workspace/skills/henu_campus_assistant
cd ~/.openclaw/workspace/skills/henu_campus_assistant

# Ubuntu/Debian: 避免 "externally managed environment" 报错
sudo apt update
sudo apt install -y python3-venv

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# 可选：激活后可直接使用 python/pip
source .venv/bin/activate
```

## 常用入口

- `setup_account`：保存账号并验证登录
- `sync_schedule`：同步最新课表
- `schedule_query --view current|day|week|full`：查当前课程、某一天课表、周课表或完整课表
- `library_query --view locations|current|records`：查图书馆区域、当前预约或历史记录
- `library_reserve` / `library_auto_signin` / `library_cancel`：图书馆写操作
- `seminar_group --action list|save|delete`：管理研讨室 group
- `seminar_query --view filters|rooms|detail|records|signin_tasks`：查研讨室筛选项、房间、详情、记录和签到任务
- `seminar_reserve` / `seminar_signin` / `seminar_cancel`：研讨室写操作
- `system_status`：查当前时间和系统状态
- `set_calibration_source`：更新节次校准源

课表查询补充说明：
- `schedule_query --view current` 只查“当前正在上的课 + 下一节课”
- `schedule_query --view day --target_date "2026-03-19"` 查某一天课表
- `schedule_query --view week` 为兼容旧调用，返回未按教学周过滤的完整周课表

## 最短流程

图书馆：
- 先执行 `system_status`
- 再执行 `library_query --view locations`
- 然后 `library_reserve`
- 后续用 `library_query --view current` / `library_query --view records`

研讨室：
- 先执行 `system_status`
- 可先用 `seminar_group --action save` 保存成员
- 按 `seminar_query --view filters` -> `seminar_query --view rooms` -> `seminar_query --view detail` 查询
- 然后 `seminar_reserve`
- 后续用 `seminar_query --view records`、`seminar_signin`、`seminar_cancel`

### 🧩 多平台接入
- 支持 Langbot 插件
- 支持 MCP 客户端
- 支持 OpenClaw Skill
- 可按不同场景选择不同接入方式

### 🔐 安全特性
- 本地加密存储账号信息
- 会话保持，减少重复登录
- 不上传任何个人数据
- 支持多账号管理

| 特性 | Langbot 插件版 | MCP 服务器版 | OpenClaw Skill 版 |
| --- | --- | --- | --- |
| 部署复杂度 | 中等 | 中等 | 简单 |
| 使用方式 | Tool 调用 / 机器人集成 | 工具调用 | 自然对话 |
| 客户端支持 | Langbot | MCP 兼容客户端 | OpenClaw |
| 账号隔离 | 支持按 QQ 隔离 | 默认单账号本地存储 | 由宿主环境决定 |
| 功能完整性 | 完整 | 完整 | 完整 |
| 适用场景 | Langbot 接入 | 开发集成 | 日常使用 |

## 说明

- Skill CLI 统一为 14 个入口，避免重复命令
- 账号与 Cookie 仅本地保存
- 研讨室 `group` 不含自己，建议保存 3-9 个同行成员
- 研讨室申请内容必须多于 10 个字
- Ubuntu/Debian 不要直接执行系统级 `pip3 install -r requirements.txt`

## 环境要求

- Python 3.9+
- 河南大学学生账号
- 网络连接（访问教务系统和图书馆系统）

## 贡献指南

欢迎提交Issue和Pull Request来改进项目：

1. Fork本仓库
2. 创建功能分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启Pull Request

## 许可证

本项目采用MIT许可证 - 查看 [LICENSE](LICENSE) 文件了解详情

## 免责声明

本项目仅供学习和个人使用，请遵守学校相关规定。使用本工具产生的任何后果由用户自行承担。

## 更新日志

- **v1.0.0** - 初始版本，支持 MCP 协议
- **v2.0.0** - 添加 OpenClaw Skill 支持，重构项目结构
- **v3.0.0** - 添加 Langbot 插件版，支持按 QQ 隔离保存账号配置
