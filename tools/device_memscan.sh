#!/system/bin/sh
# 在模拟器本地执行的内存扫描（比ADB管道快几十倍）
# 用法: sh /data/local/tmp/memscan.sh <PID> <关键词>
PID=$1
KEY=$2
echo "Scanning PID=$PID for: $KEY"
FOUND=0
cat /proc/$PID/maps | grep "rw-p" | while IFS=' ' read -r range perm offset dev inode rest; do
  START=$(echo $range | cut -d'-' -f1)
  END=$(echo $range | cut -d'-' -f2)
  START_D=$((0x$START))
  END_D=$((0x$END))
  SZ=$((END_D - START_D))
  # 跳过太大(>8MB)或太小(<4KB)
  [ $SZ -gt 8388608 ] && continue
  [ $SZ -lt 4096 ] && continue
  # 本地读+搜索，极快
  dd if=/proc/$PID/mem bs=4096 skip=$((START_D/4096)) count=$((SZ/4096)) 2>/dev/null | grep -a -c "$KEY" > /dev/null 2>&1
  if [ $? -eq 0 ]; then
    echo "FOUND:$range"
    # 提取上下文
    dd if=/proc/$PID/mem bs=4096 skip=$((START_D/4096)) count=$((SZ/4096)) 2>/dev/null | strings | grep "$KEY"
    FOUND=$((FOUND+1))
  fi
done
echo "DONE found=$FOUND"
