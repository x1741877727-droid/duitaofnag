package tun2socks;

import go.Seq;

public abstract class Tun2socks {

    private static final class proxyDBService implements Seq.Proxy, DBService {
        private final int refnum;
        proxyDBService(int i) { this.refnum = i; Seq.trackGoRef(i, this); }
        @Override public final int incRefnum() { Seq.incGoRef(this.refnum, this); return this.refnum; }
        @Override public native void insertProxyLog(String str, String str2, long j, long j2, int i, int i2, int i3, int i4, String str3, String str4, int i5);
    }

    private static final class proxyPacketFlow implements Seq.Proxy, PacketFlow {
        private final int refnum;
        proxyPacketFlow(int i) { this.refnum = i; Seq.trackGoRef(i, this); }
        @Override public final int incRefnum() { Seq.incGoRef(this.refnum, this); return this.refnum; }
        @Override public native void writePacket(byte[] bArr);
    }

    private static final class proxyVpnService implements Seq.Proxy, VpnService {
        private final int refnum;
        proxyVpnService(int i) { this.refnum = i; Seq.trackGoRef(i, this); }
        @Override public native void didStop();
        @Override public final int incRefnum() { Seq.incGoRef(this.refnum, this); return this.refnum; }
        @Override public native boolean protect(long j);
    }

    static { Seq.touch(); _init(); }
    private Tun2socks() {}

    private static native void _init();
    public static native void endLatencyMeasuring();
    public static native void inputPacket(byte[] bArr);
    public static native long measureLatency(String str, String str2, long j);
    public static native long prepareLatencyMeasuring(byte[] bArr);
    public static native void setBlockConnections(boolean z);
    public static native void setLocalDNS(String str);
    public static native boolean setNonblock(long j, boolean z);
    public static native void shutdownActiveConnections();
    public static native long startV2Ray(long j, VpnService vpnService, DBService dBService, byte[] bArr, String str, String str2, String str3, boolean z, boolean z2, String str4);
    public static native void stopV2Ray();
    public static void touch() {}
}
