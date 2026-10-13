"""The native outcomes, the device set they depend on, and M4's pre-Init reach.

`tools/s4/native_outcomes.py` reads Wine-traced native boots into
`Documentation/receipts/s4-native-outcomes-v1.json`: what the native game gets
where the runtime refuses, and which imports it calls before the Mod Loader's
Init. `tools/s4/native_imports.py replay_profile` ties a replay's native runs
and lifted run to one recorded device profile and to the traced boots' host,
and `tools/s4/import_status.py` adds the pre-Init imports to M4's reached set.
Each test below names a way one of those could say more than its evidence.

Every device value here is synthetic: GUIDs of the form
{00000000-0000-0000-0000-0000000000xx}, VID:PID 1234:5678, and names like
"Example Receiver". The recording host's devices are host inventory, and no
committed file carries them (the 2026-09-23 review found four of them here).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
import device_profile as DP  # noqa: E402
import import_status as IS  # noqa: E402
import native_imports as NI  # noqa: E402
import native_outcomes as NO  # noqa: E402
from native_outcomes import ANY, MAIN, STREAM, Refused, Step  # noqa: E402

KBD = "{00000000-0000-0000-0000-000000000000}"   # GUID_SysKeyboard, an image constant
DEVICE = "{00000000-0000-0000-0000-000000000000}"
CACHED = "{00000000-0000-0000-0000-000000000000}"


def write_log(root: Path, key: str, lines: list[str]) -> NO.Log:
    d = root / NO.CAPTURES / key
    d.mkdir(parents=True, exist_ok=True)
    (d / NO.LOG_NAME).write_text("\n".join(lines) + "\n")
    return NO.read_log(key, root)


LOG = [
    '0024:trace:loaddll:build_module Loaded L"Z:\\\\g\\\\sonic2app-unpacked.exe" at 00400000: native',
    "0024:trace:ole:CoInitializeEx 00000000, 0x2",
    "0024:trace:ole:CoCreateInstance {x}, 00000000, 0x1, {y}, 0287F52C.",
    "0030:trace:ole:CoInitializeEx 00000000, 0x2",
    "0024:trace:dinput:DirectInput8Create hinst 00400000, version 0x800, out 051B25C4.",
    "0024:trace:dinput:dinput8_a_CreateDevice impl 028826A0, guid " + KBD + ".",
    "0024:trace:dinput:dinput8_a_CreateDevice impl 028826A0, guid " + DEVICE + ".",
    "0024:trace:xaudio2:IXAudio2Impl_CreateSourceVoice Created source voice: 0293F958",
    "01d0:trace:xaudio2:XA2SRC_GetState 0293F958, 0AF6FEE8, 0x0",
    "01d0:trace:xaudio2:XA2SRC_GetState 0293F958, 0AF6FEE8, 0x0",
    '0024:trace:loaddll:build_module Loaded L"Z:\\\\g\\\\mods\\\\probe\\\\probe.dll" at 74000000: native',
]


def test_steps_cite_in_order_and_captures_carry_forward(tmp_path: Path) -> None:
    log = write_log(tmp_path, "k", LOG)
    ev, counts, ctx, _ = NO.run_steps(log, (
        Step("the call", "trace:dinput:DirectInput8Create", r"hinst 00400000, .*"),
        Step("through it", "trace:dinput:dinput8_a_CreateDevice",
             r"impl (?P<di>\w+), guid \{00000000-0000-0000-0000-000000000000\}\."),
        Step("the same interface again", "trace:dinput:dinput8_a_CreateDevice",
             r"impl <<di>>, guid .*", count=(2, 2)),
    ), "0024", ticks=0, root=tmp_path)
    assert [e["line"] for e in ev] == [5, 6, 7]
    assert ctx["di"] == "028826A0"
    assert counts == [{"log": log.rel, "fn": "trace:dinput:dinput8_a_CreateDevice",
                       "rule": r"impl <<di>>, guid .*", "thread": MAIN, "matches": 2,
                       "bounds": [2, 2], "shows": "the same interface again"}]


def test_a_step_after_the_cursor_takes_the_next_line_not_an_earlier_one(tmp_path: Path) -> None:
    log = write_log(tmp_path, "k", LOG)
    ev, _, _, _ = NO.run_steps(log, (
        Step("a", "trace:dinput:dinput8_a_CreateDevice", r"impl \w+, guid .*"),
        Step("b", "trace:dinput:dinput8_a_CreateDevice", r"impl \w+, guid .*"),
    ), "0024", ticks=0, root=tmp_path)
    assert [e["line"] for e in ev] == [6, 7]


def test_prev_is_the_threads_own_previous_line(tmp_path: Path) -> None:
    """CoInitialize is attributed by adjacency on the game thread; another
    thread's identical line between them must not count."""
    log = write_log(tmp_path, "k", LOG)
    ev, _, _, _ = NO.run_steps(log, (
        Step("create", "trace:ole:CoCreateInstance", r"\{x\}.*"),
        Step("init", "trace:ole:CoInitializeEx", r"00000000, 0x2", at="prev"),
    ), "0024", ticks=0, root=tmp_path)
    assert [e["line"] for e in ev] == [3, 2]
    with pytest.raises(Refused, match="the line after line 3 on its thread is not"):
        NO.run_steps(log, (
            Step("create", "trace:ole:CoCreateInstance", r"\{x\}.*"),
            Step("init", "trace:ole:CoInitializeEx", r"00000000, 0x2", at="next"),
        ), "0024", ticks=0, root=tmp_path)


