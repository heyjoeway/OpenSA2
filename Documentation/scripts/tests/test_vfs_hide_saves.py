"""The runtime's file layer hides the installation's saves under SA2_VFS_HIDE_INSTALL_SAVES=1.

runtime/sa2_files.c is compiled with a small driver against a scratch installation holding two
saves and a scratch overlay, and the driver's answers are compared with literal expectations,
once with the switch and once without (the column that shows the answers move with it). The
launcher's selftest sets the switch (host/launch/sa2_launch.cpp), so the game runs on no save of
the player's; play does not.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
FILES_C = ROOT / "runtime" / "sa2_files.c"

DRIVER = r"""
#include <stdio.h>
#include "sa2_files.h"

static const char *S01 = ".\\resource\\gd_PC\\SAVEDATA\\SONIC2B__S01";

int main(void) {
    char host[SA2_VFS_PATH_MAX];
    int layer = 0, status = 0, existed = 0;
    sa2_vfs_config("C:\\SA2");
    int rc = sa2_vfs_map(S01, host, sizeof host, NULL, 0, &layer);
    printf("map_save %s %d\n", sa2_vfs_status_name(rc), layer);
    uint32_t h = sa2_vfs_open(S01, SA2_VFS_ACCESS_READ, SA2_VFS_OPEN_EXISTING, &status, &existed);
    printf("open_save %s\n", sa2_vfs_status_name(h ? SA2_VFS_OK : status));
    if (h) sa2_vfs_close(h);
    sa2_vfs_dirent e;
    h = sa2_vfs_find_first(".\\resource\\gd_PC\\SAVEDATA\\*", &e, &status);
    printf("find_saves %s\n", h ? e.name : sa2_vfs_status_name(status));
    if (h) sa2_vfs_close(h);
    rc = sa2_vfs_map(".\\resource\\gd_PC\\SAVEDATA", host, sizeof host, NULL, 0, &layer);
    printf("map_dir %s %d\n", sa2_vfs_status_name(rc), layer);
    rc = sa2_vfs_map(".\\resource\\gd_PC\\ADX\\title.adx", host, sizeof host, NULL, 0, &layer);
    printf("map_other %s %d\n", sa2_vfs_status_name(rc), layer);
    static const char *const more[][2] = {
        {"map_case", ".\\RESOURCE\\GD_PC\\savedata\\sonic2b__s01"},
        {"map_dotdot", ".\\resource\\gd_PC\\ADX\\..\\SAVEDATA\\SONIC2B__S01"},
        {"map_x", ".\\resource\\gd_PC\\SAVEDATAX\\a"},
        {"map_other_dir", ".\\resource\\gd_PC\\other\\SAVEDATA\\a"},
        {"map_dup", ".\\resource\\gd_PC\\SAVEDATA\\dup"},
    };
    for (unsigned i = 0; i < sizeof more / sizeof more[0]; i++) {
        rc = sa2_vfs_map(more[i][1], host, sizeof host, NULL, 0, &layer);
        printf("%s %s %d\n", more[i][0], sa2_vfs_status_name(rc), rc == SA2_VFS_OK ? layer : -1);
    }
    rc = sa2_vfs_delete(".\\resource\\gd_PC\\SAVEDATA\\SONIC2B__S02");
    printf("delete_save %s\n", sa2_vfs_status_name(rc));
    h = sa2_vfs_open(S01, SA2_VFS_ACCESS_READ | SA2_VFS_ACCESS_WRITE, SA2_VFS_OPEN_ALWAYS, &status,
                     &existed);
    uint64_t size = 0;
    if (h) sa2_vfs_size(h, &size);
    printf("open_always %s %d %llu\n", sa2_vfs_status_name(h ? SA2_VFS_OK : status), existed,
           (unsigned long long)size);
    if (h) sa2_vfs_close(h);
    rc = sa2_vfs_map(S01, host, sizeof host, NULL, 0, &layer);
    printf("map_save_after %s %d\n", sa2_vfs_status_name(rc), layer);
    h = sa2_vfs_find_first(".\\resource\\gd_PC\\SAVEDATA\\*", &e, &status);
    printf("find_after %s\n", h ? e.name : sa2_vfs_status_name(status));
    if (h) sa2_vfs_close(h);
    return 0;
}
"""


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    cc = shutil.which("cc")
    assert cc, "no C compiler (cc) on PATH"
    work = tmp_path_factory.mktemp("vfs-hide")
    (work / "driver.c").write_text(DRIVER)
    exe = work / "driver"
    p = subprocess.run([cc, "-std=gnu11", "-O1", "-Wall", "-I", str(ROOT / "runtime"),
                        str(work / "driver.c"), str(FILES_C), "-o", str(exe)],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return exe


def answers(driver: Path, tmp_path: Path, hide: bool) -> dict[str, str]:
    install, overlay = tmp_path / "install", tmp_path / "overlay"
    saves = install / "resource" / "gd_PC" / "SAVEDATA"
    saves.mkdir(parents=True)
    (saves / "SONIC2B__S01").write_bytes(b"\1" * 64)
    (saves / "SONIC2B__S02").write_bytes(b"\2" * 64)
    (install / "resource" / "gd_PC" / "ADX").mkdir()
    (install / "resource" / "gd_PC" / "ADX" / "title.adx").write_bytes(b"adx")
    (saves / "Dup").write_bytes(b"d")          # two names differing only in case
    (saves / "DUP").write_bytes(b"D")
    for near in ("SAVEDATAX", "other/SAVEDATA"):   # look alike, and must stay visible
        (install / "resource" / "gd_PC" / near).mkdir(parents=True)
        (install / "resource" / "gd_PC" / near / "a").write_bytes(b"a")
    env = {k: v for k, v in os.environ.items() if not k.startswith("SA2_")}
    env.update(SA2_INSTALL=str(install), SA2_VFS_WRITE_ROOT=str(overlay))
    if hide:
        env["SA2_VFS_HIDE_INSTALL_SAVES"] = "1"
    p = subprocess.run([str(driver)], capture_output=True, text=True, env=env, cwd=tmp_path)
    assert p.returncode == 0, p.stderr
    assert (saves / "SONIC2B__S01").read_bytes() == b"\1" * 64        # the installation is input
    return dict(line.split(" ", 1) for line in p.stdout.splitlines())


def test_the_installation_s_saves_are_hidden_and_the_overlay_s_are_not(driver, tmp_path):
    assert answers(driver, tmp_path, hide=True) == {
        "map_save": "not-found -1",
        "map_case": "not-found -1",            # another case of the same save
        "map_dotdot": "not-found -1",          # the same save through ".."
        "map_x": "ok 0",                       # SAVEDATAX is another directory
        "map_other_dir": "ok 0",               # and so is a SAVEDATA elsewhere
        "map_dup": "not-found -1",             # hidden before the installation can refuse it
        "open_save": "not-found",
        "find_saves": "not-found",
        "map_dir": "ok 0",                   # the directory itself stays
        "map_other": "ok 0",                 # and every file outside it
        "delete_save": "not-found",          # not a refusal: there is nothing to delete
        "open_always": "ok 0 0",             # a new, empty save: nothing copied up
        "map_save_after": "ok 1",            # the run's own save, in the overlay
        "find_after": "SONIC2B__S01",          # the listing holds only the overlay's save
    }


def test_without_the_switch_the_installation_s_saves_are_read(driver, tmp_path):
    got = answers(driver, tmp_path, hide=False)
    assert got["find_saves"] in {"SONIC2B__S01", "SONIC2B__S02", "Dup", "DUP"}
    del got["find_saves"]                    # readdir's order is the file system's
    # both layers merged: the overlay's SONIC2B__S01 and the installation's Dup and DUP (its
    # SONIC2B__S02 is deleted), in the order Windows lists a directory (runtime/sa2_files.c)
    assert got.pop("find_after") == "DUP"
    assert got == {
        "map_save": "ok 0",
        "map_case": "ok 0",
        "map_dotdot": "ok 0",
        "map_x": "ok 0",
        "map_other_dir": "ok 0",
        "map_dup": "ambiguous-case -1",
        "open_save": "ok",
        "map_dir": "ok 0",
        "map_other": "ok 0",
        "delete_save": "ok",                 # a whiteout in the overlay (test_vfs_whiteout.py)
        "open_always": "ok 1 64",            # copied up from the installation
        "map_save_after": "ok 1",
    }
