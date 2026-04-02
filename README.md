# Bitable 任务通知机器人

飞书多维表格任务状态变更自动通知。每 5 分钟检查一次，任务标记为「已完成」后自动通知下游岗位。

## 配置

在 GitHub 仓库的 **Settings → Secrets and variables → Actions** 中添加以下 Secrets：

| Secret 名称 | 值 |
|---|---|
| `FEISHU_APP_ID` | 飞书 App ID |
| `FEISHU_APP_SECRET` | 飞书 App Secret |
| `FEISHU_WEBHOOK_URL` | 飞书群机器人 Webhook 地址 |
| `BITABLE_APP_TOKEN` | 多维表格 Token |
| `BITABLE_TABLE_ID` | 数据表 ID |
