#!/usr/bin/env python3
"""
任务状态变更通知 v3
- 触发条件：子任务「执行状态 = 已完成」
- 通知谁：由「岗位类型 × 父任务当前阶段」双维矩阵决定
- 阶段推进：方案 B — 所有子任务完成时在通知里建议推进
- 钉钉群 @具体负责人（手机号强提醒）
- 状态持久化：飞书多维表格「已通知」字段
"""

import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta

# 北京时间
BJT = timezone(timedelta(hours=8))

# 从环境变量读取配置
APP_ID = os.environ.get("FEISHU_APP_ID")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET")
DINGTALK_WEBHOOK_URL = os.environ.get("DINGTALK_WEBHOOK_URL", "")
APP_TOKEN = os.environ.get("BITABLE_APP_TOKEN")
TABLE_ID = os.environ.get("BITABLE_TABLE_ID")

BASE_URL = "https://open.feishu.cn/open-apis"

# ===== 团队成员：姓名 → 手机号（从环境变量读取 JSON） =====
NAME_TO_PHONE = json.loads(os.environ.get("NAME_PHONE_MAP", "{}"))

# ===== 通知矩阵：(岗位类型, 父任务当前阶段) → 通知配置 =====
# key = (岗位类型, 当前阶段)
# value = {"notify": [...岗位列表], "self": bool, "msg": "消息"}
NOTIFY_MATRIX = {
    # --- 策划 ---
    ("策划", "策划初案"):   {"notify": [],                         "self": True,  "msg": "📋 初案已完成（老板审阅通过），请继续完善完整策划案"},
    ("策划", "策划定稿"):   {"notify": ["原画", "动画", "开发"],      "self": False, "msg": "🎨 策划案已定稿，请各岗位准备评审"},
    # --- 原画 ---
    ("原画", "开发中"):     {"notify": ["策划", "开发"],             "self": False, "msg": "🎨 原画资产已完成，请策划审核、开发集成"},
    ("原画", "开发联调"):   {"notify": ["策划", "开发"],             "self": False, "msg": "🎨 原画资产已完成，请策划审核、开发集成"},
    # --- 动画 ---
    ("动画", "开发中"):     {"notify": ["策划", "开发"],             "self": False, "msg": "🏃 动画资产已完成，请策划审核、开发集成"},
    ("动画", "开发联调"):   {"notify": ["策划", "开发"],             "self": False, "msg": "🏃 动画资产已完成，请策划审核、开发集成"},
    # --- 开发 ---
    ("开发", "开发中"):     {"notify": ["测试"],                    "self": False, "msg": "🔧 开发完成，请准备联调/测试"},
    ("开发", "开发联调"):   {"notify": ["测试"],                    "self": False, "msg": "🔗 联调完成，请测试介入"},
    # --- 测试 ---
    ("测试", "功能测试"):   {"notify": ["数据产品", "开发"],         "self": False, "msg": "🧪 功能测试完成，请数据产品安排难度测试"},
    ("测试", "难度&体验测试"): {"notify": ["开发", "策划"],           "self": False, "msg": "✅ 体验测试通过，准备上线"},
    # --- 数据产品 ---
    ("数据产品", "难度&体验测试"): {"notify": ["策划", "开发"],       "self": False, "msg": "📊 难度测试数据已完成，请策划复核、开发准备上线"},
}

# ===== 兜底映射：当父任务「当前阶段」为空或未匹配时使用 =====
FALLBACK_DOWNSTREAM = {
    "策划":   {"notify": ["原画", "动画", "开发"], "msg": "📋 策划任务已完成"},
    "原画":   {"notify": ["策划", "开发"],         "msg": "🎨 原画任务已完成"},
    "动画":   {"notify": ["策划", "开发"],         "msg": "🏃 动画任务已完成"},
    "开发":   {"notify": ["测试"],                "msg": "🔧 开发任务已完成"},
    "测试":   {"notify": ["数据产品"],             "msg": "🧪 测试任务已完成"},
    "数据产品": {"notify": ["策划"],               "msg": "📊 数据产品任务已完成"},
}

# ===== 阶段推进建议：当某些条件满足时，建议推进到下一阶段 =====
STAGE_ADVANCE = {
    "策划初案": "策划定稿",
    "策划定稿": "开发评审",
    "开发评审": "工时评估",
    "工时评估": "开发中",
    "开发中":   "开发联调",
    "开发联调": "功能测试",
    "功能测试": "难度&体验测试",
    "难度&体验测试": "待上线",
    "待上线":   "已上线",
}

# 父子任务字段同步列表（不含「当前阶段」，它只属于父任务）
SYNC_FIELDS = ["所属迭代", "优先级", "任务类型"]


# ============================================================
# 工具函数
# ============================================================