def test_a_count_outside_its_bounds_is_refused(tmp_path: Path) -> None:
    log = write_log(tmp_path, "k", LOG)
    with pytest.raises(Refused, match=r"2 trace:dinput:dinput8_a_CreateDevice lines .* outside \[1, 1\]"):
        NO.run_steps(log, (Step("one", "trace:dinput:dinput8_a_CreateDevice", r".*",
                                count=(1, 1)),), "0024", ticks=0, root=tmp_path)


def test_a_zero_on_an_untraced_channel_proves_nothing(tmp_path: Path) -> None:
    log = write_log(tmp_path, "k", LOG)
    with pytest.raises(Refused, match="xinput channel is not traced"):
        NO.run_steps(log, (Step("none", "trace:xinput:XInputGetState", r".*", thread=ANY,
                                at="count", count=(0, 0)),), "0024", ticks=0, root=tmp_path)


def test_the_streaming_thread_is_the_one_other_thread_calling_the_voice(tmp_path: Path) -> None:
    log = write_log(tmp_path, "k", LOG)
    _, counts, _, roles = NO.run_steps(log, (
        Step("voice", "trace:xaudio2:IXAudio2Impl_CreateSourceVoice",
             r"Created source voice: (?P<voice>\w+)"),
        Step("polls", "trace:xaudio2:XA2SRC_GetState", r"<<voice>>, .*", thread=STREAM,
             count=(1, None)),
    ), "0024", ticks=0, root=tmp_path)
    assert roles[STREAM] == "01d0" and counts[0]["matches"] == 2
    two = LOG + ["01d4:trace:xaudio2:XA2SRC_GetState 0293F958, 0AF6FEE8, 0x0"]
    log = write_log(tmp_path, "k2", two)
    with pytest.raises(Refused, match="2 threads other than"):
        NO.run_steps(log, (
            Step("voice", "trace:xaudio2:IXAudio2Impl_CreateSourceVoice",
                 r"Created source voice: (?P<voice>\w+)"),
            Step("polls", "trace:xaudio2:XA2SRC_GetState", r"<<voice>>, .*", thread=STREAM),
        ), "0024", ticks=0, root=tmp_path)


def test_the_main_thread_and_the_init_boundary_come_from_the_log(tmp_path: Path) -> None:
    log = write_log(tmp_path, "k", LOG)
    assert NO.main_thread(log) == "0024"
    assert NO.mod_init(log, "0024", {"inputs": {"enabled_mods": ["probe"]}}) == len(LOG)
    with pytest.raises(Refused, match="never reaches the Mod Loader's Init"):
        NO.mod_init(log, "0024", {"inputs": {"enabled_mods": ["other"]}})
    bare = write_log(tmp_path, "k3", LOG[1:3])
    with pytest.raises(Refused, match="no thread is the game's"):
        NO.main_thread(bare)


# ---------------------------------------------------------------- the enumerated device

ENUMERATION = next(o for o in NO.OUTCOMES if o.id == "dinput8-enumeration").runs[0][1]
PATH = r'L"\\\\?\\hid#vid_1234&pid_5678#0&0000#' + DEVICE + '"'


