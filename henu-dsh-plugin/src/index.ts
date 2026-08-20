/**
 * HENU Assistant · DeepSeek Harness 插件入口
 *
 * 职责边界（与 henu-mcp 严格分离）：
 * 1. 插件只做 DSH 交互层：把现有 MCP 工具（library_query / library_reserve）的
 *    真实返回渲染成卡片；数据唯一来源是 MCP 服务，不伪造任何结果。
 * 2. 预约确认闸门：`tools/pre-execute` 对 mcp__henu__library_reserve 的每次调用
 *    返回 { kind: "ask" }，由 DSH Web 的原生审批对话框（允许/拒绝）做最后确认。
 *    用户未点「允许」时调用被拒绝，真实预约接口根本不会被执行。
 * 3. 工具注册直接使用 tools service 的 ctx.tools.register()（与 defineTool 编译
 *    产物同构），避免对部署内部 @deepseek-ai/dsh-tools 包的运行时依赖。
 */

import {
  confirmationCard,
  locationsCard,
  renderCard,
  resultCard,
  seatsCard,
  statusCard,
} from "./cards.js";

export const name = "henu-assistant";
export const inject = ["tools"];

/** MCP 桥接后的公开工具名（dsh-mcp-client 命名：mcp__<serverName>__<rawName>）。 */
export const MCP_SERVER_NAME = "henu";
export const MCP_RESERVE_TOOL = `mcp__${MCP_SERVER_NAME}__library_reserve`;

const noOpCancel = () => ({ success: false, msg: "用户取消了预约" });

const textRender = (_args: unknown, value: string) => [{ type: "text" as const, text: value }];

/**
 * 把 zod 风格的属性定义转成标准 JSON Schema（顶层 type: "object"）。
 * DeepSeek API 要求函数 parameters 必须是顶层 `type: "object"` 的 JSON
 * Schema；raw `ctx.tools.register()` 不会像 defineTool 那样自动编译，若
 * 直接透传属性级 `required: true` 的定义（或空对象 {}）会被 API 以
 * "schema must be type object, got type null" 拒绝。
 */
function toJsonSchema(
  properties: Record<string, { type: string; required?: boolean }>,
): Record<string, unknown> {
  const schema: Record<string, unknown> = { type: "object", properties: {} };
  const required: string[] = [];
  for (const [key, def] of Object.entries(properties)) {
    (schema.properties as Record<string, unknown>)[key] = { type: def.type };
    if (def.required === true) required.push(key);
  }
  if (required.length > 0) schema.required = required;
  return schema;
}

function registerCardTool(
  ctx: any,
  definition: {
    name: string;
    description: string;
    parameters: Record<string, { type: string; required?: boolean }>;
    execute: (args: any) => string;
  },
): void {
  ctx.tools.register({
    name: definition.name,
    description: definition.description,
    parameters: toJsonSchema(definition.parameters),
    output: {
      schema: { type: "string" },
      render: textRender,
    },
    execute: (args: any) => definition.execute(args ?? {}),
  });
}

/** DeepSeek Harness plugin entry. The existing HENU MCP server remains the source of truth. */
export function apply(ctx: any) {
  const register = ctx?.tools?.register;
  if (typeof register !== "function") throw new Error("HENU DSH plugin requires the tools service");

  registerCardTool(ctx, {
    name: "henu_reservation_locations_card",
    description:
      "把 library_query(view=\"locations\") 的真实返回渲染成图书馆区域选择卡片。参数 result 必须是该工具返回的完整对象。",
    parameters: { result: { type: "object", required: true } },
    execute: ({ result }) => renderCard(locationsCard(result)),
  });

  registerCardTool(ctx, {
    name: "henu_reservation_seats_card",
    description:
      "把 library_query(view=\"seats\") 的真实返回渲染成座位选择卡片。参数 result 必须是该工具返回的完整对象，query 可带 location/target_date/preferred_time/preferred_end_time 供卡片回填。",
    parameters: {
      result: { type: "object", required: true },
      query: { type: "object" },
    },
    execute: ({ result, query }) => renderCard(seatsCard(result, query ?? {})),
  });

  registerCardTool(ctx, {
    name: "henu_reservation_confirm",
    description:
      "显示预约确认卡片。此卡片绝不表示预约成功；只有用户明确确认后，模型才允许调用 mcp__henu__library_reserve（该调用还会触发系统审批对话框二次确认）。",
    parameters: {
      location: { type: "string", required: true },
      seat_no: { type: "string", required: true },
      target_date: { type: "string" },
      preferred_time: { type: "string" },
      preferred_end_time: { type: "string" },
      resource_id: { type: "string" },
    },
    execute: (input) => renderCard(confirmationCard(input)),
  });

  registerCardTool(ctx, {
    name: "henu_reservation_result_card",
    description:
      "把 library_reserve 的真实返回结果渲染成成功/失败卡片。仅当返回 success === true 时显示成功，否则原样展示失败原因（含 auth_required/auth_failed 等错误码）。",
    parameters: { result: { type: "object", required: true } },
    execute: ({ result }) => renderCard(resultCard(result)),
  });

  registerCardTool(ctx, {
    name: "henu_reservation_status_card",
    description:
      "把 library_query(view=\"current\") 的真实返回渲染成当前预约卡片，用于预约后核对。",
    parameters: { result: { type: "object", required: true } },
    execute: ({ result }) => renderCard(statusCard(result)),
  });

  registerCardTool(ctx, {
    name: "henu_reservation_cancel",
    description: "取消预约的占位确认，不调用任何后端预约接口，仅记录用户取消。",
    // 不使用空 schema。部分 DSH/模型适配器会把空参数对象序列化成
    // parameters: null，进而触发 Invalid schema。保留一个可选确认字段，
    // 经过 toJsonSchema 后始终是顶层 type: object。
    parameters: { acknowledged: { type: "boolean" } },
    execute: () => JSON.stringify(noOpCancel()),
  });

  // ── 预约确认闸门 ──
  // 每次真实预约调用都必须先经过 DSH 审批对话框（允许/拒绝）。用户在 Web 界面
  // 点击「允许」前，library_reserve 不会到达 HENU MCP 服务；审批不可用时按
  // ask→deny 规则直接拒绝，同样不会调用真实接口。
  const reservePreExecute = (exec: any, next: () => Promise<unknown>) => {
    if (exec?.name !== MCP_RESERVE_TOOL) return next();
    const args = (exec?.arguments ?? {}) as Record<string, unknown>;
    const location = String(args.location ?? "").trim() || "未指定区域";
    const seat = String(args.seat_no ?? "").trim() || "未指定座位";
    const date = String(args.target_date ?? "").trim() || "明天";
    const start = String(args.preferred_time ?? "").trim() || "08:00";
    const end = String(args.preferred_end_time ?? "").trim();
    const reason =
      `确认预约：区域「${location}」座位 ${seat}，日期 ${date}，` +
      `时间 ${end ? `${start} - ${end}` : `${start} - 系统可用时段`}。` +
      `点击「允许」才会向 HENU 提交真实预约请求；点击「拒绝」则不提交。`;
    return { kind: "ask", reason };
  };
  ctx.on("tools/pre-execute", reservePreExecute);

  ctx.logger?.info?.("HENU Assistant plugin loaded: cards + reserve approval gate");
}

export { confirmationCard, locationsCard, resultCard, seatsCard, statusCard };
