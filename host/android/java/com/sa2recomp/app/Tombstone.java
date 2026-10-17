package com.sa2recomp.app;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Reads the parts of an Android tombstone a crash report needs: the signal, the abort message, the
 * system's stated cause, and the crashing thread's name, registers and backtrace. Android 12 and
 * later hand an app its own process's native crash as a protobuf (ApplicationExitInfo's trace
 * stream, AOSP system/core/debuggerd/proto/tombstone.proto); this is a reader for the few fields
 * used here, so the app needs no protobuf library. A frame is printed as the module, its offset
 * in that module (rel_pc) and the module's build ID, which is what symbolizing it later takes.
 */
final class Tombstone {
    // Tombstone
    private static final int T_TID = 6, T_SIGNAL = 10, T_ABORT = 14, T_CAUSES = 15, T_THREADS = 16;
    // Signal
    private static final int S_NAME = 2, S_CODE_NAME = 4, S_HAS_FAULT = 8, S_FAULT = 9;
    // Thread
    private static final int H_ID = 1, H_NAME = 2, H_REGISTERS = 3, H_FRAMES = 4;
    // BacktraceFrame
    private static final int F_REL_PC = 1, F_FUNCTION = 4, F_FUNCTION_OFFSET = 5, F_FILE = 6, F_BUILD_ID = 8;

    private Tombstone() {}

    /** Whatever of the fields it could read: a tombstone cut short (the caller reads at most 8 MiB, and
     * the memory maps and log buffers come after the threads) or malformed further on keeps what came
     * before. */
    static String describe(byte[] data, int maxFrames, CrashReport.Redactor r) {
        StringBuilder out = new StringBuilder();
        long tid = -1;
        String signal = null, abort = null, error = null;
        List<String> causes = new ArrayList<>();
        List<Reader> threads = new ArrayList<>();
        try {
            Reader t = new Reader(data, 0, data.length);
            while (t.next()) {
                switch (t.field) {
                    case T_TID: tid = t.varint(); break;
                    case T_SIGNAL: signal = signal(t.message()); break;
                    case T_ABORT: abort = t.string(); break;
                    case T_CAUSES: {
                        Reader c = t.message();
                        while (c.next()) {
                            if (c.field == 1) causes.add(c.string());
                            else c.skip();
                        }
                        break;
                    }
                    case T_THREADS: {
                        Reader entry = t.message();     // map<uint32, Thread>: key 1, value 2
                        while (entry.next()) {
                            if (entry.field == 2) threads.add(entry.message());
                            else entry.skip();
                        }
                        break;
                    }
                    default: t.skip();
                }
            }
        } catch (RuntimeException e) {
            error = e.toString();
        }
        if (signal != null) out.append("signal: ").append(signal).append('\n');
        if (abort != null && !abort.isEmpty()) out.append("abort: ").append(r.apply(abort)).append('\n');
        for (String c : causes) out.append("cause: ").append(r.apply(c)).append('\n');
        for (Reader th : threads) {
            try {
                if (thread(th, tid, maxFrames, out, r)) break;
            } catch (RuntimeException e) {
                if (error == null) error = e.toString();
            }
        }
        if (error != null) out.append("tombstone: ").append(data.length).append(" bytes, partly read (").append(error).append(")\n");
        return out.toString();
    }

    private static String signal(Reader s) {
        String name = "?", code = "";
        boolean hasFault = false;
        long fault = 0;
        while (s.next()) {
            switch (s.field) {
                case S_NAME: name = s.string(); break;
                case S_CODE_NAME: code = s.string(); break;
                case S_HAS_FAULT: hasFault = s.varint() != 0; break;
                case S_FAULT: fault = s.varint(); break;
                default: s.skip();
            }
        }
        return name + (code.isEmpty() ? "" : " (" + code + ")")
               + (hasFault ? String.format(Locale.US, ", fault address 0x%x", fault) : "");
    }

