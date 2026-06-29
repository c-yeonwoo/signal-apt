#!/usr/bin/env bash
# 주간 데이터 자동 갱신 해제 (macOS launchd).
set -euo pipefail
PLIST="$HOME/Library/LaunchAgents/com.realtysignal.weekly.plist"
launchctl unload "$PLIST" 2>/dev/null || true
rm -f "$PLIST"
echo "✓ 해제됨: $PLIST"
