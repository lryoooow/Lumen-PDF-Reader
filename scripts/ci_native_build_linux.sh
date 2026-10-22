#!/bin/sh
# 在隔离的 CI D-Bus 会话中提供真正的系统凭据服务，不使用空后端绕过自检。
set -eu
printf '%s' 'lumen-ci-ephemeral-keyring' | gnome-keyring-daemon --unlock --components=secrets
uv run python scripts/build_desktop.py
