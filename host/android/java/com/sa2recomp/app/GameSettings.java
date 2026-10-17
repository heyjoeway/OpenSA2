package com.sa2recomp.app;

import android.app.Activity;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.ActivityInfo;
import android.graphics.Point;
import android.graphics.Rect;
import android.os.Build;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;

/**
 * The game settings the settings screen (Sa2SettingsActivity) keeps in the app's own preferences
 * ("game_settings", which an app update keeps), and what they ask of a session: the orientation
 * Sa2Activity asks the system for and passes as --orientation, the screen size it passes as
 * --screen, and the SA2_ settings it passes as --setting (host/android/native/sa2_android_session.cpp:
 * after the session's defaults, before <external files>/game.env.extra, whose lines still win).
 * Every setting at its default asks for nothing, so such a session runs as it did before there
 * were settings.
 *
 * The picture. Landscape with widescreen on draws the game at the chosen aspect (the screen's own
 * by default, never narrower than 4:3) and resolution (the largest picture of that aspect the
 * screen holds, or fewer lines); widescreen off keeps the game's own size from its config
 * (SA2_PLAY_RES=off). Portrait and auto-rotate always draw 4:3: the game's menus are laid out for
 * 4:3, and a wider picture across an upright phone would be a thin strip. Portrait sizes it across
 * the screen's width; auto-rotate sizes it for landscape, so it is scaled down when the phone is
 * upright. Upright, the picture sits where the picture position puts it (its centre a quarter of the
 * way down by default, the middle of the screen's upper half), so the touch controls below it get
 * the rest of the screen.
 */
final class GameSettings {
    static final String PREFS = "game_settings";
    static final String LANDSCAPE = "landscape", PORTRAIT = "portrait", AUTO = "auto";
    /** Resolution choices in lines, 0 the largest the screen holds. */
    static final int[] RESOLUTIONS = {0, 1440, 1080, 900, 720, 540, 480};
    static final String[] ASPECTS = {"auto", "21:9", "16:9", "4:3"};
    /** The touch overlay's on-screen buttons (host/player/touch_overlay.h SA2_TOUCH_MAP), and a pad's
     * buttons by position (host/player/player.cpp SA2_PAD_MAP), as the settings name them. */
    static final String[] TOUCH_BUTTONS = {"A", "B", "X", "Y", "START", "LT", "RT"};
    static final String[] PAD_BUTTONS = {"SOUTH", "EAST", "WEST", "NORTH", "LB", "RB", "BACK", "START", "LS", "RS",
                                         "UP", "DOWN", "LEFT", "RIGHT"};
    static final String[] PAD_DEFAULTS = {"A", "B", "X", "Y", "LB", "RB", "BACK", "START", "LS", "RS",
                                          "UP", "DOWN", "LEFT", "RIGHT"};
    /** What a button can send (host/player/input.h pad_output_named). */
    static final String[] OUTPUTS = {"A", "B", "X", "Y", "LB", "RB", "LT", "RT", "START", "BACK", "LS", "RS",
                                     "UP", "DOWN", "LEFT", "RIGHT", "NONE"};
    /** The game's own size when the session leaves its config alone (the installer's
     * Config/UserConfig.cfg: host/install/sa2_config.cpp). */
    static final int CONFIG_W = 1280, CONFIG_H = 720;

    String orientation = LANDSCAPE;
    boolean widescreen = true, hud43 = true;
    int resolution = 0;                  // lines, 0 the screen's
    String aspect = "auto";
    int touchSize = 100, touchOpacity = 100;   // percent of the overlay's own
    int pictureY = 25;                         // upright, the picture's centre, percent of the height down (0 to 50)
    String layoutLandscape = "", layoutPortrait = "";  // SA2_TOUCH_LAYOUT, SA2_TOUCH_LAYOUT_PORTRAIT; "" its own
    String touchMap = "", padMap = "";         // SA2_TOUCH_MAP and SA2_PAD_MAP, only what differs

    static GameSettings load(Context c) {
        SharedPreferences p = c.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        GameSettings s = new GameSettings();
        s.orientation = p.getString("orientation", s.orientation);
        s.widescreen = p.getBoolean("widescreen", s.widescreen);
        s.hud43 = p.getBoolean("hud43", s.hud43);
        s.resolution = p.getInt("resolution", s.resolution);
        s.aspect = p.getString("aspect", s.aspect);
        s.touchSize = p.getInt("touch_size", s.touchSize);
        s.touchOpacity = p.getInt("touch_opacity", s.touchOpacity);
        s.pictureY = p.getInt("picture_y", s.pictureY);
        s.layoutLandscape = p.getString("touch_layout", s.layoutLandscape);
        s.layoutPortrait = p.getString("touch_layout_portrait", s.layoutPortrait);
        s.touchMap = p.getString("touch_map", s.touchMap);
        s.padMap = p.getString("pad_map", s.padMap);
        return s;
    }