def get_token():
    resp = requests.post(f"{BASE_URL}/auth/v3/tenant_access_token/internal",
        json={"app_id": APP_ID, "app_secret": APP_SECRET},
        headers={"Content-Type": "application/json; charset=utf-8"})
    return resp.json()["tenant_access_token"]


def get_all_records(token):
    hd = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
    records = []
    page_token = None
    while True:
        params = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token
        resp = requests.get(f"{BASE_URL}/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records",
            headers=hd, params=params)
        data = resp.json()
        if data.get("code") != 0:
            break
        records.extend(data.get("data", {}).get("items", []))
        if not data.get("data", {}).get("has_more"):
            break
        page_token = data["data"]["page_token"]
    return records


def get_parent_id(fields):
    """从子任务的 fields 中提取父任务 record_id"""
    parent_links = fields.get("父任务")
    if not parent_links or not isinstance(parent_links, list):
        return None
    for link in parent_links:
        if isinstance(link, dict) and "record_ids" in link:
            ids = link["record_ids"]
            if ids:
                return ids[0]
    return None


def find_people_by_roles(records, parent_id, target_roles):
    """
    在同一父任务下，找到指定岗位类型的子任务负责人
    返回 [(姓名, 手机号), ...]，已去重
    """
    people = []
    seen = set()
    for r in records:
        fields = r.get("fields", {})
        pid = get_parent_id(fields)
        if pid != parent_id:
            continue
        role = fields.get("岗位类型", "")
        name = fields.get("负责人", "")
        if role in target_roles and name and name not in seen:
            seen.add(name)
            phone = NAME_TO_PHONE.get(name, "")
            people.append((name, phone))
    return people


def check_stage_advance(records, parent_id, parent_stage):
    """
    方案 B：检查同一父任务下的所有子任务是否都完成了
    如果都完成，返回建议推进的下一阶段；否则返回 None
    """
    if not parent_stage or parent_stage not in STAGE_ADVANCE:
        return None

    children = []
    for r in records:
        fields = r.get("fields", {})
        pid = get_parent_id(fields)
        if pid == parent_id:
            children.append(fields)

    if not children:
        return None

    all_done = all(c.get("执行状态") == "已完成" for c in children)
    if all_done:
        return STAGE_ADVANCE[parent_stage]
    return None


def resolve_notify_config(role, parent_stage):
    """
    查通知矩阵：先精确匹配 (岗位, 阶段)，匹配不到用兜底
    返回 {"notify": [...], "self": bool, "msg": "..."}
    """
    # 精确匹配
    config = NOTIFY_MATRIX.get((role, parent_stage))
    if config:
        return config

    # 兜底
    fallback = FALLBACK_DOWNSTREAM.get(role)
    if fallback:
        return {"notify": fallback["notify"], "self": False, "msg": fallback["msg"]}

    return None


def send_dingtalk(task_name, role, owner, parent_name, parent_stage, message, people, advance_hint=None):
    """发送钉钉群通知，@具体的人"""
    if not DINGTALK_WEBHOOK_URL:
        return False

    # 构造 @人文本
    at_mobiles = [p[1] for p in people if p[1]]
    at_text = ""
    if people:
        at_names = " ".join([f"{p[0]} @{p[1]}" for p in people if p[1]])
        at_text = f"\n- **📢 请跟进**：{at_names}"

    # 阶段推进建议
    advance_text = ""
    if advance_hint:
        advance_text = f"\n- **💡 建议**：所有子任务已完成，建议将项目推进到「{advance_hint}」"

    stage_display = parent_stage if parent_stage else "未设置"

    text = (
        f"### 🔔 任务完成通知\n\n"
        f"- **🏷️ 项目**：{parent_name}\n"
        f"- **📍 阶段**：{stage_display}\n"
        f"- **📋 完成**：{task_name}（{role} - {owner}）\n"
        f"- **💬 说明**：{message}"
        f"{at_text}"
        f"{advance_text}\n"
        f"- **🕐 时间**：{datetime.now(BJT).strftime('%H:%M')}\n\n"
        f"[打开表格查看](https://my.feishu.cn/base/{APP_TOKEN})"
    )

    payload = {
        "msgtype": "markdown",
        "markdown": {"title": "任务完成通知", "text": text},
        "at": {
            "atMobiles": at_mobiles,
            "isAtAll": False,
        },
    }

    resp = requests.post(DINGTALK_WEBHOOK_URL, json=payload,
        headers={"Content-Type": "application/json; charset=utf-8"})
    return resp.json().get("errcode") == 0


# ============================================================
# 主逻辑
# ============================================================

