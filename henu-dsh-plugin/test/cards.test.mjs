import test from "node:test";
import assert from "node:assert/strict";
import {
  asDshUi,
  confirmationCard,
  formatAppliedTime,
  locationsCard,
  needsAccountHint,
  resultCard,
  sanitizeReserveInput,
  seatsCard,
} from "../dist/cards.js";

// ── 区域卡片：真实契约 location / area_id（小写） ──

test("locations card maps real contract fields (location/area_id) to library_query", () => {
  const card = locationsCard({
    success: true,
    is_live: true,
    date: "2026-08-21",
    locations: [{ location: "第一自习室", area_id: "43", source: "live" }],
  });
  assert.equal(card.actions[0].tool, "library_query");
  assert.deepEqual(card.actions[0].input, {
    view: "seats",
    location: "第一自习室",
    area_id: "43",
  });
  assert.equal(card.fields[0].label, "第一自习室");
  assert.equal(card.fields[0].value, "区域ID 43");
  assert.equal(card.status, "info");
});

test("locations card warns on auth_required instead of claiming live data", () => {
  const card = locationsCard({
    success: false,
    error_code: "auth_required",
    msg: "未绑定账号，无法获取实时可预约区域；以下仅为非实时静态参考",
    date: "2026-08-21",
    locations: [{ location: "一楼东", area_id: "1", source: "static_fallback" }],
  });
  assert.equal(card.status, "warning");
  assert.match(card.subtitle, /非实时/);
  assert.ok(card.actions.length > 0, "静态区域仍应可选（查询结果为静态参考）");
  assert.equal(card.actions[0].input.location, "一楼东");
});

test("locations card shows failure reason when query fails", () => {
  const card = locationsCard({ success: false, error_code: "query_failed", msg: "网络错误", locations: [] });
  assert.equal(card.status, "error");
  assert.ok(card.fields.some((f) => f.label === "提示"));
  assert.equal(card.actions.length, 0);
});

// ── 座位卡片：真实契约 id / no / name / status / resourceId ──

test("seat card maps no/id/resourceId to confirmation action", () => {
  const card = seatsCard(
    {
      success: true,
      area: { id: "43", name: "第一自习室" },
      target_date: "2026-08-21",
      time_window: "08:00-22:00",
      available_count: 1,
      seats: [{ id: "998", no: "A01", name: "A01", status: "1", resourceId: "henu:library:seat:x" }],
    },
    { location: "第一自习室" },
  );
  assert.equal(card.actions[0].tool, "henu_reservation_confirm");
  const input = card.actions[0].input;
  assert.equal(input.seat_no, "A01");
  assert.equal(input.resource_id, "henu:library:seat:x");
  assert.equal(input.location, "第一自习室");
  assert.equal(input.target_date, "2026-08-21");
  assert.equal(input.preferred_time, "08:00");
  assert.equal(card.fields[0].label, "座位 A01");
  assert.equal(card.fields[0].value, "可预约");
});

test("seat card shows auth_required failure and no reserve actions", () => {
  const card = seatsCard({
    success: false,
    error_code: "auth_required",
    msg: "缺少账号",
    seats: [],
    total_count: 0,
    available_count: 0,
    returned_count: 0,
  });
  assert.equal(card.status, "error");
  assert.equal(card.actions.length, 0);
  assert.ok(card.fields.some((f) => f.label === "失败原因" && f.value.includes("缺少账号")));
  assert.ok(card.fields.some((f) => f.label === "提示"), "应提示绑定账号");
});

test("seat card shows missing_location failure verbatim", () => {
  const card = seatsCard({
    success: false,
    error_code: "missing_location",
    msg: "请提供 location 或 area_id，或在 setup_account 中设置默认图书馆区域",
    seats: [],
  });
  assert.equal(card.status, "error");
  assert.ok(card.fields.some((f) => f.value.includes("请提供 location 或 area_id")));
});