    /** Written at once (commit, not apply): the play screen runs in its own process and reads the
     * file when the session starts. */
    void save(Context c) {
        c.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putString("orientation", orientation)
                .putBoolean("widescreen", widescreen)
                .putBoolean("hud43", hud43)
                .putInt("resolution", resolution)
                .putString("aspect", aspect)
                .putInt("touch_size", touchSize)
                .putInt("touch_opacity", touchOpacity)
                .putInt("picture_y", pictureY)
                .putString("touch_layout", layoutLandscape)
                .putString("touch_layout_portrait", layoutPortrait)
                .putString("touch_map", touchMap)
                .putString("pad_map", padMap)
                .commit();
    }

    // ------------------------------------------------------------ the screen and the picture

    /** The largest window an activity of this app can have, in pixels, {longer side, shorter side}:
     * the whole screen, cutout included (the play screen lays out into it,
     * LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES, and hides the system bars). API 30's maximum
     * window metrics; on API 29 the display's real size. Null when neither answers. */
    @SuppressWarnings("deprecation")
    static int[] screen(Activity a) {
        int w = 0, h = 0;
        try {
            if (Build.VERSION.SDK_INT >= 30) {
                Rect b = a.getWindowManager().getMaximumWindowMetrics().getBounds();
                w = b.width();
                h = b.height();
            } else {
                Point p = new Point();
                a.getWindowManager().getDefaultDisplay().getRealSize(p);
                w = p.x;
                h = p.y;
            }
        } catch (RuntimeException e) {
            return null;
        }
        if (w <= 0 || h <= 0) return null;
        return new int[] {Math.max(w, h), Math.min(w, h)};
    }

    boolean upright() {
        return PORTRAIT.equals(orientation);
    }

    /** The orientation Sa2Activity asks for; -1 for landscape, the manifest's own. */
    int requestedOrientation() {
        if (PORTRAIT.equals(orientation)) return ActivityInfo.SCREEN_ORIENTATION_USER_PORTRAIT;
        if (AUTO.equals(orientation)) return ActivityInfo.SCREEN_ORIENTATION_FULL_USER;
        return -1;
    }

    /** --screen: the screen in the session's orientation, "WxH". */
    String screenArgument(int lng, int shrt) {
        return upright() ? shrt + "x" + lng : lng + "x" + shrt;
    }

    /** Every display setting at its default: the session's own full-width rule decides. */
    boolean displayDefault() {
        return LANDSCAPE.equals(orientation) && widescreen && resolution == 0 && "auto".equals(aspect);
    }

    /** Portrait and auto-rotate draw 4:3, whatever widescreen and the aspect say. */
    boolean fixed43() {
        return !LANDSCAPE.equals(orientation);
    }

    static double ratio(String aspect) {
        int colon = aspect.indexOf(':');
        try {
            return Double.parseDouble(aspect.substring(0, colon)) / Double.parseDouble(aspect.substring(colon + 1));
        } catch (RuntimeException e) {
            return 16.0 / 9;
        }
    }

    /** The picture's aspect, width over height, on a lng x shrt screen. */
    double pictureRatio(int lng, int shrt) {
        if (fixed43()) return 4.0 / 3;
        if ("auto".equals(aspect)) return Math.max((double) lng / shrt, 4.0 / 3);
        return ratio(aspect);
    }

    /** The largest picture of the chosen aspect a lng x shrt screen holds in the session's
     * orientation, {w, h}; null with widescreen off in landscape (the game's own size). */
    int[] fullPicture(int lng, int shrt) {
        if (!fixed43() && !widescreen) return null;
        int bw = upright() ? shrt : lng, bh = upright() ? lng : shrt;
        double r = pictureRatio(lng, shrt);
        if ((double) bw / bh > r) return new int[] {(int) Math.round(bh * r), bh};
        return new int[] {bw, (int) Math.round(bw / r)};
    }

