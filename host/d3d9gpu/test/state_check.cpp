// state_check.cpp: d3d9gpu_state_check, the second host's state check (R2b-prep,
// private/generated/s5/r2b/PLAN.md section 9 with the critic's amendments): for every draw-state
// tuple, the Vulkan state SDL 3.4.16 builds from d9g's SDL_gpu description (test/ref_sdl.c, SDL's
// own code) must equal the Vulkan state DXVK 3.1.1 builds from the same D3D9 state
// (test/ref_dxvk.cpp, DXVK's own code), field by field, floats bitwise, except under the named
// rules and classes below. d9g must never produce a mapped value that differs: where SDL cannot
// express DXVK's state it says unmapped, and where the choice is the user's, undecided.
//
//   d3d9gpu_state_check --params P.json MODE... [--out RESULT.json] [--fault NAME]
//
// Modes:
//   --domain             the sweep (PLAN.md 9.5, the critic's S2): every value of each D3D9
//                        state a mapping reads, products over the read sets, extremes and
//                        DXVK's magic values; read set and key sufficiency (9.4)
//   --defaults           d9g's ResetState and sampler options against DXVK's extracted lines
//   --audit FILE         every D3DRS token of DXVK's SetRenderState and PrepareDraw
//                        (extract_refs.py's render_state_audit.json) classified (the critic's M5)
//   --draw-tests         vertex counts, the fan cache against Vulkan's fan assembly, the UP
//                        arena against DXVK's FillUPVertexBuffer, draw_args
//   --self-test          the comparator names each perturbed field and nothing else; each
//                        reference side is live (the critic's S1)
//   --dumps F...         the census: every pipeline and sampler tuple of the device's keys files
//   --shader-record R    the recorded reflection of the streams' shaders (test/shader_record.py:
//                        each pixel shader's sampler and output masks and each vertex shader's
//                        input signature, from its bytecode and from dxbc-spirv's disassembly,
//                        linked to the keys files by bytecode sha256), which every census needs:
//                        the device's own reflection (R1's translator, DXVK's D3D9ShaderAnalysis:
//                        each tuple's and each created shader's, in its keys file) must equal it,
//                        so three readings of every signature and mask agree (U9, the R1 plan's
//                        section 2.4), and DXVK is evaluated with the record while d9g maps with
//                        the device's. Every tuple must be linked to the record, or the check
//                        fails (the confirm's residual 5)
//   --stream-identity F  state_census.sh's streams.json: each stream's path and sha256, which go
//                        into the result; its streams must be the dumps' (checker-logic finding 6)
//   --device-binary B    the binary the device ran in (state_census.sh: sa2_player_gpu): every
//                        keys file's library_sha256 must be its sha256 (the checker-logic
//                        re-review's finding 5); without it the keys files must agree on one
//   --controls           every mapping fault must fail the census by naming its field (or,
//                        where no census tuple exercises it, fail the sweep by name); the
//                        vertex-input controls must fail the census itself (the checker-logic
//                        re-review's finding 2: a census whose tuples do not exercise them
//                        checks the vertex input nowhere)
//   --residue-ruling R   a machine-readable ruling on DEPTH_CLAMP (residue | patch | shader; ruling 3's is
//                        Documentation/decisions/2026-10-05-S5-depth-clamp-ruling.json, "patch"),
//                        accepted only as a file under ROOT/Documentation/decisions/ committed at
//                        ROOT's HEAD, where ROOT is the checkout this check was compiled from
//                        (its __FILE__; --repo, if given, must name the same checkout: the
//                        checker-logic re-review's finding 4), its working copy equal to the
//                        committed bytes, decided_by "user", naming as its decision another such
//                        file that names DEPTH_CLAMP. Both are read and hashed as committed (git
//                        show HEAD:path); the result records both files' paths, sha256s and
//                        commits, ROOT and its HEAD. That provenance proves the ruling is
//                        committed, not who decided it. None exists: the verdict stays pending
//                        (the critic's M8)
//
// Ruling 3 (Documentation/decisions/2026-10-04-S5-user-rulings.md: the SDL patch for border
// addressing and depth clamp where the device supports them, the shader emulation as the
// fallback where it does not; sdl-patch PLAN.md section 3, phase B). The parameters name the
// device's two capabilities (check_params.py, from its report), which d9g maps with, which the
// keys files must name too, and which SDL's side (test/ref_sdl.c) reports to SDL's validation:
//   BORDER       on a capable device, a standard colour on a view DXVK leaves unswizzled is the
//                patch's CLAMP_TO_BORDER with that colour, compared field by field: the address
//                modes, sm_borderColor, sm_pNextCustomBorder (DXVK chains none for a standard
//                colour), and sm_pNextBorderSwizzle, which DXVK chains where the device has
//                borderColorSwizzle without borderColorSwizzleFromImage (NVIDIA here) and SDL never
//                does, equal only by the rule BORDER_SWIZZLE (border_swizzle_rule); anything else
//                is "fallback BORDER", the shader route's obligation (d9g's fallback_border), owed
//                and never mapped silently. Which of the two a sampler must be is decided from
//                DXVK's side alone (patch_cannot_build: the capability, DXVK's view mapping and
//                format from its sampler key, its colour's exact match), and either one where the
//                other is due fails with a named reason;
//   DEPTH_CLAMP  on a capable device the description sets the depth-clamp property and SDL builds
//                DXVK's clamp with clip, compared raw, and an R_DEPTH_CLAMP bit fails as an
//                obligation that does not hold, whatever the ruling; on another, every tuple
//                carries R_DEPTH_CLAMP, the residue class, which a ruling decides: none, pending
//                the user; "patch", owed by the shader route on that device (ruling 3's fallback),
//                and a failure on a capable device where SDL does not build clamp with clip;
//                "shader", the obligation named; "residue", accepted.
//
// The vertex input (U9, the R1 plan's section 2.4) follows the vertex shader's input signature on
// both sides, every fact compared as mapped, the attributes' locations among them, under one named
// rule:
//   NULL_STREAM  an input the declaration lacks: DXVK binds its null stream (binding 16, per
//                instance, divisor 0, R32G32B32A32_SFLOAT at offset 0, a null buffer of stride 0:
//                d3d9_device.cpp:7704-7707, :7738-7742, dxvk_context.cpp:7865-7870) and d9g its
//                kNullStreamSlot at the instance rate with pitch 0 (d9g_pipeline.h). The attribute's
//                and the binding's binding number are equal by the rule where DXVK's is its null
//                stream and d9g's its slot for it, and the binding's divisor where DXVK's is 0 and
//                SDL's 1 on a binding both build per instance with stride 0 (every instance reads
//                offset 0 under either divisor); format, offset, stride and rate are compared raw.
//                What the null binding reads is the GPU's to say: host/d3d9gpu/test_shaders.sh C7
//                measures it per driver, and the device's buffer holds that value.
//
// Markers (last line): STATE_CHECK_OK only when everything passes with nothing pending or owed;
// STATE_CHECK_PENDING_USER <items> when the only open items are the user's (DEPTH_CLAMP with no
// ruling; RT_FEEDBACK where a tuple's hazard is unresolvable: a pixel shader that samples the
// target it writes); STATE_CHECK_FALLBACK_OWED <items> (exit 4)
// when nothing fails and ruling 3's shader fallback is owed (BORDER, DEPTH_CLAMP), followed by
// PENDING_USER <items> where there are any, and TEST_LEG=<names> where the keys files were made
// under a test run's capability override (which therefore never reads OK or only pending);
// STATE_CHECK_FAIL otherwise. No flag turns a pending verdict into OK, and a run in which no mode
// ran is no pass.
#include "d9g_draw.h"
#include "d9g_format.h"
#include "d9g_pipeline.h"
#include "d9g_props.h"
#include "d9g_sampler.h"
#include "mini_json.h"
#include "ref_dxvk.h"
#include "ref_facts.h"
#include "sha256.h"

#include <sys/wait.h>

#include <algorithm>
#include <climits>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <functional>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <vector>

extern "C" {
void d9g_sdl_pipeline_facts(const SDL_GPUGraphicsPipelineCreateInfo* ci, int supports_fill_mode_non_solid, D9gPipelineFacts* out);
void d9g_sdl_sampler_facts(const SDL_GPUSamplerCreateInfo* ci, D9gSamplerFacts* out);
void d9g_sdl_enum_values(uint32_t* out, int n);
int d9g_sdl_log_warnings(void);
void d9g_sdl_poke(int which);
const void* d9g_sdl_validate_pipeline(const SDL_GPUGraphicsPipelineCreateInfo* ci, int d24s8_supported);
const void* d9g_sdl_validate_sampler(const SDL_GPUSamplerCreateInfo* ci);
const char* d9g_sdl_last_assert(void);
int d9g_sdl_patched(void);
int d9g_sdl_set_device_caps(int border, int clip_and_clamp);
int d9g_sdl_desc_props_live(void);
}

extern "C" {
const char* const d9g_pipeline_fact_names[PF_COUNT] = {
#define D9G_NAME(n) #n,
    D9G_PIPELINE_FACTS(D9G_NAME)
#undef D9G_NAME
};
const char* const d9g_sampler_fact_names[SF_COUNT] = {
#define D9G_NAME(n) #n,
    D9G_SAMPLER_FACTS(D9G_NAME)
#undef D9G_NAME
};
}

using namespace d9g;
using mini_json::Value;

