package com.sa2recomp.app;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * What a failed installer run means for the player and what to do about it, in plain words, from the
 * installer's own output (host/install: each problem on a "  refused: ..." line, then
 * "SA2_INSTALL_FAIL <code>: <message>"). The ZIP route also prints its joined summary as the first
 * refusal ("refused before extraction: a; b; and N more" or "refused: a; b; ...; the partial store at
 * ... is kept"), which repeats the per-file lines: its items are merged with them by path. The
 * installer's lines stay below this on the setup screen, so nothing is lost; this only puts the
 * cause and the fix first.
 */
final class InstallVerdict {
    private static final int NAMED = 3;
    private static final Pattern MORE = Pattern.compile("^and (\\d+) more$");

    private InstallVerdict() {}

    /** The fix for a changed or missing file: Steam's own check, then the route's next step. */
    private static String verifyThen(boolean zip) {
        return "In Steam, right-click Sonic Adventure 2, then Properties, Installed Files, Verify integrity of "
               + "game files. " + again(zip);
    }

    private static String again(boolean zip) {
        return zip ? "Then make a new ZIP of the whole game folder and choose it here."
                   : "Then copy the whole game folder to the phone again and check the folder again.";
    }

    static String explain(List<String> lines, int code, boolean zip) {
        String fail = null;
        List<String> refused = new ArrayList<>();
        for (String l : lines) {
            String t = l.trim();
            if (t.startsWith("SA2_INSTALL_FAIL ")) fail = t.substring("SA2_INSTALL_FAIL ".length());
            else if (t.startsWith("refused: ")) refused.add(t.substring("refused: ".length()));
        }
        String all = (fail == null ? "" : fail) + "\n" + String.join("\n", refused);
        String source = zip ? "ZIP" : "folder";

        if (all.contains("does not hold Sonic Adventure 2") || all.contains("is not an SA2 game folder")) {
            return zip ? "This ZIP does not contain the game. Make the ZIP from the folder that has sonic2app.exe "
                         + "in it (Steam: right-click Sonic Adventure 2, Manage, Browse local files), and include "
                         + "everything in that folder, the resource folder too."
                       : "This folder does not contain the whole game. Copy the folder that has sonic2app.exe in it "
                         + "(Steam: right-click Sonic Adventure 2, Manage, Browse local files), with everything in it, "
                         + "the resource folder too, then check the folder again.";
        }
        if (all.contains("holds the game more than once")) {
            return "This ZIP holds two copies of the game. Make a ZIP of one copy only.";
        }
        if (all.contains("is encrypted")) {
            return "This ZIP has a password. Make it again without one.";
        }
        if (all.contains("is compressed with")) {
            return "This ZIP uses a compression method the app cannot read. Make it again with your computer's "
                   + "built-in Compress or Send to ZIP option.";
        }
        if (all.toLowerCase(Locale.US).contains("not enough free space")) {
            return "There is not enough free storage on this phone. The import needs room for the ZIP and the "
                   + "installed game together. Free up space and try again.";
        }

        // Every refused item once, by path: the per-file lines, and the summary's items.
        Map<String, String> items = new LinkedHashMap<>();
        int more = 0;
        for (String r : refused) {
            String body = null;
            if (r.startsWith("refused before extraction: ")) body = r.substring("refused before extraction: ".length());
            else if (r.startsWith("refused: ")) body = r.substring("refused: ".length());
            if (body == null) {
                items.put(pathOf(r), r);
                continue;
            }
            for (String item : body.split("; ")) {
                Matcher m = MORE.matcher(item.trim());
                if (m.find()) more += Integer.parseInt(m.group(1));
                else if (!item.startsWith("the partial store at ")) items.putIfAbsent(pathOf(item), item);
            }
        }
        List<String> changed = new ArrayList<>(), missing = new ArrayList<>(), extra = new ArrayList<>();
        boolean exe = false;
        for (Map.Entry<String, String> e : items.entrySet()) {
            String path = e.getKey(), r = e.getValue();
            if (r.contains(": changed")) {
                changed.add(path);
                if (path.equalsIgnoreCase("sonic2app.exe")) exe = true;
            } else if (r.contains(": missing")) {
                missing.add(path);
            } else if (r.contains("not a game file") || r.contains("read unverified")) {
                extra.add(path);
            }
        }
        if (exe) {
            return "Your sonic2app.exe is not the one Steam installs. Copies of the game from anywhere other than "
                   + "Steam, and patched or modded executables, cannot be used. " + verifyThen(zip);
        }
        StringBuilder b = new StringBuilder();
        if (!changed.isEmpty() || !missing.isEmpty()) {
            int n = changed.size() + missing.size();
            List<String> named = new ArrayList<>(missing);
            named.addAll(changed);
            b.append(count(n, more, extra.isEmpty())).append(n == 1 && more == 0 ? " game file is" : " game files are")
             .append(" missing or different from the Steam release (").append(list(named))
             .append("). This happens with mods, an incomplete copy, or a copy from somewhere other than Steam. ")
             .append(verifyThen(zip));
            if (!extra.isEmpty()) b.append(" Also remove the files mods added (named below).");
            return b.toString();
        }
        if (!extra.isEmpty()) {
            int n = extra.size() + more;
            b.append("The ").append(source).append(" has ").append(n).append(n == 1 ? " file that is" : " files that are")
             .append(" not part of the game (").append(list(extra)).append("). They usually come from mods, and the "
                     + "game would read them. Remove the mods, or delete those files. ").append(again(zip));
            return b.toString();
        }
        if (fail != null && fail.startsWith("I/O error")) {
            return "The app could not read part of the " + source + " (" + fail.substring(fail.indexOf(':') + 1).trim()
                   + "). Try again. " + (zip ? "If you copied the ZIP over USB or a cloud app, copy it again: it may "
                                               + "be incomplete." : "If the copy to the phone was interrupted, copy "
                                               + "the folder again.");
        }
        return "The game files did not pass the check" + (fail == null ? " (exit " + code + ")" : ": " + fail)
               + ". The lines below name each problem.";
    }

    /** The path a refusal names: a quoted path ("\"x\" is not a game file ..."), or the text before ": ". */
    private static String pathOf(String r) {
        if (r.startsWith("\"")) {
            int end = r.indexOf('"', 1);
            return end > 0 ? r.substring(1, end) : r;
        }
        int colon = r.indexOf(": ");
        return colon > 0 ? r.substring(0, colon) : r;
    }

    private static String count(int n, int more, boolean moreAreThese) {
        return moreAreThese && more > 0 ? (n + more) + "" : n + "";
    }

    private static String list(List<String> names) {
        return String.join(", ", names.subList(0, Math.min(NAMED, names.size()))) + (names.size() > NAMED ? ", ..." : "");
    }
}
