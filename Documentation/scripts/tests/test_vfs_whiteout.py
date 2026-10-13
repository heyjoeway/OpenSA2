"""Saves the game folder holds can be deleted, and the save listing reads both layers.

runtime/sa2_files.c is compiled with a small driver that runs the file layer's calls named on
its command line, against a scratch game folder (the read-only installation) and a scratch write
overlay, and its answers are compared with literal expectations. Two defects carried in
Documentation/S6_INPUTS.md are what this pins:

* Deleting a save that only the game folder holds trapped (sa2_vfs_delete answered read-only,
  and imp_DeleteFileA turns that into a trap). It now leaves a marker (a whiteout) in the
  overlay, which every lookup and listing honours and the guest can neither see nor name.
* A search listed the overlay's save directory alone once it existed, so after a player's first
  save the game folder's saves dropped out of File Select. It now merges both layers, the
  overlay winning a name and a whiteout hiding one, in the order Windows lists a directory.

SA2_VFS_TEST_WINE=1 builds the same driver for x86_64-windows-gnu (zig cc, with
runtime/sa2_win32.c, the Windows host's POSIX layer) and runs every case under Proton's Wine in
a prefix of its own, so the Windows host's paths answer the same. Run it through tools/slot.sh,
as every Wine job: `tools/slot.sh env SA2_VFS_TEST_WINE=1 .venv/bin/python -m pytest -q
Documentation/scripts/tests/test_vfs_whiteout.py`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"
WINE = os.environ.get("SA2_VFS_TEST_WINE") == "1"
STEAM = Path.home() / ".local" / "share" / "Steam"
PROTON = STEAM / "steamapps" / "common" / "Proton - Experimental" / "files" / "bin"
WINE_PREFIX = ROOT / "private" / "generated" / "s8" / "saves" / "wine-prefix"

SAVES = ".\\resource\\gd_PC\\SAVEDATA"
MARK = ".sa2-whiteout."

DRIVER = r"""
#include <stdio.h>
#include <string.h>
#include "sa2_files.h"

