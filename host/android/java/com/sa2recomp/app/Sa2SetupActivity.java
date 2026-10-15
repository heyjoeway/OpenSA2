package com.sa2recomp.app;

import android.app.Activity;
import android.content.Intent;
import android.database.Cursor;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Bundle;
import android.provider.OpenableColumns;
import android.util.Log;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.Map;

/**
 * The app's first screen. With no installation it asks for a ZIP of the game folder, picked with
 * the system file picker, copied into the app's cache and verified and extracted by the installer
 * (lib/arm64-v8a/libsa2install.so, host/install's ZIP route: every file the game reads hashed
 * against the install table) into the app's private store, config and overlay; or it verifies a
 * game folder copied into the app's external files folder, game/ (the folder route). With one it runs the installer's
 * quick check (--verify) and starts the play screen (Sa2Activity). When a session ends it shows
 * what the session wrote to files/last-session.txt.
 *
 * The installer runs with the environment the native session sets (HOME = the files folder,
 * XDG_CONFIG_HOME = files/config, XDG_DATA_HOME = files/data), so both read the same config.
 *
 * Intent extras: sa2.stream (and Sa2Activity's other extras) start the play screen's stream mode
 * at once, the renderer's check with no game; sa2.no_autoplay keeps this screen up.
 */
public class Sa2SetupActivity extends Activity {
    private static final String TAG = "sa2recomp";
    public static final String EXTRA_NO_AUTOPLAY = "sa2.no_autoplay";
    private static final String GAME_EXE = "sonic2app.exe";
    private static final int PICK_ZIP = 1;

    private TextView mStatus, mLog;
    private Button mZip, mCheck, mPlay;
    private boolean mBusy, mAutoPlayDone, mInstallTried, mPlayLaunched;
    private final ArrayDeque<String> mLines = new ArrayDeque<>();

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        if (state != null) {
            mAutoPlayDone = state.getBoolean("autoplay_done");
            mInstallTried = state.getBoolean("install_tried");
        }
        buildUi();
        if (state == null) {
            importZip().delete();
            takeIntent(getIntent());
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        takeIntent(intent);
    }

