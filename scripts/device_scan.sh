#!/system/bin/sh
PID=$(pidof com.tencent.tmgp.pubgmhd | cut -d' ' -f1)
echo "PID: $PID"

# 搜索所有 rw 段中的活动配置关键词
cat /proc/$PID/maps | grep 'rw-p' | while read line; do
  RANGE=$(echo "$line" | awk '{print $1}')
  START_HEX=$(echo "$RANGE" | cut -d'-' -f1)
  END_HEX=$(echo "$RANGE" | cut -d'-' -f2)

  # 转十进制
  START_DEC=$(printf "%d" "0x$START_HEX" 2>/dev/null)
  END_DEC=$(printf "%d" "0x$END_HEX" 2>/dev/null)

  [ -z "$START_DEC" ] && continue
  [ -z "$END_DEC" ] && continue

  SIZE=$((END_DEC - START_DEC))

  # 只扫大于64KB小于128MB的段
  [ $SIZE -lt 65536 ] && continue
  [ $SIZE -gt 134217728 ] && continue

  # 用 dd 读取这个段并搜索关键词
  HITS=$(dd if=/proc/$PID/mem bs=4096 skip=$((START_DEC/4096)) count=$((SIZE/4096)) 2>/dev/null | grep -c "isShow\|actStartDate\|popupConfig\|ShowActivityUI\|Advertisings" 2>/dev/null)

  if [ "$HITS" -gt 0 ] 2>/dev/null; then
    echo "HIT seg=0x${START_HEX}-0x${END_HEX} size=$SIZE hits=$HITS"
    # 读取具体匹配位置
    dd if=/proc/$PID/mem bs=4096 skip=$((START_DEC/4096)) count=$((SIZE/4096)) 2>/dev/null | grep -boa "isShow\|actStartDate\|Advertisings" 2>/dev/null | head -10 | while read match; do
      OFFSET=$(echo "$match" | cut -d: -f1)
      WORD=$(echo "$match" | cut -d: -f2)
      ACTUAL=$((START_DEC + OFFSET))
      printf "  0x%x: %s\n" "$ACTUAL" "$WORD"
      # 读取周围200字节的上下文
      dd if=/proc/$PID/mem bs=1 skip=$ACTUAL count=200 2>/dev/null | strings -n 4 | head -3
    done
  fi
done

echo "=== SCAN DONE ==="
