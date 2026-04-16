#!/bin/bash
# gameproxy-go 部署脚本（raw 文件下载）
set -e

SRC_DIR="/opt/gameproxy-src/gameproxy-go"
DEST_DIR="/opt/gameproxy"
MIRROR=$(cat /opt/gameproxy-src/.mirror 2>/dev/null || echo "https://ghfast.top/https://raw.githubusercontent.com/x1741877727-droid/duitaofnag/main/gameproxy-go")

export PATH=$PATH:/usr/local/go/bin
export GOPROXY=https://goproxy.cn,direct

cd "$SRC_DIR"

# 1. 下载最新源码
echo "→ downloading latest sources from $MIRROR ..."
FILES="capture.go clients.go failcache.go fpconvert.go go.mod intercept.go main.go relay.go rules.go socks5.go udp.go deploy.sh"
for f in $FILES; do
    curl -sfo "$f.new" "$MIRROR/$f" && mv "$f.new" "$f"
done

# 2. 编译
echo "→ go build..."
go build -o gameproxy .

# 3. 停旧启新
pkill -9 -f "$DEST_DIR/gameproxy" 2>/dev/null || true
sleep 2

mv -f gameproxy "$DEST_DIR/gameproxy"
chmod +x "$DEST_DIR/gameproxy"

SESSION="${1:-session_latest}"
mkdir -p "$DEST_DIR/captures/$SESSION"
cd "$DEST_DIR"
nohup ./gameproxy -port 9900 -rules rules.json -dry-run \
  -capture-dir "captures/$SESSION" -capture-ports all \
  > proxy.log 2>&1 &
disown

sleep 2
if ss -tlnp | grep -q ":9900"; then
    echo "✓ deployed"
    head -8 "$DEST_DIR/proxy.log"
else
    echo "✗ failed"
    tail -20 "$DEST_DIR/proxy.log"
    exit 1
fi
