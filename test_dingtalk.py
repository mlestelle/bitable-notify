#!/usr/bin/env python3
"""
测试钉钉 @人 功能
只发一条测试消息，不修改任何任务数据
"""
import os
import json
import requests

DINGTALK_WEBHOOK_URL = os.environ.get("DINGTALK_WEBHOOK_URL", "")
NAME_TO_PHONE = json.loads(os.environ.get("NAME_PHONE_MAP", "{}"))

# 用第一个成员测试 @
test_name = list(NAME_TO_PHONE.keys())[0] if NAME_TO_PHONE else "测试"
test_phone = NAME_TO_PHONE.get(test_name, "")

print(f"📋 NAME_TO_PHONE 共 {len(NAME_TO_PHONE)} 人")
print(f"📌 测试 @{test_name} (手机号: {test_phone[:3]}****{test_phone[-4:]})")
print(f"📌 钉钉 Webhook: {'已配置' if DINGTALK_WEBHOOK_URL else '❌ 未配置'}")

text = (
    "### 🧪 任务通知系统测试\n\n"
    "- **📋 这是一条测试消息**\n"
    "- **📢 测试 @人功能**：请确认是否被 @到\n"
    f"- **👤 测试对象**：@{test_name}\n\n"
    "如果你看到这条消息并且被 @了，说明任务通知系统工作正常 ✅"
)

payload = {
    "msgtype": "markdown",
    "markdown": {"title": "通知系统测试", "text": text},
    "at": {
        "atMobiles": [test_phone],
        "isAtAll": False,
    },
}

resp = requests.post(DINGTALK_WEBHOOK_URL, json=payload,
    headers={"Content-Type": "application/json; charset=utf-8"})

print(f"\n📨 钉钉响应: {resp.status_code}")
print(f"📨 响应内容: {resp.json()}")

if resp.json().get("errcode") == 0:
    print("\n✅ 测试消息发送成功！请检查钉钉群是否收到并 @了具体的人")
else:
    print("\n❌ 发送失败！")
