#!/usr/bin/env python3
"""
任务状态变更通知 v2
- 按「当前阶段」决定通知下游（而非岗位类型）
- 钉钉群 @具体负责人（手机号）
- 状态持久化使用飞书多维表格「已通知」字段
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
# 格式: {"陈忠强":"15908118897","贺敏洪":"13700951014",...}
NAME_TO_PHONE = json.loads(os.environ.get("NAME_PHONE_MAP", "{}"))

# ===== 阶段完成后的通知配置 =====
# downstream_roles: 需要 @的下游岗位类型列表
# notify_self: 是否通知当前任务负责人自己
# message: 通知文案


# ===== 岗位完成后的提醒配置 =====
ROLE_NOTIFY = {
    "策划": {"downstream": ["原画", "动画", "开发"], "message": "📋 策划已完成，请下游跟进"},
    "原画": {"downstream": ["动画", "开发"], "message": "🎨 原画已完成"},
    "动画": {"downstream": ["开发"], "message": "🏃 动画已完成"},
    "开发": {"downstream": ["测试"], "message": "🔧 开发完成，请测试介入"},
    "测试": {"downstream": ["数据产品"], "message": "🧪 测试通过，请数据产品验收"},
    "数据产品": {"downstream": ["策划"], "message": "✅ 数据反馈已完成，请策划处理"},
}

# 父子任务字段同步列表
SYNC_FIELDS = ["当前阶段", "所属迭代", "优先级", "任务类型"]


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


def find_downstream_people(records, rmap, parent_id, downstream_roles):
    """
    在同一父任务下，找到指定岗位类型的子任务负责人
    返回 [(姓名, 手机号), ...]
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
        if role in downstream_roles and name and name not in seen:
            seen.add(name)
            phone = NAME_TO_PHONE.get(name, "")
            people.append((name, phone))
    return people


def send_dingtalk(task_name, role, parent_name, message, people, at_all=False):
    """发送钉钉群通知，@具体的人"""
    if not DINGTALK_WEBHOOK_URL:
        return False

    # 构造 @人文本
    at_mobiles = [p[1] for p in people if p[1]]
    at_text = ""
    if people:
        # 钉钉 markdown 必须在文本中包含 @手机号 才能触发强提醒
        at_names = " ".join([f"{p[0]} @{p[1]}" for p in people if p[1]])
        at_text = f"\n- **📢 请跟进**：{at_names}"

    text = (
        f"### 🔔 任务阶段完成通知\n\n"
        f"- **🏷️ 项目**：{parent_name}\n"
        f"- **📋 任务**：{task_name}\n"
        f"- **👤 岗位**：{role} ✅\n"
        f"- **💬 说明**：{message}"
        f"{at_text}\n"
        f"- **🕐 时间**：{datetime.now(BJT).strftime('%H:%M')}\n\n"
        f"[打开表格查看](https://my.feishu.cn/base/{APP_TOKEN})"
    )

    payload = {
        "msgtype": "markdown",
        "markdown": {"title": "任务完成通知", "text": text},
        "at": {
            "atMobiles": at_mobiles,
            "isAtAll": at_all,
        },
    }

    resp = requests.post(DINGTALK_WEBHOOK_URL, json=payload,
        headers={"Content-Type": "application/json; charset=utf-8"})
    return resp.json().get("errcode") == 0


def main():
    """主逻辑，返回执行结果字典"""
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

    # === 1. 父子任务字段同步 ===
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
                fields[sf] = pv  # 更新内存里的值，以便后续步骤正确读取
        if updates:
            requests.put(
                f"{BASE_URL}/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records/{r['record_id']}",
                json={"fields": updates}, headers=hd)
            sync_count += 1
            time.sleep(0.1)
    result["sync"] = sync_count
    result["logs"].append(f"   同步了 {sync_count} 条子任务")

    # === 2. 通知检查 ===
    new_count = 0
    for r in records:
        rid = r["record_id"]
        fields = r.get("fields", {})
        status = fields.get("执行状态", "")
        stage = fields.get("当前阶段", "")
        task_name = fields.get("任务名称", "?")
        already_notified = fields.get("已通知", "")

        # 只通知「已完成」且「未通知」的任务
        if status != "已完成" or already_notified == "是":
            continue

        role = fields.get("岗位类型", "")
        # 获取该岗位的下游配置
        notify_config = ROLE_NOTIFY.get(role)
        if not notify_config:
            continue

        # 找父任务名称
        parent_id = get_parent_id(fields)
        parent_name = "—"
        if parent_id and parent_id in rmap:
            parent_name = rmap[parent_id].get("fields", {}).get("任务名称", "—")

        # 找需要 @的人
        people = []
        if False:
            # 通知自己
            self_name = fields.get("负责人", "")
            if self_name:
                phone = NAME_TO_PHONE.get(self_name, "")
                people.append((self_name, phone))
        if notify_config.get("downstream") and parent_id:
            # 找同一父任务下的下游负责人
            downstream = find_downstream_people(
                records, rmap, parent_id, notify_config["downstream"])
            people.extend(downstream)

        at_all = False

        # 先标记「已通知」，防止并发运行重复发送
        requests.put(
            f"{BASE_URL}/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records/{rid}",
            json={"fields": {"已通知": "是"}}, headers=hd)
        time.sleep(0.3)

        if send_dingtalk(task_name, role, parent_name, notify_config["message"], people, at_all):
            new_count += 1
            at_names = ", ".join([p[0] for p in people]) if people else ("全员" if at_all else "无")
            result["logs"].append(f"   🔔 {task_name} [{role}] → @{at_names}")
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