def enumeration_log(created: str = DEVICE) -> list[str]:
    """Wine's joystick scan as the traced boot prints it: every cached registry
    instance first, attached or not, then the device it opened, then the game's
    callback creating one device."""
    return [
        "0024:trace:dinput:dinput8_a_EnumDevices impl 028826A0, type 0x4, callback 00864C90, "
        "ref 051B25C0, flags 0x1.",
        "0024:trace:dinput:insert_cache_entry Created instance product: abcd:0001, instance: "
        + CACHED + ', path L"", joy_id 1',
        "0024:trace:dinput:insert_cache_entry Created instance product: 1234:5678, instance: "
        + DEVICE + ', path L"", joy_id 0',
        "0024:trace:dinput:hid_joystick_device_try_open detected device type 0x00010214",
        "0024:trace:dinput:insert_cache_entry Reusing instance product: 1234:5678, instance: "
        + DEVICE + ", path " + PATH + ", joy_id 0",
        "0024:trace:dinput:dinput8_a_CreateDevice impl 028826A0, guid " + created
        + ", out 0287F5C8, outer 00000000.",
        "0024:trace:dinput:dinput_device_a_Unacquire impl 02935E60.",
        "0024:trace:dinput:dinput_device_a_Release impl 02935E60.",
    ]


def test_the_enumerated_device_is_the_one_wine_opened_not_the_first_cached(tmp_path) -> None:
    """The registry cache prints a "Created instance" line for every cached
    instance; the device the enumeration reports is the one opened, whose
    dwDevType and VID:PID are adjacent (2026-09-23 review, finding 10)."""
    log = write_log(tmp_path, "k", enumeration_log())
    ev, counts, ctx, _ = NO.run_steps(log, ENUMERATION, "0024", ticks=0, root=tmp_path)
    assert ctx["dev_type"] == "0x00010214" and "product" not in ctx
    assert [e["line"] for e in ev] == [1, 4, 5, 6, 7, 8]
    assert [c["matches"] for c in counts] == [1]
    # The VID:PID is host inventory (2026-09-23 review, N4): redacted like the
    # instance GUID and the path, and never captured.
    assert ev[2]["text"] == ("0024:trace:dinput:insert_cache_entry Reusing instance product: "
                             '<redacted>, instance: {redacted}, path L"<redacted>", joy_id 0')


def test_a_created_device_other_than_the_opened_one_is_refused(tmp_path: Path) -> None:
    log = write_log(tmp_path, "k", enumeration_log(created=CACHED))
    with pytest.raises(Refused, match="no trace:dinput:dinput8_a_CreateDevice line matching"):
        NO.run_steps(log, ENUMERATION, "0024", ticks=0, root=tmp_path)


# ---------------------------------------------------------------- D3DX's constant tables

D3DX = next(o for o in NO.OUTCOMES if o.id == "d3dx-constant-table")


def d3dx_log(extra: list[str] = (), tid: str = "0024") -> list[str]:
    """Two D3DXGetShaderConstantTable calls as Wine's +d3dx prints them, a
    vertex shader's then a pixel shader's, and nothing called on either table."""
    def call(code: str, version: str, target: str) -> list[str]:
        return [
            f"{tid}:trace:d3dx:D3DXGetShaderConstantTable ({code}, 0287FACC): Forwarded to "
            "D3DXGetShaderConstantTableEx",
            f"{tid}:trace:d3dx:D3DXGetShaderConstantTableEx byte_code {code}, flags 0, "
            "constant_table 0287FACC.",
            f"{tid}:trace:d3dx:D3DXFindShaderComment byte_code {code}, fourcc 0x42415443, "
            "data 0287FA54, size 0287FA58.",
            f"{tid}:trace:d3dx:D3DXFindShaderComment Returning comment data at 039171FC with "
            "size 136",
            f'{tid}:trace:d3dx:D3DXGetShaderConstantTableEx Creator "Example HLSL Compiler", '
            f'Version {version}, Constants 1, Target "{target}".',
            f"{tid}:trace:d3dx:parse_ctab_constant_type name g_Example, elements 1, index 0, "
            "defaultvalue 00000000, regset D3DXRS_FLOAT4",
        ]
    return (call("039171F0", "0xfffe0300", "vs_3_0") + call("051B9560", "0xffff0300", "ps_3_0")
            + list(extra))


def test_a_table_every_call_gets_and_no_method_called_is_what_the_d3dx_rules_show(tmp_path):
    log = write_log(tmp_path, "k", d3dx_log())
    ev, counts, ctx, _ = NO.run_steps(log, D3DX.runs[0][1], "0024", ticks=None, root=tmp_path)
    assert [e["line"] for e in ev] == [1, 2, 3, 4, 5, 7, 8, 9, 10, 11]
    assert [(c["fn"], c["thread"], c["matches"], c["bounds"]) for c in counts] == [
        ("trace:d3dx:D3DXGetShaderConstantTable", MAIN, 2, [1, None]),
        ("trace:d3dx:D3DXGetShaderConstantTableEx", MAIN, 2, [2, 2]),
        ("[!t]*:d3dx:D3DXGetShaderConstantTable*", ANY, 0, [0, 0]),
        ("*:d3dx:ID3DXConstantTableImpl_*", ANY, 0, [0, 0])]
    assert ctx["calls"] == "2"


