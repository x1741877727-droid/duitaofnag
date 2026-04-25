# 自有 Wheel 镜像服务器（国内 PyPI 兜底）

`tools/auto_configure.py` 装包时按这个顺序 fallback：
1. PyPI 默认源
2. 清华 (`pypi.tuna.tsinghua.edu.cn`)
3. 阿里 (`mirrors.aliyun.com/pypi`)
4. **本文档要部署的：你自己的 wheel 镜像**

国内 PyPI 偶尔抽风，自己开个镜像保命 + 提供合规分发渠道。

## 部署在哪

复用现有 `171.80.4.221`（已跑 gameproxy）。开个新的 HTTP 端口（默认 9902）静态服务一个 `wheels/` 目录就行。

## 快速部署（5 分钟，nginx）

### 1. 准备目录

```bash
ssh root@171.80.4.221
mkdir -p /opt/wheels
cd /opt/wheels
```

### 2. 下载需要的 wheel（一次性，本地有 PyPI 时跑）

```bash
# 把所有变体（OS x Python 版本）拉齐
PY_VERSIONS=(310 311 312 313)
for py in ${PY_VERSIONS[@]}; do
  # Windows DirectML
  pip download --no-deps --python-version $py --platform win_amd64 \
    --only-binary :all: -d /opt/wheels/ onnxruntime-directml
  # Linux CUDA
  pip download --no-deps --python-version $py --platform manylinux2014_x86_64 \
    --only-binary :all: -d /opt/wheels/ onnxruntime-gpu
  # 通用 onnxruntime
  pip download --no-deps --python-version $py --platform win_amd64 \
    --only-binary :all: -d /opt/wheels/ onnxruntime
  pip download --no-deps --python-version $py --platform manylinux2014_x86_64 \
    --only-binary :all: -d /opt/wheels/ onnxruntime
  pip download --no-deps --python-version $py --platform macosx_11_0_arm64 \
    --only-binary :all: -d /opt/wheels/ onnxruntime
done
```

### 3. 生成 manifest.json（含 SHA256）

```bash
cd /opt/wheels
python3 <<'EOF'
import hashlib, json, os, re
out = {"version": "1.0", "wheels": {}}
for f in sorted(os.listdir(".")):
    if not f.endswith(".whl"):
        continue
    # 文件名形如 onnxruntime-directml-1.24.4-cp312-cp312-win_amd64.whl
    m = re.match(r"^(?P<pkg>[a-z_-]+?)-[\d.]+-(?P<py>cp\d+)-cp\d+-(?P<plat>[a-z_0-9]+)\.whl$", f)
    if not m:
        print(f"SKIP unrecognized {f}")
        continue
    h = hashlib.sha256()
    with open(f, "rb") as fp:
        for chunk in iter(lambda: fp.read(8192), b""):
            h.update(chunk)
    key = f"{m['pkg']}-{m['py']}-{m['plat']}"
    out["wheels"][key] = {
        "url": f"http://171.80.4.221:9902/wheels/{f}",
        "sha256": h.hexdigest(),
        "size": os.path.getsize(f),
    }
open("manifest.json", "w").write(json.dumps(out, indent=2))
print(f"manifest 写完，{len(out['wheels'])} 条目")
EOF
```

### 4. nginx 配置

```bash
cat > /etc/nginx/sites-available/wheels <<'EOF'
server {
  listen 9902;
  server_name _;
  root /opt/wheels;
  autoindex on;

  # CORS（pip / urllib 直连不需要，但给浏览器调试用）
  add_header Access-Control-Allow-Origin *;

  # 限速（防滥用）
  limit_rate 10m;     # 每连接 10 MB/s
  client_max_body_size 0;

  location / {
    try_files $uri $uri/ =404;
  }
}
EOF

ln -s /etc/nginx/sites-available/wheels /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

### 5. 验证

```bash
curl http://171.80.4.221:9902/manifest.json | python -m json.tool | head -20
```

客户端：
```bash
GAMEBOT_WHEEL_SERVER=http://171.80.4.221:9902/wheels python tools/auto_configure.py --force
```

## 升级 wheel 版本

```bash
# 重新下 + 重新生成 manifest
cd /opt/wheels
rm -f *.whl manifest.json
# ... 重跑步骤 2 + 3
```

## 用 cloudflared 走 HTTPS（可选）

```bash
# 在 171.80.4.221 上
cloudflared tunnel --url http://localhost:9902
# 拿到 https://xxx-yyy.trycloudflare.com 的 URL，配到客户端：
# GAMEBOT_WHEEL_SERVER=https://xxx-yyy.trycloudflare.com/wheels
```

## 安全考虑

- ✅ **SHA256 校验**（client 强制比对 manifest 里的 hash，防 MITM）
- ⚠️ HTTP 明文（推荐上 HTTPS via cloudflared）
- ⚠️ 公开 endpoint 任何人都能下（限速 + 限 IP 段可加）

## 存储估算

- 每个 wheel: 25-200 MB（onnxruntime-directml 约 25 MB, onnxruntime-gpu 约 200 MB CUDA libs）
- 4 个 Python 版本 × 5 个变体 ≈ 20 个 wheel ≈ 1-2 GB
