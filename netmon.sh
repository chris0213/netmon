#!/bin/sh
# netmon 便捷入口，用法与 `python3 -m netmon` 完全一致：
#   ./netmon.sh once
#   ./netmon.sh run --duration 120 --interval 10
#   ./netmon.sh report --open
#   ./netmon.sh install --interval 30
set -e
DIR=$(cd "$(dirname "$0")" && pwd)
cd "$DIR"

PY=""
# 优先使用 PATH 中的 python3，退回系统 python3
for c in "$(command -v python3 2>/dev/null || true)" /usr/bin/python3; do
  if [ -n "$c" ] && [ -x "$c" ]; then PY="$c"; break; fi
done

if [ -z "$PY" ]; then
  echo "未找到可用的 python3（需要 3.9 或更高版本）" >&2
  exit 1
fi

exec "$PY" -m netmon "$@"