@pytest.mark.parametrize("extra,why", [
    # A method called on a table, on any thread, from any debug class.
    (["01d0:trace:d3dx:ID3DXConstantTableImpl_Release iface 05215E60."],
     r"1 \*:d3dx:ID3DXConstantTableImpl_\* lines .* outside \[0, 0\]"),
    (["0024:fixme:d3dx:ID3DXConstantTableImpl_SetDefaults iface 05215E60, stub."],
     r"1 \*:d3dx:ID3DXConstantTableImpl_\* lines .* outside \[0, 0\]"),
    # A call whose Ex function never reached the table's header.
    (["0024:trace:d3dx:D3DXGetShaderConstantTable (0, 0287FACC): Forwarded to "
      "D3DXGetShaderConstantTableEx"], r"2 trace:d3dx:D3DXGetShaderConstantTableEx lines .* "
                                      r"outside \[3, 3\]"),
    (["0024:warn:d3dx:D3DXGetShaderConstantTableEx Invalid byte_code specified."],
     r"1 \[!t\]\*:d3dx:D3DXGetShaderConstantTable\* lines .* outside \[0, 0\]"),
])
def test_a_method_called_or_a_call_without_a_table_is_refused(tmp_path, extra, why) -> None:
    log = write_log(tmp_path, "k", d3dx_log(extra))
    with pytest.raises(Refused, match=why):
        NO.run_steps(log, D3DX.runs[0][1], "0024", ticks=None, root=tmp_path)


def test_a_d3dx_only_log_names_the_one_thread_calling_the_import_as_the_games(tmp_path) -> None:
    assert NO.main_thread(write_log(tmp_path, "k", d3dx_log())) == "0024"
    two = d3dx_log() + ["01d0:trace:d3dx:D3DXGetShaderConstantTable (039171F0, 0AF6FEE8): "
                        "Forwarded to D3DXGetShaderConstantTableEx"]
    with pytest.raises(Refused, match="2 threads call D3DXGetShaderConstantTable, not one"):
        NO.main_thread(write_log(tmp_path, "k2", two))


def test_a_stage_trace_is_read_only_at_the_hash_its_receipt_checked(tmp_path) -> None:
    d = tmp_path / NO.CAPTURES / "k"
    d.mkdir(parents=True)
    text = ("tick,current_level,game_mode,game_state,ingame_frames,player,x,y,z\n"
            "1,0x0000,0x00000002,0x0000,0x00000000,0,0,0,0\n"
            "2,0x000d,0x0000000a,0x0010,0x00000001,0,0,0,0\n")
    (d / NO.STAGE_TRACE).write_text(text)
    rec = {"artifacts": [{"file": NO.STAGE_TRACE, "check": {
        "ok": True, "rows": 2, "sha256": hashlib.sha256(text.encode()).hexdigest()}}]}
    got = NO.stage_trace("k", rec, tmp_path)
    assert (got["rows"], got["in_game_rows"], got["last"]) == (2, 1, {
        "tick": "2", "current_level": "0x000d", "game_state": "0x0010",
        "ingame_frames": "0x00000001"})
    assert NO.stage_trace("k", {"artifacts": []}, tmp_path) is None
    (d / NO.STAGE_TRACE).write_text(text.replace("0x0010", "0x0007"))
    with pytest.raises(Refused, match="does not hash to the sha256"):
        NO.stage_trace("k", rec, tmp_path)


# ---------------------------------------------------------------- answers and profile records

def test_every_outcome_has_its_answer_class_and_the_profile_records_carrying_it() -> None:
    """The gate reads `answer`; `host_dependent` is derived from the records,
    which must be record kinds of the grammar the profile readers implement."""
    assert {o.id: o.answer for o in NO.OUTCOMES} == {
        "dinput8-create": "success", "dinput8-devices": "success",
        "dinput8-enumeration": "success", "wbem-scan": "success", "cocreateinstance": "success",
        "xaudio2-stream": "success", "ddraw-enumeration": "success",
        "xinput-not-connected": "failure", "dsound-create": "success",
        "d3dx-constant-table": "success"}
    kinds = NO.profile_record_kinds()
    assert all(set(o.profile) <= kinds for o in NO.OUTCOMES)
    assert "xaudio2_master" in next(o for o in NO.OUTCOMES if o.id == "xaudio2-stream").profile
    assert [o.id for o in NO.OUTCOMES if not o.profile] == ["dsound-create", "d3dx-constant-table"]
    assert "wmi_coinit" not in {r for o in NO.OUTCOMES for r in o.profile}
    assert {"end", "error"}.isdisjoint(kinds)


