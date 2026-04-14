#!/usr/bin/env python3
"""
Flask 入口 - Render Web Service
cron-job.org 定时调用 /run 端点触发通知逻辑
"""

import os
from flask import Flask, jsonify, request

app = Flask(__name__)

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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