// ── 确认卡片 ──

test("confirmation card never claims reservation success and confirm targets library_reserve", () => {
  const card = confirmationCard({ location: "第一自习室", seat_no: "A01", target_date: "2026-08-21" });
  assert.equal(card.status, "warning");
  assert.notEqual(card.title, "预约成功");
  assert.match(card.subtitle, /尚未提交预约/);
  assert.equal(card.actions[0].tool, "library_reserve");
  assert.equal(card.actions[1].tool, "henu_reservation_cancel");
  assert.match(asDshUi(card), /dsh-ui/);
});

test("confirmation card only forwards library_reserve parameters", () => {
  const card = confirmationCard({
    location: "第一自习室",
    seat_no: "A01",
    area_id: "43", // 不是 library_reserve 的参数，必须被剔除
    target_date: "2026-08-21",
    preferred_time: "09:00",
    preferred_end_time: "11:00",
    resource_id: "henu:library:seat:x",
  });
  assert.deepEqual(card.actions[0].input, {
    location: "第一自习室",
    seat_no: "A01",
    target_date: "2026-08-21",
    preferred_time: "09:00",
    preferred_end_time: "11:00",
    resource_id: "henu:library:seat:x",
  });
});

test("sanitizeReserveInput drops empty and unknown keys", () => {
  assert.deepEqual(
    sanitizeReserveInput({ location: "一楼", seat_no: "", area_id: "1", target_date: undefined }),
    { location: "一楼" },
  );
});

// ── 结果卡片 ──

test("result card success reflects backend truth", () => {
  const card = resultCard({
    success: true,
    msg: "操作成功",
    date: "2026-08-21",
    area: { id: "43", name: "第一自习室" },
    seat: { id: "998", no: "A01", status: "1" },
    applied_time: { preferred_time: "09:00", preferred_end_time: "11:00", time_window: "09:00-11:00" },
  });
  assert.equal(card.status, "success");
  assert.equal(card.title, "预约成功");
  assert.ok(card.fields.some((f) => f.label === "座位" && f.value === "A01"));
});

test("result card failure shows raw reason and auth_required error code", () => {
  const card = resultCard({ success: false, error_code: "auth_required", msg: "缺少账号" });
  assert.equal(card.status, "error");
  assert.equal(card.title, "预约未完成");
  assert.ok(card.fields.some((f) => f.label === "状态" && f.value === "auth_required"));
  assert.ok(card.fields.some((f) => f.label === "失败原因" && f.value === "缺少账号"));
  assert.ok(card.fields.some((f) => f.label === "提示"));
});

test("result card never claims success when success is missing or false", () => {
  for (const bad of [
    {},
    { success: false, msg: "座位 当前不可预约" },
    { success: "true", msg: "提交接口返回成功，但反查当前预约未确认" },
    { success: true, msg: "ok" },
  ]) {
    const card = resultCard(bad);
    // 只有严格 === true 才算成功
    assert.equal(card.status, bad.success === true ? "success" : "error");
    if (bad.success !== true) assert.equal(card.title, "预约未完成");
  }
});

test("result card formats applied_time object", () => {
  assert.equal(
    formatAppliedTime({ start_time: "09:00", end_time: "11:00", time_window: "09:00-11:00" }),
    "09:00 - 11:00",
  );
  assert.equal(formatAppliedTime({ time_window: "08:00-22:00" }), "08:00-22:00");
  assert.equal(formatAppliedTime(undefined), "");
});

test("needsAccountHint recognizes auth errors", () => {
  assert.equal(needsAccountHint({ error_code: "auth_required" }), true);
  assert.equal(needsAccountHint({ error_code: "auth_failed" }), true);
  assert.equal(needsAccountHint({ msg: "缺少账号" }), true);
  assert.equal(needsAccountHint({ error_code: "query_failed", msg: "网络错误" }), false);
});