def test_redaction_keeps_the_images_classes_and_nothing_else() -> None:
    line = ("0024:trace:x:y guid " + KBD + ", instance " + DEVICE
            + ', path L"\\\\?\\HID#VID_1234&PID_5678#0", ns L"Win32_PNPEntity"')
    assert NO.redact(line) == ("0024:trace:x:y guid " + KBD + ", instance {redacted}"
                               + ', path L"<redacted>", ns L"Win32_PNPEntity"')
    assert NO.redact("0024:trace:dinput:insert_cache_entry Reusing instance product: "
                     "12ab:5678, instance: " + KBD) == (
        "0024:trace:dinput:insert_cache_entry Reusing instance product: <redacted>, instance: "
        + KBD)


def test_host_inventory_in_any_string_is_found() -> None:
    """A host HID device's VID:PID is inventory wherever it appears, drawn from
    the recorded BUS:VENDOR:PRODUCT; one no host device has is not (2026-09-23
    review, N4: the receipt carried the keyboard receiver's twice)."""
    hid = ["0003:00001234:00005678 Example Receiver"]
    assert NO.inventory_leaks({"a": ["abcd:0001", KBD, "0x00010214", "01234:56789"]}, hid) == []
    leaks = NO.inventory_leaks({"a": {"b": "HID\\VID_1234&PID_5678&IG_00"},
                                "c": "the Example Receiver", "d": DEVICE,
                                "e": "0003:00001234:00005678", "f": ["1234:5678"],
                                "g": "Reusing instance product: 1234:5678, instance"}, hid)
    assert [x.split(" in ")[0] for x in leaks] == [
        "a device ID", "host HID device 'Example Receiver'",
        "GUID " + DEVICE, "host HID device '0003:00001234:00005678'",
        "host HID VID:PID '1234:5678'", "host HID VID:PID '1234:5678'"], leaks


def test_the_hid_digest_does_not_depend_on_order() -> None:
    a = ["0003:0000ABCD:00000001 Example Keyboard", "0003:00001234:00005678 Example Receiver"]
    assert NO.hid_digest(a) == NO.hid_digest(list(reversed(a)))
    assert NO.hid_digest(a) != NO.hid_digest(a[:1])


# ---------------------------------------------------------------- the real receipt

REAL = all((ROOT / NO.CAPTURES / k / NO.LOG_NAME).is_file() for k in NO.LOGS) \
    and NO.exe_path().is_file() and NO.RECEIPT.is_file()
needs_private = pytest.mark.skipif(not REAL, reason="the traced native boots or the "
                                                    "executable are not on this machine")


@needs_private
def test_the_tracked_receipt_is_what_the_logs_give() -> None:
    assert NO.check() == []


@needs_private
def test_the_receipt_says_what_the_ruling_needs() -> None:
    doc = NO.load()
    ids = {o["id"]: o for o in doc["outcomes"]}
    assert ids["dinput8-create"]["outcome"] == "succeeds"
    assert ids["xinput-not-connected"]["outcome"] == "not-connected"
    assert ids["xinput-not-connected"]["answer"] == "failure"
    assert ids["ddraw-enumeration"]["outcome"] == "loads"
    assert "ole32.dll!CoCreateInstance" in ids["cocreateinstance"]["imports"]
    assert {"DINPUT8.dll!DirectInput8Create", "ole32.dll!CoCreateInstance",
            "ole32.dll!CoSetProxyBlanket", "OLEAUT32.dll!Ordinal#2",
            "OLEAUT32.dll!Ordinal#6", "ole32.dll!CoInitialize",
            "ole32.dll!CoUninitialize"} <= set(NO.pre_init_imports(doc))
    # One device, recorded as a dwDevType and nothing else: its VID:PID is host
    # inventory (2026-09-23 review, N4).
    enumerated = doc["devices"]["enumerated"]
    assert len(enumerated) == doc["devices"]["gamectrl"] == 1
    assert [sorted(d) for d in enumerated] == [["dev_type"]]
    assert re.fullmatch(r"0x[0-9a-f]+", enumerated[0]["dev_type"])
    assert all(o["host_dependent"] is bool(o["profile_records"]) for o in doc["outcomes"])
    boundaries = {e["key"]: (e["mod_init"] or {}).get("line") for e in doc["logs"]}
    for p in doc["native_pre_init"]["imports"]:
        for e in p["evidence"]:
            key = next(k for k in boundaries if f"/{k}/" in e["log"])
            assert e["line"] < boundaries[key], (p["import"], e["line"])


