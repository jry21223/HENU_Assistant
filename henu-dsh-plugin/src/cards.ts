/**
 * HENU Assistant · DSH 交互卡片
 *
 * 数据契约与 henu-mcp 的真实返回严格对齐（不要凭空设计字段）：
 *
 * library_query(view="locations") 返回：
 *   { success, msg, date, is_live, source, total, returned_count, truncated,
 *     locations: [{ location: string, area_id: string, source: string,
 *                   resourceId?: string, resourceType?: string }],
 *     fallback_locations?, error_code? }
 *   - 未绑定账号时 success=false, error_code="auth_required"，locations 为静态参考。
 *
 * library_query(view="seats") 返回：
 *   { success, msg, area: { id, name }, target_date, time_window,
 *     total_count, available_count, returned_count, truncated, status_counts,
 *     seats: [{ id, no, name, status, resourceId? }], error_code? }
 *   - 座位号在 `no`（或 `name`），可用状态为 status === "1"。
 *
 * library_reserve 返回：
 *   { success, msg, date, area?: { id, name }, seat?: { id, no, status },
 *     applied_time?: { preferred_time, preferred_end_time, time_window,
 *                      start_time, end_time, reserve_type, space_type },
 *     submit_success?, retryable?, code?, verification?, error_code? }
 *   - 失败时（如未绑定账号）msg 是唯一真实原因，必须原样展示。
 */

export type CardAction = {
  id: string;
  label: string;
  tool: string;
  input: Record<string, unknown>;
  style?: "primary" | "secondary" | "danger";
};

export type DshUiCard = {
  type: "card";
  version: 1;
  title: string;
  subtitle?: string;
  status?: "info" | "success" | "warning" | "error";
  fields: Array<{ label: string; value: string }>;
  actions: CardAction[];
};

const text = (value: unknown, fallback = "—"): string => {
  const result = String(value ?? "").trim();
  return result || fallback;
};

/** 区域名称：真实字段是 `location`（静态/实时一致），兼容旧名。 */
export const areaName = (area: any): string =>
  text(area?.location ?? area?.areaName ?? area?.name ?? area?.area_name);

/** 区域 ID：真实字段是 `area_id`（小写）。 */
export const areaId = (area: any): string =>
  text(area?.area_id ?? area?.areaId ?? area?.id, "");

/** 座位号：真实字段是 `no`（兼容 `name`）。 */
export const seatNo = (seat: any): string =>
  text(seat?.no ?? seat?.seatNo ?? seat?.name, "");

const seatStatusLabel = (status: unknown): string => {
  const raw = String(status ?? "").trim();
  if (raw === "1") return "可预约";
  if (raw === "") return "可预约";
  return raw;
};

/** 格式化 applied_time 对象（真实返回是对象，不能直接字符串化）。 */
export const formatAppliedTime = (applied: any): string => {
  if (!applied || typeof applied !== "object") return "";
  const start = text(applied.start_time, "");
  const end = text(applied.end_time, "");
  if (start && end) return `${start} - ${end}`;
  const windowText = text(applied.time_window, "");
  if (windowText) return windowText;
  const preferred = text(applied.preferred_time, "");
  const preferredEnd = text(applied.preferred_end_time, "");
  if (preferred) return preferredEnd ? `${preferred} - ${preferredEnd}` : preferred;
  return "";
};

/** 是否应展示「绑定账号」提示：auth_required / auth_failed / 缺少账号。 */
export const needsAccountHint = (result: any): boolean => {
  const code = String(result?.error_code ?? "").toLowerCase();
  if (code === "auth_required" || code === "auth_failed") return true;
  return /缺少账号|未绑定账号|未绑定/.test(String(result?.msg ?? ""));
};

export const ACCOUNT_HINT =
  "请先在会话中调用 setup_account 绑定学号密码，再重试（插件不会读取或保存账号密码）";

// ─────────────────────────── 区域卡片 ───────────────────────────

