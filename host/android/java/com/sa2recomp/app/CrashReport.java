package com.sa2recomp.app;

import android.app.ActivityManager;
import android.app.ApplicationExitInfo;
import android.content.Context;
import android.content.pm.FeatureInfo;
import android.content.pm.PackageInfo;
import android.opengl.EGL14;
import android.opengl.EGLConfig;
import android.opengl.EGLContext;
import android.opengl.EGLDisplay;
import android.opengl.EGLSurface;
import android.opengl.GLES20;
import android.os.Build;
import android.util.JsonReader;
import android.util.JsonToken;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.RandomAccessFile;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.HashMap;
import java.util.Iterator;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.TimeUnit;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * The text a player copies from the setup screen and pastes into a bug report after a session goes
 * wrong, built from what the app can read after the fact:
 *
 *  - the app's version, the device, its SoC, its GPU and Vulkan driver (the player's d3d9gpu.runrec,
 *    sdl_gpu; GL_RENDERER from a throwaway EGL context when the player never got that far), and the
 *    Vulkan version the system declares (the renderer needs 1.1; below 1.3 it runs its Vulkan 1.1
 *    profile, which the "gpu device:" line names from the player's record: VulkanSupport);
 *  - how the session ended (Outcome, read from last-session.txt's "ended:" line: the game's own exit
 *    status counts, and the player's exit status alone does not, since the interactive player keeps
 *    its verifier codes and a clean Back still reads "the player exited 4");
 *  - the trace: the game's trap from game.out (status, guest address, note, guest stack words, the
 *    functions dispatched last and the last imports; the runtime writes these when its main call
 *    returns), the runtime's own fault lines from game.log, the renderer's failure lines from
 *    player.log and d3d9gpu.runrec, the game's last ticks (ticks.tsv: game mode and menu), and the
 *    system's record of how the play process ended, with the crashing thread's backtrace from its
 *    tombstone when it died of a signal (API 31);
 *  - last-session.txt, the logs' tails, and this app's warnings from logcat.
 *
 * The trace comes before everything else, so a report cut to a comment box's limit keeps it: the
 * summary is the report cut to SUMMARY_CHARS (forum comments take 10,000), and the full report
 * (Copy report, GitHub, Share) is cut to FULL_CHARS. Paths into the app's folders are shortened to <app>, <ext> and <apk>, and other
 * storage paths and content URIs are removed.
 */
final class CrashReport {
    static final int FULL_CHARS = 24000, SUMMARY_CHARS = 9500;
    private static final int TAIL_GAME = 40, TAIL_PLAYER = 30, TAIL_LOGCAT = 40, FRAMES = 24;
    private static final Pattern ENDED = Pattern.compile(
            "^ended: (.*); the game stopped by (\\S+).*\\(status (-?\\d+)\\); the player exited (-?\\d+)");
    private static final Pattern GAME_EXIT = Pattern.compile("the game ended \\(exit (-?\\d+)\\)");
    private static final Pattern PLAYER_FAILURE = Pattern.compile(
            "^(d3d9gpu: |device: |stream: |replay: |host failure|verdict: |  failure: |SDL_|sa2_player: )");
    /** The file Sa2Activity writes when Back asks the session to end ("<pid> <epoch ms>"): the session
     * may not live to write its "ended:" line, and it rewrites last-session.txt whole at every line, so
     * the note is kept apart. */
    static final String END_ASKED_FILE = "end-requested";
    /** How long after Back a play process may go and still be the player's own close (a third Back
     * finishes the activity at once): the session's own SIGTERM grace (kStopGrace, 10 s, in
     * host/android/native/sa2_android_session.cpp) and a second for the teardown. A process that
     * outlives it was waiting on a game the session had to kill. */
    private static final long QUICK_CLOSE_MS = 11_000;
    private static final Pattern DEVICE_DESCRIPTION = Pattern.compile("^device: \\d+x\\d+ fmt ");
    private static final Pattern GENERIC_DEVICE = Pattern.compile(
            "^device: (Direct3DCreate9 returned NULL|CreateDevice failed)");
    private static final Pattern GPU_FAILED = Pattern.compile(
            "(?i)(fail|cannot|could not|refused|no vulkan|not supported|lost|poison)");

    private CrashReport() {}

    // ------------------------------------------------------------ how the session ended

    /** What last-session.txt says about the end. A session that never wrote an "ended:" line either
     * failed before the game started (a FAIL line says why) or lost its play process. */
    static final class Outcome {
        final boolean clean;
        final String what;
        final int gameExit;        // the game's status (negative: killed by that signal), or 0
        final boolean playerStopped, gameHung, processLost;

        private Outcome(boolean clean, String what, int gameExit, boolean playerStopped, boolean gameHung,
                        boolean processLost) {
            this.clean = clean;
            this.what = what;
            this.gameExit = gameExit;
            this.playerStopped = playerStopped;
            this.gameHung = gameHung;
            this.processLost = processLost;
        }