@needs_private
def test_the_d3dx_outcome_is_measured_through_gameplay() -> None:
    """Both +d3dx logs: the import's one call site, a table header for every
    call, and no method line on any thread, with the second log's stage trace
    ending in City Escape (level 0x000d) being played (state 0x0010)."""
    o = NO.outcome(NO.load(), "d3dx-constant-table")
    assert (o["answer"], o["host_dependent"], o["call_sites"]) == ("success", False,
                                                                   ["0x00867225"])
    for key in ("native-outcomes-v1c", "native-outcomes-v1d"):
        mine = {c["fn"]: c for c in o["counts"] if f"/{key}/" in c["log"]}
        calls = mine["trace:d3dx:D3DXGetShaderConstantTable"]["matches"]
        assert calls > 0 and mine["trace:d3dx:D3DXGetShaderConstantTableEx"]["matches"] == calls
        assert mine["*:d3dx:ID3DXConstantTableImpl_*"]["thread"] == ANY
        assert mine["*:d3dx:ID3DXConstantTableImpl_*"]["matches"] == 0
    assert [(r["current_level"], r["game_state"]) for r in o["reaches"]] == [("0x000d", "0x0010")]


@needs_private
def test_the_enumerated_device_is_the_one_the_callback_created() -> None:
    """Read back from the private log, so no instance GUID is written here: the
    Reusing line directly follows the dwDevType line, and the CreateDevice the
    receipt cites names the instance GUID the Reusing line does."""
    ev = NO.outcome(NO.load(), "dinput8-enumeration")["evidence"]
    lines = (ROOT / ev[0]["log"]).read_text().split("\n")
    typed, reusing, created = (e["line"] for e in ev[1:4])
    assert reusing == typed + 1
    guid = re.compile(NO.GUID_ANY)
    assert guid.findall(lines[reusing - 1])[0] == guid.findall(lines[created - 1])[0]


@needs_private
@pytest.mark.parametrize("edit,why", [
    (lambda d: d["outcomes"][0]["evidence"][0].__setitem__(
        "text", d["outcomes"][0]["evidence"][0]["text"] + " (edited)"), "not the cited"),
    (lambda d: d["outcomes"][0]["evidence"][0].__setitem__(
        "line", d["outcomes"][0]["evidence"][0]["line"] + 1), "not the cited"),
    (lambda d: d["logs"][0].__setitem__("sha256", "0" * 64), "no longer hashes to its pin"),
    (lambda d: d["devices"].__setitem__("gamectrl", 0), "is not what"),
    (lambda d: d["outcomes"][0].__setitem__("answer", "failure"), "is not what"),
    (lambda d: d["outcomes"][0].__setitem__("host_dependent", False), "is not what"),
    (lambda d: d["outcomes"][0]["evidence"][0].__setitem__(
        "text", "HID\\VID_1234&PID_5678&IG_00"), "host inventory"),
])
def test_a_receipt_that_drifted_from_its_logs_is_refused(tmp_path: Path, edit, why) -> None:
    doc = json.loads(NO.RECEIPT.read_text())
    edit(doc)
    p = tmp_path / "outcomes.json"
    p.write_text(NO.render(doc))
    assert any(why in x for x in NO.check(p)), NO.check(p)


# ---------------------------------------------------------------- the device profile

HID = ["0003:00001234:00005678 Example Receiver", "0003:0000ABCD:00000001 Example Keyboard"]
OUTCOMES = {"host": {"hid_devices": {"count": 2, "sha256": NO.hid_digest(HID)}},
            "devices": {"gamectrl": 1}}
RECEIVER = DP.GameCtrl(
    instance=bytes.fromhex("000000000000000000000000000000a1"),
    product=bytes.fromhex("000000000000000000000000000000b1"),
    ffdriver=bytes(16), devtype=0x00010214, usage_page=1, usage=6,
    instance_name=b"Example Receiver", product_name=b"Example Receiver", create=0)
