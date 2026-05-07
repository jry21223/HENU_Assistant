# 河南大学校园助手

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
pip install -r requirements.txt
```

## 功能特性

### 📚 教务系统集成
- 自动登录河大教务系统
- 获取个人课表信息
- 实时查询当前课程状态
- 智能识别下一节课时间

### 🏛️ 图书馆服务
- 座位预约功能
- 预约记录查询
- 一键取消预约
- 支持多个图书馆区域

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

## 版本对比

| 特性 | Langbot 插件版 | MCP 服务器版 | OpenClaw Skill 版 |
| --- | --- | --- | --- |
| 部署复杂度 | 中等 | 中等 | 简单 |
| 使用方式 | Tool 调用 / 机器人集成 | 工具调用 | 自然对话 |
| 客户端支持 | Langbot | MCP 兼容客户端 | OpenClaw |
| 账号隔离 | 支持按 QQ 隔离 | 默认单账号本地存储 | 由宿主环境决定 |
| 功能完整性 | 完整 | 完整 | 完整 |
| 适用场景 | Langbot 接入 | 开发集成 | 日常使用 |

## 系统要求

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

- **v3.0.0** - 添加 Langbot 插件版，支持按 QQ 隔离保存账号配置
- **v2.0.0** - 添加 OpenClaw Skill 支持，重构项目结构
- **v1.0.0** - 初始版本，支持 MCP 协议