        static Outcome of(String last) {
            if (last == null) return new Outcome(true, "no session has run yet", 0, false, false, false);
            String preFail = null;
            boolean stream = false, ok = false;
            boolean noFrames = last.startsWith("FAIL the game presented nothing")
                               || last.contains("\nFAIL the game presented nothing");
            for (String l : last.split("\n")) {
                Matcher m = ENDED.matcher(l);
                if (m.find()) {
                    String endedBy = m.group(1), stopHow = m.group(2);
                    int status = Integer.parseInt(m.group(3));
                    Matcher g = GAME_EXIT.matcher(endedBy);
                    if (g.find()) {
                        int code = Integer.parseInt(g.group(1));
                        if (code != 0) return new Outcome(false, gameExitText(code), code, false, false, false);
                        if (noFrames) return new Outcome(false, "the game showed no picture and quit", 0, false, false, false);
                        return new Outcome(true, "the game quit by itself", 0, false, false, false);
                    }
                    if (stopHow.equals("SIGKILL")) {
                        return new Outcome(false, "the game stopped responding and had to be killed ("
                                           + endedBy + ")", status, false, true, false);
                    }
                    // A game that answers the session's SIGTERM quits with 0 (the runtime makes it WM_QUIT);
                    // any other status is the game's own failure, even when the renderer ended first.
                    if (status != 0 && status != -15) {
                        return new Outcome(false, gameExitText(status), status, false, false, false);
                    }
                    if (endedBy.startsWith("the player asked")) {
                        if (noFrames) return new Outcome(false, "the game showed no picture (ended with Back)", status,
                                                         false, false, false);
                        return new Outcome(true, "ended with Back", status, false, false, false);
                    }
                    return new Outcome(false, "the renderer stopped by itself", status, true, false, false);
                }
                if (l.startsWith("mode stream")) stream = true;
                if (l.startsWith("OK ")) ok = true;
                if (l.startsWith("FAIL ") && preFail == null) preFail = l.substring(5);
            }
            // The stream mode (a recorded stream through the player, no game) writes no "ended:" line.
            if (stream) {
                if (ok) return new Outcome(true, "the stream played", 0, false, false, false);
                if (preFail != null) return new Outcome(false, "the renderer stopped while playing the stream: " + preFail,
                                                        0, true, false, false);
            }
            if (preFail != null) return new Outcome(false, "the game could not start: " + preFail, 0, false, false, false);
            return new Outcome(false, "the game's process stopped before it could say why", 0, false, false, true);
        }

        /** of(), with a lost play process (no "ended:" line) told apart by the system's record of how it
         * ended (API 30) and Back's note (END_ASKED_FILE). A crash, an ANR or a low-memory kill is named,
         * whenever it came. Back asked for the end and the process then closed on purpose within
         * QUICK_CLOSE_MS: the player's own close (a third Back), no crash. Back asked and the process
         * outlived that: the session was stuck stopping a game that no longer answered, which is what a
         * frozen or black screen looks like. Closed without Back (Recents, the system): still offered as a
         * report, since a player closes a frozen game too. With no record yet (the setup screen resumes
         * while the play process is still going away; Sa2SetupActivity waits for it) or none at all (API
         * 29), Back's request stands: ended with Back. */
        static Outcome of(Context c, String last) {
            Outcome o = of(last);
            if (!o.processLost) return o;
            File session = sessionDir(last);
            int pid = sessionPid(session);
            long asked = endAsked(c, pid, session);
            ApplicationExitInfo e = Build.VERSION.SDK_INT >= 30 ? playExitInfo(c, pid) : null;
            if (e != null && asked > 0 && e.getTimestamp() < asked) asked = 0;    // the record is older than Back
            int r = e == null ? -1 : e.getReason();
            String d = e == null || e.getDescription() == null ? "" : e.getDescription();
            if (r == ApplicationExitInfo.REASON_CRASH_NATIVE) {
                return new Outcome(false, "the play process crashed (" + signalName(e.getStatus()) + ")", 0, false,
                                   false, true);
            }
            if (r == ApplicationExitInfo.REASON_CRASH) {
                return new Outcome(false, "the play process crashed (" + (d.isEmpty() ? "Java exception" : d) + ")", 0,
                                   false, false, true);
            }
            if (r == ApplicationExitInfo.REASON_ANR) {
                return new Outcome(false, "the play process stopped responding (ANR)", 0, false, true, true);
            }
            if (r == ApplicationExitInfo.REASON_LOW_MEMORY) {
                return new Outcome(false, "Android closed the game for low memory", 0, false, false, true);
            }
            boolean closed = (r == ApplicationExitInfo.REASON_EXIT_SELF && e.getStatus() == 0)
                             || r == ApplicationExitInfo.REASON_USER_REQUESTED || r == ApplicationExitInfo.REASON_USER_STOPPED
                             || (r == ApplicationExitInfo.REASON_OTHER && d.contains("remove task"));
            if (asked > 0) {
                if (e == null || (closed && e.getTimestamp() - asked <= QUICK_CLOSE_MS)) {
                    return new Outcome(true, "ended with Back", 0, false, false, false);
                }
                return new Outcome(false, "the game stopped responding, and the session could not stop it after "
                                   + "Back", 0, false, true, false);
            }
            if (closed) return new Outcome(false, "the app was closed while the game was running", 0, false, false, true);
            return o;
        }

        /** When Back asked this session (its play process's pid) to end, in epoch ms; 0 when it did not,
         * or when the note predates the session (an older process with the same pid). */
        private static long endAsked(Context c, int pid, File session) {
            String t = Sa2SetupActivity.readText(new File(c.getFilesDir(), END_ASKED_FILE));
            if (t == null || pid <= 0) return 0;
            String[] f = t.trim().split("\\s+");
            try {
                long at = f.length == 2 && Integer.parseInt(f[0]) == pid ? Long.parseLong(f[1]) : 0;
                return at > 0 && at >= sessionStart(session) ? at : 0;
            } catch (NumberFormatException x) {
                return 0;
            }
        }

        /** The session's start, from its folder's local-time stamp (<yyyyMMdd-HHmmss>-<pid>), less a
         * minute's slack; 0 when it cannot be read. */
        private static long sessionStart(File session) {
            if (session == null || session.getName().length() < 15) return 0;
            try {
                Date d = new SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).parse(session.getName().substring(0, 15));
                return d == null ? 0 : d.getTime() - 60_000;
            } catch (java.text.ParseException x) {
                return 0;
            }
        }

