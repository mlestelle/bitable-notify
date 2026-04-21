import re

with open("notify.py", "r") as f:
    content = f.read()

# Remove STAGE_NOTIFY
content = re.sub(r"STAGE_NOTIFY = \{[\s\S]*?\n\}\n", "", content)

# Add ROLE_NOTIFY
role_notify_code = """
# ===== 岗位完成后的提醒配置 =====
ROLE_NOTIFY = {
    "策划": {"downstream": ["原画", "动画", "开发"], "message": "📋 策划已完成，请下游跟进"},
    "原画": {"downstream": ["动画", "开发"], "message": "🎨 原画已完成"},
    "动画": {"downstream": ["开发"], "message": "🏃 动画已完成"},
    "开发": {"downstream": ["测试"], "message": "🔧 开发完成，请测试介入"},
    "测试": {"downstream": ["数据产品"], "message": "🧪 测试通过，请数据产品验收"},
    "数据产品": {"downstream": ["策划"], "message": "✅ 数据反馈已完成，请策划处理"},
}
"""
content = content.replace("# 父子任务字段同步列表", role_notify_code + "\n# 父子任务字段同步列表")

# Replace notify trigger from stage to role
old_trigger_logic = """
        # 该阶段是否有通知配置
        notify_config = STAGE_NOTIFY.get(stage)
"""
new_trigger_logic = """
        role = fields.get("岗位类型", "")
        # 获取该岗位的下游配置
        notify_config = ROLE_NOTIFY.get(role)
"""
content = content.replace(old_trigger_logic, new_trigger_logic)

# Replace target downstream extraction
content = content.replace("notify_config.get(\"notify_self\")", "False")
content = content.replace("notify_config[\"downstream_roles\"]", "notify_config[\"downstream\"]")
content = content.replace("notify_config.get(\"downstream_roles\")", "notify_config.get(\"downstream\")")
content = content.replace("at_all = notify_config.get(\"at_all\", False)", "at_all = False")

# Replace send parameter 'stage' with 'role'
content = content.replace(
    "send_dingtalk(task_name, stage, parent_name, notify_config[\"message\"], people, at_all)",
    "send_dingtalk(task_name, role, parent_name, notify_config[\"message\"], people, at_all)"
)
# Fix the print strings
content = content.replace("f\"   🔔 {task_name} [{stage}] → @{at_names}\"", "f\"   🔔 {task_name} [{role}] → @{at_names}\"")
content = content.replace("f\"   ❌ {task_name} [{stage}] → 发送失败，已回滚\"", "f\"   ❌ {task_name} [{role}] → 发送失败，已回滚\"")

# Fix send_dingtalk signature and string
content = content.replace(
    "def send_dingtalk(task_name, stage, parent_name, message, people, at_all=False):",
    "def send_dingtalk(task_name, role, parent_name, message, people, at_all=False):"
)
content = content.replace(
    "f\"- **📍 阶段**：{stage} ✅\\n\"",
    "f\"- **👤 岗位**：{role} ✅\\n\""
)

with open("notify.py", "w") as f:
    f.write(content)
print("Patch applied.")
