#!/bin/bash
# gameproxy-go 部署脚本
# 用法：在服务器上 /opt/gameproxy-src/gameproxy-go/ 目录下执行 ./deploy.sh

set -e

SRC_DIR="/opt/gameproxy-src/gameproxy-go"
DEST_DIR="/opt/gameproxy"
BINARY_NAME="gameproxy"

cd "$SRC_DIR"

# 1. 拉取最新代码
echo "→ git pull..."
git pull

# 2. 编译
echo "→ go build..."
go build -o "$BINARY_NAME" .

# 3. 停止旧进程
echo "→ stopping old process..."
pkill -9 -f "$DEST_DIR/$BINARY_NAME" 2>/dev/null || true
sleep 2

# 4. 替换二进制
echo "→ replacing binary..."
mv -f "$BINARY_NAME" "$DEST_DIR/$BINARY_NAME"
chmod +x "$DEST_DIR/$BINARY_NAME"

# 5. 启动
SESSION="${1:-session_latest}"
echo "→ starting gameproxy ($SESSION)..."
mkdir -p "$DEST_DIR/captures/$SESSION"
cd "$DEST_DIR"
nohup ./gameproxy -port 9900 -rules rules.json -dry-run \
  -capture-dir "captures/$SESSION" -capture-ports all \
  > proxy.log 2>&1 &
disown

sleep 2

# 6. 验证
if ss -tlnp | grep -q ":9900"; then
    echo "✓ deployed and running"
    head -8 "$DEST_DIR/proxy.log"
else
    echo "✗ failed to start"
    tail -20 "$DEST_DIR/proxy.log"
    exit 1
fi