        private static String gameExitText(int code) {
            switch (code) {
                case 1: return "the game stopped on a trap or a failed check (exit 1)";
                case 2: return "the game failed to start (exit 2)";
                case 3: return "the game crashed (exit 3, a fault outside the game's code)";
                default:
                    return code < 0 ? "the game was killed by signal " + (-code) + " (" + signalName(-code) + ")"
                                    : "the game ended with exit " + code;
            }
        }
    }

    static boolean abnormal(Context c, String last) {
        return !Outcome.of(c, last).clean;
    }

    /** The one line the setup screen shows: the outcome, sharpened by the trap or renderer failure
     * when the session's files name one. Reads files: call it off the UI thread when they may be
     * large. */
    static String headline(Context c, String last) {
        Redactor r = new Redactor(c);
        Outcome o = Outcome.of(c, last);
        File session = sessionDir(last);
        if (session != null && o.gameExit != 0) {
            Map<String, Object> g = jsonKeys(new File(session, "game.out"), "trap");
            JSONObject trap = (JSONObject) g.get("trap");
            if (failed(trap)) return r.apply(o.what + ": " + trapLine(trap));
        }
        if (session != null && o.playerStopped) {
            String why = firstPlayerFailure(session);
            if (why != null) return r.apply(o.what + ": " + why);
        }
        if (session != null && !o.clean) {
            String why = firstThreadTrap(session);
            if (why != null) return r.apply(o.what + ": " + why);
        }
        return r.apply(o.what);
    }

    /** The first guest thread that ended on a trap, as the runtime says it in game.log ("sa2 trap:
     * guest thread N ..."): the cause of a game left waiting on what that thread held. */
    private static String firstThreadTrap(File session) {
        List<String> game = tail(new File(session, "game.log"), 4000);
        if (game == null) return null;
        for (String l : game) if (l.startsWith("sa2 trap: ")) return l.substring("sa2 trap: ".length());
        return null;
    }

    // ------------------------------------------------------------ the report

    /** Builds the report, cut to maxChars. Reads files, runs logcat and asks EGL for the GPU: call it
     * off the UI thread. setupLog is the setup screen's own log, kept when it is the installer's output
     * rather than the last session's; installVerdict, when the installer refused the game, makes the
     * report about that. */
    static String build(Context c, String setupLog, int maxChars, String installVerdict) {
        boolean small = maxChars < FULL_CHARS;
        Redactor r = new Redactor(c);
        File files = c.getFilesDir();
        String last = Sa2SetupActivity.readText(new File(files, "last-session.txt"));
        File session = sessionDir(last);
        Outcome o = Outcome.of(c, last);

        Map<String, Object> gpuRec = session == null ? new HashMap<>()
                : jsonKeys(new File(session, "d3d9gpu.runrec"), "sdl_gpu", "failure", "fault", "fault_error",
                           "present", "draws");
        Map<String, Object> gameOut = session == null ? new HashMap<>()
                : jsonKeys(new File(session, "game.out"), "trap", "recent_dispatch", "recent_imports", "threads");
        List<String> gameLog = session == null ? null : tail(new File(session, "game.log"), 4000);
        List<String> playerLog = session == null ? null : tail(new File(session, "player.log"), 4000);

        StringBuilder out = new StringBuilder(8192);
        out.append(installVerdict != null ? "OpenSA2 install report" : "OpenSA2 crash report").append('\n');
        out.append("app: ").append(appVersion(c)).append('\n');
        out.append("device: ").append(Build.MANUFACTURER).append(' ').append(Build.MODEL)
           .append(" (").append(Build.DEVICE).append(", ").append(Build.HARDWARE).append(")\n");
        if (Build.VERSION.SDK_INT >= 31) {
            out.append("soc: ").append(Build.SOC_MANUFACTURER).append(' ').append(Build.SOC_MODEL).append('\n');
        }
        out.append("android: ").append(Build.VERSION.RELEASE).append(" (API ").append(Build.VERSION.SDK_INT)
           .append(", ").append(Build.DISPLAY).append(")\n");
        out.append("gpu: ").append(gpu((JSONObject) gpuRec.get("sdl_gpu"))).append('\n');
        out.append("vulkan: ").append(vulkanText(c)).append('\n');
        out.append("memory: ").append(memory(c)).append('\n');
        if (installVerdict != null) {
            // The installer refused the game: its account is the report, and any session is an older one.
            out.append("install: ").append(r.apply(installVerdict)).append('\n');
            section(out, "installer output");
            out.append(r.apply(r.setupLog(clip(setupLog, small ? 60 : 200)))).append('\n');
            if (last != null && !small) {
                section(out, "the previous game session");
                out.append(r.apply(foldPerf(last))).append('\n');
            }
            return cap(out, maxChars);
        }
        if (last != null) {
            out.append("session: ").append(session == null ? "(its folder is gone)" : session.getName()).append('\n');
        }
        out.append("outcome: ").append(r.apply(o.what)).append('\n');

        section(out, "trace");
        int before = out.length();
        trap(out, gameOut, r);
        runtimeLines(out, gameLog, r);
        renderer(out, playerLog, gpuRec, r);
        ticks(out, session);
        String exit = playExit(c, sessionPid(session), r);
        if (exit != null) out.append(exit);
        boolean tombstoned = exit != null && exit.contains("crashing thread:");
        if (out.length() == before) out.append("(no trap, fault or renderer failure was recorded)\n");

        if (last != null) {
            section(out, "last session");
            out.append(r.apply(foldPerf(last))).append('\n');
        }
        if (setupLog != null && !setupLog.trim().isEmpty() && !setupLog.startsWith("The last session:")) {
            section(out, "setup");
            out.append(r.apply(r.setupLog(clip(setupLog.trim(), 120)))).append('\n');
        }
        appendTail(out, "game.log", gameLog, TAIL_GAME, r);
        appendTail(out, "player.log", playerLog, TAIL_PLAYER, r);
        String log = logcat(tombstoned);
        if (log != null && !log.isEmpty()) {
            section(out, "logcat (this app's warnings)");
            out.append(r.apply(log));
        }
        return cap(out, maxChars);
    }

    /** The text's first lines and last lines, at most max in all, with a note of what was left out. */
    private static String clip(String text, int max) {
        if (text == null) return "";
        String[] lines = text.split("\n");
        if (lines.length <= max) return text;
        int head = max / 2, tail = max - head;
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < head; i++) b.append(lines[i]).append('\n');
        b.append("[").append(lines.length - max).append(" lines left out]\n");
        for (int i = lines.length - tail; i < lines.length; i++) b.append(lines[i]).append('\n');
        return b.toString();
    }

    private static String cap(StringBuilder out, int max) {
        if (out.length() > max) {
            out.setLength(max);
            out.append("\n[cut at ").append(max).append(" characters]\n");
        }
        return out.toString();
    }

    // ------------------------------------------------------------ the trace's parts

    /** A trap that is a failure: the runtime also ends the guest's main call as a trap when the game
     * calls ExitProcess itself, and a clean quit is ExitProcess(0). */
    private static boolean failed(JSONObject trap) {
        return trap != null && trap.optInt("status") != 0 && !trap.optString("note").contains("called ExitProcess(0)");
    }

    private static String trapLine(JSONObject trap) {
        String status;
        switch (trap.optInt("status")) {
            case 1: status = "control left the lifted code"; break;
            case 2: status = "guest memory fault"; break;
            case 3: status = "not runnable"; break;
            case 4: status = "timeout"; break;
            case 5: status = "host fault"; break;
            default: status = "status " + trap.optInt("status");
        }
        return status + " at " + trap.optString("exit_pc", "?") + ": " + trap.optString("note", "");
    }

    /** The main thread's trap, the functions dispatched last, the last imports, and every other
     * guest thread that ended on a trap (game.out). */
    private static void trap(StringBuilder out, Map<String, Object> g, Redactor r) {
        JSONObject trap = (JSONObject) g.get("trap");
        if (failed(trap)) {
            out.append("game trap: ").append(r.apply(trapLine(trap))).append('\n');
            JSONArray stack = trap.optJSONArray("stack");
            if (stack != null) {
                out.append("guest stack at ").append(trap.optString("esp", "?")).append(':');
                for (int i = 0; i < stack.length(); i++) out.append(' ').append(stack.optString(i));
                out.append('\n');
            }
            JSONObject recent = (JSONObject) g.get("recent_dispatch");
            JSONArray newest = recent == null ? null : recent.optJSONArray("newest_first");
            if (newest != null) {
                out.append("dispatched last (newest first):");
                for (int i = 0; i < Math.min(newest.length(), 16); i++) out.append(' ').append(newest.optString(i));
                out.append('\n');
            }
            JSONArray imports = (JSONArray) g.get("recent_imports");
            if (imports != null) {
                out.append("last imports (newest last):\n");
                for (int i = Math.max(0, imports.length() - 6); i < imports.length(); i++) {
                    JSONObject im = imports.optJSONObject(i);
                    if (im == null) continue;
                    out.append("  ").append(im.optString("dll")).append('!').append(im.optString("name"))
                       .append(" from ").append(im.optString("caller")).append(" (thread ")
                       .append(im.optInt("thread")).append(")\n");
                }
            }
        }
        JSONObject threads = (JSONObject) g.get("threads");
        JSONArray rows = threads == null ? null : threads.optJSONArray("rows");
        if (rows != null) {
            int shown = 0;
            for (int i = 0; i < rows.length() && shown < 8; i++) {
                JSONObject t = rows.optJSONObject(i);
                if (t == null || t.optInt("trap_status") == 0 || t.optString("trap_note").contains("called ExitThread(0)")) continue;
                shown++;
                out.append("thread ").append(t.optInt("index")).append(" (start ").append(t.optString("start_va"))
                   .append(", ").append(t.optString("guest_name")).append(") trapped at ")
                   .append(t.optString("trap_pc")).append(": ").append(r.apply(t.optString("trap_note"))).append('\n');
            }
        }
    }

    /** The runtime's fault report (sa2_trap.c: "sa2 runtime: ..." and its " callers" line). */
    private static void runtimeLines(StringBuilder out, List<String> game, Redactor r) {
        if (game == null) return;
        int n = 0;
        for (String l : game) {
            if (l.startsWith("sa2 runtime:") || l.startsWith(" callers") || l.startsWith("sa2 trap:")) {
                out.append("game: ").append(r.apply(l)).append('\n');
                if (++n >= 12) break;
            }
        }
    }

    /** The renderer: its failure lines (repeats collapsed), then what its report says about the GPU's
     * device, presents and refused draws. */
    private static void renderer(StringBuilder out, List<String> player, Map<String, Object> gpuRec, Redactor r) {
        if (player != null) {
            Map<String, Integer> seen = new HashMap<>();
            List<String> order = new ArrayList<>();
            for (String l : player) {
                if (!PLAYER_FAILURE.matcher(l).find() || DEVICE_DESCRIPTION.matcher(l).find()
                    || l.startsWith("verdict: PASS")) continue;
                String key = l.replaceAll("\\d+", "#");
                Integer n = seen.get(key);
                if (n == null) {
                    seen.put(key, 1);
                    order.add(l);
                } else {
                    seen.put(key, n + 1);
                }
            }
            int shown = 0;
            for (String l : order) {
                if (shown++ >= 20) break;
                int n = seen.get(l.replaceAll("\\d+", "#"));
                out.append("renderer: ").append(r.apply(l)).append(n > 1 ? "  [x" + n + "]" : "").append('\n');
            }
        }
        Object failure = gpuRec.get("failure");
        if (failure instanceof String && !((String) failure).isEmpty()) {
            out.append("gpu failure: ").append(r.apply((String) failure)).append('\n');
        }
        Object fault = gpuRec.get("fault");
        if (fault instanceof String && !((String) fault).isEmpty()) {
            out.append("gpu fault: ").append(r.apply((String) fault)).append(' ')
               .append(r.apply(String.valueOf(gpuRec.get("fault_error")))).append('\n');
        }
        JSONObject g = (JSONObject) gpuRec.get("sdl_gpu");
        if (g != null) {
            out.append("gpu device: lacking ").append(g.isNull("vulkan_lacking") ? "nothing" : g.optString("vulkan_lacking", "nothing"))
               .append("; depth clip and clamp ").append(g.optBoolean("depth_clip_and_clamp"))
               .append("; clip distance ").append(g.optBoolean("clip_distance"))
               .append(VulkanSupport.profileText(g)).append('\n');
        }
        JSONObject present = (JSONObject) gpuRec.get("present");
        JSONObject draws = (JSONObject) gpuRec.get("draws");
        if (present != null || draws != null) {
            out.append("gpu frames:");
            if (present != null) {
                out.append(" presented ").append(present.optLong("presented")).append(" of ")
                   .append(present.optLong("presents")).append(", present failures ").append(present.optLong("failures"));
            }
            if (draws != null) {
                out.append("; draws ").append(draws.optLong("drawn")).append(", refused ")
                   .append(draws.optLong("unmapped_count")).append(", failed ").append(draws.optLong("backend_failed_count"));
                appendReasons(out, draws.optJSONObject("unmapped"), "refused");
                appendReasons(out, draws.optJSONObject("backend_failed"), "failed");
            }
            out.append('\n');
        }
    }

    private static void appendReasons(StringBuilder out, JSONObject reasons, String what) {
        if (reasons == null || reasons.length() == 0) return;
        out.append(" (").append(what).append(':');
        int n = 0;
        for (Iterator<String> it = reasons.keys(); it.hasNext() && n < 4; n++) {
            String k = it.next();
            out.append(' ').append(k).append('=').append(reasons.opt(k));
        }
        out.append(')');
    }

    /** Why the renderer stopped, best first: its device failure ("device: " with a reason, not the
     * device's description "device: WxH fmt ..."), a d3d9gpu line that says something failed, then a
     * verdict failure other than the stream's incompleteness (which every early stop adds), then the
     * GPU report's failure field. */
    private static String firstPlayerFailure(File session) {
        List<String> player = tail(new File(session, "player.log"), 4000);
        Map<String, Object> g = jsonKeys(new File(session, "d3d9gpu.runrec"), "failure");
        String gpuFailure = g.get("failure") instanceof String && !((String) g.get("failure")).isEmpty()
                ? (String) g.get("failure") : null;
        if (player != null) {
            String lastGpu = null;
            for (String l : player) {
                if (l.startsWith("d3d9gpu: ") && !l.contains("texture format/usage is unsupported")) lastGpu = l;
                if (l.startsWith("device: ") && !DEVICE_DESCRIPTION.matcher(l).find()) {
                    // "Direct3DCreate9 returned NULL" and "CreateDevice failed: 0x..." say only that it failed:
                    // the reason is the GPU report's, or the d3d9gpu line just before.
                    if (GENERIC_DEVICE.matcher(l).find()) {
                        String why = gpuFailure != null ? gpuFailure : lastGpu;
                        return why == null ? l : l + " (" + why + ")";
                    }
                    return l;
                }
            }
            for (String l : player) {
                if (l.startsWith("d3d9gpu: ") && GPU_FAILED.matcher(l).find()) return l;
            }
            for (String l : player) {
                if (l.startsWith("  failure: ") && !l.contains("live stream incomplete")) return l.trim();
            }
        }
        return gpuFailure;
    }

    /** The game's last ticks (the launch observer's ticks.tsv: tick, presents, game mode, menu), so a
     * hang can be told from a crash and placed in the game. */
    private static void ticks(StringBuilder out, File session) {
        if (session == null) return;
        List<String> rows = tail(new File(session, "ticks.tsv"), 4);
        if (rows == null || rows.isEmpty()) return;
        out.append("last ticks (tick presents game_mode menu):");
        for (String row : rows) {
            if (row.startsWith("T\t") && !row.startsWith("T\ttick")) out.append(" [").append(row.substring(2).replace('\t', ' ')).append(']');
        }
        out.append('\n');
    }

    // ------------------------------------------------------------ the device

    @SuppressWarnings("deprecation")
    static String appVersion(Context c) {
        try {
            PackageInfo p = c.getPackageManager().getPackageInfo(c.getPackageName(), 0);
            long code = Build.VERSION.SDK_INT >= 28 ? p.getLongVersionCode() : p.versionCode;
            return p.versionName + " (" + code + ")";
        } catch (Exception e) {
            return "unknown";
        }
    }

    /** The Vulkan device and driver the player opened (d3d9gpu.runrec); GL_RENDERER and GL_VERSION
     * from a 1x1 pbuffer context when it opened none. */
    private static String gpu(JSONObject sdlGpu) {
        if (sdlGpu != null && !sdlGpu.optString("device_name").isEmpty()) {
            String info = sdlGpu.isNull("driver_info") ? "" : sdlGpu.optString("driver_info");
            return sdlGpu.optString("device_name") + ", Vulkan driver " + sdlGpu.optString("driver_version")
                   + (info.isEmpty() ? "" : " (" + info + ")");
        }
        EGLDisplay d = EGL14.eglGetDisplay(EGL14.EGL_DEFAULT_DISPLAY);
        if (d == EGL14.EGL_NO_DISPLAY) return "unknown (no EGL display)";
        EGLContext ctx = EGL14.EGL_NO_CONTEXT;
        EGLSurface surf = EGL14.EGL_NO_SURFACE;
        try {
            int[] ver = new int[2];
            if (!EGL14.eglInitialize(d, ver, 0, ver, 1)) return "unknown (EGL did not start)";
            int[] want = {EGL14.EGL_RENDERABLE_TYPE, EGL14.EGL_OPENGL_ES2_BIT,
                          EGL14.EGL_SURFACE_TYPE, EGL14.EGL_PBUFFER_BIT, EGL14.EGL_NONE};
            EGLConfig[] cfg = new EGLConfig[1];
            int[] n = new int[1];
            if (!EGL14.eglChooseConfig(d, want, 0, cfg, 0, 1, n, 0) || n[0] < 1) return "unknown (no EGL config)";
            ctx = EGL14.eglCreateContext(d, cfg[0], EGL14.EGL_NO_CONTEXT,
                                         new int[] {EGL14.EGL_CONTEXT_CLIENT_VERSION, 2, EGL14.EGL_NONE}, 0);
            surf = EGL14.eglCreatePbufferSurface(d, cfg[0], new int[] {EGL14.EGL_WIDTH, 1, EGL14.EGL_HEIGHT, 1,
                                                 EGL14.EGL_NONE}, 0);
            if (ctx == EGL14.EGL_NO_CONTEXT || surf == EGL14.EGL_NO_SURFACE
                || !EGL14.eglMakeCurrent(d, surf, surf, ctx)) return "unknown (no GL context)";
            return GLES20.glGetString(GLES20.GL_RENDERER) + " (GL: " + GLES20.glGetString(GLES20.GL_VERSION) + ")";
        } catch (RuntimeException e) {
            return "unknown (" + e + ")";
        } finally {
            EGL14.eglMakeCurrent(d, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_CONTEXT);
            if (surf != EGL14.EGL_NO_SURFACE) EGL14.eglDestroySurface(d, surf);
            if (ctx != EGL14.EGL_NO_CONTEXT) EGL14.eglDestroyContext(d, ctx);
            EGL14.eglTerminate(d);
        }
    }

    /** The Vulkan version the system declares (android.hardware.vulkan.version, encoded as
     * VK_MAKE_VERSION), or -1 when it declares none. */
    static int vulkanVersion(Context c) {
        FeatureInfo[] fs = c.getPackageManager().getSystemAvailableFeatures();
        if (fs == null) return -1;
        for (FeatureInfo f : fs) {
            if ("android.hardware.vulkan.version".equals(f.name)) return f.version;
        }
        return -1;
    }

    static String vulkanShort(Context c) {
        return VulkanSupport.shortText(vulkanVersion(c));
    }

    private static String vulkanText(Context c) {
        int level = -1;
        FeatureInfo[] fs = c.getPackageManager().getSystemAvailableFeatures();
        if (fs != null) {
            for (FeatureInfo f : fs) if ("android.hardware.vulkan.level".equals(f.name)) level = f.version;
        }
        return VulkanSupport.declaredText(vulkanVersion(c), level);
    }

    private static String memory(Context c) {
        ActivityManager am = (ActivityManager) c.getSystemService(Context.ACTIVITY_SERVICE);
        if (am == null) return "unknown";
        ActivityManager.MemoryInfo mi = new ActivityManager.MemoryInfo();
        am.getMemoryInfo(mi);
        return String.format(Locale.US, "%.1f GB total, %.1f GB free", mi.totalMem / 1e9, mi.availMem / 1e9);
    }

    // ------------------------------------------------------------ the session's files

    /** The folder last-session.txt's "session DIR" line names, when it still exists. */
    static File sessionDir(String last) {
        if (last == null) return null;
        for (String l : last.split("\n")) {
            if (l.startsWith("session ")) {
                File d = new File(l.substring(8).trim());
                return d.isDirectory() ? d : null;
            }
        }
        return null;
    }

    /** The play process's pid, the last part of the session folder's name (<stamp>-<pid>). */
    private static int sessionPid(File session) {
        if (session == null) return -1;
        String n = session.getName();
        int dash = n.lastIndexOf('-');
        try {
            return dash < 0 ? -1 : Integer.parseInt(n.substring(dash + 1));
        } catch (NumberFormatException e) {
            return -1;
        }
    }

    /** The file's last lines, reading at most its last 256 KiB; null when it does not exist. */
    private static List<String> tail(File f, int maxLines) {
        if (!f.isFile()) return null;
        try (RandomAccessFile in = new RandomAccessFile(f, "r")) {
            long len = in.length();
            int n = (int) Math.min(len, 256 * 1024);
            byte[] b = new byte[n];
            in.seek(len - n);
            in.readFully(b);
            String[] lines = new String(b, StandardCharsets.UTF_8).split("\n");
            int from = Math.max(len > n ? 1 : 0, lines.length - maxLines);
            List<String> out = new ArrayList<>();
            for (int i = from; i < lines.length; i++) out.add(lines[i]);
            return out;
        } catch (Exception e) {
            return null;
        }
    }

    /** The named top-level values of a JSON object file, streamed (game.out runs to megabytes) and
     * skipping the rest; the values are JSONObject, JSONArray, String, Number, Boolean or null.
     * Whatever was read before a malformed or truncated part is kept. */
    static Map<String, Object> jsonKeys(File f, String... keys) {
        Map<String, Object> out = new HashMap<>();
        if (!f.isFile()) return out;
        Set<String> want = new LinkedHashSet<>();
        for (String k : keys) want.add(k);
        try (JsonReader in = new JsonReader(new InputStreamReader(new FileInputStream(f), StandardCharsets.UTF_8))) {
            in.setLenient(true);
            in.beginObject();
            while (in.hasNext() && out.size() < want.size()) {
                String name = in.nextName();
                if (want.contains(name)) out.put(name, value(in));
                else in.skipValue();
            }
        } catch (Exception e) {
            // A game killed while writing leaves a partial file: what was read is still worth reporting.
        }
        return out;
    }

    private static Object value(JsonReader in) throws Exception {
        JsonToken t = in.peek();
        switch (t) {
            case BEGIN_OBJECT: {
                JSONObject o = new JSONObject();
                in.beginObject();
                while (in.hasNext()) o.put(in.nextName(), value(in));
                in.endObject();
                return o;
            }
            case BEGIN_ARRAY: {
                JSONArray a = new JSONArray();
                in.beginArray();
                while (in.hasNext()) a.put(value(in));
                in.endArray();
                return a;
            }
            case STRING: return in.nextString();
            case NUMBER: {
                String s = in.nextString();
                try {
                    return Long.parseLong(s);
                } catch (NumberFormatException e) {
                    return Double.parseDouble(s);
                }
            }
            case BOOLEAN: return in.nextBoolean();
            case NULL: in.nextNull(); return JSONObject.NULL;
            default: in.skipValue(); return JSONObject.NULL;
        }
    }

    /** last-session.txt with its 30 s perf lines folded to the last one. */
    private static String foldPerf(String last) {
        String[] lines = last.trim().split("\n");
        int lastPerf = -1, perfs = 0;
        for (int i = 0; i < lines.length; i++) {
            if (lines[i].startsWith("perf ") && !lines[i].startsWith("perf over")) {
                lastPerf = i;
                perfs++;
            }
        }
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < lines.length; i++) {
            if (lines[i].startsWith("perf ") && !lines[i].startsWith("perf over") && i != lastPerf) continue;
            if (i == lastPerf && perfs > 1) b.append("[").append(perfs - 1).append(" earlier perf lines left out]\n");
            b.append(lines[i]).append('\n');
        }
        return b.toString().trim();
    }

    private static void appendTail(StringBuilder out, String name, List<String> lines, int n, Redactor r) {
        if (lines == null) return;
        section(out, name + " (last " + Math.min(n, lines.size()) + " lines)");
        for (int i = Math.max(0, lines.size() - n); i < lines.size(); i++) out.append(r.apply(lines.get(i))).append('\n');
    }

    private static void section(StringBuilder out, String title) {
        out.append("\n--- ").append(title).append(" ---\n");
    }

    // ------------------------------------------------------------ the system's records

    /** How the play process of this session ended (its pid is in the session folder's name), from the
     * system's exit records (API 30), with the crashing thread's backtrace when it died of a signal
     * (API 31's tombstone) or the stuck main thread when it stopped responding. Null on API 29, when
     * the system kept no record of that process, or when it ended normally. */
    /** The system's record of how this session's play process ended (API 30): the newest ":play"
     * record with the session's pid, or the newest ":play" record when the pid is unknown. */
    private static ApplicationExitInfo playExitInfo(Context c, int pid) {
        if (Build.VERSION.SDK_INT < 30) return null;
        ActivityManager am = (ActivityManager) c.getSystemService(Context.ACTIVITY_SERVICE);
        if (am == null) return null;
        try {
            for (ApplicationExitInfo e : am.getHistoricalProcessExitReasons(c.getPackageName(), 0, 16)) {
                String p = e.getProcessName();
                if (p != null && p.endsWith(":play") && (pid <= 0 || e.getPid() == pid)) return e;
            }
        } catch (RuntimeException e) {
            return null;
        }
        return null;
    }

    private static String playExit(Context c, int pid, Redactor r) {
        ApplicationExitInfo e = playExitInfo(c, pid);
        if (e == null) return null;
        {
            if (e.getReason() == ApplicationExitInfo.REASON_EXIT_SELF && e.getStatus() == 0) return null;
            StringBuilder b = new StringBuilder();
            b.append("play process: ").append(reason(e.getReason())).append(", status ").append(e.getStatus())
             .append(", at ").append(new SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(new Date(e.getTimestamp())));
            if (e.getDescription() != null) b.append(", ").append(r.apply(e.getDescription()));
            b.append('\n');
            try (InputStream in = e.getTraceInputStream()) {
                if (in != null && Build.VERSION.SDK_INT >= 31 && e.getReason() == ApplicationExitInfo.REASON_CRASH_NATIVE) {
                    b.append(Tombstone.describe(readAll(in), FRAMES, r));
                } else if (in != null && e.getReason() == ApplicationExitInfo.REASON_ANR) {
                    String[] lines = new String(readAll(in), StandardCharsets.UTF_8).split("\n");
                    for (int i = 0; i < Math.min(lines.length, 40); i++) b.append(r.apply(lines[i])).append('\n');
                }
            } catch (Exception x) {
                b.append("its trace is unreadable (").append(x).append(")\n");
            }
            return b.toString();
        }
    }

    private static String reason(int r) {
        switch (r) {
            case ApplicationExitInfo.REASON_EXIT_SELF: return "exited";
            case ApplicationExitInfo.REASON_SIGNALED: return "killed by a signal";
            case ApplicationExitInfo.REASON_LOW_MEMORY: return "killed for low memory";
            case ApplicationExitInfo.REASON_CRASH: return "Java crash";
            case ApplicationExitInfo.REASON_CRASH_NATIVE: return "native crash";
            case ApplicationExitInfo.REASON_ANR: return "not responding (ANR)";
            case ApplicationExitInfo.REASON_INITIALIZATION_FAILURE: return "failed to start";
            case ApplicationExitInfo.REASON_PERMISSION_CHANGE: return "permission change";
            case ApplicationExitInfo.REASON_EXCESSIVE_RESOURCE_USAGE: return "excessive resource use";
            case ApplicationExitInfo.REASON_USER_REQUESTED: return "stopped by the user";
            case ApplicationExitInfo.REASON_USER_STOPPED: return "user stopped";
            case ApplicationExitInfo.REASON_DEPENDENCY_DIED: return "dependency died";
            case ApplicationExitInfo.REASON_OTHER: return "other";
            default: return "reason " + r;
        }
    }

    static String signalName(int s) {
        switch (s) {
            case 4: return "SIGILL";
            case 5: return "SIGTRAP";
            case 6: return "SIGABRT";
            case 7: return "SIGBUS";
            case 8: return "SIGFPE";
            case 9: return "SIGKILL";
            case 11: return "SIGSEGV";
            case 13: return "SIGPIPE";
            case 15: return "SIGTERM";
            case 31: return "SIGSYS";
            default: return "signal " + s;
        }
    }

    private static byte[] readAll(InputStream in) throws java.io.IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        byte[] buf = new byte[1 << 16];
        int n;
        while ((n = in.read(buf)) > 0 && b.size() < (8 << 20)) b.write(buf, 0, n);
        return b.toByteArray();
    }

    /** This app's own warnings and errors from logcat's main and crash buffers (an app reads only its
     * uid's lines), the last TAIL_LOGCAT; null when logcat cannot run. dropCrashDump leaves out
     * crash_dump's lines (tag DEBUG) when the trace already holds the tombstone they repeat; otherwise
     * (API 29-30, the game process's own crash) they are the only backtrace. */
    private static String logcat(boolean dropCrashDump) {
        Process p = null;
        try {
            p = new ProcessBuilder("logcat", "-d", "-b", "main,crash", "-v", "time", "-t", "2000", "*:W")
                    .redirectErrorStream(true).start();
            List<String> lines = new ArrayList<>();
            try (BufferedReader in = new BufferedReader(new InputStreamReader(p.getInputStream(), StandardCharsets.UTF_8))) {
                String l;
                while ((l = in.readLine()) != null) {
                    if (dropCrashDump && l.contains("/DEBUG")) continue;
                    lines.add(l);
                    if (lines.size() > TAIL_LOGCAT) lines.remove(0);
                }
            }
            if (!p.waitFor(5, TimeUnit.SECONDS)) return null;
            StringBuilder b = new StringBuilder();
            for (String l : lines) b.append(l).append('\n');
            return b.toString();
        } catch (Exception e) {
            return null;
        } finally {
            if (p != null) p.destroy();
        }
    }

    // ------------------------------------------------------------ privacy

    /** Shortens the app's folders, which carry the package's random install path, to fixed names
     * (first, so their readable tails stay), then replaces any other shared-storage or removable-
     * storage path with <storage> up to the next colon, quote or line end (its folder and file names
     * are the player's and may hold spaces), and content URIs with <uri>; setupLog also hides the
     * folder names a ZIP's duplicate-copy refusal quotes. */
    static final class Redactor {
        private static final Pattern STORAGE = Pattern.compile(
                "(/storage/(emulated/\\d+|self/primary|[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4})|/sdcard|/mnt/(media_rw|user/\\d+|expand)"
                + "|/data/media/\\d+)(/[^:\\n\"']*)?");
        private static final Pattern OTHER_USER = Pattern.compile("/data/user/\\d+/");
        private static final Pattern COPIES = Pattern.compile("(?m)(holds the game more than once, under )(.*?)(; make an archive)");
        private final String[][] pairs;

        Redactor(Context c) {
            File data = c.getFilesDir().getParentFile();
            File ext = c.getExternalFilesDir(null);
            String lib = c.getApplicationInfo().nativeLibraryDir;
            String apk = c.getApplicationInfo().sourceDir;
            List<String[]> p = new ArrayList<>();
            if (lib != null) p.add(new String[] {lib, "<apk>/lib"});
            if (apk != null && new File(apk).getParent() != null) p.add(new String[] {new File(apk).getParent(), "<apk>"});
            if (ext != null && ext.getParentFile() != null) p.add(new String[] {ext.getParentFile().getAbsolutePath(), "<ext>"});
            if (data != null) p.add(new String[] {data.getAbsolutePath(), "<app>"});
            p.add(new String[] {"/data/data/" + c.getPackageName(), "<app>"});
            p.add(new String[] {"/storage/emulated/0/Android/data/" + c.getPackageName(), "<ext>"});
            p.add(new String[] {"/sdcard/Android/data/" + c.getPackageName(), "<ext>"});
            pairs = p.toArray(new String[0][]);
        }

        String apply(String s) {
            if (s == null) return "";
            for (String[] p : pairs) s = s.replace(p[0], p[1]);
            s = s.replaceAll("content://\\S+", "<uri>");
            s = STORAGE.matcher(s).replaceAll("<storage>");
            return OTHER_USER.matcher(s).replaceAll("/data/user/<n>/");
        }

        String setupLog(String s) {
            Matcher m = COPIES.matcher(s);
            StringBuffer b = new StringBuffer();
            while (m.find()) m.appendReplacement(b, Matcher.quoteReplacement(m.group(1)
                    + m.group(2).replaceAll("\"[^\"]*\"", "<folder>") + m.group(3)));
            m.appendTail(b);
            return b.toString();
        }
    }
}