def main():
    result = {"ok": False, "sync": 0, "notified": 0, "overdue": 0, "total": 0, "logs": []}

    if not all([APP_ID, APP_SECRET, APP_TOKEN, TABLE_ID, DINGTALK_WEBHOOK_URL]):
        result["error"] = "环境变量未设置"
        return result

    token = get_token()
    records = get_all_records(token)
    rmap = {r["record_id"]: r for r in records}
    result["total"] = len(records)
    result["logs"].append(f"📌 检查 {len(records)} 条记录...")

    hd = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}

    # === 1. 父子任务字段同步（不含「当前阶段」）===
    sync_count = 0
    for r in records:
        fields = r.get("fields", {})
        parent_id = get_parent_id(fields)
        if not parent_id or parent_id not in rmap:
            continue
        parent_fields = rmap[parent_id].get("fields", {})
        updates = {}
        for sf in SYNC_FIELDS:
            pv = parent_fields.get(sf)
            cv = fields.get(sf)
            if pv and pv != cv:
                updates[sf] = pv
                fields[sf] = pv  # 更新内存中的值
        if updates:
            requests.put(
                f"{BASE_URL}/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records/{r['record_id']}",
                json={"fields": updates}, headers=hd)
            sync_count += 1
            time.sleep(0.1)
    result["sync"] = sync_count
    result["logs"].append(f"   同步了 {sync_count} 条子任务")

    # === 2. 通知检查（核心：岗位 × 阶段 矩阵）===
    new_count = 0
    for r in records:
        rid = r["record_id"]
        fields = r.get("fields", {})
        status = fields.get("执行状态", "")
        task_name = fields.get("任务名称", "?")
        already_notified = fields.get("已通知", "")

        # 只通知「已完成」且「未通知」的子任务
        if status != "已完成" or already_notified == "是":
            continue

        role = fields.get("岗位类型", "")
        owner = fields.get("负责人", "?")

        # 独立任务（无父任务）跳过
        parent_id = get_parent_id(fields)
        if not parent_id or parent_id not in rmap:
            continue

        parent_fields = rmap[parent_id].get("fields", {})
        parent_name = parent_fields.get("任务名称", "—")
        parent_stage = parent_fields.get("当前阶段", "")

        # 已挂起的项目不通知
        if parent_stage == "已挂起":
            continue

        # 查通知矩阵
        config = resolve_notify_config(role, parent_stage)
        if not config:
            continue

        # 找需要 @的人
        people = []
        if config.get("self"):
            phone = NAME_TO_PHONE.get(owner, "")
            if owner and owner != "?":
                people.append((owner, phone))
        if config.get("notify") and parent_id:
            downstream = find_people_by_roles(records, parent_id, config["notify"])
            people.extend(downstream)

        # 检查阶段推进建议（方案 B）
        advance_hint = check_stage_advance(records, parent_id, parent_stage)

        # 先标记「已通知」，必须成功才发通知
        mark_ok = False
        for attempt in range(3):
            mark_resp = requests.put(
                f"{BASE_URL}/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records/{rid}",
                json={"fields": {"已通知": "是"}}, headers=hd)
            if mark_resp.json().get("code") == 0:
                mark_ok = True
                break
            time.sleep(0.5)

        if not mark_ok:
            result["logs"].append(f"   ⚠️ {task_name} → 标记「已通知」失败，跳过（防止重复通知）")
            continue

        time.sleep(0.3)

        if send_dingtalk(task_name, role, owner, parent_name, parent_stage,
                         config["msg"], people, advance_hint):
            new_count += 1
            at_names = ", ".join([p[0] for p in people]) if people else "无"
            log = f"   🔔 {task_name} [{role}] → @{at_names}"
            if advance_hint:
                log += f" 💡建议推进→{advance_hint}"
            result["logs"].append(log)
        else:
            # 发送失败，回滚标记
            requests.put(
                f"{BASE_URL}/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records/{rid}",
                json={"fields": {"已通知": ""}}, headers=hd)
            result["logs"].append(f"   ❌ {task_name} [{role}] → 发送失败，已回滚")

    result["notified"] = new_count
    result["logs"].append(f"🎉 通知完成！发送 {new_count} 条通知")

    # === 3. 更新超期状态 ===
    today_start = datetime.now(BJT).replace(hour=0, minute=0, second=0, microsecond=0)
    today_ms = today_start.timestamp() * 1000
    overdue_count = 0
    for r in records:
        fields = r.get("fields", {})
        status = fields.get("执行状态", "")
        end_date = fields.get("计划结束")
        old_val = fields.get("超期状态", "")

        if status == "已完成":
            new_val = "✅ 已完成"
        elif end_date and isinstance(end_date, (int, float)) and end_date < today_ms:
            new_val = "⚠️ 超期"
            overdue_count += 1
        else:
            new_val = "正常"

        if new_val != old_val:
            requests.put(
                f"{BASE_URL}/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records/{r['record_id']}",
                json={"fields": {"超期状态": new_val}}, headers=hd)
            time.sleep(0.1)

    result["overdue"] = overdue_count
    result["logs"].append(f"   超期任务: {overdue_count} 条")
    result["ok"] = True
    return result


if __name__ == "__main__":
    r = main()
    for log in r.get("logs", []):
        print(log)