namespace {

// ------------------------------------------------------------------ parameters
struct Params {
    d9g_ref::DxvkParams dxvk;
    DeviceParams dev;
    int fill_non_solid = 0;
    std::string device_name;   // the second host's device (check_params.py), empty for a sweep axis
    std::string axis;          // the sweep axis's name (check_params.py --axis): no claim about a device
    std::string capability_override;   // a test run's override the device ran under ("" none): a test leg
    std::string json;          // as read, for the result
};

// The CPU model's device name (d9g_gpu_cpu.cpp): the one device whose keys file a sweep axis's
// parameters may check (the review's checker-logic finding 7).
const char* const kCpuModelDevice = "d9g CPU model";

std::string read_file(const std::string& p) {
    std::ifstream f(p, std::ios::binary);
    if (!f) throw std::runtime_error("cannot read " + p);
    std::stringstream s;
    s << f.rdbuf();
    return s.str();
}

Params load_params(const std::string& path) {
    Params P;
    P.json = read_file(path);
    Value v = mini_json::parse(P.json);
    if (v["schema"].str() != "sa2-d3d9gpu-check-params/v1") throw std::runtime_error(path + ": not sa2-d3d9gpu-check-params/v1");
    const Value& d = v["dxvk"];
    P.dxvk.vendor_id = d["vendor_id"].u32();
    P.dxvk.point_size_max = float(d["point_size_max"].num());
    P.dxvk.d24s8_supported = d["d24s8_supported"].boolean();
    P.dxvk.d16s8_supported = d["d16s8_supported"].boolean();
    for (const auto& [k, f] : d["features"].fields) P.dxvk.features[k] = f.boolean();
    P.dxvk.source = v["source"].str();
    P.dev.d24s8_supported = v["device"]["d24s8_supported"].boolean();
    P.dev.point_size_max = float(v["device"]["point_size_max"].num());
    P.dev.sampler_clamp_to_border = v["device"]["sampler_clamp_to_border"].boolean();
    P.dev.depth_clip_and_clamp = v["device"]["depth_clip_and_clamp"].boolean();
    if (v["device"]["capability_override"].kind == Value::String) P.capability_override = v["device"]["capability_override"].str();
    // the SDL side's device reports the capabilities to SDL's own validation; a stock SDL's cannot
    if (d9g_sdl_set_device_caps(P.dev.sampler_clamp_to_border, P.dev.depth_clip_and_clamp) != 0)
        throw std::runtime_error(path + ": the device has the SDL patch's capabilities, and this check's SDL side is the stock "
                                 "tarball's, which has neither (rebuild the check on the patched install)");
    P.fill_non_solid = v["sdl"]["supports_fill_mode_non_solid"].boolean() ? 1 : 0;
    if (v["axis"].kind == Value::String) P.axis = v["axis"].str();
    if (v["device_name"].kind == Value::String) P.device_name = v["device_name"].str();
    if (P.axis.empty() == P.device_name.empty())
        throw std::runtime_error(path + ": parameters name either a sweep axis or a device, not both or neither");
    for (const std::string& f : d9g_ref::dxvk_features_read())
        if (!P.dxvk.features.count(f)) throw std::runtime_error(path + ": DXVK feature " + f + " has no recorded value (M7)");
    return P;
}

// ------------------------------------------------------------------ the recorded reflection
// test/shader_record.py's file: per pixel shader (by its bytecode's sha256) the sampler mask and
// the output mask, each derived twice (the bytecode's tokens, dxbc-spirv's disassembly) and
// agreeing, with the sources' hashes. The census links a tuple's ps id to it through the keys
// file's "shaders" (the device's sha256 of each shader's bytecode by creation id).
struct ShaderRecord {
    std::string path, sha256;
    struct Ps { uint32_t sampler_mask = 0, rt_mask = 0; std::string label; };
    struct Vs { d9g_ref::VsSignature signature; std::string label; };
    std::map<std::string, Ps> ps;   // bytecode sha256 -> masks
    std::map<std::string, Vs> vs;   // bytecode sha256 -> input signature
};

std::string sha256_of(const std::string& bytes) {
    Sha256 h;
    h.update(bytes.data(), bytes.size());
    return h.hex();
}

std::pair<uint8_t, uint8_t> read_signature_entry(const Value& entry, const std::string& path) {
    if (entry.kind != Value::Array || entry.size() != 2)
        throw std::runtime_error(path + ": an input signature entry that is not [usage, index]");
    auto byte = [&](const Value& value) {
        if (value.kind != Value::Number || value.text.empty() ||
            value.text.find_first_not_of("0123456789") != std::string::npos)
            throw std::runtime_error(path + ": signature usage or index is not an unsigned integer");
        uint32_t result = 0;
        for (char digit : value.text) {
            result = result * 10 + uint32_t(digit - '0');
            if (result > UINT8_MAX)
                throw std::runtime_error(path + ": signature usage or index is outside a byte");
        }
        return uint8_t(result);
    };
    return {byte(entry[0]), byte(entry[1])};
}

ShaderRecord load_shader_record(const std::string& path) {
    ShaderRecord r;
    r.path = path;
    const std::string text = read_file(path);
    r.sha256 = sha256_of(text);
    Value v = mini_json::parse(text);
    if (v["schema"].str() != "sa2-d3d9gpu-shader-record/v2") throw std::runtime_error(path + ": not sa2-d3d9gpu-shader-record/v2");
    if (!v["agree"].boolean()) throw std::runtime_error(path + ": its two derivations disagree (shader_record.py)");
    for (const Value& s : v["ps"].items) {
        ShaderRecord::Ps p;
        p.sampler_mask = s["sampler_mask"].u32();
        p.rt_mask = s["rt_mask"].u32();
        p.label = s["label"].str();
        const std::string hash = s["sha256"].str();
        const auto [it, inserted] = r.ps.emplace(hash, p);
        if (!inserted && (it->second.sampler_mask != p.sampler_mask || it->second.rt_mask != p.rt_mask))
            throw std::runtime_error(path + ": conflicting pixel-shader reflection for bytecode " + hash);
    }
    for (const Value& s : v["vs"].items) {
        ShaderRecord::Vs x;
        for (const Value& in : s["input_signature"].items)
            x.signature.inputs.push_back(read_signature_entry(in, path));
        x.label = s["label"].str();
        const std::string hash = s["sha256"].str();
        const auto [it, inserted] = r.vs.emplace(hash, x);
        if (!inserted && it->second.signature.inputs != x.signature.inputs)
            throw std::runtime_error(path + ": conflicting vertex-shader reflection for bytecode " + hash);
    }
    if (r.ps.empty() || r.vs.empty()) throw std::runtime_error(path + ": no pixel shader or no vertex shader");
    return r;
}

// ------------------------------------------------------------------ a ruling, with provenance
// A DEPTH_CLAMP ruling is the user's (AGENTS.md rule 10), so the check takes one only as a
// decision record of the checkout it was built from: a file under ROOT/Documentation/decisions/,
// ROOT this source file's checkout (__FILE__ at compile time; --repo may only repeat it), that
// git has committed at ROOT's HEAD, whose working copy is the committed bytes, schema
// sa2-residue-ruling/v1, decided_by "user", naming as its decision another file the same rule
// holds and that names DEPTH_CLAMP (the review's finding 3; checker-logic finding 5; the
// re-review's finding 4). Both files are parsed and hashed as committed (git show HEAD:path), so
// an index flag (assume-unchanged) cannot put other bytes on disk under the recorded commit. The
// result records both files' hashes and commits, ROOT and its HEAD (the confirm's residual 4).
// Provenance proves the ruling is committed, not who decided it, and the result says so.
// Read-only git commands only.
struct TrackedFile { std::string rel, sha256, commit, bytes; };
struct Ruling { std::string root, head, ruling; TrackedFile file, decision; };

int run_quiet(const std::string& cmd) {
    const int rc = std::system((cmd + " > /dev/null 2>&1").c_str());
    return rc == -1 ? -1 : WEXITSTATUS(rc);
}

// A command's standard output, byte for byte; *ok false on a non-zero exit.
std::string run_bytes(const std::string& cmd, bool* ok) {
    std::string out;
    *ok = false;
    if (FILE* f = popen((cmd + " 2>/dev/null").c_str(), "r")) {
        char buf[4096];
        size_t n;
        while ((n = std::fread(buf, 1, sizeof buf, f)) > 0) out.append(buf, n);
        *ok = pclose(f) == 0;
    }
    return out;
}

std::string run_out(const std::string& cmd) {
    bool ok = false;
    std::string out = run_bytes(cmd, &ok);
    if (!ok) return std::string();
    while (!out.empty() && (out.back() == '\n' || out.back() == '\r')) out.pop_back();
    return out;
}

std::string shell_quote(const std::string& s) {
    std::string o = "'";
    for (char c : s) o += c == '\'' ? std::string("'\\''") : std::string(1, c);
    return o + "'";
}

// The checkout this check was compiled from: state_check.cpp's own path at compile time (an
// absolute .../host/d3d9gpu/test/state_check.cpp, as host/d3d9gpu/build.sh compiles it).
std::string source_root() {
    static const std::string suffix = "/host/d3d9gpu/test/state_check.cpp";
    const std::string f = __FILE__;
    if (f.empty() || f[0] != '/' || f.size() <= suffix.size() || f.compare(f.size() - suffix.size(), suffix.size(), suffix) != 0)
        throw std::runtime_error("ruling: this check was compiled from " + f + ", not an absolute path ending " + suffix +
                                 ", so it cannot name the checkout it was built from");
    char b[4096];
    if (!realpath(f.substr(0, f.size() - suffix.size()).c_str(), b))
        throw std::runtime_error("ruling: the checkout this check was built from (" + f + ") is gone");
    return std::string(b) + "/";
}

// A file under ROOT/Documentation/decisions/, committed at HEAD, its working copy the committed bytes.
TrackedFile tracked_decision_file(const std::string& root, const std::string& path, const char* what) {
    char pbuf[4096];
    if (!realpath(path.c_str(), pbuf)) throw std::runtime_error(std::string(what) + ": no file " + path);
    const std::string dir = root + "Documentation/decisions/", full = pbuf;
    if (full.compare(0, dir.size(), dir) != 0)
        throw std::runtime_error(std::string(what) + ": " + full + " is not under " + dir + " (a ruling is a decision record)");
    TrackedFile t;
    t.rel = full.substr(root.size());
    if (run_quiet("git -C " + shell_quote(root) + " ls-files --error-unmatch -- " + shell_quote(t.rel)) != 0)
        throw std::runtime_error(std::string(what) + ": " + t.rel + " is not tracked by git");
    bool ok = false;
    t.bytes = run_bytes("git -C " + shell_quote(root) + " show " + shell_quote("HEAD:" + t.rel), &ok);
    if (!ok) throw std::runtime_error(std::string(what) + ": " + t.rel + " has no committed version at HEAD");
    if (read_file(full) != t.bytes) throw std::runtime_error(std::string(what) + ": " + t.rel + " differs from its committed version");
    t.commit = run_out("git -C " + shell_quote(root) + " log -1 --format=%H HEAD -- " + shell_quote(t.rel));
    if (t.commit.size() != 40) throw std::runtime_error(std::string(what) + ": " + t.rel + " has no commit");
    t.sha256 = sha256_of(t.bytes);
    return t;
}

Ruling load_ruling(const std::string& path, const std::string& repo) {
    Ruling r;
    r.root = source_root();
    if (!repo.empty()) {
        char rbuf[4096];
        if (!realpath(repo.c_str(), rbuf)) throw std::runtime_error("ruling: no repository " + repo);
        if (std::string(rbuf) + "/" != r.root)
            throw std::runtime_error("ruling: the repository " + std::string(rbuf) + " is not the checkout this check was built from (" +
                                     r.root + ")");
    }
    r.head = run_out("git -C " + shell_quote(r.root) + " rev-parse HEAD");
    if (r.head.size() != 40) throw std::runtime_error("ruling: " + r.root + " is not a git checkout with a HEAD");
    r.file = tracked_decision_file(r.root, path, "ruling");
    Value v = mini_json::parse(r.file.bytes);
    if (!v.has("schema") || v["schema"].str() != "sa2-residue-ruling/v1") throw std::runtime_error("ruling: not sa2-residue-ruling/v1");
    if (v["residue"].str() != "DEPTH_CLAMP") throw std::runtime_error("ruling: not a DEPTH_CLAMP ruling");
    if (v["decided_by"].str() != "user") throw std::runtime_error("ruling: not decided by the user (AGENTS.md rule 10)");
    r.ruling = v["ruling"].str();
    if (r.ruling != "residue" && r.ruling != "patch" && r.ruling != "shader") throw std::runtime_error("ruling: residue | patch | shader");
    const std::string decision = v["decision"].str();
    if (decision.empty()) throw std::runtime_error("ruling: no decision named");
    r.decision = tracked_decision_file(r.root, decision[0] == '/' ? decision : r.root + decision, "ruling's decision");
    if (r.decision.rel == r.file.rel) throw std::runtime_error("ruling: its decision is the ruling itself");
    if (r.decision.bytes.find("DEPTH_CLAMP") == std::string::npos)
        throw std::runtime_error("ruling's decision: " + r.decision.rel + " does not name DEPTH_CLAMP");
    return r;
}

// ------------------------------------------------------------------ classes
enum Class {
    C_EQUAL, C_FAN, C_BGRA, C_IGN_NO_DS, C_IGN_NO_STENCIL, C_IGN_DISABLED, C_IGN_SAMPLE_BITS, C_IGN_INERT,
    C_DEPTH_CLAMP, C_RT_FEEDBACK, C_FALLBACK_BORDER, C_BORDER_SWIZZLE, C_NULL_STREAM, C_DIFFER, C_COUNT
};
const char* kClassName[C_COUNT] = {"equal", "rule FAN", "rule BGRA", "IGNORED no depth-stencil attachment",
                                   "IGNORED no stencil aspect", "IGNORED disabled by its enable",
                                   "IGNORED sample-mask bits past the sample count", "IGNORED inert stencil write mask",
                                   "residue DEPTH_CLAMP", "undecided RT_FEEDBACK (OPEN-1)",
                                   "fallback BORDER (the shader route's obligation, ruling 3)", "rule BORDER_SWIZZLE",
                                   "rule NULL_STREAM", "DIFFERS"};

bool in_range(int f, int lo, int hi) { return f >= lo && f <= hi; }
bool is_stencil_fact(int f) { return in_range(f, PF_ds_stencilTestEnable, PF_ds_back_writeMask); }
bool is_ds_fact(int f) { return in_range(f, PF_ds_depthTestEnable, PF_ds_back_writeMask); }
bool is_bias_value(int f) { return in_range(f, PF_rs_depthBiasConstantFactor, PF_rs_depthBiasExact); }
bool is_vi_fact(int f) { return in_range(f, PF_vi_attributeCount, PF_vi_attr15_location); }
int cb_index(int f) {   // which colour attachment a cbN_ fact belongs to, or -1
    const int stride = PF_cb1_blendEnable - PF_cb0_blendEnable;
    if (f < PF_cb0_blendEnable || f >= PF_cb0_blendEnable + 4 * stride) return -1;
    return (f - PF_cb0_blendEnable) / stride;
}
int cb_field(int f) { return (f - PF_cb0_blendEnable) % (PF_cb1_blendEnable - PF_cb0_blendEnable); }

// Vulkan primitive assembly (the Vulkan specification, "Primitive Topologies"): each triangle's
// vertex indices in order, the first the provoking vertex. A reference model of the FAN rule,
// in the harness only (the critic's S5).
std::vector<std::array<int64_t, 3>> assemble(uint32_t vk_topology, bool indexed, uint32_t count, uint32_t first_vertex,
                                             int32_t vertex_offset, const std::vector<uint32_t>* idx, uint32_t first_index) {
    auto v = [&](uint32_t k) -> int64_t {
        return indexed ? int64_t((*idx)[first_index + k]) + vertex_offset : int64_t(first_vertex) + k;
    };
    std::vector<std::array<int64_t, 3>> out;
    if (vk_topology == 3) {   // TRIANGLE_LIST
        for (uint32_t i = 0; i + 3 <= count; i += 3) out.push_back({v(i), v(i + 1), v(i + 2)});
    } else if (vk_topology == 4) {   // TRIANGLE_STRIP
        for (uint32_t i = 0; i + 3 <= count; i++)
            out.push_back({v(i), v(i + 1 + i % 2), v(i + 2 - i % 2)});
    } else if (vk_topology == 5) {   // TRIANGLE_FAN: (v_{i+1}, v_{i+2}, v_0)
        for (uint32_t i = 0; i + 3 <= count; i++) out.push_back({v(i + 1), v(i + 2), v(0)});
    }
    return out;
}

// The FAN rule: d9g's list through the fan cache assembles to Vulkan's fan of DXVK's draw.
bool fan_rule_holds(uint32_t method, uint32_t faults, std::string* why) {
    for (uint32_t prims : {1u, 2u, 3u, 10u, 61u, 500u})
        for (uint32_t start : {0u, 7u}) {
            const uint32_t n = d9g_ref::dxvk_vertex_count(D3DPT_TRIANGLEFAN, prims);
            const uint32_t first = method == DM_DP ? start : 0u;
            const auto ref = assemble(5, false, n, first, 0, nullptr, 0);
            const DrawArgs a = draw_args(method, D3DPT_TRIANGLEFAN, start, prims, 0, 0);
            if (!a.unmapped.empty()) { *why = a.unmapped; return false; }
            uint32_t cache = 64;
            while (cache < n) cache *= 2;
            const std::vector<uint32_t> idx = fan_cache_indices(cache, faults);
            const auto got = assemble(3, true, a.count, a.first_vertex, a.vertex_offset, &idx, a.first_index);
            if (got != ref) {
                *why = "the fan cache's list assembles differently from Vulkan's fan (" + std::to_string(prims) +
                       " primitives from vertex " + std::to_string(start) + ")";
                return false;
            }
        }
    return true;
}

// ------------------------------------------------------------------ the pipeline comparison
struct PipelineCheck {
    PipelineDesc desc;
    LegacyData legacy;
    d9g_ref::PipelineResult dx;
    D9gPipelineFacts sdl{};
    std::array<Class, PF_COUNT> cls{};
    std::array<Class, PF_COUNT> context{};   // C_EQUAL: Vulkan reads the field; else why it does not
    std::vector<int> differ;          // fact indices
    std::vector<int> standin;         // facts any stand-in decides (with_standins only)
    std::vector<std::vector<int>> standin_by;   // per stand-in (d9g_ref::standin_names), the facts it decides
    std::string reason;               // a failure no fact names: a reference refusal, SDL's validation
    // the obligations d9g's description carries that the facts' values do not hold: "BGRA" (a
    // bgra_mask bit on an attribute the BGRA rule does not class), "FAN" (fan_as_list on a
    // topology the FAN rule does not class); each also marks its fact DIFFERS (the checker-logic
    // re-review's finding 3: named, not shown as two equal values)
    std::vector<std::string> obligations;
    bool unmapped() const { return !desc.unmapped.empty(); }
    bool fails() const { return !differ.empty() || !reason.empty() || !obligations.empty(); }
    bool depth_clamp = false, rt_feedback = false, fan = false, bgra = false, null_stream = false;
    // the Vulkan state SDL builds: depth clamp beside depth clip (patches/sdl3's property), as
    // ruling 3's patch requires on a device with the capability (before any self-test poke)
    bool sdl_clamp_with_clip = false;
};

// The NULL_STREAM rule (the header): DXVK's facts `d` and SDL's `s` differ at f only because DXVK
// binds its null stream where d9g binds its kNullStreamSlot (a description with null_stream set).
// Each clause names the facts it reads (depends_on: perturbing them breaks it).
bool null_stream_rule(int f, const uint32_t* d, const uint32_t* s) {
    const uint32_t nullB = d9g_ref::dxvk_null_stream_binding();
    // an attribute's binding
    if (in_range(f, PF_vi_attr0_binding, PF_vi_attr15_offset) && (f - PF_vi_attr0_binding) % 3 == 0)
        return d[f] == nullB && s[f] == kNullStreamSlot;
    if (!in_range(f, PF_vi_bind0_binding, PF_vi_bind3_divisor)) return false;
    const int b = PF_vi_bind0_binding + 4 * ((f - PF_vi_bind0_binding) / 4);
    if (d[b] != nullB || s[b] != kNullStreamSlot) return false;
    if (f == b) return true;   // the binding's number
    // its divisor: DXVK's 0 (:7740), SDL's 1 (an instance binding without a divisor description,
    // ref_sdl.c), on a binding both build per instance (VK_VERTEX_INPUT_RATE_INSTANCE, 1) with stride
    // 0, where every instance reads offset 0 whatever the divisor
    if (f == b + 3)
        return d[f] == 0 && s[f] == 1 && d[b + 1] == 0 && s[b + 1] == 0 && d[b + 2] == 1 && s[b + 2] == 1;
    return false;
}

void fill_full_rs(const PipelineInput& in, const uint32_t base[256], uint32_t out[256]) {
    std::memcpy(out, base, 256 * 4);
    for (size_t i = 0; i < kNumPipelineRS; i++) out[kPipelineRenderStates[i]] = in.rs[i];
}

// `rec`: the shaders' recorded reflection (the census: test/shader_record.py's record), with which
// DXVK is evaluated while d9g maps with the input's own (the device's); null, the input's on both
// sides (the sweep).
PipelineCheck check_pipeline(const PipelineInput& in, const uint32_t rs_full[256], const Params& P, uint32_t faults,
                             int poke_sdl_fact = -1, bool with_standins = false,
                             const d9g_ref::RecordedReflection* rec = nullptr) {
    PipelineCheck c;
    c.desc = map_pipeline(in, P.dev, faults);
    c.legacy = map_legacy(in, faults);
    c.dx = d9g_ref::dxvk_pipeline(in, rs_full, P.dxvk, d9g_ref::standins_chosen(in, rec));
    // the stand-ins' census (the review's finding 4): DXVK once more per stand-in, that one alone
    // at its alternative value; every fact that moves is decided by it
    if (with_standins && c.dx.refused.empty()) {
        std::vector<bool> any(PF_COUNT, false);
        for (size_t k = 0; k < d9g_ref::standin_names().size(); k++) {
            const d9g_ref::PipelineResult alt = d9g_ref::dxvk_pipeline(in, rs_full, P.dxvk, d9g_ref::standins_alternative(in, rec, k));
            c.standin_by.emplace_back();
            if (!alt.refused.empty()) {   // the alternative is a state the reference does not model: it decides every fact
                for (int f = 0; f < PF_COUNT; f++) { c.standin_by.back().push_back(f); any[f] = true; }
                continue;
            }
            for (int f = 0; f < PF_COUNT; f++)
                if (c.dx.facts.v[f] != alt.facts.v[f]) { c.standin_by.back().push_back(f); any[f] = true; }
        }
        for (int f = 0; f < PF_COUNT; f++)
            if (any[f]) c.standin.push_back(f);
    }
    // the description's props, made by d9g's own maker (d9g_props.h CreateProps) through SDL's
    // property functions, which the SDL side models (test/ref_sdl.c): the spans read what d9g set
    const CreateProps props(c.desc);
    const SDL_GPUGraphicsPipelineCreateInfo ci = c.desc.create_info(props.id());
    d9g_sdl_pipeline_facts(&ci, P.fill_non_solid, &c.sdl);
    uint32_t* s = c.sdl.v;
    c.sdl_clamp_with_clip = s[PF_rs_depthClampEnable] == 1 && s[PF_rs_depthClipEnable] == 1;
    s[PF_lg_alphaCompareOp] = c.legacy.alpha_compare_vk;
    s[PF_lg_alphaPrecision] = c.legacy.alpha_precision;
    s[PF_lg_alphaRef] = c.legacy.alpha_ref;
    s[PF_lg_pointSize] = c.legacy.point_size;
    s[PF_lg_pointSizeMin] = c.legacy.point_min;
    s[PF_lg_pointSizeMax] = c.legacy.point_max;
    s[PF_lg_pointSprite] = c.legacy.point_sprite;
    s[PF_lg_pointScale] = c.legacy.point_scale;
    s[PF_lg_flatShading] = c.legacy.flat_shading;
    s[PF_rt_unresolvableHazard] = c.desc.rt_feedback;   // d9g's hazard decision (ref_facts.h)
    s[PF_vs_fixedFunction] = 0;                         // d9g maps no fixed-function draw (ref_facts.h)
    // the attributes' locations, which SDL copies from the description unchanged (ref_facts.h)
    for (uint32_t k = 0; k < c.desc.n_attrs && k < D9G_MAX_ATTRS; k++) s[PF_vi_attr0_location + k] = c.desc.attrs[k].location;
    if (poke_sdl_fact >= 0) s[poke_sdl_fact] ^= 0x5u;   // the comparator's self-test
    if (c.unmapped()) return c;   // nothing mapped: nothing compared (the census and the sweep count it)
    // a state the reference does not model is one d9g must leave unmapped (the reviews'
    // transcription findings 1, 2, 5, 6 and 7)
    if (!c.dx.refused.empty()) {
        c.reason = "mapped where the reference does not model the state: " + c.dx.refused;
        return c;
    }
    if (!props.error().empty()) c.reason = "d9g's property set: " + props.error();
    // SDL's own debug-mode validation of the create info (the confirm's residual 3), with the
    // device's capabilities: the clamp property on a device without it is refused there
    if (!d9g_sdl_validate_pipeline(&ci, P.dev.d24s8_supported ? 1 : 0))
        c.reason = std::string("SDL's validation refuses the description: ") + d9g_sdl_last_assert();
    std::string fanWhy;
    // The class of fact f against DXVK's facts `d`: equal, a context in which Vulkan does not read
    // it, a named rule, a residue, an undecided item, or DIFFERS.
    auto classify = [&](int f, const uint32_t* d, bool with_context) {
        const bool dxHasDs = d[PF_rt_depthAttachmentFormat] != 0, dxHasSt = d[PF_rt_stencilAttachmentFormat] != 0;
        // the context first: whether Vulkan reads the field in this state at all
        Class ctx = C_EQUAL;
        const int cbi = cb_index(f);
        const int cbEnable = cbi >= 0 ? PF_cb0_blendEnable + (PF_cb1_blendEnable - PF_cb0_blendEnable) * cbi : -1;
        if ((is_ds_fact(f) || f == PF_rs_depthBiasEnable || is_bias_value(f)) && !dxHasDs && !s[PF_rt_depthAttachmentFormat])
            ctx = C_IGN_NO_DS;
        else if (is_stencil_fact(f) && !dxHasSt && !s[PF_rt_stencilAttachmentFormat])
            ctx = C_IGN_NO_STENCIL;
        else if (in_range(f, PF_ds_front_failOp, PF_ds_back_writeMask) && !d[PF_ds_stencilTestEnable] && !s[PF_ds_stencilTestEnable])
            ctx = C_IGN_DISABLED;
        else if ((f == PF_ds_depthWriteEnable || f == PF_ds_depthCompareOp) && !d[PF_ds_depthTestEnable] && !s[PF_ds_depthTestEnable])
            ctx = C_IGN_DISABLED;
        else if (is_bias_value(f) && !d[PF_rs_depthBiasEnable] && !s[PF_rs_depthBiasEnable])
            ctx = C_IGN_DISABLED;
        else if (cbi >= 0 && cb_field(f) >= 1 && cb_field(f) <= 6 && !d[cbEnable] && !s[cbEnable])
            ctx = C_IGN_DISABLED;
        else if (f == PF_cb_logicOp && !d[PF_cb_logicOpEnable] && !s[PF_cb_logicOpEnable])
            ctx = C_IGN_DISABLED;
        else if (f == PF_ms_minSampleShading && !d[PF_ms_sampleShadingEnable] && !s[PF_ms_sampleShadingEnable])
            ctx = C_IGN_DISABLED;
        else if (f == PF_ms_sampleMask && d[PF_ms_rasterizationSamples] == s[PF_ms_rasterizationSamples] &&
                 d[PF_ms_rasterizationSamples] && d[PF_ms_rasterizationSamples] < 32 &&
                 ((d[f] ^ s[f]) & ((1u << d[PF_ms_rasterizationSamples]) - 1u)) == 0)
            ctx = C_IGN_SAMPLE_BITS;
        else if (f == PF_ds_front_writeMask || f == PF_ds_back_writeMask) {
            const int o = f == PF_ds_front_writeMask ? 0 : PF_ds_back_failOp - PF_ds_front_failOp;
            auto keep = [&](const uint32_t* v) {
                return v[PF_ds_front_failOp + o] == 0 && v[PF_ds_front_passOp + o] == 0 && v[PF_ds_front_depthFailOp + o] == 0;
            };
            // N1: a write mask with all three ops KEEP writes nothing (functionally inert)
            if (keep(d) && keep(s)) ctx = C_IGN_INERT;
        }
        if (with_context) c.context[f] = ctx;
        if (d[f] == s[f]) return C_EQUAL;
        if (ctx != C_EQUAL) return ctx;
        if (f == PF_ia_topology && d[f] == 5 && s[f] == 3 && c.desc.fan_as_list && fan_rule_holds(in.method, faults, &fanWhy))
            return C_FAN;
        if (in_range(f, PF_vi_attr0_binding, PF_vi_attr15_offset) && (f - PF_vi_attr0_binding) % 3 == 1 &&
            d[f] == 44 /* B8G8R8A8_UNORM */ && s[f] == 37 /* R8G8B8A8_UNORM */ &&
            ((c.desc.bgra_mask >> ((f - PF_vi_attr0_binding) / 3)) & 1u))
            return C_BGRA;
        if (c.desc.null_stream && null_stream_rule(f, d, s)) return C_NULL_STREAM;
        if (f == PF_rs_depthClampEnable && d[f] == 1 && s[f] == 0 && (c.desc.residues & R_DEPTH_CLAMP)) return C_DEPTH_CLAMP;
        if (f == PF_rt_feedbackLoop && d[f] && !s[f] && c.desc.rt_feedback) return C_RT_FEEDBACK;
        return C_DIFFER;
    };
    const uint32_t* d = c.dx.facts.v;
    for (int f = 0; f < PF_COUNT; f++) c.cls[f] = classify(f, d, true);
    // the obligations hold both ways (the review's checker-logic finding 3): every BGRA bit d9g
    // sets lands on an attribute format the BGRA rule classes, and the fan list is held by the FAN
    // rule. An obligation that fails where the values compare otherwise is named (map-bgra-extra,
    // map-bgra-decl-index and map-fan-on-list are its controls).
    for (uint32_t i = 0; i < 16; i++) {
        if (!((c.desc.bgra_mask >> i) & 1u)) continue;
        const int f = PF_vi_attr0_format + 3 * int(i);
        if (c.cls[f] == C_BGRA) continue;
        if (c.cls[f] != C_DIFFER &&
            std::find(c.obligations.begin(), c.obligations.end(), "BGRA") == c.obligations.end())
            c.obligations.push_back("BGRA");
        c.cls[f] = C_DIFFER;
    }
    if (c.desc.fan_as_list && c.cls[PF_ia_topology] != C_FAN) {
        if (c.cls[PF_ia_topology] != C_DIFFER) c.obligations.push_back("FAN");
        c.cls[PF_ia_topology] = C_DIFFER;
    }
    // ruling 3's depth clamp both ways, whatever the ruling (the I6 state-check review's finding 2):
    // on a device with depth clip and clamp the patch builds DXVK's clamp and the shader route owes
    // nothing, so an R_DEPTH_CLAMP bit there is an obligation that does not hold, and the residue
    // class never absorbs the clamp; on a device without it, the property is one its SDL cannot
    // build (SDL's validation refuses it too). map-depth-clamp-extra is its control.
    if (P.dev.depth_clip_and_clamp ? (c.desc.residues & R_DEPTH_CLAMP) != 0 : c.desc.depth_clamp) {
        if (c.cls[PF_rs_depthClampEnable] != C_DIFFER) c.obligations.push_back("DEPTH_CLAMP");
        c.cls[PF_rs_depthClampEnable] = C_DIFFER;
    }
    for (int f = 0; f < PF_COUNT; f++) {
        const Class k = c.cls[f];
        if (k == C_DIFFER) c.differ.push_back(f);
        if (k == C_DEPTH_CLAMP) c.depth_clamp = true;
        if (k == C_FAN) c.fan = true;
        if (k == C_BGRA) c.bgra = true;
        if (k == C_NULL_STREAM) c.null_stream = true;
    }
    // RT_FEEDBACK is d9g's own decision (held to DXVK's by rt_unresolvableHazard), pending whether
    // or not this device has the feedback-loop layout that makes rt_feedbackLoop differ (N10)
    c.rt_feedback = c.desc.rt_feedback;
    return c;
}

// The fields whose comparison context a field decides (an enable, a format, a sample count, a
// face's ops): perturbing it may name them too (the self-test).
bool depends_on(int g, int f) {
    if (f == PF_rs_depthBiasEnable) return is_bias_value(g);
    // the NULL_STREAM rule's divisor clause reads its binding's number, stride and rate
    if (in_range(f, PF_vi_bind0_binding, PF_vi_bind3_divisor) && (f - PF_vi_bind0_binding) % 4 != 3)
        return g == PF_vi_bind0_binding + 4 * ((f - PF_vi_bind0_binding) / 4) + 3;
    if (f == PF_ms_rasterizationSamples) return g == PF_ms_sampleMask;
    if (f == PF_ms_sampleShadingEnable) return g == PF_ms_minSampleShading;
    if (f == PF_ds_depthTestEnable) return g == PF_ds_depthWriteEnable || g == PF_ds_depthCompareOp;
    if (f == PF_ds_stencilTestEnable) return in_range(g, PF_ds_front_failOp, PF_ds_back_writeMask);
    if (f == PF_cb_logicOpEnable) return g == PF_cb_logicOp;
    if (f == PF_rt_depthAttachmentFormat) return is_ds_fact(g) || g == PF_rs_depthBiasEnable || is_bias_value(g);
    if (f == PF_rt_stencilAttachmentFormat) return is_stencil_fact(g);
    if (cb_index(f) >= 0 && cb_field(f) == 0) return cb_index(g) == cb_index(f) && cb_field(g) >= 1 && cb_field(g) <= 6;
    for (int o : {0, int(PF_ds_back_failOp - PF_ds_front_failOp)})
        if (f == PF_ds_front_failOp + o || f == PF_ds_front_passOp + o || f == PF_ds_front_depthFailOp + o)
            return g == PF_ds_front_writeMask + o;
    return false;
}

// ------------------------------------------------------------------ the sampler comparison
struct SamplerCheck {
    SamplerDesc desc;
    d9g_ref::SamplerResult dx;
    D9gSamplerFacts sdl{};
    std::array<Class, SF_COUNT> cls{};
    std::vector<int> differ;
    bool fallback = false;        // a BORDER axis d9g leaves to the shader route (fallback_border)
    bool border_mapped = false;   // a BORDER axis d9g maps to the patch's CLAMP_TO_BORDER
    bool by_rule = false;         // a fact equal only by the BORDER_SWIZZLE rule
    std::string reason;
    bool unmapped() const { return !desc.unmapped.empty(); }
};

SDL_GPUSamplerCreateInfo fields_for_check(const SamplerFields& f) {
    // Only here: a fallback BORDER stage's other fields through SDL's span, the fallback axes as
    // the placeholder REPEAT, which the comparator classes as the fallback only where DXVK's axis
    // is CLAMP_TO_BORDER.
    SDL_GPUSamplerCreateInfo ci{};
    ci.min_filter = f.min_filter; ci.mag_filter = f.mag_filter; ci.mipmap_mode = f.mipmap_mode;
    ci.address_mode_u = f.address[0]; ci.address_mode_v = f.address[1]; ci.address_mode_w = f.address[2];
    ci.mip_lod_bias = f.mip_lod_bias; ci.max_anisotropy = f.max_anisotropy; ci.compare_op = f.compare_op;
    ci.min_lod = f.min_lod; ci.max_lod = f.max_lod; ci.enable_anisotropy = f.enable_anisotropy;
    ci.enable_compare = f.enable_compare;
    return ci;
}

// The Vulkan enumerants --enum-values prints (ref_sdl.c d9g_sdl_enum_values, ref_dxvk.cpp
// dxvk_enum_values), as both bundled vulkan_core.h give them, never typed here (the extraction
// re-review's NIT 11): VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_BORDER (entry 19) and the three standard
// float border colours (entries 21, 27, 28).
constexpr int kEnumValues = 29;
uint32_t vk_enum(int k, const char* name) {
    uint32_t a[kEnumValues], b[kEnumValues];
    d9g_sdl_enum_values(a, kEnumValues);
    d9g_ref::dxvk_enum_values(b, kEnumValues);
    if (a[k] != b[k]) throw std::runtime_error(std::string("the two vulkan_core.h disagree on ") + name);
    return a[k];
}
uint32_t vk_clamp_to_border() {
    static const uint32_t v = vk_enum(19, "VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_BORDER");
    return v;
}
bool vk_standard_border_colour(uint32_t c) {
    static const uint32_t tb = vk_enum(21, "VK_BORDER_COLOR_FLOAT_TRANSPARENT_BLACK"),
                          ob = vk_enum(27, "VK_BORDER_COLOR_FLOAT_OPAQUE_BLACK"),
                          ow = vk_enum(28, "VK_BORDER_COLOR_FLOAT_OPAQUE_WHITE");
    return c == tb || c == ob || c == ow;
}

// The rule BORDER_SWIZZLE: DXVK chains a VkSamplerBorderColorComponentMappingCreateInfoEXT on every
// BORDER sampler of a device with borderColorSwizzle and without borderColorSwizzleFromImage
// (dxvk_sampler.cpp:55-60; NVIDIA's driver here), carrying its view's component mapping; SDL
// chains nothing. The two build the same border only where that mapping maps nothing, so
// sm_pNextBorderSwizzle (DXVK 1, SDL 0) is equal by this rule only where all of these hold, and
// DIFFERS anywhere else:
//   - d9g maps the BORDER (the patch's CLAMP_TO_BORDER), on every axis DXVK clamps to its border;
//   - the chained components are each VK_COMPONENT_SWIZZLE_IDENTITY or the component itself, and
//     its srgb member is VK_FALSE (ref_dxvk.cpp records them from DXVK's chain);
//   - the border colour is one of the three standard float colours on both sides, the same one,
//     and neither side chains a custom border colour.
// The clause: the Vulkan specification (Samplers, VkSamplerBorderColorComponentMappingCreateInfoEXT)
// gives its `components` as "a remapping of the border color components", and a component's
// identity swizzle (Image Views, VkComponentSwizzle: VK_COMPONENT_SWIZZLE_IDENTITY, or the
// component's own R, G, B or A) maps that component to itself, so an identity mapping remaps
// nothing; and with the identity view the border colour SDL's struct-less sampler gives is the
// colour unswizzled. The X8 views (A=1) are not identity, and fail here.
//
// The extraction's two cuts on DXVK's sampler (extract_refs.py, span DxvkSampler), re-examined now
// that BORDER is mapped: DxvkSamplerDescriptorHeap::createSampler chains its colour index, or
// rewrites the colour to transparent black, only where the create info chains a custom border
// colour, and d9g maps BORDER only where DXVK's colour is standard, which chains none
// (sm_pNextCustomBorder, compared raw, is 0 on both sides of every mapped BORDER tuple); the
// pool's fallback when full is run-time exhaustion, the same for every sampler. Neither moves a
// compared fact.
//
// Where the clause was read: the pinned registry (vk.xml) carries no prose, so the I6 reviews
// checked it on 2026-10-05 against the specification's current pages on docs.vulkan.org: the
// refpage of VkSamplerBorderColorComponentMappingCreateInfoEXT ("a VkComponentMapping structure
// specifying a remapping of the border color components", verbatim), and Texel Replacement, whose
// cases of an undefined border each involve a view swizzle that is not the identity or an srgb
// member that does not match the view. The rule's srgb clause (VK_FALSE) is stricter than the
// specification, which allows a matching srgb; no d9g mapping reaches it today (SRGBTEXTURE is
// unmapped), and the self-test perturbs it.
const char* const kBorderSwizzleClause =
    "Vulkan specification (docs.vulkan.org, checked 2026-10-05; vk.xml has no prose): "
    "VkSamplerBorderColorComponentMappingCreateInfoEXT::components is \"a remapping of the border color components\"; "
    "under the identity mapping (each component IDENTITY or itself, VkComponentSwizzle) it remaps none, and Texel "
    "Replacement leaves the border defined, so with a standard colour, an identity view and srgb VK_FALSE the chained "
    "structure and SDL's struct-less sampler give the same border";

// Where the SDL patch builds DXVK's border for a BORDER sampler (ruling 3: the patch where the
// device supports it), decided from DXVK's side and the parameters alone, never from d9g's own
// rule (the I6 reviews' state-check findings 3 and the census lens's 3 and 4): "" where it does,
// else why not. It does where the device has CLAMP_TO_BORDER, DXVK's view of the texture has the
// identity mapping and a format that is not sRGB (its sampler key's view properties), and DXVK's
// colour is one of the three standard colours by an exact match (ref_dxvk.h SamplerResult).
std::string patch_cannot_build(const d9g_ref::SamplerResult& dx, const Params& P) {
    if (!P.dev.sampler_clamp_to_border) return "the device has no CLAMP_TO_BORDER";
    if (!dx.view_identity) return "DXVK's view of the texture swizzles";
    if (dx.view_srgb) return "DXVK's view of the texture is sRGB";
    if (!dx.border_exact_standard) return "DXVK's colour is none of the three standard colours exactly";
    return "";
}

// `poke_rule`: the self-test's perturbation of one BORDER_SWIZZLE clause's DXVK-side input (1: the
// chained mapping not the identity; 2: its srgb VK_TRUE), each of which must make
// sm_pNextBorderSwizzle DIFFER.
SamplerCheck check_sampler(const SamplerInput& in, const Params& P, uint32_t faults, int poke_sdl_fact = -1, int poke_rule = 0) {
    SamplerCheck c;
    c.desc = map_sampler(in, d9g_ref::dxvk_sampler_options(), P.dev, faults);
    c.dx = d9g_ref::dxvk_sampler(in, P.dxvk);
    // a mapped description's props (its border colour), made by d9g's own maker (d9g_props.h
    // CreateProps) through SDL's property functions, which the SDL side models (test/ref_sdl.c)
    const CreateProps props(c.desc);
    const SDL_GPUSamplerCreateInfo ci = c.desc.info ? c.desc.create_info(props.id()) : fields_for_check(c.desc.fields);
    d9g_sdl_sampler_facts(&ci, &c.sdl);
    if (poke_sdl_fact >= 0) c.sdl.v[poke_sdl_fact] ^= 0x5u;   // the comparator's self-test
    c.fallback = c.desc.fallback();
    c.border_mapped = c.desc.border_colour != kBorderNone;
    if (c.unmapped()) return c;
    if (!c.dx.refused.empty()) { c.reason = "mapped where the reference does not model the state: " + c.dx.refused; return c; }
    const uint32_t* d = c.dx.facts.v;
    uint32_t* s = c.sdl.v;
    // BORDER is the patch's CLAMP_TO_BORDER or the shader route's fallback, never another address
    // mode in silence (the field differs too, and the reason names it)
    for (int a = 0; a < 3; a++)
        if (c.dx.has_border && d[SF_sm_addressModeU + a] == vk_clamp_to_border() &&
            c.desc.axes[a] != AddrDecision::fallback_border && s[SF_sm_addressModeU + a] != vk_clamp_to_border())
            c.reason = "a BORDER axis mapped to another address mode, neither CLAMP_TO_BORDER nor the shader route's fallback";
    // the fallback absorbs only what DXVK's border decides (the review's checker-logic finding 2):
    // a fallback axis must be CLAMP_TO_BORDER in DXVK's sampler, and the border colour and its
    // pNext structures are DXVK's border's only where DXVK has one
    if (c.fallback && !c.dx.has_border) c.reason = "fallback BORDER where DXVK has no border";
    for (int a = 0; a < 3; a++)
        if (c.desc.axes[a] == AddrDecision::fallback_border && d[SF_sm_addressModeU + a] != vk_clamp_to_border())
            c.reason = "a fallback axis where DXVK's is not CLAMP_TO_BORDER";
    if (!props.error().empty()) c.reason = "d9g's property set: " + props.error();
    if (c.desc.info && !d9g_sdl_validate_sampler(&ci))
        c.reason = std::string("SDL's validation refuses the description: ") + d9g_sdl_last_assert();
    // ruling 3, held from DXVK's side: BORDER is mapped exactly where the patch builds DXVK's
    // border, and the shader route's fallback everywhere else
    if (c.dx.has_border) {
        const std::string cannot = patch_cannot_build(c.dx, P);
        if (c.border_mapped && !cannot.empty()) c.reason = "BORDER mapped where the patch cannot build DXVK's border: " + cannot;
        if (c.fallback && cannot.empty())
            c.reason = "fallback BORDER where the patch builds DXVK's border (a standard colour on an identity view, on a "
                       "device with CLAMP_TO_BORDER)";
    }
    // the reference's two records of DXVK's view mapping agree: the chained structure carries the
    // key's (dxvk_sampler.cpp:21-23)
    if (d[SF_sm_pNextBorderSwizzle] == 1 &&
        (c.dx.border_swizzle != c.dx.view_swizzle || c.dx.border_swizzle_identity != c.dx.view_identity))
        c.reason = "the reference's chained border mapping is not its sampler key's view mapping";
    bool border_axes_mapped = true;   // every axis DXVK clamps to its border, SDL clamps to its border too
    for (int a = 0; a < 3; a++)
        if (d[SF_sm_addressModeU + a] == vk_clamp_to_border() && s[SF_sm_addressModeU + a] != vk_clamp_to_border())
            border_axes_mapped = false;
    const bool rule_identity = c.dx.border_swizzle_identity && poke_rule != 1, rule_srgb = c.dx.border_swizzle_srgb || poke_rule == 2;
    const bool swizzle_rule = c.border_mapped && c.dx.has_border && border_axes_mapped && d[SF_sm_pNextBorderSwizzle] == 1 &&
                              s[SF_sm_pNextBorderSwizzle] == 0 && rule_identity && !rule_srgb &&
                              d[SF_sm_borderColor] == s[SF_sm_borderColor] && vk_standard_border_colour(d[SF_sm_borderColor]) &&
                              d[SF_sm_pNextCustomBorder] == 0 && s[SF_sm_pNextCustomBorder] == 0;
    for (int f = 0; f < SF_COUNT; f++) {
        Class k = d[f] == s[f] ? C_EQUAL : C_DIFFER;
        if (k == C_DIFFER && c.fallback) {
            const int axis = f == SF_sm_addressModeU ? 0 : f == SF_sm_addressModeV ? 1 : f == SF_sm_addressModeW ? 2 : -1;
            if (axis >= 0 && c.desc.axes[axis] == AddrDecision::fallback_border && d[f] == vk_clamp_to_border())
                k = C_FALLBACK_BORDER;
            if ((f == SF_sm_borderColor || f == SF_sm_pNextCustomBorder || f == SF_sm_pNextBorderSwizzle) && c.dx.has_border)
                k = C_FALLBACK_BORDER;
        } else if (k == C_DIFFER && f == SF_sm_pNextBorderSwizzle && swizzle_rule) {
            k = C_BORDER_SWIZZLE;
            c.by_rule = true;
        }
        c.cls[f] = k;
        if (k == C_DIFFER) c.differ.push_back(f);
    }
    return c;
}

// The sampler facts whose comparison a perturbed sampler fact may also move (the self-test): the
// BORDER_SWIZZLE rule reads the address modes, the border colour and the custom-border chain.
bool sampler_depends_on(int g, int f) {
    return g == SF_sm_pNextBorderSwizzle && (f == SF_sm_addressModeU || f == SF_sm_addressModeV || f == SF_sm_addressModeW ||
                                             f == SF_sm_borderColor || f == SF_sm_pNextCustomBorder);
}

// ------------------------------------------------------------------ JSON out
struct Json {
    std::string s;
    static std::string q(const std::string& v) {
        std::string o = "\"";
        for (unsigned char ch : v) {
            if (ch == '"' || ch == '\\') { o += '\\'; o += char(ch); }
            else if (ch == '\n') o += "\\n";
            else if (ch < 0x20) { char b[8]; std::snprintf(b, sizeof b, "\\u%04x", ch); o += b; }
            else o += char(ch);
        }
        return o + "\"";
    }
};
std::string list_json(const std::vector<std::string>& v) {
    std::string s = "[";
    for (size_t i = 0; i < v.size(); i++) s += (i ? ", " : "") + Json::q(v[i]);
    return s + "]";
}
std::string hex(uint32_t v) { char b[16]; std::snprintf(b, sizeof b, "0x%x", v); return b; }

// ------------------------------------------------------------------ the D3DRS names (d3d9types.h)
struct RsName { const char* name; uint32_t v; };
#define RS(n) {#n, n}
const RsName kRsNames[] = {
    RS(D3DRS_ZENABLE), RS(D3DRS_FILLMODE), RS(D3DRS_SHADEMODE), RS(D3DRS_ZWRITEENABLE), RS(D3DRS_ALPHATESTENABLE),
    RS(D3DRS_LASTPIXEL), RS(D3DRS_SRCBLEND), RS(D3DRS_DESTBLEND), RS(D3DRS_CULLMODE), RS(D3DRS_ZFUNC), RS(D3DRS_ALPHAREF),
    RS(D3DRS_ALPHAFUNC), RS(D3DRS_DITHERENABLE), RS(D3DRS_ALPHABLENDENABLE), RS(D3DRS_FOGENABLE), RS(D3DRS_SPECULARENABLE),
    RS(D3DRS_FOGCOLOR), RS(D3DRS_FOGTABLEMODE), RS(D3DRS_FOGSTART), RS(D3DRS_FOGEND), RS(D3DRS_FOGDENSITY),
    RS(D3DRS_RANGEFOGENABLE), RS(D3DRS_STENCILENABLE), RS(D3DRS_STENCILFAIL), RS(D3DRS_STENCILZFAIL), RS(D3DRS_STENCILPASS),
    RS(D3DRS_STENCILFUNC), RS(D3DRS_STENCILREF), RS(D3DRS_STENCILMASK), RS(D3DRS_STENCILWRITEMASK), RS(D3DRS_TEXTUREFACTOR),
    RS(D3DRS_WRAP0), RS(D3DRS_WRAP1), RS(D3DRS_WRAP2), RS(D3DRS_WRAP3), RS(D3DRS_WRAP4), RS(D3DRS_WRAP5), RS(D3DRS_WRAP6),
    RS(D3DRS_WRAP7), RS(D3DRS_CLIPPING), RS(D3DRS_LIGHTING), RS(D3DRS_AMBIENT), RS(D3DRS_FOGVERTEXMODE), RS(D3DRS_COLORVERTEX),
    RS(D3DRS_LOCALVIEWER), RS(D3DRS_NORMALIZENORMALS), RS(D3DRS_DIFFUSEMATERIALSOURCE), RS(D3DRS_SPECULARMATERIALSOURCE),
    RS(D3DRS_AMBIENTMATERIALSOURCE), RS(D3DRS_EMISSIVEMATERIALSOURCE), RS(D3DRS_VERTEXBLEND), RS(D3DRS_CLIPPLANEENABLE),
    RS(D3DRS_POINTSIZE), RS(D3DRS_POINTSIZE_MIN), RS(D3DRS_POINTSPRITEENABLE), RS(D3DRS_POINTSCALEENABLE), RS(D3DRS_POINTSCALE_A),
    RS(D3DRS_POINTSCALE_B), RS(D3DRS_POINTSCALE_C), RS(D3DRS_MULTISAMPLEANTIALIAS), RS(D3DRS_MULTISAMPLEMASK),
    RS(D3DRS_PATCHEDGESTYLE), RS(D3DRS_DEBUGMONITORTOKEN), RS(D3DRS_POINTSIZE_MAX), RS(D3DRS_INDEXEDVERTEXBLENDENABLE),
    RS(D3DRS_COLORWRITEENABLE), RS(D3DRS_TWEENFACTOR), RS(D3DRS_BLENDOP), RS(D3DRS_POSITIONDEGREE), RS(D3DRS_NORMALDEGREE),
    RS(D3DRS_SCISSORTESTENABLE), RS(D3DRS_SLOPESCALEDEPTHBIAS), RS(D3DRS_ANTIALIASEDLINEENABLE), RS(D3DRS_MINTESSELLATIONLEVEL),
    RS(D3DRS_MAXTESSELLATIONLEVEL), RS(D3DRS_ADAPTIVETESS_X), RS(D3DRS_ADAPTIVETESS_Y), RS(D3DRS_ADAPTIVETESS_Z),
    RS(D3DRS_ADAPTIVETESS_W), RS(D3DRS_ENABLEADAPTIVETESSELLATION), RS(D3DRS_TWOSIDEDSTENCILMODE), RS(D3DRS_CCW_STENCILFAIL),
    RS(D3DRS_CCW_STENCILZFAIL), RS(D3DRS_CCW_STENCILPASS), RS(D3DRS_CCW_STENCILFUNC), RS(D3DRS_COLORWRITEENABLE1),
    RS(D3DRS_COLORWRITEENABLE2), RS(D3DRS_COLORWRITEENABLE3), RS(D3DRS_BLENDFACTOR), RS(D3DRS_SRGBWRITEENABLE),
    RS(D3DRS_DEPTHBIAS), RS(D3DRS_WRAP8), RS(D3DRS_WRAP9), RS(D3DRS_WRAP10), RS(D3DRS_WRAP11), RS(D3DRS_WRAP12),
    RS(D3DRS_WRAP13), RS(D3DRS_WRAP14), RS(D3DRS_WRAP15), RS(D3DRS_SEPARATEALPHABLENDENABLE), RS(D3DRS_SRCBLENDALPHA),
    RS(D3DRS_DESTBLENDALPHA), RS(D3DRS_BLENDOPALPHA),
};
#undef RS
std::string rs_name(uint32_t v) {
    for (const RsName& r : kRsNames)
        if (r.v == v) return r.name;
    return "D3DRS_" + std::to_string(v);
}

bool in_list(const uint16_t* l, size_t n, uint32_t v) {
    for (size_t i = 0; i < n; i++)
        if (l[i] == v) return true;
    return false;
}
bool key_state(uint32_t v) { return pipeline_rs_index(v) >= 0; }
bool dynamic_state(uint32_t v) { return in_list(kDynamicRenderStates, std::size(kDynamicRenderStates), v); }
bool census_extra(uint32_t v) { return in_list(kCensusExtraRenderStates, std::size(kCensusExtraRenderStates), v); }

// The classes of the render states DXVK handles that are not in the pipeline key (the critic's
// M5): what each feeds in DXVK, and so where the second host meets it.
const char* audit_class(uint32_t v) {
    if (key_state(v)) return "key: the pipeline mapping and the shader-side values (d9g_pipeline)";
    if (dynamic_state(v)) return "dynamic: blend constants, stencil reference, scissor (draw-time state in both hosts)";
    switch (v) {
    case D3DRS_FOGENABLE: case D3DRS_FOGCOLOR: case D3DRS_FOGTABLEMODE: case D3DRS_FOGSTART: case D3DRS_FOGEND:
    case D3DRS_FOGDENSITY: case D3DRS_FOGVERTEXMODE: case D3DRS_RANGEFOGENABLE:
        return "shader-side, not computed in R2b: DXVK's fog spec and push data (UpdateFog; KEEP OUT, OPEN-1)";
    case D3DRS_CLIPPLANEENABLE:
        return "shader-side, not computed in R2b: clip distances (UpdateClipPlanes; KEEP OUT, OPEN-1)";
    case D3DRS_TEXTUREFACTOR: case D3DRS_SPECULARENABLE: case D3DRS_AMBIENT: case D3DRS_LIGHTING: case D3DRS_COLORVERTEX:
    case D3DRS_LOCALVIEWER: case D3DRS_NORMALIZENORMALS: case D3DRS_DIFFUSEMATERIALSOURCE: case D3DRS_SPECULARMATERIALSOURCE:
    case D3DRS_AMBIENTMATERIALSOURCE: case D3DRS_EMISSIVEMATERIALSOURCE: case D3DRS_VERTEXBLEND:
    case D3DRS_INDEXEDVERTEXBLENDENABLE: case D3DRS_TWEENFACTOR: case D3DRS_POINTSCALE_A: case D3DRS_POINTSCALE_B:
    case D3DRS_POINTSCALE_C:
        return "fixed-function only (DXVK's FF shaders; d9g maps no fixed-function draw)";
    default:
        return nullptr;
    }
}

// ------------------------------------------------------------------ the sweep's inputs
struct Case {
    std::string label;
    PipelineInput in;
    std::array<uint32_t, 256> rs{};
};

const uint32_t kFourcc[] = {0x314d3241u /*A2M1*/, 0x304d3241u /*A2M0*/, 0x7fa05000u /*RESZ*/, 0x54534e49u /*INST*/,
                            0x544e4543u /*CENT*/, 0x434f5441u /*ATOC*/, 0x4d504f43u /*COPM*/, 0x41415353u /*SSAA*/,
                            0x4244564eu /*NVDB*/};
uint32_t fb(float f) { uint32_t u; std::memcpy(&u, &f, 4); return u; }
std::vector<uint32_t> float_values() {
    return {fb(0.0f), fb(-0.0f), fb(1.0f), fb(-1.0f), fb(0.5f), fb(64.0f), fb(2047.0f), fb(1e10f), fb(NAN), fb(INFINITY),
            fb(-INFINITY), 0x00000001u /* denormal */};
}

struct Base {
    std::string name;
    uint32_t rt_fmt, rt_w, rt_h;
    uint32_t ds_fmt, ds_w, ds_h;   // ds_fmt 0: none
    int decl;                      // 0 FLOAT3; 1 the census's FLOAT3 + D3DCOLOR + FLOAT2
    uint32_t prim, method;
};

Case make_case(const Base& b, const uint32_t defaults[256]) {
    Case c;
    c.label = b.name;
    std::memcpy(c.rs.data(), defaults, 256 * 4);
    PipelineInput& in = c.in;
    in.prim = b.prim;
    in.method = uint8_t(b.method);
    in.vs_id = in.ps_id = 1;
    in.decl_id = 1;
    if (b.decl == 0) {
        in.n_elems = 1;
        in.elems[0] = VertexElem{0, 0, D3DDECLTYPE_FLOAT3, 0, D3DDECLUSAGE_POSITION, 0};
        in.stride0 = 12;
    } else {
        in.n_elems = 3;
        in.elems[0] = VertexElem{0, 0, D3DDECLTYPE_FLOAT3, 0, D3DDECLUSAGE_POSITION, 0};
        in.elems[1] = VertexElem{0, 12, D3DDECLTYPE_D3DCOLOR, 0, D3DDECLUSAGE_COLOR, 0};
        in.elems[2] = VertexElem{0, 16, D3DDECLTYPE_FLOAT2, 0, D3DDECLUSAGE_TEXCOORD, 0};
        in.stride0 = 24;
    }
    // the shaders' reflection: a vertex shader reading every declared element in declaration order
    // (the identity layout), and a pixel shader writing oC0 and sampling nothing
    in.vs_n_inputs = in.n_elems;
    for (uint32_t i = 0; i < in.n_elems; i++) in.vs_inputs[i] = VsInput{in.elems[i].usage, in.elems[i].usage_index};
    in.ps_rt_mask = 1;
    in.ps_sampler_mask = 0;
    in.rt[0] = TargetDesc{b.rt_fmt, b.rt_w, b.rt_h, 0, 0, 0, true, false};
    if (b.ds_fmt) in.ds = TargetDesc{b.ds_fmt, b.ds_w, b.ds_h, 0, 0, 0, true, false};
    for (size_t i = 0; i < kNumPipelineRS; i++) in.rs[i] = c.rs[kPipelineRenderStates[i]];
    return c;
}

void set_signature(PipelineInput& in, std::initializer_list<VsInput> sig) {
    in.vs_n_inputs = 0;
    for (const VsInput& v : sig) in.vs_inputs[in.vs_n_inputs++] = v;
}

// Every pixel-shader sampler slot, 0-15: R2b's superset stand-in for the sampler mask, a value the
// sweep still gives the reflection (the PS's slots a shader can sample).
constexpr uint32_t kAllPsSlots = 0xFFFFu;

void set_rs(Case& c, uint32_t state, uint32_t v) {
    c.rs[state] = v;
    const int i = pipeline_rs_index(state);
    if (i >= 0) c.in.rs[i] = v;
}

// ------------------------------------------------------------------ the run
struct Totals {
    uint64_t tuples = 0, equal = 0, by_rule = 0, unmapped = 0, border = 0, depth_clamp = 0, rt_feedback = 0, differs = 0;
    uint64_t standin_tuples = 0;
    std::map<std::string, uint64_t> class_counts;   // fact class -> facts
    std::map<std::string, uint64_t> standin_facts;  // fact -> tuples where a stand-in decides it
};

struct Runner {
    Params P;
    uint32_t faults = 0;
    std::vector<std::string> failures;   // the verdict's reasons
    std::string out;                     // JSON fields
    bool pending_depth_clamp = false;
    bool pending_rt_feedback = false;    // RT_FEEDBACK in the census: NEW, the user's (the critic's M4)
    // ruling 3's shader fallback, owed by the shader route where the device lacks the patch's
    // capability: BORDER (fallback BORDER sampler tuples), DEPTH_CLAMP (R_DEPTH_CLAMP tuples under
    // the "patch" ruling on a device without depth clip and clamp)
    bool owed_border = false, owed_depth_clamp = false;
    std::string test_leg;                // a test run's capability override the keys files were made under
    std::vector<std::string> pending_items;
    std::string ruling;                  // "", "residue", "patch", "shader"
    ShaderRecord record;                 // --shader-record
    bool have_record = false;