export function locationsCard(result: any, _query: Record<string, unknown> = {}): DshUiCard {
  const locations = Array.isArray(result?.locations) ? result.locations : [];
  const success = result?.success !== false;
  const isLive = result?.is_live === true;
  const errorCode = String(result?.error_code ?? "");

  const title = "选择图书馆区域";
  const date = text(result?.date, "明天");

  let subtitle: string;
  let status: DshUiCard["status"];
  if (errorCode === "auth_required") {
    subtitle = `${date} · 未绑定账号，以下为静态参考区域（非实时）`;
    status = "warning";
  } else if (!success) {
    subtitle = text(result?.msg, "区域查询失败");
    status = "error";
  } else {
    subtitle = `${date} · ${isLive ? "实时可预约区域" : "区域列表"}`;
    status = "info";
  }

  const fields = locations.map((location: any) => ({
    label: areaName(location),
    value: `区域ID ${text(areaId(location), "未知")}`,
  }));

  const actions: CardAction[] = locations.map((location: any) => {
    const id = areaId(location);
    const name = areaName(location);
    return {
      id: `select-area:${id}`,
      label: `查看「${name}」座位`,
      tool: "library_query",
      input: { view: "seats", location: name, area_id: id },
      style: "secondary" as const,
    };
  });

  if (!fields.length && !success) {
    fields.push({ label: "提示", value: text(result?.msg, "查询失败，请稍后重试") });
  }

  return { type: "card", version: 1, title, subtitle, status, fields, actions };
}

// ─────────────────────────── 座位卡片 ───────────────────────────

export function seatsCard(result: any, query: Record<string, unknown> = {}): DshUiCard {
  const seats = Array.isArray(result?.seats) ? result.seats : [];
  const success = result?.success !== false;
  const errorCode = String(result?.error_code ?? "");
  const areaNameText = text(result?.area?.name, text(query.location, "图书馆"));
  const date = text(result?.target_date, text(query.target_date, "明天"));
  const timeWindow = text(result?.time_window, "");
  const availableCount =
    result?.available_count !== undefined && result?.available_count !== null
      ? String(result.available_count)
      : String(seats.length);

  const title = `${areaNameText}：选择座位`;
  let subtitle: string;
  let status: DshUiCard["status"];
  if (errorCode === "auth_required") {
    subtitle = `${date} · 未绑定账号，无法查询实时座位`;
    status = "error";
  } else if (!success) {
    subtitle = text(result?.msg, "座位查询失败");
    status = "error";
  } else {
    subtitle = `${date}${timeWindow ? ` · ${timeWindow}` : ""} · 可用 ${availableCount} 个`;
    status = "info";
  }

  const fields: Array<{ label: string; value: string }> = [];
  if (!success) {
    const reason = text(result?.msg, "未知错误");
    fields.push({ label: "失败原因", value: errorCode ? `${reason}（${errorCode}）` : reason });
    if (needsAccountHint(result)) fields.push({ label: "提示", value: ACCOUNT_HINT });
  } else {
    for (const seat of seats.slice(0, 30)) {
      fields.push({
        label: `座位 ${text(seatNo(seat))}`,
        value: seatStatusLabel(seat?.status),
      });
    }
    if (seats.length > 30) {
      fields.push({ label: "提示", value: `仅展示前 30 个，共 ${seats.length} 个可用座位` });
    }
  }

  const actions: CardAction[] = [];
  if (success) {
    for (const seat of seats.slice(0, 30)) {
      const no = seatNo(seat);
      if (!no) continue;
      const resourceId = text(seat?.resourceId ?? seat?.resource_id, "");
      actions.push({
        id: `reserve:${resourceId || no}`,
        label: `预约 ${no}`,
        tool: "henu_reservation_confirm",
        input: {
          location: areaNameText,
          seat_no: no,
          resource_id: resourceId,
          target_date: date === "明天" ? "" : date,
          preferred_time: text(query.preferred_time, "08:00"),
          preferred_end_time: text(query.preferred_end_time, ""),
        },
        style: "primary" as const,
      });
    }
  }

  return { type: "card", version: 1, title, subtitle, status, fields, actions };
}

// ─────────────────────────── 确认卡片 ───────────────────────────

/** library_reserve 只接受这些参数；多余字段（如 area_id）一律剔除。 */
const RESERVE_PARAM_KEYS = [
  "location",
  "seat_no",
  "resource_id",
  "target_date",
  "preferred_time",
  "preferred_end_time",
] as const;

export function sanitizeReserveInput(input: Record<string, unknown>): Record<string, unknown> {
  const clean: Record<string, unknown> = {};
  for (const key of RESERVE_PARAM_KEYS) {
    const value = input?.[key];
    if (value !== undefined && value !== null && String(value).trim() !== "") {
      clean[key] = value;
    }
  }
  return clean;
}