/* Each argument is one call: op|path[|data]. One line out per call. */
int main(int argc, char **argv) {
    sa2_vfs_config("C:\\SA2");
    for (int i = 1; i < argc; i++) {
        char op[64], path[SA2_VFS_PATH_MAX], data[256];
        const char *a = argv[i], *b = strchr(a, '|');
        if (!b) b = a + strlen(a);
        snprintf(op, sizeof op, "%.*s", (int)(b - a), a);
        const char *rest = *b ? b + 1 : b, *c = strchr(rest, '|');
        snprintf(path, sizeof path, "%.*s", c ? (int)(c - rest) : (int)strlen(rest), rest);
        snprintf(data, sizeof data, "%s", c ? c + 1 : "");
        char host[SA2_VFS_PATH_MAX];
        int layer = 0, status = 0, existed = 0, is_dir = 0;
        if (!strcmp(op, "map")) {
            int rc = sa2_vfs_map(path, host, sizeof host, NULL, 0, &layer);
            printf("%s %d\n", sa2_vfs_status_name(rc), rc == SA2_VFS_OK ? layer : -1);
        } else if (!strcmp(op, "exists")) {
            int rc = sa2_vfs_exists(path, &is_dir, &layer);
            printf("%s %d\n", sa2_vfs_status_name(rc), is_dir);
        } else if (!strcmp(op, "attr")) {
            unsigned attr = sa2_vfs_attributes(path, &status);
            printf("%08x %s\n", attr, sa2_vfs_status_name(status));
        } else if (!strcmp(op, "read")) {
            uint32_t h = sa2_vfs_open(path, SA2_VFS_ACCESS_READ, SA2_VFS_OPEN_EXISTING, &status,
                                      &existed);
            char buf[256] = {0};
            uint32_t got = 0;
            if (h) { sa2_vfs_read(h, buf, sizeof buf - 1, &got); sa2_vfs_close(h); }
            if (h) printf("ok %u %.*s\n", got, (int)got, buf);
            else printf("%s\n", sa2_vfs_status_name(status));
        } else if (!strcmp(op, "always") || !strcmp(op, "create") || !strcmp(op, "existing")) {
            uint32_t disp = !strcmp(op, "always") ? SA2_VFS_OPEN_ALWAYS
                          : !strcmp(op, "create") ? SA2_VFS_CREATE_ALWAYS : SA2_VFS_OPEN_EXISTING;
            uint32_t h = sa2_vfs_open(path, SA2_VFS_ACCESS_READ | SA2_VFS_ACCESS_WRITE, disp,
                                      &status, &existed);
            uint64_t size = 0;
            uint32_t put = 0;
            if (h) {
                sa2_vfs_size(h, &size);
                if (data[0]) sa2_vfs_write(h, data, (uint32_t)strlen(data), &put);
                sa2_vfs_close(h);
                printf("ok %d %llu\n", existed, (unsigned long long)size);
            } else {
                printf("%s\n", sa2_vfs_status_name(status));
            }
        } else if (!strcmp(op, "del")) {
            printf("%s\n", sa2_vfs_status_name(sa2_vfs_delete(path)));
        } else if (!strcmp(op, "find")) {
            sa2_vfs_dirent e;
            uint32_t h = sa2_vfs_find_first(path, &e, &status);
            if (!h) { printf("%s\n", sa2_vfs_status_name(status)); continue; }
            int n = 0;
            do printf("%s%s:%llu", n++ ? "," : "", e.name, (unsigned long long)e.size);
            while (sa2_vfs_find_next(h, &e));
            printf("\n");
            sa2_vfs_close(h);
        } else if (!strcmp(op, "stats")) {
            sa2_vfs_stats_t st;
            sa2_vfs_stats(&st);
            printf("deletes=%u whiteouts=%u install_write_attempts=%u\n", st.deletes,
                   st.whiteouts, st.install_write_attempts);
        } else {
            printf("? %s\n", op);
        }
    }
    return 0;
}
"""


def wine_env(base: dict[str, str]) -> dict[str, str]:
    env = {k: v for k, v in base.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    env.update(WINEDEBUG="-all", WINEDLLOVERRIDES="mscoree,mshtml=;winemenubuilder.exe=d",
               WINEPREFIX=str(WINE_PREFIX))
    return env


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    work = tmp_path_factory.mktemp("vfs-whiteout")
    (work / "driver.c").write_text(DRIVER)
    if WINE:
        zig = shutil.which("zig")
        assert zig, "no zig on PATH: source tools/env.sh"
        exe = work / "driver.exe"
        argv = [zig, "cc", "-target", "x86_64-windows-gnu", "-std=gnu11", "-O1", "-Wall",
                "-I", str(RUNTIME), str(work / "driver.c"), str(RUNTIME / "sa2_files.c"),
                str(RUNTIME / "sa2_win32.c"), "-o", str(exe)]
    else:
        cc = shutil.which("cc")
        assert cc, "no C compiler (cc) on PATH"
        exe = work / "driver"
        argv = [cc, "-std=gnu11", "-O1", "-Wall", "-Wextra", "-I", str(RUNTIME),
                str(work / "driver.c"), str(RUNTIME / "sa2_files.c"), "-o", str(exe)]
    p = subprocess.run(argv, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    if WINE:
        WINE_PREFIX.mkdir(parents=True, exist_ok=True)
    return exe


def tree(top: Path) -> dict[str, bytes]:
    """Every file under `top`, by its path relative to it."""
    if not top.is_dir():
        return {}
    return {p.relative_to(top).as_posix(): p.read_bytes()
            for p in sorted(top.rglob("*")) if p.is_file()}


@pytest.fixture
def game(tmp_path) -> Path:
    """A game folder holding three saves and a file that is no save, and no overlay yet."""
    saves = tmp_path / "install" / "resource" / "gd_PC" / "SAVEDATA"
    saves.mkdir(parents=True)
    (saves / "SONIC2B__ALF").write_bytes(b"alf")
    (saves / "SONIC2B__S01").write_bytes(b"\1" * 64)
    (saves / "SONIC2B__S02").write_bytes(b"\2" * 32)
    adx = tmp_path / "install" / "resource" / "gd_PC" / "ADX"
    adx.mkdir()
    (adx / "title.adx").write_bytes(b"adx")
    return tmp_path


def run(driver: Path, cwd: Path, *ops: str, hide: bool = False) -> list[str]:
    """The driver's answers to `ops`, one process, against cwd's install and overlay; the
    installation must come out byte-identical."""
    before = tree(cwd / "install")
    env = {k: v for k, v in os.environ.items() if not k.startswith("SA2_")}
    env.update(SA2_INSTALL="install", SA2_VFS_WRITE_ROOT="overlay")
    if hide:
        env["SA2_VFS_HIDE_INSTALL_SAVES"] = "1"
    argv = [str(driver), *ops]
    if WINE:
        env = wine_env(env)
        argv = ["/usr/bin/bwrap", "--dev-bind", "/", "/", "--ro-bind", str(STEAM), str(STEAM),
                "--die-with-parent", "--chdir", str(cwd), "--", str(PROTON / "wine"), *argv]
    p = subprocess.run(argv, capture_output=True, text=True, env=env, cwd=cwd, timeout=300)
    assert p.returncode == 0, p.stderr
    assert tree(cwd / "install") == before                     # the installation is input
    lines = p.stdout.splitlines()
    assert len(lines) == len(ops), p.stdout
    return lines


def save(name: str) -> str:
    return f"{SAVES}\\{name}"


def overlay_saves(cwd: Path) -> dict[str, bytes]:
    return tree(cwd / "overlay" / "resource" / "gd_PC" / "SAVEDATA")


def spelled(files: dict[str, bytes]) -> dict[str, bytes]:
    """Overlay names as the host reads them. A Windows host spells a new overlay name as the
    guest did (write_host_of takes the installation's spelling from a lookup, and a case-blind
    host's lookup answers with the name it was asked), and reads either spelling as the same
    file, so there names compare case-blind."""
    return {k.lower(): v for k, v in files.items()} if WINE else files


# ------------------------------------------------------------------ deleting a save


def test_deleting_a_save_only_the_game_folder_holds_hides_it_everywhere(driver, game):
    s01 = save("SONIC2B__S01")
    assert run(driver, game,
               f"del|{s01}",
               f"map|{s01}",
               "map|.\\RESOURCE\\gd_pc\\savedata\\sonic2b__s01",       # another case of it
               f"exists|{s01}",
               f"attr|{s01}",
               f"read|{s01}",
               f"existing|{s01}",                                       # OPEN_EXISTING, write
               f"find|{s01}",
               f"find|{SAVES}\\*",
               f"del|{s01}",                                            # nothing left to delete
               f"map|{save('SONIC2B__S02')}",
               "stats") == [
        "ok",
        "not-found -1",
        "not-found -1",
        "not-found 0",
        "ffffffff not-found",
        "not-found",
        "not-found",
        "not-found",
        "SONIC2B__ALF:3,SONIC2B__S02:32",
        "not-found",
        "ok 0",
        "deletes=1 whiteouts=1 install_write_attempts=0",
    ]
    assert overlay_saves(game) == {MARK + "SONIC2B__S01": b""}
    # and the next run, a new process, still finds it deleted
    assert run(driver, game, f"map|{s01}", f"find|{SAVES}\\*") == [
        "not-found -1", "SONIC2B__ALF:3,SONIC2B__S02:32"]


def test_the_marker_is_spelled_as_the_game_folder_spells_the_save(driver, game):
    assert run(driver, game, "del|.\\resource\\gd_pc\\savedata\\sonic2b__s01",
               f"map|{save('SONIC2B__S01')}") == ["ok", "not-found -1"]
    assert spelled(tree(game / "overlay")) == spelled(
        {f"resource/gd_PC/SAVEDATA/{MARK}SONIC2B__S01": b""})


def test_the_marker_is_no_file_the_guest_can_see_or_name(driver, game):
    assert run(driver, game, f"del|{save('SONIC2B__S01')}") == ["ok"]
    marker = save(MARK + "SONIC2B__S01")
    assert run(driver, game,
               f"map|{marker}",
               f"exists|{marker}",
               f"read|{marker}",
               f"create|{marker}|x",
               f"del|{marker}",
               f"map|.\\{MARK}anything\\x",                              # any component
               f"find|{SAVES}\\{MARK}*",
               f"find|{SAVES}\\*S01",
               f"find|{SAVES}\\*") == [
        "bad-path -1",
        "bad-path 0",
        "bad-path",
        "bad-path",
        "bad-path",
        "bad-path -1",
        "not-found",
        "not-found",
        "SONIC2B__ALF:3,SONIC2B__S02:32",
    ]
    assert overlay_saves(game) == {MARK + "SONIC2B__S01": b""}


def test_a_deleted_save_made_again_is_new_and_takes_the_marker_away(driver, game):
    s01 = save("SONIC2B__S01")
    assert run(driver, game,
               f"del|{s01}",
               f"always|{s01}|new",                 # OPEN_ALWAYS: a new, empty save, no copy-up
               f"map|{s01}",
               f"read|{s01}",
               f"find|{SAVES}\\*") == [
        "ok",
        "ok 0 0",
        "ok 1",
        "ok 3 new",
        "SONIC2B__ALF:3,SONIC2B__S01:3,SONIC2B__S02:32",
    ]
    assert overlay_saves(game) == {"SONIC2B__S01": b"new"}


def test_create_always_and_another_case_take_the_marker_away_too(driver, game):
    assert run(driver, game,
               f"del|{save('SONIC2B__S02')}",
               f"create|{save('sonic2b__s02')}|again",
               f"read|{save('SONIC2B__S02')}") == ["ok", "ok 0 0", "ok 5 again"]
    assert spelled(overlay_saves(game)) == spelled({"SONIC2B__S02": b"again"})


def test_deleting_the_overlay_s_copy_of_a_game_folder_save_leaves_a_marker(driver, game):
    s02 = save("SONIC2B__S02")
    assert run(driver, game,
               f"always|{s02}",                     # copied up: the overlay's copy
               f"map|{s02}",
               f"del|{s02}",
               f"map|{s02}",                        # the game folder's copy stays hidden
               f"read|{s02}",
               f"find|{SAVES}\\*",
               "stats") == [
        "ok 1 32",
        "ok 1",
        "ok",
        "not-found -1",
        "not-found",
        "SONIC2B__ALF:3,SONIC2B__S01:64",
        "deletes=1 whiteouts=1 install_write_attempts=0",
    ]
    assert overlay_saves(game) == {MARK + "SONIC2B__S02": b""}


def test_deleting_a_save_only_the_overlay_holds_leaves_no_marker(driver, game):
    s05 = save("SONIC2B__S05")
    assert run(driver, game, f"create|{s05}|x", f"del|{s05}", f"map|{s05}", "stats") == [
        "ok 0 0", "ok", "not-found -1", "deletes=1 whiteouts=0 install_write_attempts=0"]
    assert overlay_saves(game) == {}


def test_a_game_folder_file_outside_the_save_directory_still_refuses(driver, game):
    assert run(driver, game, "del|.\\resource\\gd_PC\\ADX\\title.adx",
               "del|.\\resource\\gd_PC\\SAVEDATA",            # the save directory itself
               "map|.\\resource\\gd_PC\\ADX\\title.adx") == [
        "install-is-read-only", "install-is-read-only", "ok 0"]
    assert tree(game / "overlay") == {}


def test_the_selftest_s_hidden_saves_get_no_marker(driver, game):
    """SA2_VFS_HIDE_INSTALL_SAVES=1 (the launcher's selftest): the game folder's saves are not
    there, so deleting the run's own save of the same name leaves no marker, and the next run
    without the switch reads the game folder's save as before."""
    s01 = save("SONIC2B__S01")
    assert run(driver, game, f"create|{s01}|x", f"del|{s01}", f"del|{save('SONIC2B__S02')}",
               hide=True) == ["ok 0 0", "ok", "not-found"]
    assert overlay_saves(game) == {}
    assert run(driver, game, f"map|{s01}", f"map|{save('SONIC2B__S02')}") == ["ok 0", "ok 0"]


# ------------------------------------------------------------------ the listing


def test_after_the_first_save_the_game_folder_s_saves_still_list(driver, game):
    """The defect: once the overlay had a save directory, a search read it alone."""
    assert run(driver, game,
               f"create|{save('SONIC2B__S05')}|five",
               f"find|{SAVES}\\*",
               f"find|{save('SONIC2B__S01')}",                     # one save by name
               f"find|{save('sonic2b__s02')}",
               f"find|{SAVES}\\SONIC2B__S0?") == [
        "ok 0 0",
        "SONIC2B__ALF:3,SONIC2B__S01:64,SONIC2B__S02:32,SONIC2B__S05:4",
        "SONIC2B__S01:64",
        "SONIC2B__S02:32",
        "SONIC2B__S01:64,SONIC2B__S02:32,SONIC2B__S05:4",
    ]


def test_the_overlay_wins_a_name_under_any_case_and_a_marker_hides_one(driver, game):
    saves = game / "overlay" / "resource" / "gd_PC" / "SAVEDATA"
    saves.mkdir(parents=True)
    (saves / "sonic2b__s02").write_bytes(b"o" * 5)      # the overlay's own spelling
    (saves / "SONIC2B__S10").write_bytes(b"t")
    (saves / "SONIC2B__D01").write_bytes(b"d")
    assert run(driver, game,
               f"del|{save('SONIC2B__S01')}",
               f"find|{SAVES}\\*",
               f"find|{save('SONIC2B__S02')}",
               f"read|{save('SONIC2B__S02')}") == [
        "ok",
        "SONIC2B__ALF:3,SONIC2B__D01:1,sonic2b__s02:5,SONIC2B__S10:1",
        "sonic2b__s02:5",
        "ok 5 ooooo",
    ]


def test_only_the_save_directory_merges_and_another_lists_the_layer_the_map_chose(driver, game):
    """Outside the save directory nothing changed: a search lists the one layer the map
    chose, the overlay's directory when it has one. The game searches its own folder at boot,
    and a merge there handed it the game folder's root files (installscript.vdf,
    steam_appid.txt), which it then opened and the selftest refused (the review of
    2026-10-12)."""
    adx = game / "install" / "resource" / "gd_PC" / "ADX"
    (adx / "B_1").write_bytes(b"1")
    (adx / "bz2").write_bytes(b"22")
    over = game / "overlay" / "resource" / "gd_PC" / "ADX"
    over.mkdir(parents=True)
    (over / "extra.adx").write_bytes(b"e")
    (over / "TITLE.ADX").write_bytes(b"overlay")
    got = run(driver, game,
              "find|.\\resource\\gd_PC\\ADX\\*",
              "find|.\\resource\\gd_PC\\nowhere\\*",
              "find|.\\resource\\gd_PC\\ADX\\nothing")
    assert sorted(got[0].split(",")) == ["TITLE.ADX:7", "extra.adx:1"]   # the overlay alone
    assert got[1:] == ["not-found", "not-found"]
