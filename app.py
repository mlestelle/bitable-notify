#!/usr/bin/env python3
"""
Flask 入口 - Render Web Service
端点：
  GET  /       - 健康检查
  GET  /run    - 定时通知检查（cron-job.org 调用）
  GET  /sync   - 手动触发父子任务字段同步
  POST /sync   - 飞书多维表格自动化 webhook 触发同步
"""

import os
import logging
from flask import Flask, jsonify, request

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

API_TOKEN = os.environ.get("API_TOKEN", "")


@app.route("/")
def health():
    """健康检查"""
    return jsonify({"status": "ok", "service": "bitable-notify"})


@app.route("/run")
def run_notify():
    """执行通知检查，由 cron-job.org 定时调用"""
    # 简单 token 验证
    token = request.args.get("token", "")
    if API_TOKEN and token != API_TOKEN:
        return jsonify({"error": "unauthorized"}), 401

    from notify import main
    result = main()
    return jsonify(result)


@app.route("/sync", methods=["GET", "POST"])
def run_sync():
    """
    父子任务字段同步端点。
    - GET:  手动触发（带 ?token=xxx 验证）
    - POST: 飞书多维表格自动化 webhook 触发（无需 token，因为自动化配置了固定 URL）
    """
    # GET 请求需要 token 验证（手动调用 / cron）
    if request.method == "GET":
        token = request.args.get("token", "")
        if API_TOKEN and token != API_TOKEN:
            return jsonify({"error": "unauthorized"}), 401

    # POST 请求来自飞书自动化，记录 payload 用于调试
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        app.logger.info(f"🔔 收到飞书自动化 webhook: {payload}")

    from sync import sync_parent_fields
    result = sync_parent_fields()

    app.logger.info(f"🔄 同步完成: synced={result.get('synced', 0)}")
    return jsonify(result)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
