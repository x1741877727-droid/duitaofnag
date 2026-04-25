"""diagnose 1 mp4 file decoding"""
import sys, os
import av

p = sys.argv[1] if len(sys.argv) > 1 else r"D:\game-automation\duitaofnag\captures\inst_0_emulator-5554.mp4"

if not os.path.isfile(p):
    print(f"NOT EXIST: {p}")
    sys.exit(1)

print(f"file: {p}  size: {os.path.getsize(p)} bytes")

try:
    c = av.open(p)
    print(f"streams: {[s.type for s in c.streams]}")
    if not c.streams.video:
        print("NO VIDEO STREAM")
        sys.exit(2)
    v = c.streams.video[0]
    print(f"codec: {v.codec_context.name}")
    print(f"frames declared: {v.frames}")
    print(f"duration: {v.duration}")
    print(f"time_base: {v.time_base}")
    print(f"width x height: {v.width} x {v.height}")
    n = 0
    first_pix = None
    for frame in c.decode(v):
        n += 1
        if n == 1:
            arr = frame.to_ndarray(format="bgr24")
            first_pix = arr.mean()
            print(f"  frame 1: shape={arr.shape}, mean={arr.mean():.1f}")
        if n > 10:
            break
    print(f"decoded frames: {n}")
    c.close()
except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"ERROR: {e}")
