package com.fightmaster.vpn;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaFormat;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.net.LocalServerSocket;
import android.net.LocalSocket;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.util.Log;
import android.view.Surface;

import java.io.IOException;
import java.io.OutputStream;
import java.nio.ByteBuffer;

/**
 * 屏幕捕获前台服务
 *
 * 数据流：
 *   MediaProjection
 *     → VirtualDisplay (默认 1280x720 / 可配置 scale)
 *     → MediaCodec.createInputSurface() (H.264 编码器)
 *     → encoded NALU
 *     → LocalServerSocket("fmcapture") 推送给 Python 客户端
 *
 * Python 通过 `adb forward tcp:NNNN localabstract:fmcapture` 接入
 *
 * 协议（client 视角）：
 *   while True:
 *     length = read 4 bytes BE u32
 *     payload = read `length` bytes  (raw H.264 NALU, 含 Annex-B start code)
 *
 * 比 screenrecord 优势：
 *   - 单进程常驻（screenrecord 每 170s 重启）
 *   - bitrate 1.5 Mbps（screenrecord 默认 20 Mbps）
 *   - foregroundServiceType=mediaProjection，OOM 保护
 */
public class CaptureService extends Service {

    private static final String TAG = "FMCapture";
    private static final String CHANNEL_ID = "fightmaster_capture";
    private static final int NOTIF_ID = 2002;

    public static final String ACTION_START = "com.fightmaster.vpn.CAPTURE_START";
    public static final String ACTION_STOP = "com.fightmaster.vpn.CAPTURE_STOP";
    public static final String EXTRA_RESULT_CODE = "result_code";
    public static final String EXTRA_RESULT_DATA = "result_data";
    public static final String EXTRA_WIDTH = "width";
    public static final String EXTRA_HEIGHT = "height";
    public static final String EXTRA_BITRATE = "bitrate";
    public static final String EXTRA_FPS = "fps";

    private static final int DEFAULT_WIDTH = 1280;
    private static final int DEFAULT_HEIGHT = 720;
    private static final int DEFAULT_BITRATE = 1_500_000;  // 1.5 Mbps
    private static final int DEFAULT_FPS = 15;
    private static final int DEFAULT_I_FRAME_INTERVAL = 1; // 1s I-frame
    private static final String SOCKET_NAME = "fmcapture";
    private static final String MIME_TYPE = "video/avc";
    private static final long DEQUEUE_TIMEOUT_US = 10_000;  // 10ms

    private MediaProjection projection;
    private VirtualDisplay virtualDisplay;
    private MediaCodec encoder;
    private Surface inputSurface;
    private LocalServerSocket serverSocket;
    private HandlerThread acceptThread;
    private HandlerThread encodeThread;
    private Handler acceptHandler;
    private Handler encodeHandler;
    private volatile boolean running = false;
    private volatile LocalSocket activeClient;
    private final Object clientLock = new Object();