    void fail(const std::string& why) {
        if (failures.size() < 200) failures.push_back(why);
        else if (failures.size() == 200) failures.push_back("(more)");
    }
    // a verdict-level reason (a ruling's rule): first, whatever the per-tuple failures number
    void fail_first(const std::string& why) { failures.insert(failures.begin(), why); }
    void field(const std::string& k, const std::string& v) { out += (out.empty() ? "" : ",\n") + std::string("  ") + Json::q(k) + ": " + v; }
};

std::string fact_list(const std::vector<int>& f, const char* const* names) {
    std::string s;
    for (size_t i = 0; i < f.size(); i++) s += (i ? ", " : "") + std::string(names[f[i]]);
    return s;
}

// ---- the sweep
struct SweepResult {
    // disjoint: tuples = equal + by_rule + undecided + unmapped + wrong (the review's checker-logic finding 9)
    uint64_t tuples = 0, equal = 0, unmapped = 0, undecided = 0, wrong = 0, by_rule = 0;
    std::vector<std::string> wrong_examples;
    std::set<std::string> unmapped_names;
    std::map<std::string, std::map<std::string, std::set<std::string>>> table;   // state -> value -> outcomes
    std::set<int> differing_fields;          // facts any mapped tuple differs in (empty on pass)
    std::set<std::string> wrong_reasons;     // failures no fact names (refusals, SDL's validation)
    std::set<std::string> obligations;       // obligations that failed ("BGRA", "FAN")
    std::bitset<256> read;
    std::vector<std::string> key_insufficient;
    // disjoint: samplers = sampler_mapped + sampler_by_rule + sampler_fallback + sampler_unmapped + sampler_wrong;
    // sampler_border_mapped counts the passing ones with a BORDER axis mapped (in mapped or by rule)
    uint64_t samplers = 0, sampler_mapped = 0, sampler_by_rule = 0, sampler_fallback = 0, sampler_unmapped = 0, sampler_wrong = 0;
    uint64_t sampler_border_mapped = 0;
    std::set<int> sampler_differing;
};

std::string outcome_of(const PipelineCheck& c) {
    if (c.unmapped()) return "unmapped";
    if (c.fails()) return "DIFFERS";
    if (c.rt_feedback) return "undecided RT_FEEDBACK";
    if (c.fan || c.bgra || c.null_stream) return "equal by rule";
    return "equal";
}

void sweep_one(SweepResult& R, const Case& c, const Params& P, uint32_t faults, const std::string& state, const std::string& value) {
    uint32_t full[256];
    fill_full_rs(c.in, c.rs.data(), full);
    PipelineCheck k = check_pipeline(c.in, full, P, faults);
    R.tuples++;
    const std::string o = outcome_of(k);
    R.table[state][value].insert(o);
    if (k.unmapped()) {
        R.unmapped++;
        for (const auto& u : k.desc.unmapped) R.unmapped_names.insert(u);
        return;
    }
    R.read |= k.dx.rs_read;
    if (k.fails()) {
        R.wrong++;
        for (int f : k.differ) R.differing_fields.insert(f);
        if (!k.reason.empty()) R.wrong_reasons.insert(k.reason);
        for (const auto& o : k.obligations) R.obligations.insert(o);
        if (R.wrong_examples.size() < 40) {
            std::string e = c.label + " " + state + "=" + value + ": " + fact_list(k.differ, d9g_pipeline_fact_names) + " (DXVK";
            for (int f : k.differ) e += " " + hex(k.dx.facts.v[f]);
            e += ", SDL";
            for (int f : k.differ) e += " " + hex(k.sdl.v[f]);
            for (const auto& o : k.obligations) e += "; the " + o + " obligation does not hold";
            R.wrong_examples.push_back(e + ")" + (k.reason.empty() ? "" : " " + k.reason));
        }
        return;
    }
    if (k.rt_feedback) R.undecided++;
    else if (k.fan || k.bgra || k.null_stream) R.by_rule++;
    else R.equal++;
}

SweepResult run_sweep(const Params& P, uint32_t faults) {
    SweepResult R;
    uint32_t defaults[256];
    bool set[256];
    uint32_t ss_def[14];
    d9g_ref::dxvk_defaults(defaults, set, ss_def, P.dxvk, true);
    const Base bases[] = {
        {"rt1280-ds1280", D3DFMT_A8R8G8B8, 1280, 720, D3DFMT_D24S8, 1280, 720, 0, D3DPT_TRIANGLELIST, M_DP},
        {"rt512-ds1280", D3DFMT_A8R8G8B8, 512, 512, D3DFMT_D24S8, 1280, 720, 0, D3DPT_TRIANGLELIST, M_DP},
        {"rt512-ds256", D3DFMT_A8R8G8B8, 512, 512, D3DFMT_D24S8, 256, 256, 0, D3DPT_TRIANGLELIST, M_DP},
        {"no-ds", D3DFMT_A8R8G8B8, 1280, 720, 0, 0, 0, 0, D3DPT_TRIANGLELIST, M_DP},
        {"census-like-up-strip", D3DFMT_A8R8G8B8, 1280, 720, D3DFMT_D24S8, 1280, 720, 1, D3DPT_TRIANGLESTRIP, M_DPUP},
    };
    std::vector<Case> base_cases;
    for (const Base& b : bases) {
        Case c = make_case(b, defaults);
        set_rs(c, D3DRS_MULTISAMPLEANTIALIAS, 0);
        base_cases.push_back(c);
        sweep_one(R, c, P, faults, "base", b.name);
    }
    // ---- one field at a time: every pipeline state over its values
    for (const Case& base : base_cases) {
        for (size_t si = 0; si < kNumPipelineRS; si++) {
            const uint32_t st = kPipelineRenderStates[si];
            std::vector<uint32_t> vals;
            for (uint32_t v = 0; v <= 20; v++) vals.push_back(v);
            for (uint32_t v : {0x7Fu, 0x80u, 0xFFu, 0x100u, 0xFFFFu, 0xFFFFFFFEu, 0xFFFFFFFFu}) vals.push_back(v);
            for (uint32_t v : float_values()) vals.push_back(v);
            for (uint32_t v : kFourcc) vals.push_back(v);
            for (uint32_t v : vals) {
                Case c = base;
                set_rs(c, st, v);
                sweep_one(R, c, P, faults, rs_name(st), hex(v));
            }
        }
        // primitive types, methods, declaration types, strides, frequencies
        for (uint32_t prim : {0u, 1u, 2u, 3u, 4u, 5u, 6u, 7u, 8u, 0x7FFFFFFFu, 0xFFFFFFFFu})
            for (uint32_t m = 0; m < 4; m++) {
                Case c = base;
                c.in.prim = prim;
                c.in.method = uint8_t(m);
                sweep_one(R, c, P, faults, "prim/method", std::to_string(prim) + "/" + std::to_string(m));
            }
        for (uint32_t t = 0; t <= 17; t++) {
            Case c = base;
            c.in.elems[0].type = uint8_t(t);
            sweep_one(R, c, P, faults, "decltype", std::to_string(t));
            c.in.elems[0].stream = 1;
            sweep_one(R, c, P, faults, "decltype on stream 1", std::to_string(t));
        }
        for (uint32_t stride : {0u, 4u, 12u, 16u, 24u, 32u, 36u, 4096u}) {
            Case c = base;
            c.in.stride0 = stride;
            sweep_one(R, c, P, faults, "stride0", std::to_string(stride));
        }
        for (uint32_t fq : {1u, 2u, 0x40000002u, 0x80000003u}) {
            Case c = base;
            c.in.freq0 = fq;
            sweep_one(R, c, P, faults, "freq0", hex(fq));
        }
        // a stream other than 0 (SetStreamSourceFreq's instancing): the first element on stream 1 at
        // its own stride, per vertex (1, 2) and per instance (INSTANCEDATA with divisor 1, which SDL's
        // instance rate is, and 2, which it cannot step by: unmapped)
        for (uint32_t fq : {1u, 2u, 0x80000001u, 0x80000002u}) {
            Case c = base;
            c.in.elems[0].stream = 1;
            c.in.stream_stride[1] = 12;
            c.in.stream_freq[1] = fq;
            sweep_one(R, c, P, faults, "stream 1 frequency", hex(fq));
        }
        for (uint32_t usage : {0u, 9u /* POSITIONT */}) {
            Case c = base;
            c.in.elems[0].usage = uint8_t(usage);
            c.in.vs_id = 0;
            sweep_one(R, c, P, faults, "no VS, usage", std::to_string(usage));
            c.in.vs_id = 1;
            c.in.ps_id = 0;
            sweep_one(R, c, P, faults, "no PS, usage", std::to_string(usage));
            c.in.ps_id = 1;   // both shaders: POSITIONT turns DXVK's vertex processing fixed-function (N7)
            sweep_one(R, c, P, faults, "VS and PS, usage", std::to_string(usage));
            c.in.decl_id = 0;   // no declaration, the same (UseProgrammableVS)
            sweep_one(R, c, P, faults, "VS and PS, no declaration, usage", std::to_string(usage));
        }
        // the vertex input by the shader's signature (U9): the declaration's semantics in other
        // orders, inputs it lacks (the null stream, once or many times), an element unread, a
        // semantic the shader reads twice, sixteen inputs and more; on the census's declaration and
        // on ones with a semantic declared twice (the last element wins), an UNUSED element, an
        // element on stream 1, a usage index DXVK's packed semantic aliases (16 reads as 0), and a
        // POSITIONT element (fixed function in DXVK: never mapped)
        if (base.in.n_elems == 3) {
            const VsInput P0{D3DDECLUSAGE_POSITION, 0}, C0{D3DDECLUSAGE_COLOR, 0}, T0{D3DDECLUSAGE_TEXCOORD, 0},
                T1{D3DDECLUSAGE_TEXCOORD, 1}, N0{D3DDECLUSAGE_NORMAL, 0}, W0{D3DDECLUSAGE_BLENDWEIGHT, 0};
            const std::vector<std::pair<std::string, std::vector<VsInput>>> sigs = {
                {"P", {P0}}, {"CP", {C0, P0}}, {"TCP", {T0, C0, P0}}, {"PN", {P0, N0}}, {"NTPC", {N0, T0, P0, C0}},
                {"PP", {P0, P0}}, {"PT1", {P0, T1}}, {"CTPNW", {C0, T0, P0, N0, W0}}, {"none", {}}};
            struct Decl { std::string name; std::vector<VertexElem> elems; };
            const std::vector<Decl> decls = {
                {"census", {base.in.elems[0], base.in.elems[1], base.in.elems[2]}},
                {"colour twice", {{0, 0, D3DDECLTYPE_FLOAT3, 0, D3DDECLUSAGE_POSITION, 0}, {0, 12, D3DDECLTYPE_D3DCOLOR, 0, D3DDECLUSAGE_COLOR, 0},
                                  {0, 16, D3DDECLTYPE_FLOAT4, 0, D3DDECLUSAGE_COLOR, 0}}},
                {"unused colour", {{0, 0, D3DDECLTYPE_FLOAT3, 0, D3DDECLUSAGE_POSITION, 0}, {0, 12, D3DDECLTYPE_UNUSED, 0, D3DDECLUSAGE_COLOR, 0},
                                   {0, 16, D3DDECLTYPE_FLOAT2, 0, D3DDECLUSAGE_TEXCOORD, 0}}},
                {"colour on stream 1", {{0, 0, D3DDECLTYPE_FLOAT3, 0, D3DDECLUSAGE_POSITION, 0}, {1, 0, D3DDECLTYPE_D3DCOLOR, 0, D3DDECLUSAGE_COLOR, 0},
                                        {0, 12, D3DDECLTYPE_FLOAT2, 0, D3DDECLUSAGE_TEXCOORD, 0}}},
                {"texcoord16", {{0, 0, D3DDECLTYPE_FLOAT3, 0, D3DDECLUSAGE_POSITION, 0}, {0, 12, D3DDECLTYPE_D3DCOLOR, 0, D3DDECLUSAGE_COLOR, 0},
                                {0, 16, D3DDECLTYPE_FLOAT2, 0, D3DDECLUSAGE_TEXCOORD, 16}}},
                {"positiont", {{0, 0, D3DDECLTYPE_FLOAT4, 0, D3DDECLUSAGE_POSITIONT, 0}, {0, 16, D3DDECLTYPE_D3DCOLOR, 0, D3DDECLUSAGE_COLOR, 0},
                               {0, 20, D3DDECLTYPE_FLOAT2, 0, D3DDECLUSAGE_TEXCOORD, 0}}}};
            for (const Decl& dl : decls)
                for (const auto& [sn, sig] : sigs) {
                    Case c = base;
                    c.in.n_elems = uint8_t(dl.elems.size());
                    for (size_t e = 0; e < dl.elems.size(); e++) c.in.elems[e] = dl.elems[e];
                    c.in.vs_n_inputs = 0;
                    for (const VsInput& v : sig) c.in.vs_inputs[c.in.vs_n_inputs++] = v;
                    sweep_one(R, c, P, faults, "vertex input: declaration x signature", dl.name + " x " + sn);
                }
            Case c = base;   // sixteen inputs, fifteen of them the null stream's
            set_signature(c.in, {P0});
            for (uint32_t i = 1; i < 16; i++) c.in.vs_inputs[c.in.vs_n_inputs++] = VsInput{D3DDECLUSAGE_TEXCOORD, uint8_t(i - 1)};
            sweep_one(R, c, P, faults, "vertex input: declaration x signature", "census x P + 15 texcoords");
            c.in.vs_inputs_overflow = true;
            sweep_one(R, c, P, faults, "vertex input: declaration x signature", "census x more than 16 inputs");
        }
        // states d9g leaves unmapped and the reference refuses, never models (the reviews'
        // transcription findings 1, 2, 6 and 7): a target at a mip level other than 0, a
        // multisample type, an FVF declaration, a sampled depth-stencil surface
        for (uint32_t lv : {1u, 2u}) {
            Case c = base;
            c.in.rt[0].level = lv;
            c.in.rt[0].texture = true;
            sweep_one(R, c, P, faults, "render target mip level", std::to_string(lv));
            if (base.in.ds.bound) {
                Case d = base;
                d.in.ds.level = lv;
                d.in.ds.texture = true;
                sweep_one(R, d, P, faults, "depth-stencil mip level", std::to_string(lv));
            }
        }
        for (uint32_t ms : {1u, 2u, 3u, 4u})
            for (uint32_t q : {0u, 1u, 2u}) {
                Case c = base;
                c.in.rt[0].ms = ms;
                c.in.rt[0].quality = q;
                if (c.in.ds.bound) { c.in.ds.ms = ms; c.in.ds.quality = q; }
                sweep_one(R, c, P, faults, "multisample type/quality", std::to_string(ms) + "/" + std::to_string(q));
            }
        {
            Case c = base;
            c.in.fvf_decl = true;
            sweep_one(R, c, P, faults, "FVF declaration", "1");
        }
        if (base.in.ds.bound) {
            Case c = base;
            c.in.ds.texture = true;
            c.in.ds_tex_sampled = 1;
            sweep_one(R, c, P, faults, "depth-stencil surface sampled", "1");
        }
    }
    // ---- products over each function's read set (the critic's S2)
    const Case& b0 = base_cases[0];
    // blend x colour write x RT format
    for (uint32_t rtf : {uint32_t(D3DFMT_A8R8G8B8), uint32_t(D3DFMT_X8R8G8B8), uint32_t(D3DFMT_R5G6B5),
                         uint32_t(D3DFMT_A16B16G16R16F), uint32_t(D3DFMT_R32F)})
        for (uint32_t en : {0u, 1u})
            for (uint32_t src = 1; src <= 17; src++)
                for (uint32_t dst = 1; dst <= 17; dst++)
                    for (uint32_t op = 1; op <= 5; op++)
                        for (uint32_t cw : {0u, 7u, 8u, 15u}) {
                            if (rtf != D3DFMT_A8R8G8B8 && (op > 1 || cw == 7 || cw == 8)) continue;
                            Case c = b0;
                            c.in.rt[0].fmt = rtf;
                            set_rs(c, D3DRS_ALPHABLENDENABLE, en);
                            set_rs(c, D3DRS_SRCBLEND, src);
                            set_rs(c, D3DRS_DESTBLEND, dst);
                            set_rs(c, D3DRS_BLENDOP, op);
                            set_rs(c, D3DRS_COLORWRITEENABLE, cw);
                            sweep_one(R, c, P, faults, "blend product", std::to_string(rtf) + ":" + std::to_string(en) + ":" +
                                      std::to_string(src) + ":" + std::to_string(dst) + ":" + std::to_string(op) + ":" + std::to_string(cw));
                        }
    for (uint32_t src = 1; src <= 17; src++)
        for (uint32_t dst = 1; dst <= 17; dst++)
            for (uint32_t op = 1; op <= 5; op++) {
                Case c = b0;
                set_rs(c, D3DRS_ALPHABLENDENABLE, 1);
                set_rs(c, D3DRS_SEPARATEALPHABLENDENABLE, 1);
                set_rs(c, D3DRS_SRCBLENDALPHA, src);
                set_rs(c, D3DRS_DESTBLENDALPHA, dst);
                set_rs(c, D3DRS_BLENDOPALPHA, op);
                sweep_one(R, c, P, faults, "separate alpha product", std::to_string(src) + ":" + std::to_string(dst) + ":" + std::to_string(op));
            }
    // depth x stencil x depth-stencil format (stencil normalisation reads the depth op)
    for (uint32_t dsf : {uint32_t(D3DFMT_D24S8), uint32_t(D3DFMT_D16), uint32_t(D3DFMT_D24X8), uint32_t(D3DFMT_D32F_LOCKABLE), 0u})
        for (uint32_t ze : {0u, 1u, 2u})
            for (uint32_t zw : {0u, 1u})
                for (uint32_t zf = 1; zf <= 8; zf++)
                    for (uint32_t se : {0u, 1u})
                        for (uint32_t sf = 1; sf <= 8; sf++)
                            for (uint32_t sp : {1u, 3u})
                                for (uint32_t sfail : {1u, 2u})
                                    for (uint32_t szf : {1u, 7u})
                                        for (uint32_t wm : {0u, 0xFFu})
                                            for (uint32_t two : {0u, 1u}) {
                                                if (!se && (sf > 1 || sp > 1 || sfail > 1 || szf > 1 || wm || two)) continue;
                                                Case c = b0;
                                                if (dsf) c.in.ds.fmt = dsf;
                                                else c.in.ds = TargetDesc{};
                                                set_rs(c, D3DRS_ZENABLE, ze);
                                                set_rs(c, D3DRS_ZWRITEENABLE, zw);
                                                set_rs(c, D3DRS_ZFUNC, zf);
                                                set_rs(c, D3DRS_STENCILENABLE, se);
                                                set_rs(c, D3DRS_STENCILFUNC, sf);
                                                set_rs(c, D3DRS_STENCILPASS, sp);
                                                set_rs(c, D3DRS_STENCILFAIL, sfail);
                                                set_rs(c, D3DRS_STENCILZFAIL, szf);
                                                set_rs(c, D3DRS_STENCILWRITEMASK, wm);
                                                set_rs(c, D3DRS_TWOSIDEDSTENCILMODE, two);
                                                set_rs(c, D3DRS_CCW_STENCILFUNC, 9 - sf);
                                                set_rs(c, D3DRS_CCW_STENCILPASS, 4 - sp);
                                                sweep_one(R, c, P, faults, "depth-stencil product",
                                                          std::to_string(dsf) + ":" + std::to_string(ze) + ":" + std::to_string(zw) + ":" +
                                                          std::to_string(zf) + ":" + std::to_string(se) + ":" + std::to_string(sf) + ":" +
                                                          std::to_string(sp) + ":" + std::to_string(sfail) + ":" + std::to_string(szf) + ":" +
                                                          hex(wm) + ":" + std::to_string(two));
                                            }
    // the depth-stencil binding x RT0 extents x write masks x the PS output mask x hazards: where
    // RT0's texture is bound (a pixel-shader slot, a vertex-shader slot) and the PS sampler mask
    // (every slot, none, the slot, another slot)
    for (uint32_t rw : {1u, 256u, 512u, 1280u})
        for (uint32_t dw : {256u, 1280u})
            for (uint32_t cw : {0u, 15u, 0x10u, 0xFFFFFFF0u})
                for (uint32_t psm : {0u, 1u})
                    for (uint32_t stages : {0u, 0x20u, 0x20000u, 0x60u})
                        for (uint32_t smask : {kAllPsSlots, 0u, 0x20u, 0x40u}) {
                            if (!stages && smask != kAllPsSlots) continue;
                            Case c = b0;
                            c.in.rt[0].w = c.in.rt[0].h = rw;
                            c.in.ds.w = c.in.ds.h = dw;
                            set_rs(c, D3DRS_COLORWRITEENABLE, cw);
                            c.in.ps_rt_mask = psm;
                            c.in.rt_tex_sampled = stages ? 1u : 0u;
                            c.in.rt_tex_stages = stages;
                            c.in.ps_sampler_mask = smask;
                            c.in.rt[0].texture = stages != 0;
                            sweep_one(R, c, P, faults, "framebuffer product",
                                      std::to_string(rw) + ":" + std::to_string(dw) + ":" + std::to_string(cw) + ":" +
                                      std::to_string(psm) + ":" + hex(stages) + ":" + hex(smask));
                        }
    // a hazard on a target other than RT0, with RT0 a different extent (unresolvable whether or
    // not the target is bound: UpdateActiveHazardsRT reads no extent), and on two targets at once
    for (uint32_t w1 : {1280u, 512u})
        for (uint32_t cw1 : {0u, 15u})
            for (uint32_t smask : {kAllPsSlots, 0u}) {
                Case c = b0;
                c.in.rt[1] = TargetDesc{D3DFMT_A8R8G8B8, w1, w1 == 1280u ? 720u : w1, 0, 0, 0, true, true};
                c.in.ps_rt_mask = 0x3;
                set_rs(c, D3DRS_COLORWRITEENABLE1, cw1);
                c.in.rt_tex_sampled = 2;
                c.in.rt_tex_stages = 0x80;
                c.in.ps_sampler_mask = smask;
                sweep_one(R, c, P, faults, "hazard on RT1", std::to_string(w1) + ":" + std::to_string(cw1) + ":" + hex(smask));
                c.in.rt[0].texture = true;
                c.in.rt_tex_sampled = 3;
                c.in.rt_tex_stages = 0xC0;
                sweep_one(R, c, P, faults, "hazards on RT0 and RT1", std::to_string(w1) + ":" + std::to_string(cw1) + ":" + hex(smask));
            }
    // more render targets: slots, extents, gaps
    for (uint32_t mask = 1; mask < 16; mask++) {
        Case c = b0;
        for (uint32_t i = 0; i < 4; i++)
            if ((mask >> i) & 1u) c.in.rt[i] = TargetDesc{D3DFMT_A8R8G8B8, 1280, 720, 0, 0, 0, true, false};
            else c.in.rt[i] = TargetDesc{};
        c.in.ps_rt_mask = 0xF;
        sweep_one(R, c, P, faults, "render-target slots", hex(mask));
    }
    // ATOC x vendor x multisampling (UpdateAlphaToCoverangeAndAlphaTest)
    for (uint32_t ps : {fb(1.0f), 0x314d3241u, 0x304d3241u})
        for (uint32_t ty : {0u, 0x434f5441u})
            for (uint32_t ate : {0u, 1u})
                for (uint32_t ms : {0u, 2u}) {
                    Case c = b0;
                    set_rs(c, D3DRS_POINTSIZE, ps);
                    set_rs(c, D3DRS_ADAPTIVETESS_Y, ty);
                    set_rs(c, D3DRS_ALPHATESTENABLE, ate);
                    set_rs(c, D3DRS_ALPHAFUNC, D3DCMP_GREATER);
                    c.in.rt[0].ms = ms;
                    c.in.ds.ms = ms;
                    sweep_one(R, c, P, faults, "atoc product", hex(ps) + ":" + hex(ty) + ":" + std::to_string(ate) + ":" + std::to_string(ms));
                }
    // point lists with the point states
    for (uint32_t sprite : {0u, 1u})
        for (uint32_t scale : {0u, 1u})
            for (uint32_t vs : {0u, 1u}) {
                Case c = b0;
                c.in.prim = D3DPT_POINTLIST;
                c.in.vs_id = vs;
                set_rs(c, D3DRS_POINTSPRITEENABLE, sprite);
                set_rs(c, D3DRS_POINTSCALEENABLE, scale);
                sweep_one(R, c, P, faults, "point product", std::to_string(sprite) + ":" + std::to_string(scale) + ":" + std::to_string(vs));
            }

    // ---- key sufficiency (9.4): no render state outside the key changes DXVK's facts
    for (const Case& base : base_cases) {
        uint32_t full[256];
        fill_full_rs(base.in, base.rs.data(), full);
        const d9g_ref::PipelineResult ref = d9g_ref::dxvk_pipeline(base.in, full, P.dxvk, d9g_ref::standins_chosen(base.in));
        for (uint32_t st = 0; st < 256; st++) {
            if (key_state(st)) continue;
            std::vector<uint32_t> vals = {0u, 1u, 2u, 3u, 0xFFu, 0xFFFFFFFFu, fb(1.0f), fb(0.5f), fb(NAN)};
            for (uint32_t v : kFourcc) vals.push_back(v);
            for (uint32_t v : vals) {
                uint32_t p[256];
                std::memcpy(p, full, sizeof p);
                p[st] = v;
                const d9g_ref::PipelineResult r = d9g_ref::dxvk_pipeline(base.in, p, P.dxvk, d9g_ref::standins_chosen(base.in));
                if (std::memcmp(r.facts.v, ref.facts.v, sizeof r.facts.v) != 0) {
                    R.key_insufficient.push_back(rs_name(st) + "=" + hex(v) + " on " + base.label);
                    break;
                }
            }
        }
    }

    // ---- samplers: every address mode triple, then filters x mip x levels x cube x depth x
    // anisotropy x bias x max mip level (PLAN.md 9.5, the critic's S2 extremes)
    auto sampler_one = [&](const SamplerInput& in, const std::string& label) {
        SamplerCheck k = check_sampler(in, P, faults);
        R.samplers++;
        std::string o;
        if (k.unmapped()) { R.sampler_unmapped++; o = "unmapped"; for (const auto& u : k.desc.unmapped) R.unmapped_names.insert(u); }
        else if (!k.differ.empty() || !k.reason.empty()) {
            R.sampler_wrong++;
            for (int f : k.differ) R.sampler_differing.insert(f);
            if (!k.reason.empty()) R.wrong_reasons.insert(k.reason);
            o = "DIFFERS";
            if (R.sampler_wrong <= 30)
                R.wrong_examples.push_back("sampler " + label + ": " + fact_list(k.differ, d9g_sampler_fact_names) + " " + k.reason);
        } else if (k.fallback) { R.sampler_fallback++; o = "fallback BORDER"; }
        else if (k.by_rule) { R.sampler_by_rule++; o = "equal by rule BORDER_SWIZZLE"; }
        else { R.sampler_mapped++; o = "equal"; }
        if (k.border_mapped && o.rfind("equal", 0) == 0) R.sampler_border_mapped++;
        // value by value (S3): each sampler state's value, and the texture's properties
        static const char* kSs[14] = {"", "ADDRESSU", "ADDRESSV", "ADDRESSW", "BORDERCOLOR", "MAGFILTER", "MINFILTER",
                                      "MIPFILTER", "MIPMAPLODBIAS", "MAXMIPLEVEL", "MAXANISOTROPY", "SRGBTEXTURE",
                                      "ELEMENTINDEX", "DMAPOFFSET"};
        for (int st = 1; st < 14; st++) R.table[std::string("D3DSAMP_") + kSs[st]][hex(in.ss[st])].insert(o);
        const std::string tex = std::string(in.tex ? "levels " + std::to_string(in.levels) + " format " + std::to_string(in.tex_fmt) : "none") +
                                (in.cube ? " cube" : "") + (in.depth ? " depth" : "");
        R.table["sampler texture"][tex].insert(o);
        // the samplers with a BORDER axis alone, by texture and by colour: where the patch maps
        // BORDER and where the shader route owes it (an X8 texture's never, the plan's B1)
        bool border_axis = false;
        for (int st : {D3DSAMP_ADDRESSU, D3DSAMP_ADDRESSV, D3DSAMP_ADDRESSW}) border_axis |= in.ss[st] == D3DTADDRESS_BORDER;
        if (border_axis && !in.cube) {
            R.table["sampler texture with a BORDER axis"][tex].insert(o);
            R.table["D3DSAMP_BORDERCOLOR with a BORDER axis"][hex(in.ss[D3DSAMP_BORDERCOLOR]) + (in.depth ? " depth" : "")].insert(o);
        }
        (void)label;
    };
    // every address triple x border colours (the three standard ones, a custom one, and two that
    // only DXVK's one-component match with depth compare makes standard: white with alpha 0, red)
    // x every texture format d9g makes (d9g_format.h all_formats: a format added later is swept
    // with no change here; X8R8G8B8's DXVK view is not the identity, D24S8 turns depth compare on)
    // and none
    std::vector<const FormatInfo*> texes{nullptr};
    {
        size_t nf = 0;
        const FormatInfo* fs = all_formats(&nf);
        for (size_t i = 0; i < nf; i++) texes.push_back(&fs[i]);
    }
    for (uint32_t u = 0; u <= 6; u++)
        for (uint32_t v = 0; v <= 6; v++)
            for (uint32_t w = 0; w <= 6; w++)
                for (uint32_t bc : {0x00000000u, 0xFF000000u, 0xFFFFFFFFu, 0x80402010u, 0x00ffffffu, 0xffff0000u})
                    for (const FormatInfo* fi : texes) {
                        SamplerInput in;
                        for (uint32_t i = 0; i < 14; i++) in.ss[i] = ss_def[i];
                        in.ss[D3DSAMP_ADDRESSU] = u; in.ss[D3DSAMP_ADDRESSV] = v; in.ss[D3DSAMP_ADDRESSW] = w;
                        in.ss[D3DSAMP_BORDERCOLOR] = bc;
                        in.tex = fi != nullptr; in.levels = in.tex ? 6 : 0; in.tex_fmt = fi ? fi->d3d : 0;
                        in.depth = fi && fi->depth;
                        sampler_one(in, std::string("address ") + (fi ? fi->name : "none") + " " + std::to_string(u) +
                                            std::to_string(v) + std::to_string(w) + " border " + hex(bc));
                    }
    const uint32_t biases[] = {fb(0.0f), fb(-0.5f), fb(1.0f / 512.0f), fb(40.0f), fb(-40.0f), fb(NAN), fb(INFINITY), fb(0.25f)};
    for (uint32_t mn = 0; mn <= 4; mn++)
        for (uint32_t mg = 0; mg <= 4; mg++)
            for (uint32_t mp = 0; mp <= 3; mp++)
                for (uint32_t lv : {0u, 1u, 6u})
                    for (bool cube : {false, true})
                        for (bool depth : {false, true})
                            for (uint32_t an : {0u, 1u, 2u, 16u, 17u, 0xFFFFFFFFu})
                                for (uint32_t bi : biases)
                                    for (uint32_t mm : {0u, 3u, 16u, 0xFFFFFFFFu}) {
                                        SamplerInput in;
                                        for (uint32_t i = 0; i < 14; i++) in.ss[i] = ss_def[i];
                                        in.ss[D3DSAMP_MINFILTER] = mn; in.ss[D3DSAMP_MAGFILTER] = mg; in.ss[D3DSAMP_MIPFILTER] = mp;
                                        in.ss[D3DSAMP_MAXANISOTROPY] = an; in.ss[D3DSAMP_MIPMAPLODBIAS] = bi;
                                        in.ss[D3DSAMP_MAXMIPLEVEL] = mm;
                                        // a depth texture is a D24S8 one, its depth bit the
                                        // device's rule (d9g_format: the format's depth aspect),
                                        // which the reference derives itself (DetermineShadowState)
                                        in.tex = lv != 0; in.levels = lv; in.cube = cube;
                                        in.tex_fmt = depth ? uint32_t(D3DFMT_D24S8) : uint32_t(D3DFMT_A8R8G8B8);
                                        const FormatInfo* fi = format_info(in.tex_fmt);
                                        in.depth = in.tex && fi && fi->depth;
                                        sampler_one(in, "filter " + std::to_string(mn) + std::to_string(mg) + std::to_string(mp) + " lv" +
                                                        std::to_string(lv) + (cube ? " cube" : "") + (depth ? " D24S8" : "") +
                                                        " an" + hex(an) + " bias" + hex(bi) + " mm" + hex(mm));
                                    }
    for (uint32_t srgb : {0u, 1u, 2u, 3u}) {
        SamplerInput in;
        for (uint32_t i = 0; i < 14; i++) in.ss[i] = ss_def[i];
        in.ss[D3DSAMP_SRGBTEXTURE] = srgb;
        in.tex = true; in.levels = 1; in.tex_fmt = D3DFMT_A8R8G8B8;
        sampler_one(in, "srgb " + std::to_string(srgb));
    }
    return R;
}

// ---- the census: the device's keys files
struct StreamTuples {
    std::string name, path, device_name;
    std::string keys_sha256, library_sha256;   // the keys file's own hash; the binary the device ran in
    std::vector<std::string> vs_sha256, ps_sha256;   // the keys file's "shaders": by creation id - 1
    // the device's own reflection of each shader, by creation id - 1 (the keys file's "shaders"):
    // a vertex shader's input signature, a pixel shader's sampler and output masks
    std::vector<d9g_ref::VsSignature> vs_reflection;
    std::vector<std::pair<uint32_t, uint32_t>> ps_reflection;
    std::vector<PipelineInput> inputs;
    std::vector<std::array<uint32_t, 256>> full_rs;
    std::vector<uint64_t> input_draws;
    std::vector<SamplerInput> samplers;
    std::vector<uint64_t> sampler_draws;
    // the device's own BORDER decision per sampler (its keys file's fallback_border and
    // border_colour), which the census holds to the check's mapping of the same tuple
    std::vector<bool> sampler_fallback;
    std::vector<int32_t> sampler_colour;
};

// Every field the check reads must be in the keys file: none takes a default (the review's
// checker-logic finding 4).
PipelineInput parse_input(const Value& r, const std::vector<uint32_t>& key_order) {
    PipelineInput in;
    const Value& rs = r["rs"];
    if (rs.size() != kNumPipelineRS || key_order.size() != kNumPipelineRS) throw std::runtime_error("keys file: render-state list differs");
    for (size_t i = 0; i < kNumPipelineRS; i++) {
        if (key_order[i] != kPipelineRenderStates[i]) throw std::runtime_error("keys file: render-state order differs");
        in.rs[i] = rs[i].u32();
    }
    in.prim = r["prim"].u32();
    in.method = uint8_t(r["method"].u32());
    in.stride0 = r["stride0"].u32();
    in.freq0 = r["freq0"].u32();
    // the other streams the declaration names, [stream, stride, frequency] (absent: none, as every
    // keys file before streams 1-14 were mapped)
    bool listed[16] = {};
    if (r.has("streams")) {
        const Value& sv = r["streams"];
        for (size_t i = 0; i < sv.size(); i++) {
            if (sv[i].size() != 3) throw std::runtime_error("keys file: a stream that is not three numbers");
            const uint32_t st = sv[i][0].u32();
            if (st == 0 || st >= 16) throw std::runtime_error("keys file: a stream entry for stream 0 or past 15");
            in.stream_stride[st] = sv[i][1].u32();
            in.stream_freq[st] = sv[i][2].u32();
            listed[st] = true;
        }
    }
    const Value& el = r["elems"];
    if (el.size() > 16) throw std::runtime_error("keys file: more than 16 elements in an input");
    in.n_elems = uint8_t(el.size());
    for (size_t i = 0; i < in.n_elems; i++) {
        if (el[i].size() != 6) throw std::runtime_error("keys file: an element that is not six numbers");
        in.elems[i] = VertexElem{uint16_t(el[i][0].u32()), uint16_t(el[i][1].u32()), uint8_t(el[i][2].u32()),
                                 uint8_t(el[i][3].u32()), uint8_t(el[i][4].u32()), uint8_t(el[i][5].u32())};
        // an element on stream 1-15 is compared on that stream's stride and frequency, which the
        // device writes in "streams" for every stream its declaration names; a keys file without
        // that entry (one made before streams 1-14 were mapped) is refused, never read as stride
        // and frequency 0
        const uint32_t st = in.elems[i].stream;
        if (st >= 1 && st < 16 && !listed[st])
            throw std::runtime_error("keys file: an element on stream " + std::to_string(st) +
                                     " with no \"streams\" entry for it (a keys file from before streams 1-14 "
                                     "were mapped); make it again with the current device");
    }
    in.decl_overflow = r["decl_overflow"].boolean();
    in.fvf_decl = r["fvf"].boolean();
    auto tgt = [](const Value& t) {
        if (t.size() != 8) throw std::runtime_error("keys file: a target that is not eight values");
        return TargetDesc{t[0].u32(), t[1].u32(), t[2].u32(), t[3].u32(), t[4].u32(), t[5].u32(), t[6].boolean(), t[7].boolean()};
    };
    if (r["rt"].size() != 4) throw std::runtime_error("keys file: not four render targets");
    for (size_t i = 0; i < 4; i++) in.rt[i] = tgt(r["rt"][i]);
    in.ds = tgt(r["ds"]);
    in.rt_tex_sampled = r["rt_tex_sampled"].u32();
    in.rt_tex_stages = r["rt_tex_stages"].u32();
    in.ds_tex_sampled = r["ds_tex_sampled"].u32();
    in.ps_rt_mask = r["ps_rt_mask"].u32();
    in.ps_sampler_mask = r["ps_sampler_mask"].u32();
    const Value& sig = r["vs_inputs"];
    if (sig.size() > in.vs_inputs.size()) throw std::runtime_error("keys file: more than 16 vertex-shader inputs in an input");
    in.vs_n_inputs = uint8_t(sig.size());
    for (size_t i = 0; i < in.vs_n_inputs; i++) {
        const auto entry = read_signature_entry(sig[i], "keys file");
        in.vs_inputs[i] = VsInput{entry.first, entry.second};
    }
    in.vs_inputs_overflow = r["vs_inputs_overflow"].boolean();
    in.vs_id = r["vs"].u32();
    in.ps_id = r["ps"].u32();
    in.decl_id = r["decl"].u32();
    return in;
}

StreamTuples load_dump(const std::string& path, const Params& P) {
    StreamTuples t;
    t.path = path;
    const std::string text = read_file(path);
    t.keys_sha256 = sha256_of(text);
    Value v = mini_json::parse(text);
    if (v["schema"].str() != "sa2-d3d9gpu-keys/v3") throw std::runtime_error(path + ": not sa2-d3d9gpu-keys/v3");
    // a keys file made under a mapping fault is a negative control's run, never a verdict's
    // input; its binary is held by main (--device-binary, or one binary for every keys file):
    // the checker-logic re-review's finding 5
    if (v["map_faults"].u32() != 0)
        throw std::runtime_error(path + ": made under mapping fault bits " + std::to_string(v["map_faults"].u32()) +
                                 " (a negative control's run, never a verdict)");
    t.library_sha256 = v["library_sha256"].str();
    {
        // the stream's name: its run directory's (state_census.sh's R/<name>/d3d9gpu.keys.json)
        const size_t e = path.find_last_of('/');
        const size_t b = e == std::string::npos || e == 0 ? std::string::npos : path.find_last_of('/', e - 1);
        t.name = e == std::string::npos ? path : path.substr(b == std::string::npos ? 0 : b + 1, e - (b == std::string::npos ? 0 : b + 1));
    }
    if (!v["complete"].boolean()) throw std::runtime_error(path + ": the device did not finish its keys file (complete false)");
    if (!v.has("shaders")) throw std::runtime_error(path + ": the keys file names no shader bytecode (\"shaders\")");
    for (const Value& x : v["shaders"]["vs"].items) t.vs_sha256.push_back(x.str());
    for (const Value& x : v["shaders"]["ps"].items) t.ps_sha256.push_back(x.str());
    for (const Value& x : v["shaders"]["vs_input_signature"].items) {
        d9g_ref::VsSignature sg;
        for (const Value& e : x.items)
            sg.inputs.push_back(read_signature_entry(e, path));
        t.vs_reflection.push_back(sg);
    }
    for (const Value& x : v["shaders"]["ps_masks"].items) {
        if (x.size() != 2) throw std::runtime_error(path + ": a pixel shader's masks that are not [sampler, output]");
        t.ps_reflection.emplace_back(x[0].u32(), x[1].u32());
    }
    if (t.vs_reflection.size() != t.vs_sha256.size() || t.ps_reflection.size() != t.ps_sha256.size())
        throw std::runtime_error(path + ": the keys file's shader reflections are not one per shader");
    std::vector<uint32_t> order;
    for (const Value& x : v["pipeline_render_states"].items) order.push_back(x.u32());
    std::vector<uint32_t> extra_order;
    for (const Value& x : v["census_extra_render_states"].items) extra_order.push_back(x.u32());
    if (!std::equal(extra_order.begin(), extra_order.end(), std::begin(kCensusExtraRenderStates), std::end(kCensusExtraRenderStates)))
        throw std::runtime_error(path + ": census_extra_render_states differs from d9g_state.h's");
    // the device the keys file was made on is the one the parameters describe: the check maps
    // each input again with P.dev (the review's checker-logic findings 4 and 7)
    const Value& dv = v["device"];
    DeviceParams dp{};
    dp.d24s8_supported = dv["d24s8_supported"].boolean();
    dp.point_size_max = float(dv["point_size_max"].num());
    dp.sampler_clamp_to_border = dv["sampler_clamp_to_border"].boolean();
    dp.depth_clip_and_clamp = dv["depth_clip_and_clamp"].boolean();
    const std::string override_names = dv["capability_override"].kind == Value::String ? dv["capability_override"].str() : "";
    t.device_name = dv["device_name"].str();
    if (dp.d24s8_supported != P.dev.d24s8_supported || dp.point_size_max != P.dev.point_size_max)
        throw std::runtime_error(path + ": the keys file's device (d24s8 " + std::to_string(dp.d24s8_supported) + ", point size max " +
                                 std::to_string(dp.point_size_max) + ") is not the parameters' (" + std::to_string(P.dev.d24s8_supported) +
                                 ", " + std::to_string(P.dev.point_size_max) + ")");
    // the SDL patch's capabilities the device mapped with, and a test run's override, are the
    // parameters' (the check maps every tuple again with P.dev)
    if (dp.sampler_clamp_to_border != P.dev.sampler_clamp_to_border || dp.depth_clip_and_clamp != P.dev.depth_clip_and_clamp ||
        override_names != P.capability_override)
        throw std::runtime_error(path + ": the keys file's capabilities (clamp to border " + std::to_string(dp.sampler_clamp_to_border) +
                                 ", depth clip and clamp " + std::to_string(dp.depth_clip_and_clamp) + ", override \"" + override_names +
                                 "\") are not the parameters' (" + std::to_string(P.dev.sampler_clamp_to_border) + ", " +
                                 std::to_string(P.dev.depth_clip_and_clamp) + ", \"" + P.capability_override + "\")");
    if (!P.axis.empty() && t.device_name != kCpuModelDevice)
        throw std::runtime_error(path + ": sweep-axis parameters (" + P.axis + ") are no claim about a device; the keys file of " +
                                 t.device_name + " needs check_params.py's parameters from that device's DXVK log and report");
    if (P.axis.empty() && t.device_name != P.device_name)
        throw std::runtime_error(path + ": the keys file's device " + t.device_name + " is not the parameters' " + P.device_name);
    // the full render-state array: DXVK's defaults (the device's own, which --defaults holds to
    // DXVK's) under the tuple's key states
    uint32_t defaults[256];
    uint32_t ss[14];
    std::memset(defaults, 0, sizeof defaults);
    state_defaults(defaults, ss, dp, dv["auto_depth"].boolean());
    for (const Value& r : v["inputs"].items) {
        PipelineInput in = parse_input(r, order);
        std::array<uint32_t, 256> full{};
        std::memcpy(full.data(), defaults, sizeof defaults);
        for (size_t i = 0; i < kNumPipelineRS; i++) full[kPipelineRenderStates[i]] = in.rs[i];
        const Value& extra = r["extra"];
        if (extra.size() != std::size(kCensusExtraRenderStates)) throw std::runtime_error(path + ": an input's extra states are not the list's");
        for (size_t i = 0; i < std::size(kCensusExtraRenderStates); i++) full[kCensusExtraRenderStates[i]] = extra[i].u32();
        t.inputs.push_back(in);
        t.full_rs.push_back(full);
        t.input_draws.push_back(r["draws"].u64());
    }
    for (const Value& r : v["samplers"].items) {
        SamplerInput s;
        if (r["ss"].size() != 14) throw std::runtime_error(path + ": a sampler that is not 14 states");
        for (size_t i = 0; i < 14; i++) s.ss[i] = r["ss"][i].u32();
        s.tex = r["tex"].boolean();
        s.levels = r["levels"].u32();
        s.cube = r["cube"].boolean();
        s.depth = r["depth"].boolean();
        s.tex_fmt = r["fmt"].u32();
        t.samplers.push_back(s);
        t.sampler_draws.push_back(r["draws"].u64());
        t.sampler_fallback.push_back(r["fallback_border"].boolean());
        t.sampler_colour.push_back(int32_t(r["border_colour"].num()));
    }
    return t;
}

struct CensusResult {
    std::map<std::string, std::string> per_stream;
    std::set<std::string> failing_fields;    // pipeline and sampler facts that DIFFER anywhere (and named reasons)
    std::vector<std::string> failures;
    std::vector<std::string> pending;        // the items only the user can rule on, per tuple
    std::vector<std::string> reasons;        // named reasons (a BORDER axis mapped silently, ...)
    uint64_t pipeline_tuples = 0, sampler_tuples = 0, depth_clamp = 0, fallback_border = 0, unmapped = 0, differ = 0, rt_feedback = 0;
    uint64_t border_mapped = 0;              // sampler tuples with a BORDER axis the patch's CLAMP_TO_BORDER (passing)
    uint64_t sampler_by_rule = 0;            // sampler tuples equal by the BORDER_SWIZZLE rule (the disjoint class)
    uint64_t border_swizzle_rule = 0;        // ... and those with a history compare bit too (beside the classes)
    uint64_t depth_clamp_property = 0;       // pipeline tuples whose description sets the depth-clamp property
    uint64_t clamp_with_clip_missing = 0;    // mapped tuples on a device with depth clip and clamp where SDL
                                             // does not build clamp with clip (ruling 3's patch fails them)
    uint64_t null_stream = 0;                // passing tuples with an input the declaration lacks (rule NULL_STREAM)
    uint64_t reflected = 0;                  // tuples evaluated with a recorded pixel shader
    uint64_t vs_reflected = 0;               // tuples evaluated with a recorded vertex shader
    uint64_t reflection_differs = 0;         // tuples whose device reflection is not the record's (failures)
    uint64_t shaders_checked = 0;            // created shaders whose device reflection the record held
    uint64_t sampler_history = 0;            // samplers whose compare bit is history (no texture)
    uint64_t undecided = 0;                  // tuples undecided (RT_FEEDBACK), not failing
    std::bitset<256> read;                   // the render states DXVK's code read on the census's mapped tuples
    std::map<std::string, std::set<std::string>> census_values;   // key render state -> values the census has
};

std::string count_map_json(const std::map<std::string, uint64_t>& m) {
    std::string s = "{";
    bool first = true;
    for (const auto& [k, v] : m) { s += (first ? "" : ", ") + Json::q(k) + ": " + std::to_string(v); first = false; }
    return s + "}";
}

std::string signature_text(const d9g_ref::VsSignature& s) {
    std::string o = "[";
    for (size_t i = 0; i < s.inputs.size(); i++)
        o += (i ? ", [" : "[") + std::to_string(s.inputs[i].first) + ", " + std::to_string(s.inputs[i].second) + "]";
    return o + "]";
}

d9g_ref::VsSignature signature_of(const PipelineInput& in) {
    d9g_ref::VsSignature s;
    for (uint32_t i = 0; i < in.vs_n_inputs && i < in.vs_inputs.size(); i++) s.inputs.emplace_back(in.vs_inputs[i].usage, in.vs_inputs[i].usage_index);
    return s;
}

// The census. `record` is test/shader_record.py's (main refuses a census without one): three
// readings of every shader's reflection must agree, the device's (its keys file: each created
// shader's, and each tuple's) and the record's two (the bytecode's tokens, dxbc-spirv's disassembly,
// which shader_record.py already holds equal). DXVK is evaluated with the record's reflection, d9g
// maps with the device's (U9, the R1 plan's section 2.4).
CensusResult run_census(const std::vector<StreamTuples>& streams, const Params& P, uint32_t faults, const ShaderRecord& record) {
    CensusResult R;
    const std::vector<std::string>& snames = d9g_ref::standin_names();
    for (const StreamTuples& t : streams) {
        // disjoint (the checker-logic re-review's finding 7): pipeline_tuples = equal +
        // equal_by_rule + undecided + unmapped + differ; sampler_tuples = sampler_equal +
        // sampler_equal_by_rule + sampler_equal_history + sampler_fallback_border +
        // sampler_unmapped + sampler_differ (a by-rule sampler with a history compare bit is
        // history's there). DEPTH_CLAMP (the R_DEPTH_CLAMP tuples of a device without depth clip
        // and clamp), RT_FEEDBACK, NULL_STREAM, the BORDER mapped and the BORDER_SWIZZLE rule's
        // samplers (history or not) are counted beside them.
        uint64_t n = 0, eq = 0, rule = 0, und = 0, dc = 0, rtf = 0, um = 0, df = 0, sn = 0, seq = 0, sfb = 0, sum = 0, sdf = 0;
        uint64_t sbm = 0, srule = 0, srule_all = 0, dcp = 0, ccm = 0, nulls = 0, rdiff = 0, checked = 0;
        uint64_t stand = 0, hist = 0, reflected = 0, no_record = 0, vs_reflected = 0, vs_no_record = 0, shist = 0;
        std::map<std::string, uint64_t> standin_facts;
        std::map<std::string, std::map<std::string, uint64_t>> by_standin;   // stand-in -> fact -> tuples
        std::map<std::string, uint64_t> cls;
        // every created shader's own reflection (the keys file's "shaders") is the record's
        for (size_t i = 0; i < t.vs_sha256.size(); i++) {
            const auto it = record.vs.find(t.vs_sha256[i]);
            if (it == record.vs.end()) {
                R.failures.push_back(t.name + " vertex shader " + std::to_string(i + 1) + ": no independent recorded reflection");
                continue;
            }
            checked++;
            if (t.vs_reflection[i].inputs != it->second.signature.inputs)
                R.failures.push_back(t.name + " vertex shader " + std::to_string(i + 1) + ": the device's input signature " +
                                     signature_text(t.vs_reflection[i]) + " is not the recorded " +
                                     signature_text(it->second.signature) + " of " + it->second.label);
        }
        for (size_t i = 0; i < t.ps_sha256.size(); i++) {
            const auto it = record.ps.find(t.ps_sha256[i]);
            if (it == record.ps.end()) {
                R.failures.push_back(t.name + " pixel shader " + std::to_string(i + 1) + ": no independent recorded reflection");
                continue;
            }
            checked++;
            if (t.ps_reflection[i] != std::make_pair(it->second.sampler_mask, it->second.rt_mask))
                R.failures.push_back(t.name + " pixel shader " + std::to_string(i + 1) + ": the device's sampler and output masks " +
                                     hex(t.ps_reflection[i].first) + ", " + hex(t.ps_reflection[i].second) + " are not the recorded " +
                                     hex(it->second.sampler_mask) + ", " + hex(it->second.rt_mask) + " of " + it->second.label);
        }
        for (size_t i = 0; i < t.inputs.size(); i++) {
            // the recorded reflection of the tuple's shaders, linked by the bytecode's sha256; the
            // input's own where the record holds no such shader (counted, and a failure below)
            const PipelineInput& in = t.inputs[i];
            d9g_ref::RecordedReflection rec{signature_of(in), in.ps_sampler_mask, in.ps_rt_mask};
            bool have_ps = false, have_vs = false;
            const std::string where = t.name + " pipeline tuple " + std::to_string(i);
            if (in.ps_id && in.ps_id <= t.ps_sha256.size()) {
                const auto it = record.ps.find(t.ps_sha256[in.ps_id - 1]);
                if (it != record.ps.end()) {
                    have_ps = true;
                    reflected++;
                    rec.ps_sampler_mask = it->second.sampler_mask;
                    rec.ps_rt_mask = it->second.rt_mask;
                }
                if (std::make_pair(in.ps_sampler_mask, in.ps_rt_mask) != t.ps_reflection[in.ps_id - 1])
                    R.failures.push_back(where + ": its pixel shader's masks are not the keys file's for pixel shader " +
                                         std::to_string(in.ps_id));
            }
            if (in.vs_id && in.vs_id <= t.vs_sha256.size()) {
                const auto it = record.vs.find(t.vs_sha256[in.vs_id - 1]);
                if (it != record.vs.end()) {
                    have_vs = true;
                    vs_reflected++;
                    rec.vs_signature = it->second.signature;
                }
                if (signature_of(in).inputs != t.vs_reflection[in.vs_id - 1].inputs)
                    R.failures.push_back(where + ": its vertex shader's signature is not the keys file's for vertex shader " +
                                         std::to_string(in.vs_id));
            }
            if (!have_ps) no_record++;
            if (!have_vs) vs_no_record++;
            // the device's reflection is the record's: the three readings agree
            if ((have_ps && (in.ps_sampler_mask != rec.ps_sampler_mask || in.ps_rt_mask != rec.ps_rt_mask)) ||
                (have_vs && signature_of(in).inputs != rec.vs_signature.inputs)) {
                rdiff++;
                R.failures.push_back(where + ": the device's reflection (signature " + signature_text(signature_of(in)) +
                                     ", sampler mask " + hex(in.ps_sampler_mask) + ", output mask " + hex(in.ps_rt_mask) +
                                     ") is not the record's (" + signature_text(rec.vs_signature) + ", " +
                                     hex(rec.ps_sampler_mask) + ", " + hex(rec.ps_rt_mask) + ")");
            }
            PipelineCheck k = check_pipeline(in, t.full_rs[i].data(), P, faults, -1, true, &rec);
            n++;
            if (!k.standin.empty()) stand++;
            for (int f : k.standin) standin_facts[d9g_pipeline_fact_names[f]]++;
            for (size_t s = 0; s < k.standin_by.size(); s++)
                for (int f : k.standin_by[s]) by_standin[snames[s]][d9g_pipeline_fact_names[f]]++;
            if (k.dx.history_standin) hist++;
            if (k.unmapped()) {
                um++;
                R.failures.push_back(where + " unmapped: " + k.desc.unmapped[0]);
                continue;
            }
            for (int f = 0; f < PF_COUNT; f++) cls[kClassName[k.cls[f]]]++;
            R.read |= k.dx.rs_read;
            if (k.depth_clamp) dc++;
            if (k.desc.depth_clamp && k.sdl_clamp_with_clip) dcp++;
            if (P.dev.depth_clip_and_clamp && !k.sdl_clamp_with_clip) ccm++;
            if (k.fails()) {
                df++;
                for (int f : k.differ) R.failing_fields.insert(d9g_pipeline_fact_names[f]);
                if (!k.reason.empty()) R.failing_fields.insert("reason: " + k.reason);
                for (const auto& o : k.obligations) R.failing_fields.insert("obligation " + o);
                std::string e = where + ": " + fact_list(k.differ, d9g_pipeline_fact_names) + " (DXVK";
                for (int f : k.differ) e += " " + hex(k.dx.facts.v[f]);
                e += ", SDL";
                for (int f : k.differ) e += " " + hex(k.sdl.v[f]);
                for (const auto& o : k.obligations) e += "; the " + o + " obligation d9g's description carries does not hold";
                R.failures.push_back(e + ")" + (k.reason.empty() ? "" : " " + k.reason));
                continue;
            }
            if (k.rt_feedback) und++;
            else if (k.fan || k.bgra || k.null_stream) rule++;
            else eq++;
            if (k.null_stream) nulls++;
            if (k.rt_feedback) {
                rtf++;
                char st[96];
                std::snprintf(st, sizeof st, "stages 0x%x, sampler mask 0x%x", in.rt_tex_stages, in.ps_sampler_mask);
                R.pending.push_back(where + ": RT_FEEDBACK (a written render target's texture is bound at " + st + ")");
            }
        }
        for (size_t i = 0; i < t.samplers.size(); i++) {
            SamplerCheck k = check_sampler(t.samplers[i], P, faults);
            sn++;
            // the device's BORDER decision is the check's (a keys file of no fault: --controls'
            // faults are the check's own mapping, the device's ran without): the I6 review's NIT 9
            if (!faults && (k.fallback != t.sampler_fallback[i] || k.desc.border_colour != t.sampler_colour[i]) && k.reason.empty())
                k.reason = "the device's BORDER decision (fallback " + std::to_string(t.sampler_fallback[i]) + ", colour " +
                           std::to_string(t.sampler_colour[i]) + ") is not the check's mapping (fallback " + std::to_string(k.fallback) +
                           ", colour " + std::to_string(k.desc.border_colour) + ")";
            if (k.unmapped()) {
                sum++;
                R.failures.push_back(t.name + " sampler tuple " + std::to_string(i) + " unmapped: " + k.desc.unmapped[0]);
                continue;
            }
            if (!k.reason.empty()) R.reasons.push_back(k.reason);
            if (!k.differ.empty() || !k.reason.empty()) {
                sdf++;
                for (int f : k.differ) R.failing_fields.insert(d9g_sampler_fact_names[f]);
                if (!k.reason.empty()) R.failing_fields.insert("reason: " + k.reason);
                R.failures.push_back(t.name + " sampler tuple " + std::to_string(i) + ": " +
                                     fact_list(k.differ, d9g_sampler_fact_names) + " " + k.reason);
            } else if (k.fallback) sfb++;
            else if (k.dx.history) shist++;   // equal at the compare bit's initial 0: history, not counted equal
            else if (k.by_rule) srule++;
            else seq++;
            if (k.border_mapped && k.differ.empty() && k.reason.empty()) sbm++;
            if (k.by_rule && k.differ.empty() && k.reason.empty()) srule_all++;
        }
        if (n != eq + rule + und + um + df)
            R.failures.push_back(t.name + ": the pipeline tuple classes are not disjoint (an accounting fault in the check)");
        if (sn != seq + srule + shist + sfb + sum + sdf)
            R.failures.push_back(t.name + ": the sampler tuple classes are not disjoint (an accounting fault in the check)");
        R.pipeline_tuples += n;
        R.sampler_tuples += sn;
        R.depth_clamp += dc;
        R.fallback_border += sfb;
        R.border_mapped += sbm;
        R.sampler_by_rule += srule;
        R.border_swizzle_rule += srule_all;
        R.depth_clamp_property += dcp;
        R.clamp_with_clip_missing += ccm;
        R.unmapped += um + sum;
        R.differ += df + sdf;
        R.rt_feedback += rtf;
        R.null_stream += nulls;
        R.undecided += und;
        R.reflected += reflected;
        R.vs_reflected += vs_reflected;
        R.reflection_differs += rdiff;
        R.shaders_checked += checked;
        R.sampler_history += shist;
        if (n == 0) R.failures.push_back(t.name + ": no pipeline tuple (a vacuous stream)");
        if (t.ps_sha256.empty() || t.vs_sha256.empty())
            R.failures.push_back(t.name + ": the keys file names no shader bytecode (\"shaders\")");
        // every tuple is evaluated with the record: one without it is held to nothing but the
        // device's own reading (the confirm's residual 5)
        if (no_record || vs_no_record)
            R.failures.push_back(t.name + ": " + std::to_string(no_record) + " of " + std::to_string(n) +
                                 " pipeline tuples have no recorded pixel shader, " + std::to_string(vs_no_record) +
                                 " no recorded vertex shader");
        char b[4096];
        std::snprintf(b, sizeof b,
                      "{\"path\": %s, \"keys_sha256\": %s, \"library_sha256\": %s, \"device_name\": %s, "
                      "\"pipeline_tuples\": %llu, \"equal\": %llu, \"equal_by_rule\": %llu, \"undecided\": %llu, "
                      "\"depth_clamp\": %llu, \"depth_clamp_property\": %llu, \"clamp_with_clip_missing\": %llu, "
                      "\"rt_feedback\": %llu, \"null_stream\": %llu, \"tuples_with_recorded_reflection\": %llu, "
                      "\"tuples_without_recorded_reflection\": %llu, \"tuples_with_recorded_vs_signature\": %llu, "
                      "\"tuples_without_recorded_vs_signature\": %llu, \"reflection_differs\": %llu, "
                      "\"shaders_held_to_the_record\": %llu, \"unmapped\": %llu, \"differ\": %llu, "
                      "\"standin_decided\": %llu, \"sampler_tuples\": %llu, \"sampler_equal\": %llu, "
                      "\"sampler_equal_by_rule\": %llu, \"sampler_fallback_border\": %llu, \"sampler_border_mapped\": %llu, "
                      "\"sampler_unmapped\": %llu, \"sampler_differ\": %llu, \"sampler_equal_history\": %llu, \"history_standin\": %llu, "
                      "\"fact_classes\": ",
                      Json::q(t.path).c_str(), Json::q(t.keys_sha256).c_str(), Json::q(t.library_sha256).c_str(),
                      Json::q(t.device_name).c_str(), (unsigned long long)n, (unsigned long long)eq,
                      (unsigned long long)rule, (unsigned long long)und, (unsigned long long)dc, (unsigned long long)dcp,
                      (unsigned long long)ccm, (unsigned long long)rtf, (unsigned long long)nulls, (unsigned long long)reflected,
                      (unsigned long long)no_record, (unsigned long long)vs_reflected, (unsigned long long)vs_no_record,
                      (unsigned long long)rdiff, (unsigned long long)checked,
                      (unsigned long long)um, (unsigned long long)df, (unsigned long long)stand, (unsigned long long)sn,
                      (unsigned long long)seq, (unsigned long long)srule, (unsigned long long)sfb, (unsigned long long)sbm,
                      (unsigned long long)sum, (unsigned long long)sdf,
                      (unsigned long long)shist, (unsigned long long)hist);
        std::string bj = "{";
        bool fb = true;
        for (const std::string& s : snames) {
            bj += (fb ? "" : ", ") + Json::q(s) + ": " + count_map_json(by_standin[s]);
            fb = false;
        }
        bj += "}";
        R.per_stream[t.name] = std::string(b) + count_map_json(cls) + ", \"facts_decided_by_standins\": " +
                               count_map_json(standin_facts) + ", \"facts_decided_by_each_standin\": " + bj + "}";
        // the values the census exercises, per key render state (coverage, the critic's S3)
        for (size_t i = 0; i < t.inputs.size(); i++)
            for (size_t si = 0; si < kNumPipelineRS; si++) R.census_values[rs_name(kPipelineRenderStates[si])].insert(hex(t.inputs[i].rs[si]));
    }
    return R;
}

// The fields each control's fault may break (it must break at least one, and nothing else).
// `census_required`: a vertex-input control, which the census must fail itself, never only the
// sweep: every census tuple has a declaration and a vertex shader, and a census whose tuples do not
// exercise the control checks the vertex input nowhere (the checker-logic re-review's finding 2,
// which held them to the census while VS_SIGNATURE could absorb them; U9 keeps the rule).
// `needs`: the SDL patch's capability the fault acts on ("border", "depth-clamp"; "" none). On a
// device without it the fault changes nothing there, so where the census does not fail, the sweep
// runs with that capability on (the SDL side's device reporting it too), and says so.
struct Control {
    const char* name;
    std::vector<std::string> targets;
    bool census_required = false;
    const char* needs = "";
};
std::vector<Control> controls() {
    std::vector<std::string> cb0;
    for (int f = PF_cb0_blendEnable; f < PF_cb1_blendEnable; f++) cb0.push_back(d9g_pipeline_fact_names[f]);
    std::vector<std::string> vi, vi_off, vi_all, vi_rate;
    for (int i = 0; i < 16; i++) vi.push_back(d9g_pipeline_fact_names[PF_vi_attr0_format + 3 * i]);
    for (int i = 0; i < 16; i++) vi_off.push_back(d9g_pipeline_fact_names[PF_vi_attr0_offset + 3 * i]);
    for (int f = 0; f < PF_COUNT; f++)
        if (is_vi_fact(f)) vi_all.push_back(d9g_pipeline_fact_names[f]);
    vi_all.push_back("obligation BGRA");
    for (int i = 0; i < D9G_MAX_BINDINGS; i++) {
        vi_rate.push_back(d9g_pipeline_fact_names[PF_vi_bind0_inputRate + 4 * i]);
        vi_rate.push_back(d9g_pipeline_fact_names[PF_vi_bind0_divisor + 4 * i]);
    }
    std::vector<std::string> vi_bgra = vi;
    vi_bgra.push_back("obligation BGRA");
    // the render-target hazard and what it decides (BindFramebuffer's skipped targets, :6807-6808)
    std::vector<std::string> hazard = {"rt_unresolvableHazard", "rt_feedbackLoop", "rt_colorAttachmentCount", "cb_attachmentCount"};
    for (int f = PF_cb0_blendEnable; f <= PF_rt3_colorFormat; f++) hazard.push_back(d9g_pipeline_fact_names[f]);
    std::vector<std::string> ds = {"rt_depthAttachmentFormat", "rt_stencilAttachmentFormat"};
    for (int f = PF_ds_depthTestEnable; f <= PF_ds_back_writeMask; f++) ds.push_back(d9g_pipeline_fact_names[f]);
    for (int f = PF_rs_depthBiasEnable; f <= PF_rs_depthBiasExact; f++) ds.push_back(d9g_pipeline_fact_names[f]);
    return {
        {"map-blend-swap", {"cb0_srcColorBlendFactor", "cb0_srcAlphaBlendFactor"}},
        {"map-zfunc-swap", {"ds_depthCompareOp"}},
        {"map-normalize-off", cb0},
        {"map-writemask", cb0},
        {"map-address-swap", {"sm_addressModeU", "sm_addressModeV", "sm_addressModeW"}},
        {"map-border-clamp", {"sm_addressModeU", "sm_addressModeV", "sm_addressModeW", "sm_borderColor",
                              "sm_pNextCustomBorder", "sm_pNextBorderSwizzle",
                              "reason: a BORDER axis mapped to another address mode, neither CLAMP_TO_BORDER nor the shader "
                              "route's fallback"}},
        {"map-border-colour", {"sm_borderColor", "sm_pNextBorderSwizzle"}, false, "border"},
        {"map-depth-clamp-off", {"rs_depthClampEnable"}, false, "depth-clamp"},
        {"map-depth-bias-push", {"rs_depthBiasEnable", "rs_depthBiasConstantFactor", "rs_depthBiasClamp", "rs_depthBiasSlopeFactor",
                                 "rs_depthBiasRepresentation", "rs_depthBiasExact"}},
        {"map-depth-clamp-extra", {"rs_depthClampEnable", "obligation DEPTH_CLAMP"}, false, "depth-clamp"},
        {"map-border-swizzled-view", {"sm_borderColor", "sm_pNextBorderSwizzle",
                                      "reason: BORDER mapped where the patch cannot build DXVK's border: DXVK's view of the texture "
                                      "swizzles"}, false, "border"},
        {"map-border-fallback", {"reason: fallback BORDER where the patch builds DXVK's border (a standard colour on an "
                                 "identity view, on a device with CLAMP_TO_BORDER)"}, false, "border"},
        {"map-lod-unquantised", {"sm_maxLod"}},
        {"map-bgra-mask", vi, true},
        {"map-fan-order", {"ia_topology"}},
        {"map-depth-clip-off", {"rs_depthClipEnable"}},
        {"map-alpha-op", {"lg_alphaCompareOp"}},
        {"map-ds-bind", ds},
        {"map-cull-swap", {"rs_cullMode"}},
        {"map-vi-offset", vi_off, true},
        {"map-bgra-extra", vi_bgra, true},
        {"map-fan-on-list", {"ia_topology", "obligation FAN"}},
        {"map-vi-declorder", vi_all, true},
        {"map-vi-no-null", vi_all, true},
        {"map-null-vertex-rate", vi_rate, true},
        {"map-vi-unread-attr", vi_all, true},
        {"map-bgra-decl-index", vi_bgra, true},
        {"map-ps-sampler-superset", hazard},
    };
}

}  // namespace