    /** Appends the thread when it is the crashing one (its id is the tombstone's tid); says
     * whether it was. */
    private static boolean thread(Reader th, long tid, int maxFrames, StringBuilder out, CrashReport.Redactor r) {
        long id = -1;
        String name = "";
        List<String> regs = new ArrayList<>();
        List<String> frames = new ArrayList<>();
        while (th.next()) {
            switch (th.field) {
                case H_ID: id = th.varint(); break;
                case H_NAME: name = th.string(); break;
                case H_REGISTERS: {
                    Reader g = th.message();
                    String rn = "";
                    long v = 0;
                    while (g.next()) {
                        if (g.field == 1) rn = g.string();
                        else if (g.field == 2) v = g.varint();
                        else g.skip();
                    }
                    if (rn.equals("pc") || rn.equals("lr") || rn.equals("sp") || rn.equals("rip") || rn.equals("rsp")) {
                        regs.add(String.format(Locale.US, "%s 0x%x", rn, v));
                    }
                    break;
                }
                case H_FRAMES:
                    if (frames.size() < maxFrames) frames.add(frame(th.message(), frames.size(), r));
                    else th.skip();
                    break;
                default: th.skip();
            }
        }
        if (tid >= 0 && id != tid) return false;
        out.append("crashing thread: ").append(id).append(" \"").append(name).append("\"");
        if (!regs.isEmpty()) out.append(", ").append(String.join(", ", regs));
        out.append('\n');
        for (String f : frames) out.append(f).append('\n');
        return true;
    }

    private static String frame(Reader f, int n, CrashReport.Redactor r) {
        long rel = 0, off = 0;
        String fn = "", file = "", build = "";
        while (f.next()) {
            switch (f.field) {
                case F_REL_PC: rel = f.varint(); break;
                case F_FUNCTION: fn = f.string(); break;
                case F_FUNCTION_OFFSET: off = f.varint(); break;
                case F_FILE: file = f.string(); break;
                case F_BUILD_ID: build = f.string(); break;
                default: f.skip();
            }
        }
        StringBuilder b = new StringBuilder(String.format(Locale.US, "  #%02d pc %08x  %s", n, rel, r.apply(file)));
        if (!fn.isEmpty()) b.append(" (").append(fn).append('+').append(off).append(')');
        if (!build.isEmpty()) b.append(" (BuildId: ").append(build).append(')');
        return b.toString();
    }

    /** A protobuf wire-format reader over a slice: next() reads a key, then the caller takes the
     * value with varint(), string() or message(), or skip(). */
    private static final class Reader {
        private final byte[] b;
        private int p;
        private final int end;
        int field, wire;

        Reader(byte[] b, int from, int to) {
            this.b = b;
            this.p = from;
            this.end = to;
        }

        boolean next() {
            if (p >= end) return false;
            long key = varint();
            field = (int) (key >>> 3);
            wire = (int) (key & 7);
            return true;
        }

        long varint() {
            long v = 0;
            for (int shift = 0; shift < 64; shift += 7) {
                if (p >= end) throw new IllegalStateException("truncated varint");
                int x = b[p++] & 0xff;
                v |= (long) (x & 0x7f) << shift;
                if ((x & 0x80) == 0) return v;
            }
            throw new IllegalStateException("bad varint");
        }

        private int length() {
            long n = varint();
            if (n < 0 || n > end - p) throw new IllegalStateException("bad length");
            return (int) n;
        }

        String string() {
            int n = length();
            String s = new String(b, p, n, StandardCharsets.UTF_8);
            p += n;
            return s;
        }

        Reader message() {
            int n = length();
            Reader m = new Reader(b, p, p + n);
            p += n;
            return m;
        }

        void skip() {
            switch (wire) {
                case 0: varint(); break;
                case 1: p += 8; break;
                case 2: {
                    int n = length();       // first: p += length() would add to p as it was before the length's bytes
                    p += n;
                    break;
                }
                case 5: p += 4; break;
                default: throw new IllegalStateException("wire type " + wire);
            }
            if (p > end) throw new IllegalStateException("truncated field");
        }
    }
}
