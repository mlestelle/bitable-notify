#!/usr/bin/env python3
"""
任务状态变更通知（Render 云端版）
密钥从环境变量读取，通过 Flask HTTP 端点触发
状态持久化使用飞书多维表格「已通知」字段，无需本地文件
"""

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
    """主逻辑，返回执行结果字典"""
    result = {"ok": False, "sync": 0, "notified": 0, "overdue": 0, "total": 0, "logs": []}

    if not all([APP_ID, APP_SECRET, APP_TOKEN, TABLE_ID]) or not any([DINGTALK_WEBHOOK_URL, FEISHU_WEBHOOK_URL]):
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
    sync_fields = ["当前阶段", "所属迭代", "优先级", "任务类型"]
    for r in records:
        fields = r.get("fields", {})
        parent_links = fields.get("父任务")
        if not parent_links or not isinstance(parent_links, list):
            continue
        parent_id = None
        for link in parent_links:
            if isinstance(link, dict) and "record_ids" in link:
                ids = link["record_ids"]
                if ids:
                    parent_id = ids[0]
                break
        if not parent_id or parent_id not in rmap:
            continue
        parent_fields = rmap[parent_id].get("fields", {})
        updates = {}
        for sf in sync_fields:
            pv = parent_fields.get(sf)
            cv = fields.get(sf)
            if pv and pv != cv:
                updates[sf] = pv
        if updates:
            requests.put(
                f"{BASE_URL}/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records/{r['record_id']}",
                json={"fields": updates}, headers=hd)
            sync_count += 1
            time.sleep(0.1)
    result["sync"] = sync_count
    result["logs"].append(f"   同步了 {sync_count} 条子任务")

    # === 2. 通知检查（使用 Bitable「已通知」字段判断） ===
    new_count = 0

    for r in records:
        rid = r["record_id"]
        fields = r.get("fields", {})
        status = fields.get("执行状态", "")
        task_name = fields.get("任务名称", "?")
        role = fields.get("岗位类型", "?")
        already_notified = fields.get("已通知", "")

        # 只通知「已完成」且「未通知」的任务
        if status == "已完成" and already_notified != "是":
            parent_name = "—"
            parent_links = fields.get("父任务", [])
            if parent_links and isinstance(parent_links, list):
                for link in parent_links:
                    if isinstance(link, dict) and "record_ids" in link:
                        pid = link["record_ids"][0] if link["record_ids"] else None
                        if pid and pid in rmap:
                            parent_name = rmap[pid].get("fields", {}).get("任务名称", "—")
                        break

            # 先标记「已通知」，防止并发运行重复发送
            requests.put(
                f"{BASE_URL}/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records/{rid}",
                json={"fields": {"已通知": "是"}}, headers=hd)
            time.sleep(0.3)

            if send_notification(task_name, role, parent_name):
                new_count += 1
                result["logs"].append(f"   🔔 {task_name} ({role}) → 已通知")
            else:
                # 发送失败，回滚标记，下次重试
                requests.put(
                    f"{BASE_URL}/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records/{rid}",
                    json={"fields": {"已通知": ""}}, headers=hd)
                result["logs"].append(f"   ❌ {task_name} ({role}) → 发送失败，已回滚")

    result["notified"] = new_count
    result["logs"].append(f"🎉 通知完成！发送 {new_count} 条通知")

    # === 3. 更新超期状态 ===
    # 用今天零点比较，截止日期当天不算超期
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
