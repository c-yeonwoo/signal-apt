#!/usr/bin/env bash
# 주간 데이터 자동 갱신 설치 (macOS launchd) — 매주 토 09:00 `signal watch` 실행.
# KB 발표일(금) 다음날 최신 지표 수집 + 등급 변화 시 알림.
set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.realtysignal.weekly.plist"

if [ ! -x "$PROJECT/.venv/bin/signal" ]; then
  echo "✗ $PROJECT/.venv/bin/signal 없음 — venv 설치 먼저(.venv)" >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT/data/cache"
sed "s#__PROJECT__#$PROJECT#g" \
  "$PROJECT/scripts/com.realtysignal.weekly.plist.template" > "$PLIST"

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo "✓ 설치됨: $PLIST"
echo "  실행: 매주 토 09:00 → signal watch (fetch + 등급변화 알림)"
echo "  로그: $PROJECT/data/cache/watch.log"
launchctl list | grep realtysignal && echo "✓ launchd 등록 확인" || echo "(launchctl list에 아직 안 보이면 재로그인 후 확인)"