    private int width = DEFAULT_WIDTH;
    private int height = DEFAULT_HEIGHT;
    private int bitrate = DEFAULT_BITRATE;
    private int fps = DEFAULT_FPS;

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null) return START_NOT_STICKY;
        String action = intent.getAction();
        if (ACTION_STOP.equals(action)) {
            stopCapture();
            stopForeground(true);
            stopSelf();
            return START_NOT_STICKY;
        }

        if (!ACTION_START.equals(action)) {
            return START_NOT_STICKY;
        }

        int resultCode = intent.getIntExtra(EXTRA_RESULT_CODE, -1);
        Intent resultData = intent.getParcelableExtra(EXTRA_RESULT_DATA);
        if (resultData == null) {
            Log.e(TAG, "ACTION_START 缺少 result_data");
            stopSelf();
            return START_NOT_STICKY;
        }

        width = intent.getIntExtra(EXTRA_WIDTH, DEFAULT_WIDTH);
        height = intent.getIntExtra(EXTRA_HEIGHT, DEFAULT_HEIGHT);
        bitrate = intent.getIntExtra(EXTRA_BITRATE, DEFAULT_BITRATE);
        fps = intent.getIntExtra(EXTRA_FPS, DEFAULT_FPS);

        startForeground(NOTIF_ID, buildNotification());

        try {
            startCapture(resultCode, resultData);
        } catch (Exception e) {
            Log.e(TAG, "startCapture 失败", e);
            stopSelf();
        }

        return START_STICKY;
    }

    private void startCapture(int resultCode, Intent resultData) throws IOException {
        MediaProjectionManager mpm =
                (MediaProjectionManager) getSystemService(MEDIA_PROJECTION_SERVICE);
        if (mpm == null) throw new IOException("MediaProjectionManager 不可用");
        projection = mpm.getMediaProjection(resultCode, resultData);
        if (projection == null) throw new IOException("getMediaProjection 返回 null");

        // 1. 配置 MediaCodec H.264 encoder
        MediaFormat format = MediaFormat.createVideoFormat(MIME_TYPE, width, height);
        format.setInteger(MediaFormat.KEY_COLOR_FORMAT,
                MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface);
        format.setInteger(MediaFormat.KEY_BIT_RATE, bitrate);
        format.setInteger(MediaFormat.KEY_FRAME_RATE, fps);
        format.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, DEFAULT_I_FRAME_INTERVAL);

        encoder = MediaCodec.createEncoderByType(MIME_TYPE);
        encoder.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
        inputSurface = encoder.createInputSurface();
        encoder.start();

        // 2. VirtualDisplay 把屏幕镜像到 encoder 的 input surface
        int dpi = getResources().getDisplayMetrics().densityDpi;
        virtualDisplay = projection.createVirtualDisplay(
                "FMCapture",
                width, height, dpi,
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                inputSurface,
                null, null);

        // 3. LocalServerSocket（独占名，6 实例每个 Android instance 都跑一份）
        serverSocket = new LocalServerSocket(SOCKET_NAME);
        Log.i(TAG, String.format(
                "CaptureService 启动 %dx%d @ %d fps / %d bps, socket=%s",
                width, height, fps, bitrate, SOCKET_NAME));

        running = true;

        // 4. accept 线程（等 Python 客户端连接，每次 accept 后切到 active client）
        acceptThread = new HandlerThread("CaptureAccept");
        acceptThread.start();
        acceptHandler = new Handler(acceptThread.getLooper());
        acceptHandler.post(this::acceptLoop);

        // 5. encode 线程（drain encoder output → 推到当前 client socket）
        encodeThread = new HandlerThread("CaptureEncode");
        encodeThread.start();
        encodeHandler = new Handler(encodeThread.getLooper());
        encodeHandler.post(this::encodeLoop);
    }

    private void acceptLoop() {
        while (running) {
            try {
                LocalSocket client = serverSocket.accept();
                Log.i(TAG, "client connected from PID " +
                        client.getPeerCredentials().getPid());
                synchronized (clientLock) {
                    if (activeClient != null) {
                        try { activeClient.close(); } catch (IOException ignored) {}
                    }
                    activeClient = client;
                }
                // request encoder 立刻发一帧 IDR，让新 client 能从头解码
                if (encoder != null) {
                    android.os.Bundle params = new android.os.Bundle();
                    params.putInt(MediaCodec.PARAMETER_KEY_REQUEST_SYNC_FRAME, 0);
                    try { encoder.setParameters(params); } catch (Exception ignored) {}
                }
            } catch (IOException e) {
                if (running) Log.w(TAG, "accept err: " + e.getMessage());
                break;
            }
        }
    }

    private void encodeLoop() {
        MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
        while (running) {
            try {
                int outIdx = encoder.dequeueOutputBuffer(info, DEQUEUE_TIMEOUT_US);
                if (outIdx >= 0) {
                    if (info.size > 0) {
                        ByteBuffer outBuf = encoder.getOutputBuffer(outIdx);
                        if (outBuf != null) {
                            outBuf.position(info.offset);
                            outBuf.limit(info.offset + info.size);
                            byte[] data = new byte[info.size];
                            outBuf.get(data);
                            sendToClient(data);
                        }
                    }
                    encoder.releaseOutputBuffer(outIdx, false);
                    if ((info.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0) {
                        Log.i(TAG, "encoder EOS");
                        break;
                    }
                }
                // outIdx < 0: INFO_TRY_AGAIN_LATER / INFO_OUTPUT_FORMAT_CHANGED — 忽略继续
            } catch (IllegalStateException e) {
                if (running) Log.w(TAG, "encoder dequeue err: " + e.getMessage());
                break;
            }
        }
    }

    private void sendToClient(byte[] data) {
        LocalSocket c;
        synchronized (clientLock) {
            c = activeClient;
        }
        if (c == null) return;  // 没人连接，丢弃这一帧（不缓存，避免 OOM）
        try {
            OutputStream out = c.getOutputStream();
            // 4 byte BE length + payload
            out.write((data.length >>> 24) & 0xff);
            out.write((data.length >>> 16) & 0xff);
            out.write((data.length >>> 8) & 0xff);
            out.write(data.length & 0xff);
            out.write(data);
            out.flush();
        } catch (IOException e) {
            Log.i(TAG, "client 断开: " + e.getMessage());
            synchronized (clientLock) {
                if (activeClient == c) {
                    try { activeClient.close(); } catch (IOException ignored) {}
                    activeClient = null;
                }
            }
        }
    }

    private void stopCapture() {
        running = false;
        if (encoder != null) {
            try { encoder.stop(); } catch (Exception ignored) {}
            try { encoder.release(); } catch (Exception ignored) {}
            encoder = null;
        }
        if (inputSurface != null) {
            try { inputSurface.release(); } catch (Exception ignored) {}
            inputSurface = null;
        }
        if (virtualDisplay != null) {
            try { virtualDisplay.release(); } catch (Exception ignored) {}
            virtualDisplay = null;
        }
        if (projection != null) {
            try { projection.stop(); } catch (Exception ignored) {}
            projection = null;
        }
        synchronized (clientLock) {
            if (activeClient != null) {
                try { activeClient.close(); } catch (Exception ignored) {}
                activeClient = null;
            }
        }
        if (serverSocket != null) {
            try { serverSocket.close(); } catch (Exception ignored) {}
            serverSocket = null;
        }
        if (encodeThread != null) {
            encodeThread.quitSafely();
            encodeThread = null;
            encodeHandler = null;
        }
        if (acceptThread != null) {
            acceptThread.quitSafely();
            acceptThread = null;
            acceptHandler = null;
        }
        Log.i(TAG, "CaptureService 已停止");
    }

    @Override
    public void onDestroy() {
        stopCapture();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private Notification buildNotification() {
        NotificationManager nm =
                (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && nm != null) {
            NotificationChannel ch = new NotificationChannel(
                    CHANNEL_ID, "FightMaster Capture", NotificationManager.IMPORTANCE_LOW);
            ch.setDescription("屏幕捕获服务");
            nm.createNotificationChannel(ch);
        }
        Notification.Builder builder = (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
                ? new Notification.Builder(this, CHANNEL_ID)
                : new Notification.Builder(this);
        return builder
                .setContentTitle("FightMaster Capture")
                .setContentText("正在捕获屏幕")
                .setSmallIcon(android.R.drawable.ic_menu_camera)
                .setOngoing(true)
                .build();
    }
}