// ======================================================================== main
int main(int argc, char** argv) {
    std::string params_path, out_path, audit_path, ruling_path, record_path, repo_path, identity_path, device_binary;
    std::vector<std::string> dumps;
    bool domain = false, defaults = false, draw_tests = false, self_test = false, do_controls = false, enum_values = false;
    uint32_t faults = 0;
    std::vector<std::string> fault_names;
    for (int i = 1; i < argc; i++) {
        const std::string a = argv[i];
        auto next = [&]() -> std::string {
            if (i + 1 >= argc) { std::fprintf(stderr, "%s needs a value\n", a.c_str()); std::exit(2); }
            return argv[++i];
        };
        if (a == "--params") params_path = next();
        else if (a == "--out") out_path = next();
        else if (a == "--audit") audit_path = next();
        else if (a == "--residue-ruling") ruling_path = next();
        else if (a == "--shader-record") record_path = next();
        else if (a == "--repo") repo_path = next();
        else if (a == "--stream-identity") identity_path = next();
        else if (a == "--device-binary") device_binary = next();
        else if (a == "--domain") domain = true;
        else if (a == "--defaults") defaults = true;
        else if (a == "--draw-tests") draw_tests = true;
        else if (a == "--self-test") self_test = true;
        else if (a == "--controls") do_controls = true;
        else if (a == "--enum-values") enum_values = true;
        else if (a == "--fault") {
            const std::string n = next();
            const uint32_t b = map_fault_bit(n);
            if (!b) { std::fprintf(stderr, "unknown fault %s\n", n.c_str()); return 2; }
            faults |= b;
            fault_names.push_back(n);
        } else if (a == "--dumps") {
            while (i + 1 < argc && argv[i + 1][0] != '-') dumps.push_back(argv[++i]);
        } else {
            std::fprintf(stderr, "unknown argument %s\n", a.c_str());
            return 2;
        }
    }
    if (enum_values) {
        // the Vulkan enumerants the facts use, as each side's bundled header gives them
        uint32_t a[kEnumValues], b[kEnumValues];
        d9g_sdl_enum_values(a, kEnumValues);
        d9g_ref::dxvk_enum_values(b, kEnumValues);
        for (int k = 0; k < kEnumValues; k++) std::printf("%u %u\n", a[k], b[k]);
        return 0;
    }
    if (params_path.empty()) { std::fprintf(stderr, "--params is required (no device parameter has a default: M7)\n"); return 2; }
    Runner run;
    try {
        run.P = load_params(params_path);
    } catch (const std::exception& e) {
        std::fprintf(stderr, "STATE_CHECK_REFUSED: %s\n", e.what());
        return 2;
    }
    const Params& P = run.P;
    run.faults = faults;
    // a run in which no mode ran checks nothing, and is no pass (the checker-logic re-review's
    // finding 6)
    const bool any_mode = domain || defaults || draw_tests || self_test || do_controls || !audit_path.empty() ||
                          !dumps.empty() || !ruling_path.empty();
    if (!any_mode) run.fail("no mode ran: --params alone checks nothing");
    run.field("schema", "\"sa2-d3d9gpu-state-check/v1\"");
    run.field("params", P.json.empty() ? "null" : P.json);
    run.field("faults", list_json(fault_names));
    try {
        // ---------------------------------------------------------------- defaults
        if (defaults) {
            uint32_t drs[256], dss[14], mrs[256] = {}, mss[14] = {};
            bool set[256];
            std::vector<std::string> bad;
            for (bool auto_depth : {true, false}) {
                d9g_ref::dxvk_defaults(drs, set, dss, P.dxvk, auto_depth);
                std::memset(mrs, 0, sizeof mrs);
                state_defaults(mrs, mss, P.dev, auto_depth);
                for (uint32_t s = 0; s < 256; s++)
                    if ((set[s] ? drs[s] : 0u) != mrs[s]) bad.push_back(rs_name(s) + " (auto depth " + std::to_string(auto_depth) + ")");
                for (uint32_t s = 1; s < 14; s++)
                    if (dss[s] != mss[s]) bad.push_back("sampler state " + std::to_string(s));
            }
            const SamplerOptions o = d9g_ref::dxvk_sampler_options(), m;
            if (o.sampler_anisotropy != m.sampler_anisotropy || o.seamless_cubes != m.seamless_cubes ||
                o.sampler_lod_bias != m.sampler_lod_bias || o.clamp_negative_lod_bias != m.clamp_negative_lod_bias)
                bad.push_back("sampler options");
            int nset = 0;
            for (bool b : set) nset += b;
            for (const auto& b : bad) run.fail("default differs from DXVK's ResetState: " + b);
            run.field("defaults", "{\"dxvk_sets\": " + std::to_string(nset) + ", \"differ\": " + list_json(bad) + "}");
            std::printf("defaults: DXVK's ResetState sets %d render states and 13 sampler states; %zu differ\n", nset, bad.size());
        }
        // ---------------------------------------------------------------- audit (M5)
        if (!audit_path.empty()) {
            Value a = mini_json::parse(read_file(audit_path));
            std::vector<std::string> unclassified;
            std::map<std::string, std::vector<std::string>> by;
            std::set<std::string> tokens;
            for (const Value& t : a["set_render_state_cases"].items) tokens.insert(t.str());
            for (const Value& t : a["tokens_read"].items) tokens.insert(t.str());
            for (const std::string& t : tokens) {
                uint32_t v = 0xFFFF;
                for (const RsName& r : kRsNames)
                    if (t == r.name) v = r.v;
                const char* cl = v == 0xFFFF ? nullptr : audit_class(v);
                if (!cl && v != 0xFFFF && census_extra(v)) cl = "census-extra: no pipeline effect (key sufficiency holds it)";
                if (!cl) unclassified.push_back(t);
                else by[cl].push_back(t);
            }
            // every DXVK magic value the switch compares is in the sweep's values
            std::vector<std::string> magic_missing;
            for (const Value& m : a["magic_formats"].items) {
                const std::string n = m.str();
                if (n.size() != 4) { magic_missing.push_back(n); continue; }
                const uint32_t fc = uint32_t(uint8_t(n[0])) | uint32_t(uint8_t(n[1])) << 8 | uint32_t(uint8_t(n[2])) << 16 |
                                    uint32_t(uint8_t(n[3])) << 24;
                if (std::find(std::begin(kFourcc), std::end(kFourcc), fc) == std::end(kFourcc)) magic_missing.push_back(n);
            }
            for (const auto& [nm, val] : a["magic_constants"].fields)
                if (std::find(std::begin(kFourcc), std::end(kFourcc), uint32_t(std::stoul(val.str(), nullptr, 16))) == std::end(kFourcc))
                    magic_missing.push_back(nm);
            for (const auto& u : unclassified) run.fail("render state not classified: " + u);
            for (const auto& u : magic_missing) run.fail("DXVK magic value not in the sweep: " + u);
            std::string j = "{";
            bool first = true;
            for (const auto& [k, v] : by) { j += (first ? "" : ", ") + Json::q(k) + ": " + list_json(v); first = false; }
            j += "}";
            run.field("audit", "{\"tokens\": " + std::to_string(tokens.size()) + ", \"unclassified\": " + list_json(unclassified) +
                                   ", \"magic_missing\": " + list_json(magic_missing) + ", \"classes\": " + j + "}");
            std::printf("audit: %zu D3DRS tokens of SetRenderState and PrepareDraw, %zu unclassified; %zu magic values missing\n",
                        tokens.size(), unclassified.size(), magic_missing.size());
        }
        // ---------------------------------------------------------------- draw tests
        if (draw_tests) {
            std::vector<std::string> bad;
            uint64_t n = 0;
            for (uint32_t t = 0; t <= 9; t++)
                for (uint32_t c : {0u, 1u, 2u, 3u, 1000u, 0x80000000u, 0xFFFFFFFFu}) {
                    n++;
                    if (vertex_count(t, c) != d9g_ref::dxvk_vertex_count(t, c))
                        bad.push_back("vertex_count(" + std::to_string(t) + ", " + std::to_string(c) + ")");
                }
            // fan_indices(n): Vulkan's fan triangles (v_{i+1}, v_{i+2}, v_0) as an expression of i
            for (uint32_t v = 0; v <= 300; v++) {
                const std::vector<uint32_t> f = fan_indices(v);
                n++;
                bool ok = f.size() == (v >= 3 ? 3u * (v - 2) : 0u);
                for (uint32_t i = 0; ok && i + 2 < v; i++) ok = f[3 * i] == i + 1 && f[3 * i + 1] == i + 2 && f[3 * i + 2] == 0;
                if (!ok) bad.push_back("fan_indices(" + std::to_string(v) + ")");
            }
            for (uint32_t m : {uint32_t(DM_DP), uint32_t(DM_DPUP)}) {
                std::string why;
                n++;
                if (!fan_rule_holds(m, 0, &why)) bad.push_back("FAN rule, method " + std::to_string(m) + ": " + why);
            }
            // the cache grows: a larger cache's prefix is the smaller one
            for (uint32_t a = 3; a < 200; a += 17) {
                const auto s1 = fan_indices(a), s2 = fan_indices(a * 2);
                n++;
                if (!std::equal(s1.begin(), s1.end(), s2.begin())) bad.push_back("fan cache prefix " + std::to_string(a));
            }
            // the declaration size GetUPBufferSize reads, for every type and offsets
            for (uint32_t t = 0; t <= 20; t++)
                for (uint16_t off : {uint16_t(0), uint16_t(12), uint16_t(255)})
                    for (uint16_t stream : {uint16_t(0), uint16_t(1)}) {
                        n++;
                        PipelineInput decl;
                        decl.n_elems = 2;
                        decl.elems[0] = VertexElem{0, 0, D3DDECLTYPE_FLOAT1, 0, 0, 0};
                        decl.elems[1] = VertexElem{stream, off, uint8_t(t), 0, 0, 0};
                        if (decl_stream0_size(decl.elems.data(), decl.n_elems) != d9g_ref::dxvk_decl_size0(decl))
                            bad.push_back("decl_stream0_size type " + std::to_string(t) + " offset " + std::to_string(off));
                    }
            // the UP arena: each block is DXVK's FillUPVertexBuffer over GetUPBufferSize
            for (uint32_t stride : {12u, 16u, 24u, 32u})
                for (uint32_t declsz : {12u, 16u, 24u, 36u})
                    for (uint32_t vc : {1u, 3u, 1000u})
                        for (uint32_t isz : {0u, 2u, 4u}) {
                            n++;
                            PipelineInput decl;
                            decl.n_elems = 1;
                            // a declaration of size declsz: one element ending there
                            const uint8_t type = declsz == 12 ? D3DDECLTYPE_FLOAT3 : declsz == 16 ? D3DDECLTYPE_FLOAT4 : D3DDECLTYPE_FLOAT1;
                            const uint16_t off = uint16_t(declsz - (type == D3DDECLTYPE_FLOAT3 ? 12 : type == D3DDECLTYPE_FLOAT4 ? 16 : 4));
                            decl.elems[0] = VertexElem{0, off, type, 0, 0, 0};
                            const uint32_t size0 = d9g_ref::dxvk_decl_size0(decl);
                            std::vector<uint8_t> data(size_t(vc) * stride);
                            for (size_t k = 0; k < data.size(); k++) data[k] = uint8_t(k * 7 + 1);
                            std::vector<uint8_t> idx(size_t(vc) * isz, 0xAB);
                            UpArena arena;
                            arena.add(data.data(), 1, stride, size0);   // a first block, so offsets move
                            const UpBlock b = arena.add(data.data(), vc, stride, size0, isz ? idx.data() : nullptr, uint32_t(idx.size()));
                            const uint32_t want_buf = d9g_ref::dxvk_up_buffer_size(vc, stride, decl);
                            std::vector<uint8_t> ref(want_buf);
                            d9g_ref::dxvk_fill_up(ref.data(), data.data(), d9g_ref::dxvk_up_data_size(vc, stride), want_buf);
                            const auto& bytes = arena.bytes();
                            const bool ok = b.buffer_bytes == want_buf && b.data_bytes == d9g_ref::dxvk_up_data_size(vc, stride) &&
                                            b.vb_offset % 64 == 0 && b.vb_offset >= d9g_ref::dxvk_up_aligned(1) &&
                                            std::equal(ref.begin(), ref.end(), bytes.begin() + long(b.vb_offset)) &&
                                            b.ib_offset == b.vb_offset + want_buf &&
                                            (!isz || std::equal(idx.begin(), idx.end(), bytes.begin() + long(b.ib_offset)));
                            if (!ok)
                                bad.push_back("UP block stride " + std::to_string(stride) + " decl " + std::to_string(declsz) +
                                              " vertices " + std::to_string(vc) + " index size " + std::to_string(isz));
                        }
            // draw_args: DXVK's draw emission, per method and type
            for (uint32_t m = 0; m < 4; m++)
                for (uint32_t t = 1; t <= 6; t++) {
                    n++;
                    const DrawArgs a = draw_args(m, t, 5, 10, -2, D3DFMT_INDEX16);
                    const uint32_t vc = d9g_ref::dxvk_vertex_count(t, 10);
                    bool ok;
                    if (t == D3DPT_TRIANGLEFAN && (m == DM_DIP || m == DM_DIPUP)) ok = !a.unmapped.empty();
                    else if (t == D3DPT_TRIANGLEFAN) ok = a.indexed && a.fan_cache && a.count == 3 * (vc - 2) && a.vertex_offset == (m == DM_DP ? 5 : 0);
                    else if (m == DM_DP) ok = !a.indexed && a.count == vc && a.first_vertex == 5;
                    else if (m == DM_DPUP) ok = !a.indexed && a.count == vc && a.first_vertex == 0;
                    else if (m == DM_DIP) ok = a.indexed && a.count == vc && a.first_index == 5 && a.vertex_offset == -2 && a.index_size == 2;
                    else ok = a.indexed && a.count == vc && a.first_index == 0 && a.vertex_offset == 0 && a.index_size == 2;
                    if (!ok) bad.push_back("draw_args(" + std::to_string(m) + ", " + std::to_string(t) + ")");
                }
            for (const auto& b : bad) run.fail("draw test: " + b);
            run.field("draw_tests", "{\"cases\": " + std::to_string(n) + ", \"failed\": " + list_json(bad) + "}");
            std::printf("draw tests: %llu cases, %zu failed\n", (unsigned long long)n, bad.size());
        }
        // ---------------------------------------------------------------- domain
        SweepResult sweep;
        if (domain || do_controls) {
            sweep = run_sweep(P, faults);
            std::vector<std::string> extra_reads;
            for (uint32_t s = 0; s < 256; s++)
                if (sweep.read[s] && !key_state(s) && !dynamic_state(s)) extra_reads.push_back(rs_name(s));
            for (const auto& e : extra_reads) run.fail("DXVK reads a render state the key omits: " + e);
            for (const auto& e : sweep.key_insufficient) run.fail("key insufficient: " + e);
            if (sweep.wrong) run.fail(std::to_string(sweep.wrong) + " sweep pipeline tuples where d9g maps a value DXVK does not build");
            if (sweep.sampler_wrong) run.fail(std::to_string(sweep.sampler_wrong) + " sweep sampler tuples where d9g maps a value DXVK does not build");
            for (const auto& e : sweep.wrong_examples) run.fail("  " + e);
            std::vector<std::string> unm(sweep.unmapped_names.begin(), sweep.unmapped_names.end());
            std::vector<std::string> reads;
            for (uint32_t s = 0; s < 256; s++)
                if (sweep.read[s]) reads.push_back(rs_name(s));
            std::string table = "{";
            bool first = true;
            for (const auto& [st, vals] : sweep.table) {
                std::map<std::string, std::vector<std::string>> by_outcome;
                for (const auto& [val, outs] : vals) {
                    std::string o;
                    for (const auto& x : outs) o += (o.empty() ? "" : "+") + x;
                    by_outcome[o].push_back(val);
                }
                std::string inner = "{";
                bool f2 = true;
                for (const auto& [o, vs] : by_outcome) { inner += (f2 ? "" : ", ") + Json::q(o) + ": " + list_json(vs); f2 = false; }
                table += (first ? "" : ", ") + Json::q(st) + ": " + inner + "}";
                first = false;
            }
            table += "}";
            std::vector<std::string> diff_fields;
            for (int f : sweep.differing_fields) diff_fields.push_back(d9g_pipeline_fact_names[f]);
            for (int f : sweep.sampler_differing) diff_fields.push_back(d9g_sampler_fact_names[f]);
            char b[512];
            std::snprintf(b, sizeof b,
                          "{\"pipeline_tuples\": %llu, \"mapped_equal\": %llu, \"equal_by_rule\": %llu, \"unmapped\": %llu, "
                          "\"undecided_rt_feedback\": %llu, \"mapped_but_differs\": %llu, \"sampler_tuples\": %llu, "
                          "\"sampler_equal\": %llu, \"sampler_equal_by_rule\": %llu, \"sampler_fallback_border\": %llu, "
                          "\"sampler_border_mapped\": %llu, \"sampler_unmapped\": %llu, \"sampler_differs\": %llu, ",
                          (unsigned long long)sweep.tuples, (unsigned long long)sweep.equal, (unsigned long long)sweep.by_rule,
                          (unsigned long long)sweep.unmapped, (unsigned long long)sweep.undecided, (unsigned long long)sweep.wrong,
                          (unsigned long long)sweep.samplers, (unsigned long long)sweep.sampler_mapped,
                          (unsigned long long)sweep.sampler_by_rule, (unsigned long long)sweep.sampler_fallback,
                          (unsigned long long)sweep.sampler_border_mapped, (unsigned long long)sweep.sampler_unmapped,
                          (unsigned long long)sweep.sampler_wrong);
            run.field("domain", std::string(b) + "\"differing_fields\": " + list_json(diff_fields) + ", \"wrong_reasons\": " +
                                    list_json(std::vector<std::string>(sweep.wrong_reasons.begin(), sweep.wrong_reasons.end())) +
                                    ", \"unmapped_values\": " +
                                    list_json(unm) + ", \"read_set\": " + list_json(reads) + ", \"reads_outside_key\": " +
                                    list_json(extra_reads) + ", \"key_insufficient\": " + list_json(sweep.key_insufficient) +
                                    ", \"classification\": " + table + "}");
            std::printf("domain: %llu pipeline tuples (%llu equal, %llu equal by rule, %llu unmapped, %llu undecided, %llu WRONG); "
                        "%llu sampler tuples (%llu equal, %llu equal by rule BORDER_SWIZZLE, %llu fallback BORDER, %llu unmapped, "
                        "%llu WRONG; %llu with BORDER mapped); read set %zu states, %zu outside the key; key sufficiency %zu failures\n",
                        (unsigned long long)sweep.tuples, (unsigned long long)sweep.equal, (unsigned long long)sweep.by_rule,
                        (unsigned long long)sweep.unmapped, (unsigned long long)sweep.undecided, (unsigned long long)sweep.wrong,
                        (unsigned long long)sweep.samplers, (unsigned long long)sweep.sampler_mapped,
                        (unsigned long long)sweep.sampler_by_rule, (unsigned long long)sweep.sampler_fallback,
                        (unsigned long long)sweep.sampler_unmapped, (unsigned long long)sweep.sampler_wrong,
                        (unsigned long long)sweep.sampler_border_mapped, reads.size(), extra_reads.size(), sweep.key_insufficient.size());
        }
        // ---------------------------------------------------------------- self-test (S1)
        if (self_test) {
            std::vector<std::string> bad;
            uint32_t defs[256], ss[14];
            bool set[256];
            d9g_ref::dxvk_defaults(defs, set, ss, P.dxvk, true);
            // bases on which each fact is compared raw: blending on, stencil on, depth on
            std::vector<Case> cs;
            Base b{"self", D3DFMT_A8R8G8B8, 1280, 720, D3DFMT_D24S8, 1280, 720, 1, D3DPT_TRIANGLESTRIP, M_DPUP};
            Case c = make_case(b, defs);
            set_rs(c, D3DRS_MULTISAMPLEANTIALIAS, 0);
            set_rs(c, D3DRS_ALPHABLENDENABLE, 1);
            set_rs(c, D3DRS_SRCBLEND, D3DBLEND_SRCALPHA);
            set_rs(c, D3DRS_DESTBLEND, D3DBLEND_INVSRCALPHA);
            set_rs(c, D3DRS_STENCILENABLE, 1);
            set_rs(c, D3DRS_STENCILFUNC, D3DCMP_EQUAL);
            set_rs(c, D3DRS_STENCILPASS, D3DSTENCILOP_REPLACE);
            set_rs(c, D3DRS_STENCILFAIL, D3DSTENCILOP_INCR);
            set_rs(c, D3DRS_STENCILZFAIL, D3DSTENCILOP_DECR);
            set_rs(c, D3DRS_STENCILWRITEMASK, 0xFF);
            cs.push_back(c);
            Case c2 = c;
            c2.in.prim = D3DPT_TRIANGLELIST;
            cs.push_back(c2);
            Case c3 = c;   // four render targets, all written
            for (uint32_t i = 1; i < 4; i++) c3.in.rt[i] = c3.in.rt[0];
            c3.in.ps_rt_mask = 0xF;
            cs.push_back(c3);
            uint64_t testable = 0;
            std::vector<std::string> never;
            // every base must be compared: a base unmapped or failing would skip every fact silently
            // (the checker-logic re-review's finding 6)
            for (size_t bi = 0; bi < cs.size(); bi++) {
                uint32_t full[256];
                fill_full_rs(cs[bi].in, cs[bi].rs.data(), full);
                const PipelineCheck base = check_pipeline(cs[bi].in, full, P, 0);
                if (base.unmapped()) bad.push_back("self-test base " + std::to_string(bi) + " is unmapped: " + base.desc.unmapped[0]);
                else if (base.fails())
                    bad.push_back("self-test base " + std::to_string(bi) + " fails: {" + fact_list(base.differ, d9g_pipeline_fact_names) +
                                  "} " + base.reason);
            }
            // The facts no base can compare raw, each for a reason the device's mapping gives: the
            // depth-bias values (d9g maps no non-zero bias, so the bias is off on every mapped
            // tuple), the minimum sample shading (SM2/SM3 have no sample-rate shading, so it is off)
            // and the logic op (D3D9 has none, so it is off). Any other fact never perturbed fails.
            static const int kNeverRaw[] = {PF_rs_depthBiasConstantFactor, PF_rs_depthBiasClamp, PF_rs_depthBiasSlopeFactor,
                                            PF_rs_depthBiasRepresentation, PF_rs_depthBiasExact, PF_ms_minSampleShading,
                                            PF_cb_logicOp};
            for (int f = 0; f < PF_COUNT; f++) {
                bool tested = false;
                for (const Case& x : cs) {
                    uint32_t full[256];
                    fill_full_rs(x.in, x.rs.data(), full);
                    const PipelineCheck base = check_pipeline(x.in, full, P, 0);
                    // a field compared raw, or held by a rule or a residue (perturbing it must break them)
                    const bool held = base.cls[f] == C_EQUAL || base.cls[f] == C_BGRA || base.cls[f] == C_FAN ||
                                      base.cls[f] == C_DEPTH_CLAMP || base.cls[f] == C_IGN_SAMPLE_BITS ||
                                      base.cls[f] == C_NULL_STREAM;
                    const bool read = base.context[f] == C_EQUAL || base.context[f] == C_IGN_SAMPLE_BITS;
                    if (base.unmapped() || base.fails() || !held || !read) continue;
                    const PipelineCheck k = check_pipeline(x.in, full, P, 0, f);
                    tested = true;
                    // the perturbed field named; anything else only a field whose context it is
                    bool named = false, other = false;
                    for (int g : k.differ) {
                        if (g == f) named = true;
                        else if (!depends_on(g, f)) other = true;
                    }
                    if (!named || other)
                        bad.push_back(std::string("perturbing ") + d9g_pipeline_fact_names[f] + " names {" +
                                      fact_list(k.differ, d9g_pipeline_fact_names) + "}");
                    break;
                }
                if (tested) testable++;
                else {
                    never.push_back(d9g_pipeline_fact_names[f]);
                    if (std::find(std::begin(kNeverRaw), std::end(kNeverRaw), f) == std::end(kNeverRaw))
                        bad.push_back(std::string("fact ") + d9g_pipeline_fact_names[f] +
                                      " is never perturbed (no base compares it raw, and it is not one of the facts no base can)");
                }
            }
            // the vertex input by the signature (U9), on a base whose vertex shader reads the colour
            // first, then the position, then an input the declaration lacks (the null stream), and not
            // the texture coordinate: compared, with the rule NULL_STREAM; every fact the rule holds,
            // perturbed, is named (with the divisor where its clause reads the fact perturbed), and so
            // is each location and each null-stream fact compared raw
            uint64_t null_rule_perturbed = 0;
            {
                Case c4 = cs[0];
                set_signature(c4.in, {VsInput{D3DDECLUSAGE_COLOR, 0}, VsInput{D3DDECLUSAGE_POSITION, 0}, VsInput{D3DDECLUSAGE_NORMAL, 0}});
                uint32_t full[256];
                fill_full_rs(c4.in, c4.rs.data(), full);
                const PipelineCheck base = check_pipeline(c4.in, full, P, 0);
                if (base.unmapped() || base.fails() || !base.null_stream || !base.desc.null_stream || base.desc.bgra_mask != 1u)
                    bad.push_back("the NULL_STREAM self-test's base is not a compared null-stream tuple with BGRA at location 0: {" +
                                  fact_list(base.differ, d9g_pipeline_fact_names) + "} " + base.reason);
                else
                    for (int f = 0; f < PF_COUNT; f++) {
                        if (!is_vi_fact(f) || (base.cls[f] != C_NULL_STREAM && base.cls[f] != C_EQUAL && base.cls[f] != C_BGRA)) continue;
                        const PipelineCheck k = check_pipeline(c4.in, full, P, 0, f);
                        null_rule_perturbed++;
                        bool named = false, other = false;
                        for (int g : k.differ) {
                            if (g == f) named = true;
                            else if (!depends_on(g, f)) other = true;
                        }
                        if (!named || other)
                            bad.push_back(std::string("perturbing ") + d9g_pipeline_fact_names[f] + " on the null-stream base names {" +
                                          fact_list(k.differ, d9g_pipeline_fact_names) + "}");
                    }
            }
            // the transcriptions' sRGB rule (the transcription re-review's finding 1): an sRGB write
            // or sample on a format with no sRGB variant is the linear view (IsSrgbCompatible,
            // d3d9_common_texture.h:309-311; GetRenderTargetView, d3d9_subresource.h:86-88), so
            // DXVK's facts with SRGBWRITEENABLE or SRGBTEXTURE on equal its facts with them off.
            // d9g leaves both states unmapped, so no compared tuple reaches this rule: it is held
            // here, on the reference itself.
            for (uint32_t fmt : {uint32_t(D3DFMT_R5G6B5), uint32_t(D3DFMT_A16B16G16R16F)}) {
                Case c = cs[0];
                c.in.rt[0].fmt = fmt;
                uint32_t off[256], on[256];
                fill_full_rs(c.in, c.rs.data(), off);
                std::memcpy(on, off, sizeof on);
                on[D3DRS_SRGBWRITEENABLE] = 1;
                const d9g_ref::PipelineResult a = d9g_ref::dxvk_pipeline(c.in, off, P.dxvk, d9g_ref::standins_chosen(c.in));
                const d9g_ref::PipelineResult b = d9g_ref::dxvk_pipeline(c.in, on, P.dxvk, d9g_ref::standins_chosen(c.in));
                if (!a.refused.empty() || !b.refused.empty() || std::memcmp(a.facts.v, b.facts.v, sizeof a.facts.v) != 0)
                    bad.push_back("the reference's sRGB rule: SRGBWRITEENABLE on render-target format " + std::to_string(fmt) +
                                  " (no sRGB variant) moves DXVK's facts");
                SamplerInput si;
                for (uint32_t i = 0; i < 14; i++) si.ss[i] = ss[i];
                for (int ax : {D3DSAMP_ADDRESSU, D3DSAMP_ADDRESSV, D3DSAMP_ADDRESSW}) si.ss[ax] = D3DTADDRESS_BORDER;
                si.ss[D3DSAMP_BORDERCOLOR] = 0x80402010u;
                si.tex = true; si.levels = 1; si.tex_fmt = fmt;
                d9g_ref::DxvkParams q = P.dxvk;   // the custom border colour needs the view's format here
                q.features["extCustomBorderColor.customBorderColorWithoutFormat"] = false;
                const d9g_ref::SamplerResult sa = d9g_ref::dxvk_sampler(si, q);
                si.ss[D3DSAMP_SRGBTEXTURE] = 1;
                const d9g_ref::SamplerResult sb = d9g_ref::dxvk_sampler(si, q);
                if (!sa.refused.empty() || !sb.refused.empty() || std::memcmp(sa.facts.v, sb.facts.v, sizeof sa.facts.v) != 0)
                    bad.push_back("the reference's sRGB rule: SRGBTEXTURE on texture format " + std::to_string(fmt) +
                                  " (no sRGB variant) moves DXVK's sampler facts");
            }
            // liveness: an SDL table entry and a DXVK span result, each perturbed, must be named
            {
                uint32_t full[256];
                fill_full_rs(cs[0].in, cs[0].rs.data(), full);
                d9g_sdl_poke(1);   // SDLToVK_BlendFactor's SRC_ALPHA entry
                const PipelineCheck k = check_pipeline(cs[0].in, full, P, 0);
                d9g_sdl_poke(0);
                if (std::find(k.differ.begin(), k.differ.end(), int(PF_cb0_srcColorBlendFactor)) == k.differ.end())
                    bad.push_back("SDL table liveness: perturbing SDLToVK_BlendFactor was not named");
                d9g_ref::set_poke(1);   // DXVK's dynamic depth compare op, after its span
                const PipelineCheck k2 = check_pipeline(cs[0].in, full, P, 0);
                d9g_ref::set_poke(0);
                if (k2.differ.size() != 1 || k2.differ[0] != PF_ds_depthCompareOp)
                    bad.push_back("DXVK span liveness: perturbing the depth compare op named {" +
                                  fact_list(k2.differ, d9g_pipeline_fact_names) + "}");
            }
            // the sampler comparator, on three bases: each sampler fact perturbed on SDL's side is
            // named (the review's checker-logic finding 8). A plain sampler compares every fact
            // raw; a mapped BORDER one (where the device has CLAMP_TO_BORDER) names each alone or
            // with the sm_pNextBorderSwizzle the BORDER_SWIZZLE rule reads it for, and with it
            // where DXVK chains its mapping, and each of the rule's DXVK-side clauses perturbed
            // alone names sm_pNextBorderSwizzle; a fallback BORDER one (a custom colour, on every
            // device) absorbs only what DXVK's border decides, the fallback axes, the colour and
            // its two chains, and names every other fact alone (the I6 reviews' finding 1: a
            // fallback that absorbed anything else would hide a d9g fault on every stock device)
            uint64_t sampler_perturbed = 0, border_perturbed = 0, rule_clauses_perturbed = 0, fallback_perturbed = 0;
            uint64_t fallback_absorbed = 0;
            {
                SamplerInput si;
                for (uint32_t i = 0; i < 14; i++) si.ss[i] = ss[i];
                si.ss[D3DSAMP_MINFILTER] = D3DTEXF_LINEAR;
                si.ss[D3DSAMP_MIPFILTER] = D3DTEXF_LINEAR;
                si.tex = true; si.levels = 6; si.tex_fmt = D3DFMT_A8R8G8B8;
                const SamplerCheck base = check_sampler(si, P, 0);
                if (base.unmapped() || !base.differ.empty() || !base.reason.empty() || base.fallback)
                    bad.push_back("the sampler self-test's base is not compared raw");
                else
                    for (int f = 0; f < SF_COUNT; f++) {
                        const SamplerCheck k = check_sampler(si, P, 0, f);
                        sampler_perturbed++;
                        if (k.differ.size() != 1 || k.differ[0] != f)
                            bad.push_back(std::string("perturbing ") + d9g_sampler_fact_names[f] + " names {" +
                                          fact_list(k.differ, d9g_sampler_fact_names) + "}");
                    }
                for (int ax : {D3DSAMP_ADDRESSU, D3DSAMP_ADDRESSV, D3DSAMP_ADDRESSW}) si.ss[ax] = D3DTADDRESS_BORDER;
                if (P.dev.sampler_clamp_to_border) {
                    si.ss[D3DSAMP_BORDERCOLOR] = 0;
                    const SamplerCheck bb = check_sampler(si, P, 0);
                    const bool chained = bb.dx.facts.v[SF_sm_pNextBorderSwizzle] == 1;
                    if (bb.unmapped() || !bb.differ.empty() || !bb.reason.empty() || bb.fallback || !bb.border_mapped)
                        bad.push_back("the BORDER sampler self-test's base is not a mapped BORDER compared: {" +
                                      fact_list(bb.differ, d9g_sampler_fact_names) + "} " + bb.reason);
                    else {
                        for (int f = 0; f < SF_COUNT; f++) {
                            const SamplerCheck k = check_sampler(si, P, 0, f);
                            border_perturbed++;
                            bool named = false, other = false, rule = false;
                            for (int g : k.differ) {
                                if (g == f) named = true;
                                else if (sampler_depends_on(g, f)) rule = true;
                                else other = true;
                            }
                            // where DXVK chains the mapping, a fact the rule reads breaks the rule too
                            const bool rule_reads = sampler_depends_on(SF_sm_pNextBorderSwizzle, f);
                            if (!named || other || (chained && rule_reads && !rule) || (!chained && rule))
                                bad.push_back(std::string("perturbing ") + d9g_sampler_fact_names[f] + " on a BORDER sampler names {" +
                                              fact_list(k.differ, d9g_sampler_fact_names) + "}");
                        }
                        if (chained)
                            for (int clause : {1, 2}) {
                                const SamplerCheck k = check_sampler(si, P, 0, -1, clause);
                                rule_clauses_perturbed++;
                                if (k.differ.size() != 1 || k.differ[0] != SF_sm_pNextBorderSwizzle || k.by_rule)
                                    bad.push_back(std::string("the BORDER_SWIZZLE rule's ") + (clause == 1 ? "identity" : "srgb") +
                                                  " clause perturbed names {" + fact_list(k.differ, d9g_sampler_fact_names) + "}");
                            }
                    }
                }
                si.ss[D3DSAMP_BORDERCOLOR] = 0x80402010u;   // a custom colour: the fallback on every device
                const SamplerCheck fb = check_sampler(si, P, 0);
                if (fb.unmapped() || !fb.differ.empty() || !fb.reason.empty() || !fb.fallback || !fb.dx.has_border)
                    bad.push_back("the fallback BORDER self-test's base is not a fallback compared: {" +
                                  fact_list(fb.differ, d9g_sampler_fact_names) + "} " + fb.reason);
                else
                    for (int f = 0; f < SF_COUNT; f++) {
                        const int axis = f == SF_sm_addressModeU ? 0 : f == SF_sm_addressModeV ? 1 : f == SF_sm_addressModeW ? 2 : -1;
                        const bool absorbed = (axis >= 0 && fb.desc.axes[axis] == AddrDecision::fallback_border) ||
                                              f == SF_sm_borderColor || f == SF_sm_pNextCustomBorder || f == SF_sm_pNextBorderSwizzle;
                        if (absorbed) { fallback_absorbed++; continue; }
                        const SamplerCheck k = check_sampler(si, P, 0, f);
                        fallback_perturbed++;
                        if (k.differ.size() != 1 || k.differ[0] != f)
                            bad.push_back(std::string("perturbing ") + d9g_sampler_fact_names[f] + " on a fallback BORDER sampler names {" +
                                          fact_list(k.differ, d9g_sampler_fact_names) + "}");
                    }
            }
            for (const auto& x : bad) run.fail("self-test: " + x);
            run.field("self_test", "{\"facts\": " + std::to_string(PF_COUNT) + ", \"perturbed\": " + std::to_string(testable) +
                                       ", \"null_stream_base_perturbed\": " + std::to_string(null_rule_perturbed) +
                                       ", \"never_compared_raw_on_the_self_test_bases\": " + list_json(never) +
                                       ", \"sampler_facts\": " + std::to_string(SF_COUNT) + ", \"sampler_perturbed\": " +
                                       std::to_string(sampler_perturbed) + ", \"border_sampler_perturbed\": " +
                                       std::to_string(border_perturbed) + ", \"border_swizzle_clauses_perturbed\": " +
                                       std::to_string(rule_clauses_perturbed) + ", \"fallback_sampler_perturbed\": " +
                                       std::to_string(fallback_perturbed) + ", \"fallback_sampler_absorbed\": " +
                                       std::to_string(fallback_absorbed) + ", \"failed\": " + list_json(bad) + "}");
            std::printf("self-test: %llu of %d facts perturbed and named alone; %zu not compared raw on its bases; %llu vertex-input "
                        "facts perturbed on the null-stream base; sampler facts "
                        "(%d) perturbed: %llu on a plain sampler, %llu on a mapped BORDER one (and %llu BORDER_SWIZZLE clauses), "
                        "%llu on a fallback BORDER one (%llu absorbed there); %zu failed\n",
                        (unsigned long long)testable, PF_COUNT, never.size(), (unsigned long long)null_rule_perturbed, SF_COUNT,
                        (unsigned long long)sampler_perturbed,
                        (unsigned long long)border_perturbed, (unsigned long long)rule_clauses_perturbed,
                        (unsigned long long)fallback_perturbed, (unsigned long long)fallback_absorbed, bad.size());
        }
        // ---------------------------------------------------------------- census
        std::vector<StreamTuples> streams;
        CensusResult census;
        if (!dumps.empty()) {
            for (const auto& d : dumps) streams.push_back(load_dump(d, P));
            std::set<std::string> names;
            for (const auto& t : streams)
                if (!names.insert(t.name).second) throw std::runtime_error("two keys files of stream " + t.name);
            // the streams' identities (state_census.sh's streams.json): their sha256s go into the
            // result, and they must be the dumps' streams (the review's checker-logic finding 6)
            if (!identity_path.empty()) {
                const std::string text = read_file(identity_path);
                Value idv = mini_json::parse(text);
                std::set<std::string> id_names;
                for (const auto& [k, x] : idv.fields) {
                    id_names.insert(k);
                    if (x["sha256"].str().size() != 64) throw std::runtime_error(identity_path + ": stream " + k + " has no sha256");
                }
                if (id_names != names) throw std::runtime_error(identity_path + ": its streams are not the keys files' streams");
                run.field("stream_identity", "{\"path\": " + Json::q(identity_path) + ", \"sha256\": " + Json::q(sha256_of(text)) +
                                                 ", \"streams\": " + text + "}");
            }
            // every census needs the record: without it the device's reflection is held to nothing
            if (record_path.empty())
                throw std::runtime_error("a census needs --shader-record (test/shader_record.py): the device's reflection is "
                                         "held to the record's two readings of the bytecode, and DXVK is evaluated with it");
            {
                run.record = load_shader_record(record_path);
                run.have_record = true;
                run.field("shader_record", "{\"path\": " + Json::q(run.record.path) + ", \"sha256\": " +
                                               Json::q(run.record.sha256) + ", \"pixel_shaders\": " +
                                               std::to_string(run.record.ps.size()) + ", \"vertex_shaders\": " +
                                               std::to_string(run.record.vs.size()) + "}");
            }
            // the binary the device ran in: one for every keys file, and, given, --device-binary's
            // (the checker-logic re-review's finding 5)
            std::set<std::string> libs;
            for (const auto& t : streams) libs.insert(t.library_sha256);
            if (libs.size() != 1) run.fail("the keys files name " + std::to_string(libs.size()) + " device binaries, not one");
            std::string want_lib;
            if (!device_binary.empty()) {
                want_lib = sha256_of(read_file(device_binary));
                for (const auto& t : streams)
                    if (t.library_sha256 != want_lib)
                        run.fail(t.path + ": made in the binary " + (t.library_sha256.empty() ? std::string("(none named)") : t.library_sha256) +
                                 ", not --device-binary " + device_binary + " (" + want_lib + ")");
            }
            run.field("device_binary", device_binary.empty() ? std::string("null")
                                                              : "{\"path\": " + Json::q(device_binary) + ", \"sha256\": " + Json::q(want_lib) + "}");
            census = run_census(streams, P, faults, run.record);
            for (const auto& f : census.failures) run.fail(f);
            // DXVK's code reads, on the census's own tuples, only the states the keys file carries
            // (the key and the census-extra states) and the dynamic ones: any other state is the
            // device's default there, not the stream's (the checker-logic re-review's NIT 12)
            std::vector<std::string> census_extra_reads;
            for (uint32_t s = 0; s < 256; s++)
                if (census.read[s] && !key_state(s) && !dynamic_state(s) && !census_extra(s)) census_extra_reads.push_back(rs_name(s));
            for (const auto& e : census_extra_reads)
                run.fail("DXVK reads a render state the keys file does not carry, on a census tuple: " + e);
            run.pending_items = census.pending;
            if (census.fallback_border) run.owed_border = true;
            run.test_leg = P.capability_override;
            if (census.rt_feedback) run.pending_rt_feedback = true;
            std::string ps = "{";
            bool first = true;
            for (const auto& [k, v] : census.per_stream) { ps += (first ? "" : ", ") + Json::q(k) + ": " + v; first = false; }
            ps += "}";
            std::vector<std::string> stream_names;
            for (const auto& t : streams) stream_names.push_back(t.name);
            run.field("census", "{\"streams\": " + ps + ", \"stream_names\": " + list_json(stream_names) +
                                    ", \"pipeline_tuples\": " + std::to_string(census.pipeline_tuples) +
                                    ", \"sampler_tuples\": " + std::to_string(census.sampler_tuples) +
                                    ", \"depth_clamp_tuples\": " + std::to_string(census.depth_clamp) +
                                    ", \"depth_clamp_property_tuples\": " + std::to_string(census.depth_clamp_property) +
                                    ", \"clamp_with_clip_missing_tuples\": " + std::to_string(census.clamp_with_clip_missing) +
                                    ", \"fallback_border_sampler_tuples\": " + std::to_string(census.fallback_border) +
                                    ", \"border_mapped_sampler_tuples\": " + std::to_string(census.border_mapped) +
                                    ", \"sampler_equal_by_rule_tuples\": " + std::to_string(census.sampler_by_rule) +
                                    ", \"border_swizzle_rule_sampler_tuples\": " + std::to_string(census.border_swizzle_rule) +
                                    ", \"rt_feedback_tuples\": " + std::to_string(census.rt_feedback) +
                                    ", \"null_stream_tuples\": " + std::to_string(census.null_stream) +
                                    ", \"tuples_with_recorded_reflection\": " + std::to_string(census.reflected) +
                                    ", \"tuples_with_recorded_vs_signature\": " + std::to_string(census.vs_reflected) +
                                    ", \"reflection_differs_tuples\": " + std::to_string(census.reflection_differs) +
                                    ", \"shaders_held_to_the_record\": " + std::to_string(census.shaders_checked) +
                                    ", \"undecided_tuples\": " + std::to_string(census.undecided) +
                                    ", \"reads_outside_the_keys_file\": " + list_json(census_extra_reads) +
                                    ", \"sampler_history_tuples\": " + std::to_string(census.sampler_history) + "}");
            std::string sn;
            for (const auto& x : stream_names) sn += (sn.empty() ? "" : " ") + x;
            std::printf("census: %zu streams (%s), %llu pipeline tuples (%llu DEPTH_CLAMP, %llu with the depth-clamp property, "
                        "%llu with the null stream by rule NULL_STREAM, %llu undecided: %llu RT_FEEDBACK), %llu sampler tuples "
                        "(%llu BORDER mapped, %llu fallback BORDER, %llu equal by rule BORDER_SWIZZLE, %llu with a history compare "
                        "bit); %llu unmapped, %llu differ\n",
                        streams.size(), sn.c_str(), (unsigned long long)census.pipeline_tuples, (unsigned long long)census.depth_clamp,
                        (unsigned long long)census.depth_clamp_property, (unsigned long long)census.null_stream,
                        (unsigned long long)census.undecided, (unsigned long long)census.rt_feedback,
                        (unsigned long long)census.sampler_tuples, (unsigned long long)census.border_mapped,
                        (unsigned long long)census.fallback_border, (unsigned long long)census.border_swizzle_rule,
                        (unsigned long long)census.sampler_history, (unsigned long long)census.unmapped,
                        (unsigned long long)census.differ);
            std::printf("capabilities: clamp to border %d, depth clip and clamp %d%s\n", P.dev.sampler_clamp_to_border ? 1 : 0,
                        P.dev.depth_clip_and_clamp ? 1 : 0,
                        P.capability_override.empty() ? "" : (" (a test run's override: " + P.capability_override + " forced off)").c_str());
            std::printf("reflection: %llu of %llu pipeline tuples with a recorded pixel shader, %llu with a recorded vertex "
                        "shader; the device's reflection the record's on %llu created shaders, %llu tuples differ\n",
                        (unsigned long long)census.reflected, (unsigned long long)census.pipeline_tuples,
                        (unsigned long long)census.vs_reflected, (unsigned long long)census.shaders_checked,
                        (unsigned long long)census.reflection_differs);
            if (census.depth_clamp) run.pending_depth_clamp = true;
        }
        // ---------------------------------------------------------------- coverage (S3)
        if (!dumps.empty() && domain) {
            uint64_t census_vals = 0, sweep_only = 0, values = 0;
            std::string cov = "{";
            bool first = true;
            for (size_t si = 0; si < kNumPipelineRS; si++) {
                const std::string st = rs_name(kPipelineRenderStates[si]);
                std::set<std::string> swept;
                for (const auto& [v, o] : sweep.table[st]) swept.insert(v);
                const std::set<std::string>& cv = census.census_values[st];
                std::vector<std::string> in_census(cv.begin(), cv.end());
                values += swept.size();
                for (const auto& v : swept) (cv.count(v) ? census_vals : sweep_only)++;
                cov += (first ? "" : ", ") + Json::q(st) + ": {\"census\": " + list_json(in_census) + ", \"swept\": " +
                       std::to_string(swept.size()) + "}";
                first = false;
            }
            cov += "}";
            run.field("coverage", "{\"swept_values\": " + std::to_string(values) + ", \"census_exercised\": " +
                                      std::to_string(census_vals) + ", \"sweep_only\": " + std::to_string(sweep_only) +
                                      ", \"by_state\": " + cov + "}");
            std::printf("coverage: %llu swept render-state values, %llu the census exercises, %llu sweep-only\n",
                        (unsigned long long)values, (unsigned long long)census_vals, (unsigned long long)sweep_only);
        }
        // ---------------------------------------------------------------- controls
        if (do_controls) {
            if (faults) throw std::runtime_error("--controls runs each fault itself: no --fault with it");
            std::string res = "[";
            bool first = true;
            for (const Control& ctl : controls()) {
                const uint32_t bit = map_fault_bit(ctl.name);
                std::string outcome, named;
                std::set<std::string> fields;
                uint64_t census_differ = 0;   // the census tuples the control fails (pipeline and sampler)
                if (!streams.empty()) {
                    const CensusResult r = run_census(streams, P, bit, run.record);
                    fields = r.failing_fields;
                    census_differ = r.differ;
                }
                std::set<std::string> allowed(ctl.targets.begin(), ctl.targets.end());
                auto judge = [&](const std::set<std::string>& f, const char* where) {
                    bool any = false, other = false;
                    for (const auto& x : f) (allowed.count(x) ? any : other) = true;
                    for (const auto& x : f) named += (named.empty() ? "" : ", ") + x;
                    if (any && !other) return std::string("fails as required (") + where + ")";
                    if (other) return std::string("FAILS OTHER FIELDS (") + where + ")";
                    return std::string();
                };
                outcome = streams.empty() ? "" : judge(fields, "census");
                if (outcome.empty()) {
                    Params Pc = P;
                    std::string with;
                    if (!std::strcmp(ctl.needs, "border") && !Pc.dev.sampler_clamp_to_border) {
                        Pc.dev.sampler_clamp_to_border = true;
                        with = ", with CLAMP_TO_BORDER on";
                    }
                    if (!std::strcmp(ctl.needs, "depth-clamp") && !Pc.dev.depth_clip_and_clamp) {
                        Pc.dev.depth_clip_and_clamp = true;
                        with = ", with depth clip and clamp on";
                    }
                    if (d9g_sdl_set_device_caps(Pc.dev.sampler_clamp_to_border, Pc.dev.depth_clip_and_clamp) != 0)
                        throw std::runtime_error(std::string("control ") + ctl.name + ": this check's SDL side is the stock tarball's, "
                                                 "which has no capability to run it with");
                    const SweepResult s = run_sweep(Pc, bit);
                    (void)d9g_sdl_set_device_caps(P.dev.sampler_clamp_to_border, P.dev.depth_clip_and_clamp);
                    std::set<std::string> sf;
                    for (int f : s.differing_fields) sf.insert(d9g_pipeline_fact_names[f]);
                    for (int f : s.sampler_differing) sf.insert(d9g_sampler_fact_names[f]);
                    for (const auto& w : s.wrong_reasons) sf.insert("reason: " + w);
                    for (const auto& o : s.obligations) sf.insert("obligation " + o);
                    named.clear();
                    const std::string o = judge(sf, ("domain sweep" + with).c_str());
                    outcome = o.empty() ? "PASSES (the control is broken)" : (streams.empty() ? o : "not exercised by census; " + o);
                }
                if (outcome.find("fails as required") == std::string::npos) run.fail("control " + std::string(ctl.name) + ": " + outcome);
                else if (ctl.census_required && !streams.empty() && outcome.rfind("fails as required (census)", 0) != 0)
                    run.fail("control " + std::string(ctl.name) + ": a vertex-input control the census must fail itself, and only "
                             "the sweep did (" + outcome + "): the census's tuples check the vertex input nowhere it acts");
                res += std::string(first ? "" : ", ") + "{\"name\": " + Json::q(ctl.name) + ", \"outcome\": " + Json::q(outcome) +
                       ", \"fields\": " + Json::q(named) + ", \"census_tuples_failed\": " + std::to_string(census_differ) + "}";
                first = false;
                std::printf("control %-20s %s: %s\n", ctl.name, outcome.c_str(), named.c_str());
            }
            run.field("controls", res + "]");
        }
        // ---------------------------------------------------------------- the ruling (M8)
        if (!ruling_path.empty()) {
            const Ruling rl = load_ruling(ruling_path, repo_path);
            run.ruling = rl.ruling;
            run.field("ruling_file", "{\"path\": " + Json::q(rl.file.rel) + ", \"sha256\": " + Json::q(rl.file.sha256) +
                                          ", \"commit\": " + Json::q(rl.file.commit) + ", \"repository\": " + Json::q(rl.root) +
                                          ", \"head\": " + Json::q(rl.head) + ", \"decision\": {\"path\": " +
                                          Json::q(rl.decision.rel) + ", \"sha256\": " + Json::q(rl.decision.sha256) +
                                          ", \"commit\": " + Json::q(rl.decision.commit) + "}, \"provenance\": " +
                                          Json::q("both files are committed at HEAD of the checkout this check was built from, "
                                                  "read as committed, their working copies unmodified; that proves "
                                                  "they are committed, not who decided them") + "}");
            // ruling 3: the patch where the device has depth clip and clamp, the shader emulation
            // where it does not. On a device with it, SDL must build clamp with clip on every mapped
            // tuple (a tuple without the property, or one still carrying R_DEPTH_CLAMP, fails); on a
            // device without it, the R_DEPTH_CLAMP tuples are the shader route's, owed
            if (run.ruling == "patch") {
                if (P.dev.depth_clip_and_clamp && (run.pending_depth_clamp || census.clamp_with_clip_missing))
                    run.fail_first("DEPTH_CLAMP ruled a local SDL patch: SDL must build depthClampEnable with depth clip, and it does not (" +
                             std::to_string(census.clamp_with_clip_missing) + " mapped tuples without clamp with clip, " +
                             std::to_string(census.depth_clamp) + " with R_DEPTH_CLAMP, on a device with depth clip and clamp)");
                else if (run.pending_depth_clamp)
                    run.owed_depth_clamp = true;
            }
            if (run.ruling == "shader") {
                // the obligation the key carries: R_DEPTH_CLAMP in every DEPTH_CLAMP tuple's key
                // (C_DEPTH_CLAMP requires the bit; a key without it DIFFERS), discharged by the
                // shader route clamping fragment depth to the viewport's depth range
                run.field("obligations", "[{\"name\": \"DEPTH_CLAMP by the shader route\", \"key_bit\": \"R_DEPTH_CLAMP\", "
                                         "\"what\": \"the shader route clamps fragment depth to the viewport depth range, which "
                                         "DXVK's depthClampEnable does and SDL's pipeline does not\", \"tuples\": " +
                                         std::to_string(census.depth_clamp) + "}]");
            }
        }
    } catch (const std::exception& e) {
        run.fail(std::string("error: ") + e.what());
    }
    // ---------------------------------------------------------------- verdict
    std::string verdict, marker;
    std::string pending, owed;
    if (run.pending_depth_clamp && run.ruling.empty()) pending += " DEPTH_CLAMP";
    if (run.pending_rt_feedback) pending += " RT_FEEDBACK";
    if (run.owed_border) owed += " BORDER";
    if (run.owed_depth_clamp) owed += " DEPTH_CLAMP";
    if (!run.failures.empty()) { verdict = "fail"; marker = "STATE_CHECK_FAIL"; }
    else if (!owed.empty() || !run.test_leg.empty()) {
        // ruling 3's fallback owed, or a test leg: never OK, never only pending
        marker = "STATE_CHECK_FALLBACK_OWED" + owed + (pending.empty() ? "" : " PENDING_USER" + pending) +
                 (run.test_leg.empty() ? "" : " TEST_LEG=" + run.test_leg);
        verdict = "fallback owed:" + (owed.empty() ? std::string(" none") : owed) + (pending.empty() ? "" : "; pending user:" + pending) +
                  (run.test_leg.empty() ? "" : "; a test leg under the capability override " + run.test_leg);
    } else if (!pending.empty()) {
        verdict = "pending user:" + pending + " only";
        marker = "STATE_CHECK_PENDING_USER" + pending;
    } else { verdict = "pass"; marker = "STATE_CHECK_OK"; }
    run.field("verdict", Json::q(verdict));
    {
        std::vector<std::string> o;
        if (run.owed_border) o.push_back("BORDER");
        if (run.owed_depth_clamp) o.push_back("DEPTH_CLAMP");
        run.field("fallback_owed", list_json(o));
        run.field("test_leg", run.test_leg.empty() ? "null" : Json::q(run.test_leg));
    }
    run.field("ruling", run.ruling.empty() ? "null" : Json::q(run.ruling));
    run.field("border_swizzle_rule", Json::q(kBorderSwizzleClause));   // the clause the rule rests on, cited in the result
    run.field("failures", list_json(run.failures));
    run.field("pending_user", list_json(run.pending_items));
    if (!out_path.empty()) {
        std::ofstream o(out_path + ".tmp");
        o << "{\n" << run.out << "\n}\n";
        o.close();
        std::rename((out_path + ".tmp").c_str(), out_path.c_str());
    }
    for (size_t i = 0; i < run.failures.size() && i < 30; i++) std::printf("  FAIL: %s\n", run.failures[i].c_str());
    for (size_t i = 0; i < run.pending_items.size() && i < 30; i++) std::printf("  PENDING: %s\n", run.pending_items[i].c_str());
    std::printf("%s\n", marker.c_str());
    return marker == "STATE_CHECK_OK" ? 0 : marker.rfind("STATE_CHECK_PENDING_USER", 0) == 0 ? 3
         : marker.rfind("STATE_CHECK_FALLBACK_OWED", 0) == 0 ? 4 : 1;
}
