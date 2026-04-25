package main

import "testing"

func TestParseFrame17500(t *testing.T) {
	// 真实样本 pkt 4 (10377B, seq=05, declared payload = 0x2870 = 10352)
	// header: 33 66 00 0a 00 0a 40 13 01 00 00 00 05 00 00 00 19 00 00 28 70 00 10 79 00
	pkt := make([]byte, 10377)
	copy(pkt, []byte{
		0x33, 0x66, 0x00, 0x0a, 0x00, 0x0a, // magic
		0x40, 0x13, // type LE = 0x1340
		0x01, 0x00, 0x00, 0x00, // const
		0x05, 0x00, 0x00, 0x00, // srv_seq LE = 5
		0x19, 0x00, 0x00, // fixed
		0x28, 0x70, // payload_len BE = 0x2870 = 10352
		0x00, 0x00, 0x10, 0x79, 0x00, // fixed
	})
	has, typ, total := parseFrame17500(pkt)
	if !has {
		t.Fatal("expected magic")
	}
	if typ != 0x1340 {
		t.Errorf("type want 0x1340 got 0x%04x", typ)
	}
	if total != 10377 {
		t.Errorf("total want 10377 got %d", total)
	}

	// 跨 TCP Read 的情况：pkt seq=0x48，declared=20640，this read 只有 5696
	part := make([]byte, 5696)
	copy(part, []byte{
		0x33, 0x66, 0x00, 0x0a, 0x00, 0x0a,
		0x40, 0x13,
		0x01, 0x00, 0x00, 0x00,
		0x48, 0x00, 0x00, 0x00,
		0x19, 0x00, 0x00,
		0x50, 0xa0,
		0x00, 0x00, 0x10, 0x79, 0x00,
	})
	has, typ, total = parseFrame17500(part)
	if !has || typ != 0x1340 {
		t.Fatal("expected 0x1340 magic")
	}
	if total != 20665 {
		t.Errorf("total want 20665 got %d", total)
	}
	// 验证会触发续包跟踪
	remaining := total - len(part)
	if remaining != 14969 {
		t.Errorf("remaining want 14969 got %d", remaining)
	}

	// 非控制帧（无 magic）
	enc := make([]byte, 5000)
	enc[0] = 0x5f
	has2, _, _ := parseFrame17500(enc)
	if has2 {
		t.Error("encrypted continuation should not match magic")
	}

	// 多帧打包：模拟真实 conn_000005/s2c_000005.bin 结构
	//   @0:   seq=0x07 declared=480 (total 505)
	//   @505: seq=0x08 declared=32 (total 57)
	//   @562: seq=0x09 declared=32 (total 57)
	multi := make([]byte, 505+57+57)
	copy(multi[0:], []byte{
		0x33, 0x66, 0x00, 0x0a, 0x00, 0x0a, 0x40, 0x13,
		0x01, 0x00, 0x00, 0x00, 0x07, 0x00, 0x00, 0x00,
		0x19, 0x00, 0x00, 0x01, 0xe0, 0x00, 0x10, 0x52, 0x00,
	})
	copy(multi[505:], []byte{
		0x33, 0x66, 0x00, 0x0a, 0x00, 0x0a, 0x40, 0x13,
		0x01, 0x00, 0x00, 0x00, 0x08, 0x00, 0x00, 0x00,
		0x19, 0x00, 0x00, 0x00, 0x20, 0x00, 0x10, 0x52, 0x00,
	})
	h1, t1, s1 := parseFrame17500(multi[0:])
	if !h1 || t1 != 0x1340 || s1 != 505 {
		t.Errorf("frame@0 want 0x1340/505, got %v/0x%04x/%d", h1, t1, s1)
	}
	h2, t2, s2 := parseFrame17500(multi[505:])
	if !h2 || t2 != 0x1340 || s2 != 57 {
		t.Errorf("frame@505 want 0x1340/57, got %v/0x%04x/%d", h2, t2, s2)
	}
}
