# FightMaster VPN proguard rules
# 仅混淆业务类，保留所有 v2ray / tun2socks / VPN 框架必需类

# tun2socks JNI 导出（不保留运行时 native 调用找不到类会崩）
-keep class tun2socks.** { *; }
-keep class go.** { *; }
-keepclasseswithmembers class * {
    native <methods>;
}

# Android VpnService 框架（onStartCommand 等回调）
-keep class * extends android.net.VpnService
-keepclassmembers class * extends android.net.VpnService {
    public <init>(...);
    public *;
}

# FightMaster 业务入口（保留类名但允许混淆内部方法名）
-keep public class com.fightmaster.vpn.FightMasterVpnService { public *; }
-keep public class com.fightmaster.vpn.MainActivity { public *; }
-keep public class com.fightmaster.vpn.AppSelectActivity { public *; }
-keep public class com.fightmaster.vpn.Tun2socksWrapper { *; }

# JSON 处理（v2ray config 构造用反射）
-keepclassmembers class org.json.** { *; }

# 保留 HostObfuscate（混淆后名字变乱，但逻辑还在）
-keepclassmembers class com.fightmaster.vpn.HostObfuscate {
    static java.lang.String getHost();
    static int getPort();
}

# 去掉 debug 日志
-assumenosideeffects class android.util.Log {
    public static *** v(...);
    public static *** d(...);
}

# 保留序列化相关（如果用 Parcelable/Serializable）
-keepclassmembers class * implements java.io.Serializable {
    static final long serialVersionUID;
    private static final java.io.ObjectStreamField[] serialPersistentFields;
    private void writeObject(java.io.ObjectOutputStream);
    private void readObject(java.io.ObjectInputStream);
    java.lang.Object writeReplace();
    java.lang.Object readResolve();
}
-keep class * implements android.os.Parcelable {
    public static final android.os.Parcelable$Creator *;
}
