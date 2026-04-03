#!/usr/bin/env python3
"""
任务状态变更通知（云端版）
密钥从环境变量读取，适用于 GitHub Actions
"""

import json
import os
import time
import requests
from datetime import datetime, timezone, timedelta

# 北京时间
BJT = timezone(timedelta(hours=8))

# 从环境变量读取配置
APP_ID = os.environ.get("FEISHU_APP_ID")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET")
FEISHU_WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL", "")
DINGTALK_WEBHOOK_URL = os.environ.get("DINGTALK_WEBHOOK_URL", "")
APP_TOKEN = os.environ.get("BITABLE_APP_TOKEN")
TABLE_ID = os.environ.get("BITABLE_TABLE_ID")

BASE_URL = "https://open.feishu.cn/open-apis"

DOWNSTREAM = {
    "策划": "原画/动画",
    "原画": "动画/开发",
    "动画": "开发",
    "开发": "测试",
    "测试": "数据产品",
    "数据产品": "策划/研发负责人",
}

# GitHub Actions 中用文件保存状态（会被 cache 持久化）
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notified_tasks.json")


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


def load_notified():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_notified(notified):
    with open(STATE_FILE, "w") as f:
        json.dump(list(notified), f)


def send_dingtalk(task_name, role, parent_name, downstream, now):
    """发送钉钉群通知"""
    message = {
        "msgtype": "markdown",
        "markdown": {
            "title": "任务完成通知",
            "text": (
                f"### 🔔 任务完成通知\n\n"
                f"- **📋 任务**：{task_name}\n"
                f"- **🏷️ 项目**：{parent_name}\n"
                f"- **👤 岗位**：{role}\n"
                f"- **✅ 状态**：已完成\n"
                f"- **📢 下游提醒**：请 **{downstream}** 跟进\n"
                f"- **🕐 时间**：{now}\n\n"
                f"[打开表格查看](https://my.feishu.cn/base/{APP_TOKEN})"
            ),
        },
    }
    resp = requests.post(DINGTALK_WEBHOOK_URL, json=message,
        headers={"Content-Type": "application/json; charset=utf-8"})
    return resp.json().get("errcode") == 0


def send_feishu(task_name, role, parent_name, downstream, now):
    """发送飞书群通知"""
    message = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": "🔔 任务完成通知"},
                "template": "green",
            },
            "elements": [{
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**📋 任务**：{task_name}\n"
                        f"**🏷️ 项目**：{parent_name}\n"
                        f"**👤 岗位**：{role}\n"
                        f"**✅ 状态**：已完成\n"
                        f"**📢 下游提醒**：请 **{downstream}** 跟进\n"
                        f"**🕐 时间**：{now}"
                    ),
                },
            }],
        },
    }
    resp = requests.post(FEISHU_WEBHOOK_URL, json=message,
        headers={"Content-Type": "application/json; charset=utf-8"})
    return resp.status_code == 200


def send_notification(task_name, role, parent_name):
    downstream = DOWNSTREAM.get(role, "相关同事")
    now = datetime.now(BJT).strftime("%H:%M")
    ok = False
    if DINGTALK_WEBHOOK_URL:
        ok = send_dingtalk(task_name, role, parent_name, downstream, now)
    if FEISHU_WEBHOOK_URL:
        ok = send_feishu(task_name, role, parent_name, downstream, now) or ok
    return ok


def main():
    if not all([APP_ID, APP_SECRET, APP_TOKEN, TABLE_ID]) or not any([DINGTALK_WEBHOOK_URL, FEISHU_WEBHOOK_URL]):
        print("❌ 环境变量未设置")
        return

    token = get_token()
    records = get_all_records(token)
    notified = load_notified()
    rmap = {r["record_id"]: r for r in records}

    print(f"📌 检查 {len(records)} 条记录...")
    new_count = 0

    for r in records:
        rid = r["record_id"]
        fields = r.get("fields", {})
        status = fields.get("执行状态", "")
        task_name = fields.get("任务名称", "?")
        role = fields.get("岗位类型", "?")

        if status == "已完成" and rid not in notified:
            parent_name = "—"
            parent_links = fields.get("父任务", [])
            if parent_links and isinstance(parent_links, list):
                for link in parent_links:
                    if isinstance(link, dict) and "record_ids" in link:
                        pid = link["record_ids"][0] if link["record_ids"] else None
                        if pid and pid in rmap:
                            parent_name = rmap[pid].get("fields", {}).get("任务名称", "—")
                        break

            if send_notification(task_name, role, parent_name):
                notified.add(rid)
                new_count += 1
                print(f"   🔔 {task_name} ({role}) → 已通知")
            time.sleep(0.5)

    save_notified(notified)
    print(f"🎉 通知完成！发送 {new_count} 条通知")

    # === 更新超期状态 ===
    print("📌 刷新超期状态...")
    hd = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
    today_ms = datetime.now(BJT).timestamp() * 1000
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

    print(f"   超期任务: {overdue_count} 条")


if __name__ == "__main__":
    main()
