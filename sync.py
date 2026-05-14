#!/usr/bin/env python3
"""
父子任务字段同步模块
当父任务的「当前阶段」等字段变更时，自动同步到所有子任务。
由飞书多维表格自动化 webhook 触发，也可手动调用。
"""

import os
import time
import requests

APP_ID = os.environ.get("FEISHU_APP_ID")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET")
APP_TOKEN = os.environ.get("BITABLE_APP_TOKEN")
TABLE_ID = os.environ.get("BITABLE_TABLE_ID")
BASE_URL = "https://open.feishu.cn/open-apis"

# 需要从父任务同步到子任务的字段
SYNC_FIELDS = ["当前阶段", "所属迭代", "优先级", "任务类型"]


def get_token():
    resp = requests.post(
        f"{BASE_URL}/auth/v3/tenant_access_token/internal",
        json={"app_id": APP_ID, "app_secret": APP_SECRET},
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    return resp.json()["tenant_access_token"]


def get_all_records(token):
    hd = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    records = []
    page_token = None
    while True:
        params = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token
        resp = requests.get(
            f"{BASE_URL}/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records",
            headers=hd,
            params=params,
        )
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


def sync_parent_fields():
    """
    同步父任务字段到所有子任务。
    返回 {"ok": bool, "synced": int, "total": int, "details": [...]}
    """
    if not all([APP_ID, APP_SECRET, APP_TOKEN, TABLE_ID]):
        return {"ok": False, "error": "环境变量未设置", "synced": 0}

    token = get_token()
    records = get_all_records(token)
    rmap = {r["record_id"]: r for r in records}
    hd = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    sync_count = 0
    details = []

    for r in records:
        fields = r.get("fields", {})
        parent_id = get_parent_id(fields)
        if not parent_id or parent_id not in rmap:
            continue

        parent_fields = rmap[parent_id].get("fields", {})
        updates = {}
        changes = []
        for sf in SYNC_FIELDS:
            pv = parent_fields.get(sf)
            cv = fields.get(sf)
            if pv and pv != cv:
                updates[sf] = pv
                changes.append(f"{sf}: {cv or '空'} → {pv}")

        if updates:
            task_name = fields.get("任务名称", "?")
            resp = requests.put(
                f"{BASE_URL}/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records/{r['record_id']}",
                json={"fields": updates},
                headers=hd,
            )
            if resp.json().get("code") == 0:
                details.append(f"✅ {task_name}: {', '.join(changes)}")
                sync_count += 1
            else:
                details.append(
                    f"❌ {task_name}: {resp.json().get('msg', '未知错误')}"
                )
            time.sleep(0.15)

    return {
        "ok": True,
        "synced": sync_count,
        "total": len(records),
        "details": details,
    }
