# HENU Assistant · DeepSeek Harness 插件

这个插件只负责 DSH 的交互层，不替换 HENU Assistant 的核心链路或账号逻辑。

流程是：

1. 现有 MCP 调用 `library_query(view="locations")`，插件渲染区域卡片；
2. 点击区域后调用 `library_query(view="seats")`，插件渲染座位卡片；
3. 点击座位后显示确认卡片（确认卡片绝不显示「预约成功」）；
4. 只有用户明确确认后，模型才调用 `library_reserve`，且每次调用都会触发
   DSH Web 的原生审批对话框（`tools/pre-execute` 返回 `{kind:"ask"}`）做二次确认；
5. 预约结果再由 `henu_reservation_result_card` 显示成功或失败，
   `success === true` 才显示成功，失败原因（含 `auth_required` 等错误码）原样展示。

## 在 DSH 中加载

1. 构建：`npm run build`（入口必须是构建产物 `dist/index.js`）。
2. 把 `cordis.yml` 的 `insert` 内容合并进
   `C:/Users/HONOR/.dsh/profiles/web/cordis.patch.yml`（已合并，`watchUserPatches`
   会对运行中的 Web UI 热应用）；或作为 overlay 启动：

```bash
pnpm dsh web --patch C:/Users/HONOR/Desktop/jerry/henu-dsh-plugin/cordis.yml
```

`cordis.yml` 同时注册两个条目：

- `henu-assistant`：本地插件（`file:///.../dist/index.js`）——Windows 下 Node
  ESM 只接受 `file:///` 绝对路径，且 Node 类型剥离不会把 `src/index.ts` 里的
  `./cards.js` 映射到 `cards.ts`，所以不能直接加载 `src/index.ts`；
- `mcp-henu`：官方 `@deepseek-ai/dsh-mcp-client`，stdio 启动
  `henu-mcp/mcp_server.py`，工具以 `mcp__henu__library_query`、
  `mcp__henu__library_reserve` 等名字注册。

## 与现有 HENU MCP 的边界

插件不会读取密码、模拟成功、直接访问校园 API，也不会改动 `henu-mcp`。
`library_reserve` 的返回值仍是唯一真实预约状态；`auth_required`、失败原因和
日期/时段会原样进入结果卡片。

## 本地测试

```bash
npm run build
npm test
```

测试覆盖：区域/座位/确认/结果四类卡片对真实 MCP 契约
（`location`/`area_id`/`no`/`id`/`resourceId`/`applied_time` 等字段）的映射、
`auth_required` 等错误态、确认卡片不伪造成功、结果卡片只认 `success === true`。

## 常见问题

- 改了 `src/` 后 Web 端不生效：DSH 加载的是 `dist/index.js`，必须重新
  `npm run build`（无需重启 Web，Loader 按文件重载入口）。
- schema 报错 `type: null`：工具 `parameters` 必须编译成顶层
  `type: "object"` 的标准 JSON Schema（`registerCardTool` 已用 `toJsonSchema`
  处理，勿改为直接透传属性级定义）。