export function confirmationCard(input: Record<string, unknown>): DshUiCard {
  const location = text(input?.location, "未指定区域");
  const seat = text(input?.seat_no, "未指定座位");
  const date = text(input?.target_date, "明天");
  const start = text(input?.preferred_time, "08:00");
  const end = text(input?.preferred_end_time, "");
  const resourceId = text(input?.resource_id, "");

  const fields: Array<{ label: string; value: string }> = [
    { label: "区域", value: location },
    { label: "座位", value: seat },
    { label: "日期", value: date },
    { label: "时间", value: end ? `${start} - ${end}` : `${start} - 系统可用时段` },
  ];
  if (resourceId) fields.push({ label: "资源ID", value: resourceId });

  return {
    type: "card",
    version: 1,
    title: "确认预约",
    subtitle: "尚未提交预约。点击「确认预约」后才会调用真实预约接口；点「取消」不会提交任何请求。",
    status: "warning",
    fields,
    actions: [
      {
        id: "confirm",
        label: "确认预约",
        tool: "library_reserve",
        input: sanitizeReserveInput(input),
        style: "primary",
      },
      { id: "cancel", label: "取消", tool: "henu_reservation_cancel", input: {}, style: "secondary" },
    ],
  };
}

// ─────────────────────────── 结果卡片 ───────────────────────────

export function resultCard(result: any): DshUiCard {
  // 只有 library_reserve 真实返回 success === true 才允许显示成功。
  const ok = result?.success === true;
  const errorCode = String(result?.error_code ?? "");

  const fields: Array<{ label: string; value: string }> = [
    { label: "状态", value: ok ? "成功" : errorCode || "失败" },
    { label: "区域", value: text(result?.area?.name ?? result?.location) },
    { label: "座位", value: text(result?.seat?.no ?? result?.seat_no) },
    { label: "日期", value: text(result?.date ?? result?.target_date) },
    { label: "时段", value: formatAppliedTime(result?.applied_time) || text(result?.time) },
  ];

  if (!ok) {
    // 失败原因原样展示，绝不改写。
    fields.push({ label: "失败原因", value: text(result?.msg, "未知错误") });
    if (needsAccountHint(result)) fields.push({ label: "提示", value: ACCOUNT_HINT });
  }

  return {
    type: "card",
    version: 1,
    title: ok ? "预约成功" : "预约未完成",
    subtitle: text(result?.msg, ok ? "预约成功" : "请根据失败原因重试"),
    status: ok ? "success" : "error",
    fields,
    actions: [],
  };
}

// ─────────────────────────── 当前预约卡片 ───────────────────────────

export function statusCard(result: any): DshUiCard {
  const appointments = Array.isArray(result?.appointments) ? result.appointments : [];
  const success = result?.success !== false;
  const fields =
    appointments.length > 0
      ? appointments.map((item: any) => ({
          label: `预约 ${text(item?.no ?? item?.seat_no ?? item?.seatNo ?? item?.id)}`,
          value: `${text(item?.date ?? item?.target_date ?? "")} · ${text(
            item?.time ?? item?.time_window ?? item?.begin_time ?? "",
          )} · ${text(item?.status ?? item?.statusName ?? "未知")}`,
        }))
      : success
        ? [{ label: "提示", value: "当前没有进行中的预约" }]
        : [{ label: "失败原因", value: text(result?.msg, "查询失败") }];

  return {
    type: "card",
    version: 1,
    title: "当前预约",
    subtitle: text(result?.msg, success ? "" : "查询失败"),
    status: success ? "info" : "error",
    fields,
    actions: [],
  };
}

// ─────────────────────────── 渲染 ───────────────────────────

/** DSH UI 渲染协议（JSON 围栏；Web 端有渲染器时按卡片渲染，否则显示为文本）。 */
export function asDshUi(card: DshUiCard): string {
  return `\`\`\`dsh-ui\n${JSON.stringify(card, null, 2)}\n\`\`\``;
}

/** 人类可读的 Markdown 版本（模型与聊天窗口都能直接阅读）。 */
export function asMarkdown(card: DshUiCard): string {
  const lines: string[] = [`### ${card.title}`];
  if (card.subtitle) lines.push(card.subtitle);
  if (card.fields.length) {
    lines.push("");
    for (const field of card.fields) lines.push(`- **${field.label}**：${field.value}`);
  }
  if (card.actions.length) {
    lines.push("");
    for (const action of card.actions) {
      const input = JSON.stringify(action.input);
      lines.push(`> [${action.label}] → \`${action.tool}\` ${input}`);
    }
  }
  return lines.join("\n");
}

/** 模型可见的完整渲染：Markdown + dsh-ui JSON 围栏。 */
export function renderCard(card: DshUiCard): string {
  return `${asMarkdown(card)}\n\n${asDshUi(card)}`;
}
