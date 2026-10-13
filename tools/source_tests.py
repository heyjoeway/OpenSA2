"""Explicit scope for test_source.py; full platform tests remain available."""
from __future__ import annotations

PRIVATE_BASELINES = {
    "test_a_game_folder_with_no_user_config_gets_the_defaults_in_the_overlay",
    "test_a_game_folders_own_config_files_are_left_alone",
    "test_an_overlay_config_file_is_kept_and_its_folder_spelling_too",
    "test_the_zip_route_writes_the_defaults_when_the_archive_has_none",
    "test_a_game_folder_with_no_user_config_runs_on_the_installers_defaults",
}
WINDOWS_CROSS_BUILD = {
    "test_the_launcher_compiles_for_windows",
    "test_windows_gets_a_strong_observer_and_a_recorder_with_o_cloexec",
}


def pytest_collection_modifyitems(config, items):
    selected, platform = [], []
    for item in items:
        needs_local_inputs = (item.originalname in PRIVATE_BASELINES | WINDOWS_CROSS_BUILD
                              or "release_sdl" in item.fixturenames)
        (platform if needs_local_inputs else selected).append(item)
    if platform:
        config.hook.pytest_deselected(items=platform)
    items[:] = selected


def pytest_report_header(config):
    return "Source scope: synthetic component tests; local installation, SDL3 dialogs, and Windows cross-build cases are outside this run."
