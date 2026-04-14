package go;

import go.Seq;

public abstract class Universe {

    private static final class proxyerror extends Exception implements Seq.Proxy, error {
        private final int refnum;

        proxyerror(int i) {
            this.refnum = i;
            Seq.trackGoRef(i, this);
        }

        @Override
        public native String error();

        @Override
        public String getMessage() {
            return error();
        }

        @Override
        public final int incRefnum() {
            Seq.incGoRef(this.refnum, this);
            return this.refnum;
        }
    }

    static {
        Seq.touch();
        _init();
    }

    private Universe() {
    }

    private static native void _init();

    public static void touch() {
    }
}