    private void takeIntent(Intent it) {
        if (it == null) return;
        if (it.getBooleanExtra(EXTRA_NO_AUTOPLAY, false)) mAutoPlayDone = true;
        if (it.getStringExtra(Sa2Activity.EXTRA_STREAM) != null) {
            mAutoPlayDone = true;
            Intent play = new Intent(this, Sa2Activity.class);
            play.putExtras(it);
            launchPlay(play);
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        out.putBoolean("autoplay_done", mAutoPlayDone);
        out.putBoolean("install_tried", mInstallTried);
    }

    @Override
    protected void onResume() {
        super.onResume();
        refresh();
    }

    // ------------------------------------------------------------ paths

    private File gameDir() {
        File ext = getExternalFilesDir(null);
        return ext == null ? null : new File(ext, "game");
    }

    private File configFile() {
        return new File(getFilesDir(), "config/sa2recomp/sa2recomp.cfg");
    }

    /** Where a picked ZIP is copied for the installer, which reads a path; removed after the run. */
    private File importZip() {
        return new File(getCacheDir(), "import.zip");
    }

    private File installer() {
        return new File(getApplicationInfo().nativeLibraryDir, "libsa2install.so");
    }

    /** The folder holding sonic2app.exe: game/ itself, or a folder in it (the copied folder
     * whole), or one below that; null when there is none. */
    private File findGameRoot() {
        File g = gameDir();
        if (g == null) return null;
        File hit = holdsExe(g);
        if (hit != null) return hit;
        File[] subs = g.listFiles();
        if (subs == null) return null;
        for (File s : subs) {
            if (!s.isDirectory()) continue;
            hit = holdsExe(s);
            if (hit != null) return hit;
            File[] deeper = s.listFiles();
            if (deeper == null) continue;
            for (File d : deeper) {
                if (d.isDirectory() && holdsExe(d) != null) return d;
            }
        }
        return null;
    }

    private static File holdsExe(File dir) {
        String[] names = dir.list();
        if (names == null) return null;
        for (String n : names) {
            if (n.equalsIgnoreCase(GAME_EXE)) return dir;
        }
        return null;
    }

    private static String readText(File f) {
        if (!f.isFile()) return null;
        try (FileInputStream in = new FileInputStream(f)) {
            byte[] b = new byte[(int) Math.min(f.length(), 1 << 20)];
            int n = 0;
            while (n < b.length) {
                int r = in.read(b, n, b.length - n);
                if (r < 0) break;
                n += r;
            }
            return new String(b, 0, n, StandardCharsets.UTF_8);
        } catch (Exception e) {
            return null;
        }
    }

    private String configValue(String key) {
        String text = readText(configFile());
        if (text == null) return null;
        for (String line : text.split("\n")) {
            if (line.startsWith(key + "=")) return line.substring(key.length() + 1);
        }
        return null;
    }

    // ------------------------------------------------------------ the screen

    private void refresh() {
        if (mBusy) return;
        File g = gameDir();
        if (g != null && !g.isDirectory() && !g.mkdirs()) Log.w(TAG, "cannot create " + g);
        String last = mPlayLaunched ? readText(new File(getFilesDir(), "last-session.txt")) : null;
        mPlayLaunched = false;
        if (configFile().isFile()) {
            String root = configValue("read_root");
            mStatus.setText("Sonic Adventure 2 is installed from\n" + root + "\n\nTap Play to start.");
            mPlay.setEnabled(true);
            mZip.setText("Install again from a ZIP");
            mCheck.setText("Install again");
            if (last != null) {
                setLog("The last session:\n" + last);
            } else if (!mAutoPlayDone) {
                mAutoPlayDone = true;
                verifyThenPlay();
            }
            return;
        }
        mPlay.setEnabled(false);
        mZip.setText("Choose game ZIP");
        mCheck.setText("Check game folder");
        File root = findGameRoot();
        String where = g == null ? "(no external storage)" : g.getAbsolutePath();
        mStatus.setText(
                "First run: this app needs your own copy of Sonic Adventure 2 (the 2012 PC version, Steam).\n\n"
                + "1. On your computer, open the game's folder (Steam: right-click Sonic Adventure 2, "
                + "Manage, Browse local files) and put everything in it into a ZIP.\n"
                + "2. Copy the ZIP to this phone: USB, a cloud drive, or any other way.\n"
                + "3. Tap Choose game ZIP and pick it. Every file the game reads is checked against the "
                + "known hashes and copied into the app's own storage; the ZIP can be deleted afterwards.\n\n"
                + "Or copy the folder itself into\n"
                + "   Internal storage/Android/data/" + getPackageName() + "/files/game/\n"
                + "   (adb push \"Sonic Adventure 2/.\" " + where.replace("/storage/emulated/0", "/sdcard") + "/)\n"
                + "and tap Check game folder.\n\n"
                + (root != null ? "Found " + GAME_EXE + " in " + root.getAbsolutePath()
                                 : "No " + GAME_EXE + " under " + where + " yet."));
        if (root != null && !mInstallTried) {
            mInstallTried = true;
            install();
        }
    }

    private void setLog(String text) {
        mLines.clear();
        for (String l : text.split("\n")) mLines.add(l);
        mLog.setText(text);
    }

    private void addLine(String line) {
        mLines.add(line);
        while (mLines.size() > 60) mLines.removeFirst();
        StringBuilder b = new StringBuilder();
        for (String l : mLines) b.append(l).append('\n');
        mLog.setText(b.toString());
    }

    private void setBusy(boolean busy) {
        mBusy = busy;
        if (busy) getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        else getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        mZip.setEnabled(!busy);
        mCheck.setEnabled(!busy);
        mPlay.setEnabled(!busy && configFile().isFile());
    }

    // ------------------------------------------------------------ the installer

    private interface Done {
        void exited(int code);
    }

    private void runInstaller(final String what, final String[] args, final Done done) {
        setBusy(true);
        setLog(what + "...");
        new Thread(() -> {
            int code;
            try {
                String[] cmd = new String[args.length + 1];
                cmd[0] = installer().getAbsolutePath();
                System.arraycopy(args, 0, cmd, 1, args.length);
                ProcessBuilder pb = new ProcessBuilder(cmd);
                File files = getFilesDir();
                Map<String, String> env = pb.environment();
                env.put("HOME", files.getAbsolutePath());
                env.put("XDG_CONFIG_HOME", new File(files, "config").getAbsolutePath());
                env.put("XDG_DATA_HOME", new File(files, "data").getAbsolutePath());
                env.put("TMPDIR", getCacheDir().getAbsolutePath());
                new File(files, "logs").mkdirs();
                pb.directory(files);
                pb.redirectErrorStream(true);
                Process p = pb.start();
                try (BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream(), StandardCharsets.UTF_8))) {
                    String line;
                    while ((line = r.readLine()) != null) {
                        final String l = line;
                        Log.i(TAG, "installer: " + l);
                        runOnUiThread(() -> addLine(l));
                    }
                }
                code = p.waitFor();
            } catch (Exception e) {
                final String m = "cannot run the installer: " + e;
                Log.e(TAG, m);
                runOnUiThread(() -> addLine(m));
                code = -1;
            }
            final int c = code;
            runOnUiThread(() -> {
                setBusy(false);
                addLine(what + ": exit " + c);
                done.exited(c);
            });
        }).start();
    }

    private void install() {
        final File root = findGameRoot();
        if (root == null) {
            File g = gameDir();
            setLog("No " + GAME_EXE + " under " + (g == null ? "(no external storage)" : g.getAbsolutePath())
                   + ": copy the game folder there first.");
            return;
        }
        File logs = new File(getFilesDir(), "logs");
        runInstaller("Checking the game folder", new String[] {"--source", "folder", root.getAbsolutePath(), "--yes",
                     "--threads", "0", "--record", new File(logs, "install.runrec").getAbsolutePath()}, code -> {
            if (code == 0) {
                addLine("The game folder verified. Starting the game.");
                mAutoPlayDone = true;
                refresh();
                play();
            } else {
                mStatus.setText(mStatus.getText() + "\n\nThe game folder did not verify (exit " + code + "): "
                                + "the lines below name each file. A changed or missing file must be copied again "
                                + "from the PC; mods must be removed.");
            }
        });
    }

    // ------------------------------------------------------------ the ZIP route

    private void pickZip() {
        Intent pick = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        pick.addCategory(Intent.CATEGORY_OPENABLE);
        pick.setType("*/*");
        pick.putExtra(Intent.EXTRA_MIME_TYPES, new String[] {"application/zip", "application/x-zip-compressed",
                      "application/x-zip", "multipart/x-zip", "application/octet-stream"});
        try {
            startActivityForResult(pick, PICK_ZIP);
        } catch (android.content.ActivityNotFoundException e) {
            setLog("This device has no file picker: copy the game folder instead (see above).");
        }
    }

    @Override
    protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request != PICK_ZIP || result != RESULT_OK || data == null || data.getData() == null) return;
        mAutoPlayDone = true;
        installZip(data.getData());
    }

    /** The picked document's display name and size in bytes (-1 when the provider does not say). */
    private Object[] describe(Uri uri) {
        String name = uri.getLastPathSegment();
        long size = -1;
        try (Cursor c = getContentResolver().query(uri, new String[] {OpenableColumns.DISPLAY_NAME,
                                                   OpenableColumns.SIZE}, null, null, null)) {
            if (c != null && c.moveToFirst()) {
                if (!c.isNull(0)) name = c.getString(0);
                if (!c.isNull(1)) size = c.getLong(1);
            }
        } catch (RuntimeException e) {
            Log.w(TAG, "cannot describe " + uri + ": " + e);
        }
        return new Object[] {name, size};
    }

    private static String gb(long bytes) {
        return String.format(java.util.Locale.US, "%.2f GB", bytes / 1e9);
    }

    /** Copies the picked ZIP into the cache (the installer reads a path, and a provider's stream
     * may not seek), then runs the installer's ZIP route on the copy and removes it. */
    private void installZip(final Uri uri) {
        final File copy = importZip();
        Object[] d = describe(uri);
        final String name = (String) d[0];
        final long size = (Long) d[1];
        long free = getCacheDir().getUsableSpace();
        if (size > 0 && free < size + (64L << 20)) {
            setLog("Not enough free space to copy " + name + " (" + gb(size) + "; " + gb(free) + " free). "
                   + "The import needs room for the ZIP and the extracted game together.");
            return;
        }
        setBusy(true);
        setLog("Copying " + name + (size > 0 ? " (" + gb(size) + ")" : "") + "...");
        new Thread(() -> {
            String err = null;
            long done = 0;
            try (InputStream in = getContentResolver().openInputStream(uri);
                 OutputStream out = new FileOutputStream(copy)) {
                if (in == null) throw new java.io.IOException("the provider gave no stream");
                byte[] buf = new byte[1 << 20];
                long shown = 0;
                int n;
                while ((n = in.read(buf)) > 0) {
                    out.write(buf, 0, n);
                    done += n;
                    if (done - shown >= (64L << 20)) {
                        shown = done;
                        final String line = "  copied " + gb(done) + (size > 0 ? " of " + gb(size) : "");
                        runOnUiThread(() -> addLine(line));
                    }
                }
            } catch (Exception e) {
                err = e.toString();
            }
            final String failure = err;
            final long copied = done;
            runOnUiThread(() -> {
                setBusy(false);
                if (failure != null) {
                    copy.delete();
                    addLine("Cannot copy " + name + ": " + failure);
                    return;
                }
                addLine("Copied " + gb(copied) + ".");
                File logs = new File(getFilesDir(), "logs");
                runInstaller("Checking the game ZIP", new String[] {"--source", "zip", copy.getAbsolutePath(), "--yes",
                             "--threads", "0", "--record", new File(logs, "install.runrec").getAbsolutePath()}, code -> {
                    copy.delete();
                    if (code == 0) {
                        addLine("The game ZIP verified. Starting the game.");
                        mAutoPlayDone = true;
                        refresh();
                        play();
                    } else {
                        mStatus.setText(mStatus.getText() + "\n\nThe game ZIP did not verify (exit " + code + "): "
                                        + "the lines below name each file. The ZIP must hold the unmodified game "
                                        + "folder; mods must be removed.");
                    }
                });
            });
        }).start();
    }

    private void verifyThenPlay() {
        File logs = new File(getFilesDir(), "logs");
        runInstaller("Checking the installation", new String[] {"--verify", "--threads", "0", "--record",
                     new File(logs, "verify.runrec").getAbsolutePath()}, code -> {
            if (code == 0) {
                play();
            } else {
                mStatus.setText("The installation no longer verifies (exit " + code + "). Tap Install again "
                                + "from a ZIP, or copy the game folder again and tap Install again.");
            }
        });
    }

    private void play() {
        launchPlay(new Intent(this, Sa2Activity.class));
    }

    private void launchPlay(Intent play) {
        mPlayLaunched = true;
        startActivity(play);
    }

    private void buildUi() {
        float dp = getResources().getDisplayMetrics().density;
        int pad = (int) (16 * dp);
        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setPadding(pad, pad, pad, pad);
        col.setBackgroundColor(Color.rgb(0x10, 0x18, 0x30));

        TextView title = new TextView(this);
        title.setText("SA2 Recompiled");
        title.setTextColor(Color.WHITE);
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 24);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        col.addView(title);

        mStatus = new TextView(this);
        mStatus.setTextColor(Color.rgb(0xE0, 0xE6, 0xF0));
        mStatus.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
        mStatus.setPadding(0, pad / 2, 0, pad / 2);
        mStatus.setTextIsSelectable(true);
        col.addView(mStatus);

        mZip = new Button(this);
        mZip.setText("Choose game ZIP");
        mZip.setOnClickListener(v -> pickZip());
        col.addView(mZip, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                                                         ViewGroup.LayoutParams.WRAP_CONTENT));

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.START);
        mCheck = new Button(this);
        mCheck.setText("Check game folder");
        mCheck.setOnClickListener(v -> {
            mAutoPlayDone = true;
            install();
        });
        mPlay = new Button(this);
        mPlay.setText("Play");
        mPlay.setOnClickListener(v -> {
            mAutoPlayDone = true;
            verifyThenPlay();
        });
        row.addView(mCheck);
        row.addView(mPlay);
        col.addView(row);

        mLog = new TextView(this);
        mLog.setTextColor(Color.rgb(0xA8, 0xB4, 0xC8));
        mLog.setTypeface(Typeface.MONOSPACE);
        mLog.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11);
        mLog.setPadding(0, pad / 2, 0, 0);
        mLog.setTextIsSelectable(true);
        col.addView(mLog);

        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(Color.rgb(0x10, 0x18, 0x30));
        scroll.setFitsSystemWindows(true);
        scroll.addView(col, new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                                                        ViewGroup.LayoutParams.WRAP_CONTENT));
        setContentView(scroll);
        mPlay.setEnabled(false);
        mStatus.setVisibility(View.VISIBLE);
    }
}