ONE = DP.dump(dataclasses.replace(DP.default(), gamectrl=(RECEIVER,)))
OTHER = DP.dump(dataclasses.replace(DP.default(), gamectrl=(RECEIVER,), vidmem_total=0x08000000))
NONE = DP.dump(DP.default())
PAD = DP.dump(dataclasses.replace(DP.default(), gamectrl=(RECEIVER,),
                                  xinput=(0,) + (DP.NOT_CONNECTED,) * 3))
PROBE_SOURCE = b"/* a stand-in for harness/replay/native_device_profile.c */\n"
PROFILE_MOD = "device_profile"          # tools/oracle/run_oracle_probe.py PROFILE_MOD


def counts_of(profile: bytes) -> dict:
    try:
        return DP.counts(DP.parse(profile))
    except DP.Refusal:
        return {"gamectrl": 1, "xinput_connected": 1, "wmi_pnp": 0, "ddraw": 1}


def profile_entry(root: Path, profiles: list[bytes | None], hid: list | None = HID,
                  lifted: object = "same", counts: dict | None = None,
                  probe: bytes = PROBE_SOURCE) -> dict:
    """A registered replay whose runs' receipts, profile files and lifted record
    live in root, each run a capture directory as the oracle runner writes one."""
    (root / DP.PROBE).parent.mkdir(parents=True, exist_ok=True)
    (root / DP.PROBE).write_bytes(PROBE_SOURCE)
    runs = []
    for i, text in enumerate(profiles):
        d = root / f"run{i}"
        d.mkdir()
        session: dict = {"host_hid_devices": hid} if hid is not None else {}
        if text is not None:
            (d / DP.FILE).write_bytes(text)
            session["device_profile"] = {"file": DP.FILE,
                                         "sha256": hashlib.sha256(text).hexdigest(),
                                         "counts": counts or counts_of(text)}
        # As tools/oracle/run_oracle_probe.py writes a --device-profile run: the
        # profile probe built last as mod PROFILE_MOD ("device_profile") and
        # loaded last, which DP.capture_profile requires.
        (d / "receipt.json").write_text(json.dumps({
            "session": session,
            "cwd": "/home/user/x/oracle/game",
            "command": "proton runinprefix ./sonic2app-unpacked.exe",
            "inputs": {"built_probes": [{"mod_id": PROFILE_MOD, "source": DP.PROBE,
                                         "source_sha256": hashlib.sha256(probe).hexdigest()}],
                       "enabled_mods": [PROFILE_MOD]}}))
        runs.append(f"run{i}/receipt.json")
    used = profiles[0] if lifted == "same" else lifted
    rec = root / NI.TICK_REPLAY
    rec.parent.mkdir(parents=True, exist_ok=True)
    record = ({"path": "run0/" + DP.FILE, "sha256": hashlib.sha256(used).hexdigest()}
              if isinstance(used, bytes) else used)
    rec.write_text(json.dumps({"replays": [{"name": "r", "lifted": {"device_profile": record}}]}))
    return {"name": "r", "native": {"receipt": runs[0],
                                    "repeats": [{"receipt": r} for r in runs[1:]],
                                    "imports": {"receipt": runs[0], "repeats": []}}}


def test_a_replay_whose_sides_share_one_profile_on_the_traced_host_passes(tmp_path) -> None:
    e = profile_entry(tmp_path, [ONE, ONE])
    got = NI.replay_profile(e, tmp_path, OUTCOMES)
    assert got == {"sha256": hashlib.sha256(ONE).hexdigest(),
                   "counts": DP.counts(DP.parse(ONE)),
                   "receipts": ["run0/receipt.json", "run1/receipt.json"]}
    assert got["counts"]["gamectrl"] == 1