    /** The game's back buffer for these settings on a lng x shrt screen, {w, h}; null when the game
     * keeps its config's own size (widescreen off in landscape, or the default settings on a
     * screen no wider than 4:3, as the session decides). `lines` overrides the resolution (0 the
     * default, the largest picture except where the session keeps the config's size), for
     * listing the choices; as many lines as the largest picture's, or more, is the largest. */
    int[] picture(int lng, int shrt, int lines) {
        // At these lines the session's own rule decides, as it does when every display setting is
        // at its default (the stored resolution is not the one asked about here).
        boolean sessionRule = LANDSCAPE.equals(orientation) && widescreen && "auto".equals(aspect) && lines == 0;
        if (sessionRule) return (long) lng * 3 > (long) shrt * 4 ? new int[] {lng, shrt} : null;
        int[] full = fullPicture(lng, shrt);
        if (full == null) return null;
        int w = full[0], h = full[1];
        if (lines > 0 && lines < h) {
            w = (int) Math.round(lines * pictureRatio(lng, shrt));
            h = lines;
        }
        w = Math.max(320, Math.min(16384, w));
        h = Math.max(320, Math.min(16384, h));
        return new int[] {w, h};
    }

    int[] picture(int lng, int shrt) {
        return picture(lng, shrt, resolution);
    }

    /** Where the player puts a letterboxed picture in an upright window (SA2_PLAYER_PICTURE_Y), or -1
     * (centred) when the orientation is landscape. */
    float pictureAnchor() {
        return LANDSCAPE.equals(orientation) ? -1 : pictureY / 100f;
    }

    /** The frame the touch overlay will see: the picture, or the config's own size. */
    int[] frame(int lng, int shrt) {
        int[] p = picture(lng, shrt);
        return p != null ? p : new int[] {CONFIG_W, CONFIG_H};
    }

    // ------------------------------------------------------------ the session's settings

    /** The --setting values for these settings on a lng x shrt screen: none at the defaults. */
    List<String> sessionSettings(int lng, int shrt) {
        List<String> e = new ArrayList<>();
        if (!displayDefault()) {
            int[] p = picture(lng, shrt);
            e.add("SA2_PLAY_RES=" + (p == null ? "off" : p[0] + "x" + p[1]));
        }
        if (!hud43) e.add("SA2_PLAY_HUD43=0");
        if (!LANDSCAPE.equals(orientation)) e.add("SA2_PLAYER_PICTURE_Y=" + fraction(pictureY));
        if (touchSize != 100) e.add("SA2_TOUCH_SCALE=" + fraction(touchSize));
        if (touchOpacity != 100) e.add("SA2_TOUCH_OPACITY=" + fraction(touchOpacity));
        if (!layoutLandscape.isEmpty()) e.add("SA2_TOUCH_LAYOUT=" + layoutLandscape);
        if (!layoutPortrait.isEmpty()) e.add("SA2_TOUCH_LAYOUT_PORTRAIT=" + layoutPortrait);
        if (!touchMap.isEmpty()) e.add("SA2_TOUCH_MAP=" + touchMap);
        if (!padMap.isEmpty()) e.add("SA2_PAD_MAP=" + padMap);
        return e;
    }

    private static String fraction(int percent) {
        return String.format(Locale.US, "%.2f", percent / 100.0);
    }

    // ------------------------------------------------------------ button maps

    /** What `button` sends in `map` ("A=B,LT=START"), or `own` when the map leaves it. */
    static String sends(String map, String button, String own) {
        for (String item : map.isEmpty() ? new String[0] : map.split(",")) {
            int eq = item.indexOf('=');
            if (eq > 0 && item.substring(0, eq).equals(button)) return item.substring(eq + 1);
        }
        return own;
    }

