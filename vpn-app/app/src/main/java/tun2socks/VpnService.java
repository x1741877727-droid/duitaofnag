package tun2socks;
public interface VpnService {
    boolean protect(long fd);
    void didStop();
}