@pytest.mark.parametrize("build,why", [
    (lambda t: profile_entry(t, [None]), "re-register the replay from those runs"),
    (lambda t: profile_entry(t, [ONE, OTHER]), "different device profiles"),
    (lambda t: profile_entry(t, [ONE], lifted=None), "lifted run that stands for it"),
    # tools/s4/tick_replay.py records a run on the default profile as the string
    # "default"; it is a different device set, refused by name, not an AttributeError.
    (lambda t: profile_entry(t, [ONE], lifted="default"), "used device profile 'default'"),
    (lambda t: profile_entry(t, [ONE], lifted=OTHER), "lifted run that stands for it"),
    (lambda t: profile_entry(t, [ONE], hid=None), "does not record the host's HID devices"),
    (lambda t: profile_entry(t, [ONE], hid=HID[:1]), "other HID devices"),
    (lambda t: profile_entry(t, [NONE], lifted=NONE), "game-controller-class"),
    # What capture_profile holds and the copy it replaced did not:
    (lambda t: profile_entry(t, [ONE], counts={"gamectrl": 1, "xinput_connected": 0,
                                               "wmi_pnp": 3, "ddraw": 1}), "receipt counts"),
    (lambda t: profile_entry(t, [ONE], probe=b"another probe\n"), "not the one in the tree"),
    (lambda t: profile_entry(t, [PAD]), "a pad's input is part of no recording"),
])
def test_a_replay_whose_device_set_is_not_one_recorded_fact_is_refused(tmp_path, build, why):
    e = build(tmp_path)
    with pytest.raises(SystemExit, match=why):
        NI.replay_profile(e, tmp_path, OUTCOMES)


def test_a_profile_file_that_changed_since_its_receipt_is_refused(tmp_path: Path) -> None:
    e = profile_entry(tmp_path, [ONE])
    (tmp_path / "run0" / DP.FILE).write_bytes(OTHER)
    with pytest.raises(SystemExit, match="its receipt records"):
        NI.replay_profile(e, tmp_path, OUTCOMES)


@pytest.mark.skipif(not (ROOT / "Documentation" / "replays.json").is_file(), reason="no registry")
def test_the_replays_registered_today_are_refused_until_recaptured() -> None:
    """They predate the device profile, and the refusal says what to do about it."""
    reg = json.loads((ROOT / "Documentation" / "replays.json").read_text())
    for e in reg["replays"]:
        rels = NI.replay_receipts(e)
        if not all((ROOT / r).is_file() for r in rels):
            pytest.skip("the registered captures' receipts are not on this machine")
        recs = [json.loads((ROOT / r).read_text()) for r in rels]
        if all((r.get("session") or {}).get("device_profile") for r in recs):
            continue
        with pytest.raises(SystemExit, match="re-capture this replay's native runs"):
            NI.replay_profile(e, ROOT, OUTCOMES)


# ---------------------------------------------------------------- M4's reach

def test_the_pre_init_imports_join_the_reached_set_under_their_own_label(tmp_path, monkeypatch):
    receipt = tmp_path / NO.RECEIPT_REL
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({
        "schema": NO.SCHEMA, "id": NO.RECEIPT_ID, "logs": [], "outcomes": [], "host": {},
        "devices": {}, "native_pre_init": {"imports": [
            {"import": "ole32.dll!CoSetProxyBlanket"}, {"import": "A.dll!f"}]}}))
    seen = {}
    monkeypatch.setattr(NI, "replay_profile",
                        lambda e, root, outcomes: seen.setdefault(e["name"], {"sha256": "p"}))
    monkeypatch.setattr(NI, "replay_calls", lambda e, table, root, probe: ["A.dll!g"])
    rows = [{"dll": "A.dll", "name": "f", "calls": 3}, {"dll": "B.dll", "name": "h", "calls": 1}]
    got = IS.replay_reach({"replays": [{"name": "boot"}]}, rows, root=tmp_path)
    assert seen == {"boot": {"sha256": "p"}}
    assert got["native_pre_init"] == ["A.dll!f", "ole32.dll!CoSetProxyBlanket"]
    assert got["reached"] == ["A.dll!f", "A.dll!g", "B.dll!h", "ole32.dll!CoSetProxyBlanket"]
    assert got["native_only"] == ["A.dll!g", "ole32.dll!CoSetProxyBlanket"]
    assert got["lifted_only"] == ["B.dll!h"]
    assert got["device_profiles"] == {"boot": {"sha256": "p"}}
    assert got["native_pre_init_source"]["receipt"] == NO.RECEIPT_REL


def test_the_profile_is_asked_before_any_log_is_counted(tmp_path, monkeypatch) -> None:
    receipt = tmp_path / NO.RECEIPT_REL
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({"schema": NO.SCHEMA, "id": NO.RECEIPT_ID, "logs": [],
                                   "outcomes": [], "host": {}, "devices": {},
                                   "native_pre_init": {"imports": []}}))

    def refuse(e, root, outcomes):
        raise SystemExit("no profile")
    monkeypatch.setattr(NI, "replay_profile", refuse)
    monkeypatch.setattr(NI, "replay_calls", lambda *a: pytest.fail("counted a log first"))
    with pytest.raises(SystemExit, match="no profile"):
        IS.replay_reach({"replays": [{"name": "boot"}]}, [], root=tmp_path)
