package tun2socks;
public interface DBService {
    void insertProxyLog(String target, String tag, long start, long elapsed, int sent, int received, int protocol, int logType, String uid, String extra, int status);
}
