package com.sa2recomp.app;

import android.app.Activity;
import android.content.Context;
import android.content.pm.ActivityInfo;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.os.Build;
import android.os.Bundle;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * The touch controls' layout editor, opened from the settings screen for one orientation: the
 * whole screen as the game will have it (the picture where the game's will be, the controls at
 * their size and places, GameSettings.TouchLayout), each group (the stick, the A/B/X/Y diamond,
 * START, LT, RT) dragged to a new place. Save keeps the places as fractions of the control area
 * (the screen, or the band under the picture when the controls go below it), which the overlay
 * reads as SA2_TOUCH_LAYOUT or SA2_TOUCH_LAYOUT_BELOW; Reset puts every group back; Swap sides
 * mirrors the stick and the diamond. The buttons sit under START (in the picture's middle when
 * the controls are below it), and hide while a control is dragged.
 */
public class Sa2LayoutActivity extends Activity {
    static final String EXTRA_ORIENTATION = "sa2.orientation";

    private GameSettings mS;
    private Editor mEditor;
    private LinearLayout mBar;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        mS = GameSettings.load(this);
        boolean upright = GameSettings.PORTRAIT.equals(getIntent().getStringExtra(EXTRA_ORIENTATION));
        setRequestedOrientation(upright ? ActivityInfo.SCREEN_ORIENTATION_SENSOR_PORTRAIT
                                        : ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE);
        if (Build.VERSION.SDK_INT >= 28) {
            WindowManager.LayoutParams lp = getWindow().getAttributes();
            lp.layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES;
            getWindow().setAttributes(lp);
        }
        int[] screen = GameSettings.screen(this);
        int[] frame = mS.frame(screen != null ? screen[0] : 1920, screen != null ? screen[1] : 1080);
        mEditor = new Editor(this, frame[0], frame[1], mS.touchSize / 100f, mS.layoutOver, mS.layoutBelow);

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);
        root.addView(mEditor, new FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT,
                                                           FrameLayout.LayoutParams.MATCH_PARENT));
        LinearLayout bar = mBar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.VERTICAL);
        bar.setGravity(Gravity.CENTER_HORIZONTAL);
        TextView hint = new TextView(this);
        hint.setText("Drag a control to move it");
        hint.setTextColor(Color.rgb(0xE8, 0xED, 0xF7));
        hint.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
        hint.setGravity(Gravity.CENTER_HORIZONTAL);
        bar.addView(hint);
        Button reset = button("Reset", Ui.QUIET, v -> mEditor.reset());
        Button swap = button("Swap sides", Ui.QUIET, v -> mEditor.swap());
        Button cancel = button("Cancel", Ui.SECONDARY, v -> finish());
        Button save = button("Save", Ui.PRIMARY, v -> {
            if (mEditor.layout == null) return;
            if (mEditor.layout.below) mS.layoutBelow = mEditor.layout.value();
            else mS.layoutOver = mEditor.layout.value();
            mS.save(this);
            finish();
        });
        LinearLayout row1 = Ui.row(this, reset, swap);
        LinearLayout row2 = Ui.row(this, cancel, save);
        bar.addView(row1, fixedWidth(Ui.dp(this, 10)));
        bar.addView(row2, fixedWidth(Ui.dp(this, 8)));
        root.addView(bar, new FrameLayout.LayoutParams(FrameLayout.LayoutParams.WRAP_CONTENT,
                                                       FrameLayout.LayoutParams.WRAP_CONTENT,
                                                       Gravity.TOP | Gravity.CENTER_HORIZONTAL));
        mEditor.laidOut = () -> bar.post(this::placeBar);
        mEditor.dragging = on -> bar.setVisibility(on ? View.INVISIBLE : View.VISIBLE);
        setContentView(root);
        hideSystemBars();
    }

    /** The buttons under START, clear of the controls' own places; in the middle of the picture
     * when the controls are below it. */
    private void placeBar() {
        GameSettings.TouchLayout l = mEditor.layout;
        if (l == null) return;
        int h = mBar.getHeight(), screen = mEditor.getHeight();
        float top = l.below ? (screen - h) / 2f : l.y[2] + l.halfH(2) + Ui.dp(this, 16);
        mBar.setTranslationY(Math.max(0, Math.min(screen - h, top)));
    }

    private LinearLayout.LayoutParams fixedWidth(int top) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(Ui.dp(this, 300), LinearLayout.LayoutParams.WRAP_CONTENT);
        lp.topMargin = top;
        return lp;
    }

    private Button button(String text, int kind, View.OnClickListener click) {
        Button b = Ui.button(this, text, click);
        Ui.style(b, kind);
        b.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
        b.setMinHeight(Ui.dp(this, 44));
        b.setMinimumHeight(Ui.dp(this, 44));
        return b;
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) hideSystemBars();
    }

    /** The play screen's full screen (Sa2Activity), so the editor's screen is the game's. */
    @SuppressWarnings("deprecation")
    private void hideSystemBars() {
        if (Build.VERSION.SDK_INT >= 30) {
            getWindow().setDecorFitsSystemWindows(false);
            WindowInsetsController c = getWindow().getInsetsController();
            if (c != null) {
                c.hide(WindowInsets.Type.systemBars());
                c.setSystemBarsBehavior(WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
            }
        } else {
            getWindow().getDecorView().setSystemUiVisibility(
                    View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                    | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                    | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_LAYOUT_STABLE);
        }
    }

    /** The screen: the picture's place, the control area's edge when it is a band, and the groups. */
    private static final class Editor extends View {
        final int frameW, frameH;
        final float scale;
        String over, below;          // the places as saved, kept while the screen changes size
        GameSettings.TouchLayout layout;
        Runnable laidOut;                            // a new layout for a new size
        java.util.function.Consumer<Boolean> dragging;   // a control picked up, and let go
        private int held = -1;
        private float grabX, grabY;
        private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG), line = new Paint(Paint.ANTI_ALIAS_FLAG),
                            label = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final RectF rect = new RectF();

        Editor(Context c, int frameW, int frameH, float scale, String over, String below) {
            super(c);
            this.frameW = frameW;
            this.frameH = frameH;
            this.scale = scale;
            this.over = over;
            this.below = below;
            line.setStyle(Paint.Style.STROKE);
            label.setColor(Color.rgb(0x5D, 0x6A, 0x85));
            label.setTextAlign(Paint.Align.CENTER);
            label.setTextSize(TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, 14, c.getResources().getDisplayMetrics()));
        }

        @Override
        protected void onSizeChanged(int w, int h, int oldw, int oldh) {
            if (layout != null) keep();
            layout = GameSettings.touchLayout(w, h, frameW, frameH, scale, over, below);
            if (laidOut != null) laidOut.run();
        }

        /** The places as they stand, so a new size lays them out again. */
        private void keep() {
            if (layout.below) below = layout.value();
            else over = layout.value();
        }

        void reset() {
            if (layout == null) return;
            if (layout.below) below = "";
            else over = "";
            layout = GameSettings.touchLayout(getWidth(), getHeight(), frameW, frameH, scale, over, below);
            invalidate();
        }

        void swap() {
            if (layout == null) return;
            for (int g = 0; g < 2; g++) place(g, 2 * layout.ax + layout.aw - layout.x[g], layout.y[g]);
            keep();
            invalidate();
        }

        /** Group g's centre at (x, y), kept whole inside the control area. */
        private void place(int g, float x, float y) {
            GameSettings.TouchLayout l = layout;
            float hw = Math.min(l.halfW(g), l.aw / 2), hh = Math.min(l.halfH(g), l.ah / 2);
            l.x[g] = Math.max(l.ax + hw, Math.min(l.ax + l.aw - hw, x));
            l.y[g] = Math.max(l.ay + hh, Math.min(l.ay + l.ah - hh, y));
        }

        @Override
        public boolean onTouchEvent(MotionEvent e) {
            if (layout == null) return false;
            float x = e.getX(), y = e.getY();
            switch (e.getActionMasked()) {
                case MotionEvent.ACTION_DOWN: {
                    held = -1;
                    float best = Float.MAX_VALUE, slop = 0.04f * layout.s;
                    for (int g = 0; g < 5; g++) {
                        float dx = Math.abs(x - layout.x[g]), dy = Math.abs(y - layout.y[g]);
                        if (dx > layout.halfW(g) + slop || dy > layout.halfH(g) + slop) continue;
                        float d = dx * dx + dy * dy;
                        if (d < best) {
                            best = d;
                            held = g;
                        }
                    }
                    if (held < 0) return false;
                    grabX = x - layout.x[held];
                    grabY = y - layout.y[held];
                    if (dragging != null) dragging.accept(true);
                    invalidate();
                    return true;
                }
                case MotionEvent.ACTION_MOVE:
                    if (held < 0) return false;
                    place(held, x - grabX, y - grabY);
                    invalidate();
                    return true;
                case MotionEvent.ACTION_UP:
                case MotionEvent.ACTION_CANCEL:
                    if (held >= 0) {
                        keep();
                        if (dragging != null) dragging.accept(false);
                    }
                    held = -1;
                    invalidate();
                    return true;
                default:
                    return held >= 0;
            }
        }

        private void circle(Canvas c, float x, float y, float r, int color, int alpha, int ringAlpha, float ring) {
            fill.setColor(color);
            fill.setAlpha(alpha);
            c.drawCircle(x, y, r, fill);
            line.setColor(Color.WHITE);
            line.setAlpha(ringAlpha);
            line.setStrokeWidth(ring);
            c.drawCircle(x, y, r - ring / 2, line);
        }

        private void pill(Canvas c, float x, float y, float hw, float hh, int bars, float s) {
            rect.set(x - hw, y - hh, x + hw, y + hh);
            fill.setColor(Color.rgb(0xD9, 0xD9, 0xD9));
            fill.setAlpha(97);
            c.drawRoundRect(rect, hh, hh, fill);
            line.setColor(Color.WHITE);
            line.setAlpha(115);
            line.setStrokeWidth(Math.max(1.5f, 0.005f * s));
            c.drawRoundRect(rect, hh, hh, line);
            fill.setColor(Color.WHITE);
            fill.setAlpha(204);
            for (int i = 0; i < bars; i++) {
                float by = bars == 1 ? y : y + (i == 0 ? -0.3f : 0.3f) * hh, bw = (bars == 1 ? 0.5f : 0.45f) * hw,
                      bh = (bars == 1 ? 0.12f : 0.1f) * hh;
                rect.set(x - bw, by - bh, x + bw, by + bh);
                c.drawRoundRect(rect, bh, bh, fill);
            }
        }

        @Override
        protected void onDraw(Canvas c) {
            if (layout == null) return;
            GameSettings.TouchLayout l = layout;
            int w = getWidth(), h = getHeight();
            // The picture, centred at its own aspect, as the renderer presents it.
            float pw = w, ph = h;
            if ((double) w * frameH > (double) h * frameW) pw = Math.round(h * (float) frameW / frameH);
            else ph = Math.round(w * (float) frameH / frameW);
            float px = (float) Math.floor((w - pw) / 2), py = (float) Math.floor((h - ph) / 2);
            fill.setColor(Color.rgb(0x1A, 0x22, 0x36));
            fill.setAlpha(255);
            c.drawRect(px, py, px + pw, py + ph, fill);
            c.drawText("The game's picture", px + pw / 2, py + label.getTextSize() * 1.6f, label);
            if (l.below) {
                line.setColor(Color.rgb(0x3A, 0x48, 0x66));
                line.setAlpha(255);
                line.setStrokeWidth(2);
                c.drawRect(l.ax + 1, l.ay + 1, l.ax + l.aw - 1, l.ay + l.ah - 1, line);
            }
            float s = l.s, ring = Math.max(1.5f, 0.005f * s);
            // The stick: its base and knob.
            float R = 0.12f * s;
            circle(c, l.x[0], l.y[0], R, Color.rgb(0x0D, 0x0D, 0x14), 46, 89, Math.max(2f, 0.008f * s));
            fill.setColor(Color.WHITE);
            fill.setAlpha(102);
            c.drawCircle(l.x[0], l.y[0], 0.42f * R, fill);
            // The diamond: A below, B right, X left, Y above.
            float r = 0.075f * s, d = 0.135f * s, fx = l.x[1], fy = l.y[1];
            circle(c, fx, fy + d, r, Color.rgb(0x4D, 0xCC, 0x59), 97, 115, ring);
            circle(c, fx + d, fy, r, Color.rgb(0xEB, 0x4D, 0x4D), 97, 115, ring);
            circle(c, fx - d, fy, r, Color.rgb(0x4D, 0x8C, 0xF2), 97, 115, ring);
            circle(c, fx, fy - d, r, Color.rgb(0xF2, 0xCC, 0x40), 97, 115, ring);
            pill(c, l.x[2], l.y[2], 0.075f * s, 0.035f * s, 2, s);
            pill(c, l.x[3], l.y[3], 0.09f * s, 0.04f * s, 1, s);
            pill(c, l.x[4], l.y[4], 0.09f * s, 0.04f * s, 1, s);
            if (held >= 0) {
                line.setColor(Color.rgb(0xFF, 0xC2, 0x1A));
                line.setAlpha(255);
                line.setStrokeWidth(3);
                rect.set(l.x[held] - l.halfW(held), l.y[held] - l.halfH(held),
                         l.x[held] + l.halfW(held), l.y[held] + l.halfH(held));
                c.drawRoundRect(rect, 12, 12, line);
            }
        }
    }
}