    /** `map` with `button` sending `out`: an entry only when it differs from `own`, in `order`. */
    static String withSend(String map, String[] order, String[] owns, String button, String out) {
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < order.length; i++) {
            String v = order[i].equals(button) ? out : sends(map, order[i], owns[i]);
            if (v.equals(owns[i])) continue;
            if (b.length() > 0) b.append(',');
            b.append(order[i]).append('=').append(v);
        }
        return b.toString();
    }

    // ------------------------------------------------------------ the touch layout

    /** The overlay's groups, in SA2_TOUCH_LAYOUT's names and order. */
    static final String[] GROUPS = {"STICK", "FACE", "START", "LT", "RT"};

    /**
     * The touch overlay's layout on a w x h window showing a fw x fh frame, for the layout editor:
     * host/player/touch_overlay.cpp layout(), the same arithmetic, so the editor shows the controls
     * where the game will. The control area is the window, or the band under the picture when that
     * band is at least 30% of the window's short side; s sizes everything (0.12 s the stick's
     * radius); x and y are each group's centre in pixels. The places set for the window's
     * orientation (portrait when it is taller than wide) apply, each held whole inside the area.
     */
    static final class TouchLayout {
        boolean below, upright;
        float ax, ay, aw, ah, s;
        float pictureTop, pictureBottom;   // the picture's rows (the whole height when it fills it)
        final float[] x = new float[5], y = new float[5];
        final float[] dx = new float[5], dy = new float[5];   // the overlay's own places

        /** Half the group's width and height, for the editor's dragging and drawing. */
        float halfW(int g) {
            return g == 0 ? 0.12f * s : g == 1 ? 0.21f * s : g == 2 ? 0.075f * s : 0.09f * s;
        }

        float halfH(int g) {
            return g == 0 ? 0.12f * s : g == 1 ? 0.21f * s : g == 2 ? 0.035f * s : 0.04f * s;
        }

        /** The places as SA2_TOUCH_LAYOUT's value, "" when every group is at its own place. */
        String value() {
            boolean moved = false;
            for (int g = 0; g < 5; g++) moved |= Math.abs(x[g] - dx[g]) > 0.5f || Math.abs(y[g] - dy[g]) > 0.5f;
            if (!moved) return "";
            StringBuilder b = new StringBuilder();
            for (int g = 0; g < 5; g++) {
                if (b.length() > 0) b.append(';');
                b.append(GROUPS[g]).append('=')
                 .append(String.format(Locale.US, "%.4f,%.4f", clamp01((x[g] - ax) / aw), clamp01((y[g] - ay) / ah)));
            }
            return b.toString();
        }
    }

    static float clamp01(float v) {
        return Math.max(0f, Math.min(1f, v));
    }

    /** `anchor` and `minTop`: the picture's place in a window taller than wide, as the player sets it
     * (host/d3d9gpu/d9g_gpu.h fit_rect), its centre at anchor of the height (negative: centred), its
     * top no higher than minTop; a landscape window keeps it centred. */
    static TouchLayout touchLayout(float w, float h, int fw, int fh, float scale, float anchor, float minTop,
                                   String landscapeValue, String portraitValue) {
        TouchLayout l = new TouchLayout();
        l.upright = h > w;
        float shortSide = Math.min(w, h);
        float s = shortSide * scale;
        float top = 0, bottom = h;
        if (fw > 0 && fh > 0 && (double) w * fh <= (double) h * fw) {
            float ph = Math.min(h, Math.round(w * (float) fh / fw));
            if (h - ph > 2f) {
                top = (float) Math.floor((h - ph) / 2);
                if (anchor >= 0 && h > w)
                    top = Math.min(top, Math.max(minTop, (float) Math.round((double) anchor * h - ph / 2.0)));
                bottom = top + ph;
            }
        }
        l.pictureTop = top;
        l.pictureBottom = bottom;
        l.below = h - bottom >= 0.30f * shortSide;
        l.ax = 0;
        l.ay = 0;
        l.aw = w;
        l.ah = h;
        if (l.below) {
            l.ay = bottom;
            l.ah = h - bottom;
            s = Math.min(s, l.ah / 0.60f);
        }
        l.s = s;
        float m = 0.04f * s, R = 0.12f * s, r = 0.075f * s, d = 0.135f * s, ph = 0.035f * s, th = 0.04f * s,
              tw = 0.09f * s;
        l.dx[0] = l.ax + m + 1.6f * R;
        l.dy[0] = l.ay + l.ah - m - 1.6f * R;
        l.dx[1] = l.ax + l.aw - m - d - r;
        l.dy[1] = l.ay + l.ah - m - d - r;
        l.dx[2] = l.ax + l.aw * 0.5f;
        l.dy[2] = l.ay + m + ph;
        l.dx[3] = l.ax + m + tw;
        l.dy[3] = l.ay + m + th;
        l.dx[4] = l.ax + l.aw - m - tw;
        l.dy[4] = l.ay + m + th;
        for (int g = 0; g < 5; g++) {
            l.x[g] = l.dx[g];
            l.y[g] = l.dy[g];
        }
        String value = l.upright ? portraitValue : landscapeValue;
        for (String item : value.isEmpty() ? new String[0] : value.split(";")) {
            int eq = item.indexOf('='), comma = item.indexOf(',');
            int g = eq < 0 ? -1 : Arrays.asList(GROUPS).indexOf(item.substring(0, eq));
            if (g < 0 || comma < eq) continue;
            float px, py;
            try {
                px = l.ax + clamp01(Float.parseFloat(item.substring(eq + 1, comma))) * l.aw;
                py = l.ay + clamp01(Float.parseFloat(item.substring(comma + 1))) * l.ah;
            } catch (NumberFormatException e) {
                continue;   // a place that does not read is the group's own, as the overlay refuses it
            }
            float hw = Math.min(l.halfW(g), l.aw / 2), hh = Math.min(l.halfH(g), l.ah / 2);
            l.x[g] = Math.max(l.ax + hw, Math.min(l.ax + l.aw - hw, px));
            l.y[g] = Math.max(l.ay + hh, Math.min(l.ay + l.ah - hh, py));
        }
        return l;
    }
}
