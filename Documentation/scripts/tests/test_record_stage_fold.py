"""The re-take tool and the recorder after the word log: the fold review of U2's tests (2026-09-25)."""
import json

import test_record_stage as TRS
from test_record_stage import vanilla, kept_log  # noqa: F401  (fixtures)

R = TRS.R


def test_v12_a_log_its_run_refused_is_not_rebuilt_and_the_masks_stay_apart(vanilla, kept_log):
    TRS.capture("a")
    TRS.capture("b", tick_change={(3, "d_01760000"): "0x0bad0bad"})
    TRS.built_from_log("a", "1" * 64)
    TRS.built_from_log("b", "2" * 64)
    TRS.keep_log("a", kept_log)
    TRS.keep_log("b", kept_log)
    path = R.CAPTURES / "b" / "receipt.json"
    rec = json.loads(path.read_text())
    for a in rec["artifacts"]:
        if a.get("file") == kept_log.name:
            a["check"].update(ok=False, problems=["frame_count differs from the log"])
    path.write_text(json.dumps(rec))
    got = R.compare_to("a", "b")
    assert got["masks_same"] is False
    assert got["digests"].startswith("not rebuilt from the word logs:")
    assert "its run refused it" in got["digests"]
