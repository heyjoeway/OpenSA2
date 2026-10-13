// Host 2's shader translator, the SDL half (R1 U2): DxvkIrShader::getCode's steps
// (src/dxvk/dxvk_shader_ir.cpp:2022-2143) with SDL_gpu's ABI in place of DXVK's binding model.
//
// What differs from DXVK, and only this:
//   - spec constants: dxbc-spirv's own LowerIoPass::lowerSpecConstantsToCbv, which is what DXVK's
//     graphics-pipeline-library variant does (:2077), for every build, linked or not;
//   - push data: DXVK's two push blocks (D3D9SharedPushData, D3D9VsPushData) become members of the
//     same uniform buffer, after the seven spec dwords, with DXVK's member layout;
//   - descriptors: SDL_gpu's sets and slots (d9g_shader.h) instead of DXVK's heap and sets;
//   - the dynamic float buffer: declared at SDL's 4 KiB window (256 vectors) where DXVK declares
//     its count plus one zero element (d3d9_shader.cpp:328-343); the device never maps a draw
//     whose dynamic float count would read the 257th;
//   - with the profile's state_words (a device without 8- and 16-bit access), the push blocks'
//     u8 and u16 members are declared as the 32-bit words that hold them, at the same bytes, and
//     each read of one is a bit extract of its word (lower_narrow below).
// Everything else (the SPIR-V options, the linkage I/O resolution, the builder) is DXVK's.
#include "d9g_shader.h"
#include "d9g_shader_internal.h"

#include <ir/ir_serialize.h>
#include <spirv/spirv_builder.h>
#include <spirv/spirv_mapping.h>

#include <algorithm>
#include <cstring>
#include <sstream>

namespace d9g::shader {

using namespace dxbc_spv;

namespace {

// D3D9ShaderResourceMapping::CbvIndex (src/d3d9/d3d9_shader.h:53-63)
constexpr uint32_t kCbvVsClipPlanes = 0u, kCbvVsStatic = 3u, kCbvVsDynamic = 4u, kCbvPsShared = 5u, kCbvPsStatic = 6u;

class SdlResourceMapping : public spirv::ResourceMapping {
 public:
  SdlResourceMapping(Stage stage, std::vector<uint32_t> samplers) : m_stage(stage), m_samplers(std::move(samplers)) {}

  spirv::DescriptorBinding mapDescriptor(ir::ScalarType type, uint32_t regSpace, uint32_t regIndex) override {
    spirv::DescriptorBinding b = {};
    bool vs = m_stage == Stage::Vertex;
    switch (type) {
      case ir::ScalarType::eCbv:
        b.set = vs ? 1u : 3u;
        if (regSpace == kStateSpace && regIndex == 0u)
          b.binding = vs ? kVsState : kPsState;
        else if (regSpace == kBorderSpace && regIndex == 0u && !vs)
          b.binding = kPsBorderColours;
        else if (regSpace == 0u && vs && regIndex == kCbvVsStatic)
          b.binding = kVsStaticConstants;
        else if (regSpace == 0u && vs && regIndex == kCbvVsDynamic)
          b.binding = kVsDynamicConstants;
        else if (regSpace == 0u && vs && regIndex == kCbvVsClipPlanes)
          b.binding = kVsClipPlanes;
        else if (regSpace == 0u && !vs && regIndex == kCbvPsStatic)
          b.binding = kPsStaticConstants;
        else if (regSpace == 0u && !vs && regIndex == kCbvPsShared)
          b.binding = kPsTextureStages;
        else
          fail("a constant buffer outside SDL's slot table (space " + std::to_string(regSpace) + ", index " + std::to_string(regIndex) + ")");
        m_slots.push_back(b.binding);
        break;
      case ir::ScalarType::eSrv:
      case ir::ScalarType::eSampler: {
        b.set = vs ? 0u : 2u;
        // dxbc-spirv's SM3 front end declares a sampler register's sampler in space 1 and its
        // image in space 0 (sm3_resources.cpp); like DXVK (D3D9ShaderConverter::
        // determineResourceIndex, d3d9_shader.cpp:1133-1156), only the register index matters.
        auto it = std::find(m_samplers.begin(), m_samplers.end(), regIndex);
        if (it == m_samplers.end())
          fail("a sampler register outside the shader's declared set");
        b.binding = uint32_t(it - m_samplers.begin());
      } break;
      default:
        fail("a descriptor type SDL_gpu's graphics stages have no slot for");
    }
    return b;
  }

  uint32_t mapPushData(ir::ShaderStageMask) override {
    fail("push data left after the SDL ABI pass");
    return 0u;
  }

  const std::string& error() const { return m_error; }
  const std::vector<uint32_t>& slots() const { return m_slots; }

 private:
  void fail(const std::string& e) {
    if (m_error.empty())
      m_error = e;
  }
  Stage m_stage;
  std::vector<uint32_t> m_samplers;
  std::vector<uint32_t> m_slots;
  std::string m_error;
};

// getCode's SPIR-V options (dxvk_shader_ir.cpp:2086-2134) for the profile.
spirv::SpirvBuilder::Options spirv_options(const ShaderProfile& p) {
  spirv::SpirvBuilder::Options options = {};
  options.includeDebugNames = true;
  options.nvRawAccessChains = p.supports_nv_raw_access_chains;
  options.dualSourceBlending = false;  // D3D9 has no dual-source blending (linkage->fsDualSrcBlend)

  if (p.independent_denorm_mode && p.supports_rte[1] && p.supports_denorm_flush[1]) {
    if (p.supports_rte[0]) options.supportedRoundModesF16 |= ir::RoundMode::eNearestEven;
    if (p.supports_rte[1]) options.supportedRoundModesF32 |= ir::RoundMode::eNearestEven;
    if (p.supports_rte[2]) options.supportedRoundModesF64 |= ir::RoundMode::eNearestEven;
    if (p.supports_rtz[0]) options.supportedRoundModesF16 |= ir::RoundMode::eZero;
    if (p.supports_rtz[1]) options.supportedRoundModesF32 |= ir::RoundMode::eZero;
    if (p.supports_rtz[2]) options.supportedRoundModesF64 |= ir::RoundMode::eZero;
    if (p.supports_denorm_flush[0]) options.supportedDenormModesF16 |= ir::DenormMode::eFlush;
    if (p.supports_denorm_flush[1]) options.supportedDenormModesF32 |= ir::DenormMode::eFlush;
    if (p.supports_denorm_flush[2]) options.supportedDenormModesF64 |= ir::DenormMode::eFlush;
    if (p.supports_denorm_preserve[0]) options.supportedDenormModesF16 |= ir::DenormMode::ePreserve;
    if (p.supports_denorm_preserve[1]) options.supportedDenormModesF32 |= ir::DenormMode::ePreserve;
    if (p.supports_denorm_preserve[2]) options.supportedDenormModesF64 |= ir::DenormMode::ePreserve;
    if (p.supports_sz_inf_nan_preserve[1])
      options.floatControls2 = p.supports_float_controls2;
  }

  options.supportsZeroInfNanPreserveF16 = p.supports_sz_inf_nan_preserve[0];
  options.supportsZeroInfNanPreserveF32 = p.supports_sz_inf_nan_preserve[1];
  options.supportsZeroInfNanPreserveF64 = p.supports_sz_inf_nan_preserve[2];
  options.maxCbvSize = p.max_uniform_buffer_size;
  options.maxCbvCount = p.max_uniform_buffer_count;
  return options;
}

// D3D9SharedPushData and D3D9VsPushData as DXVK's lowering declares them
// (d3d9_shader.cpp:226-264, src/d3d9/d3d9_state.h:37-60).
ir::Type shared_push_type() {
  return ir::Type()
      .addStructMember(ir::ScalarType::eU8, 3u)
      .addStructMember(ir::ScalarType::eU8)
      .addStructMember(ir::ScalarType::eF32)
      .addStructMember(ir::ScalarType::eF32)
      .addStructMember(ir::ScalarType::eF32);
}

ir::Type vs_push_type() {
  return ir::Type()
      .addStructMember(ir::ScalarType::eU16)
      .addStructMember(ir::ScalarType::eU16)
      .addStructMember(ir::ScalarType::eU16)
      .addStructMember(ir::ScalarType::eU16);
}

const char* const kSharedNames[5] = {"fogColor", "alphaRef", "fogDistanceScale", "fogDistanceEnd", "fogDensity"};
const char* const kVsNames[4] = {"cFSize", "pointSize", "pointSizeMin", "pointSizeMax"};

// The same blocks as state_words declares them: the bytes of D3D9SharedPushData's u8x3 fogColor and
// u8 alphaRef are one u32 (fogColor[i] in bits 8i to 8i+7, alphaRef in bits 24-31; the state buffer is
// little-endian, d9g_draw_state.cpp writes bytes 28-31), and D3D9VsPushData's four u16 are two u32
// (member m in bits 16(m % 2) to 16(m % 2)+15 of word m / 2). The layout, the offsets and the size are
// the ones above, so the device's bytes are unchanged.
ir::Type shared_word_type() {
  return ir::Type()
      .addStructMember(ir::ScalarType::eU32)
      .addStructMember(ir::ScalarType::eF32)
      .addStructMember(ir::ScalarType::eF32)
      .addStructMember(ir::ScalarType::eF32);
}

ir::Type vs_word_type() {
  return ir::Type().addStructMember(ir::ScalarType::eU32).addStructMember(ir::ScalarType::eU32);
}

const char* const kSharedWordNames[4] = {"fogColorAlphaRef", "fogDistanceScale", "fogDistanceEnd", "fogDensity"};
const char* const kVsWordNames[2] = {"cFSizePointSize", "pointSizeMinMax"};

// Where member m of DXVK's block lands in the state buffer's type: its own member (bits 0), or the
// word that holds it and its first bit and width.
struct Place {
  uint32_t member = 0, bit = 0, bits = 0;
};

Place place_of(bool vs_block, uint32_t m, uint32_t base, bool words) {
  if (!words) return {base + m, 0u, 0u};
  if (vs_block) return {base + m / 2u, 16u * (m % 2u), 16u};
  if (m == 0u) return {base, 0u, 8u};    // fogColor: element i at 8i
  if (m == 1u) return {base, 24u, 8u};   // alphaRef
  return {base + m - 1u, 0u, 0u};
}

bool is_u32(const ir::Type& t) {
  return t.isScalarType() && t.getBaseType(0u).getBaseType() == ir::ScalarType::eU32;
}

// state_words: every use of `def`, a sub-dword value (u8, u16 or a u8 vector) read from bits [bit,
// bit + bits) of `word` (each vector element `bits` wide), made a use of a u32 bit extract of the
// word, and `def` removed. DXVK's lowering converts each such load at once (d3d9_shader.cpp: alphaRef
// and the dynamic float count by ConvertItoI to u32, the point sizes by ConvertItoF, the fog colour
// by CompositeExtract then ConvertItoF); any other use is refused by name, so a module that would still
// need sub-dword types is never built.
bool lower_narrow(ir::Builder& b, ir::SsaDef def, ir::SsaDef word, uint32_t bit, uint32_t bits, const char* name,
                  std::string& error) {
  const ir::Type type = b.getOp(def).getType();
  util::small_vector<ir::SsaDef, 16u> uses;
  b.getUses(def, uses);
  for (auto use : uses) {
    const ir::Op op = b.getOp(use);
    switch (op.getOpCode()) {
      case ir::OpCode::eConvertItoI:
        if (!type.isScalarType() || !is_u32(op.getType())) break;
        b.rewriteOp(use, ir::Op::UBitExtract(ir::ScalarType::eU32, word, b.makeConstant(bit), b.makeConstant(bits)));
        continue;
      case ir::OpCode::eConvertItoF: {
        if (!type.isScalarType()) break;
        auto x = b.addBefore(use, ir::Op::UBitExtract(ir::ScalarType::eU32, word, b.makeConstant(bit), b.makeConstant(bits)));
        b.rewriteOp(use, ir::Op(op).setOperand(0u, x));
      } continue;
      case ir::OpCode::eCompositeExtract: {
        const auto& address = b.getOpForOperand(op, 1u);
        if (!type.isVectorType() || !address || !address.isConstant() || address.getOperandCount() != 1u) break;
        const uint32_t i = uint32_t(address.getOperand(0u));
        if (i >= type.getBaseType(0u).getVectorSize()) break;
        if (!lower_narrow(b, use, word, bit + bits * i, bits, name, error)) return false;
      } continue;
      case ir::OpCode::eDebugName:
        b.remove(use);
        continue;
      default:
        break;
    }
    std::ostringstream s;
    s << op.getOpCode();
    error = std::string("unmapped: the state buffer's ") + name + " is read by " + s.str() +
            ", which the 32-bit state words (no 8- or 16-bit access on this device) cannot lower";
    return false;
  }
  b.remove(def);
  return true;
}

ir::SsaDef find_entry_point(ir::Builder& b) {
  for (auto it = b.getDeclarations().first; it != b.getDeclarations().second; ++it)
    if (it->getOpCode() == ir::OpCode::eEntryPoint)
      return it->getDef();
  return ir::SsaDef();
}

void remove_with_debug_names(ir::Builder& b, ir::SsaDef def) {
  util::small_vector<ir::SsaDef, 16u> uses;
  b.getUses(def, uses);
  for (auto use : uses) {
    auto code = b.getOp(use).getOpCode();
    if (code == ir::OpCode::eDebugName || code == ir::OpCode::eDebugMemberName)
      b.remove(use);
  }
  b.remove(def);
}

}  // namespace

bool deserialize(const std::vector<uint8_t>& ir, ir::Builder& builder) {
  ir::Deserializer deserializer(ir.data(), ir.size());
  return deserializer.deserialize(builder);
}

bool lower_sdl_abi(ir::Builder& b, Stage stage, bool state_words, std::string& error, bool& capped_dynamic) {
  auto entry = find_entry_point(b);
  if (!entry) {
    error = "no entry point";
    return false;
  }
  // Spec constants: declare spec id kSpecDwords - 1 if absent, so that lowerSpecConstantsToCbv
  // builds the full seven-dword prefix whatever the shader reads; its own RemoveUnusedPass drops
  // the extra declaration again.
  bool hasLast = false;
  for (auto it = b.getDeclarations().first; it != b.getDeclarations().second; ++it) {
    if (it->getOpCode() == ir::OpCode::eDclSpecConstant) {
      uint32_t id = uint32_t(it->getOperand(it->getFirstLiteralOperandIndex()));
      if (id >= kSpecDwords) {
        error = "a spec constant beyond D3D9SpecData's seven dwords";
        return false;
      }
      hasLast = hasLast || id == kSpecDwords - 1u;
      if (!it->getType().isScalarType() || it->getType().getBaseType(0u).getBaseType() != ir::ScalarType::eU32) {
        error = "a spec constant that is not a u32";
        return false;
      }
    }
  }
  if (!hasLast)
    b.add(ir::Op::DclSpecConstant(ir::ScalarType::eU32, entry, kSpecDwords - 1u, 0u));
  ir::SsaDef state;
  {
    ir::LowerIoPass io(b);
    state = io.lowerSpecConstantsToCbv(kStateSpace, 0u);
  }
  if (!state || b.getOp(state).getType().getStructMemberCount() != kSpecDwords) {
    error = "lowerSpecConstantsToCbv did not build the seven-dword state prefix";
    return false;
  }

  // Push data: append DXVK's push blocks to the state buffer's type, then turn every PushDataLoad
  // into a load of the member it reads.
  ir::Type stateType = b.getOp(state).getType();
  const uint32_t sharedBase = stateType.getStructMemberCount();
  ir::Type shared = state_words ? shared_word_type() : shared_push_type();
  for (uint32_t i = 0; i < shared.getStructMemberCount(); i++)
    stateType.addStructMember(shared.getBaseType(i));
  uint32_t vsBase = stateType.getStructMemberCount();
  ir::Type vsType = state_words ? vs_word_type() : vs_push_type();
  if (stage == Stage::Vertex) {
    for (uint32_t i = 0; i < vsType.getStructMemberCount(); i++)
      stateType.addStructMember(vsType.getBaseType(i));
  }
  if (stateType.byteOffset(sharedBase) != kStateSharedPushOffset ||
      (stage == Stage::Vertex && stateType.byteOffset(vsBase) != kStateVsPushOffset) ||
      stateType.byteSize() != (stage == Stage::Vertex ? kStateBytesVs : kStateBytesPs)) {
    error = "the state buffer's layout is not the fixed one";
    return false;
  }
  b.rewriteOp(state, ir::Op(b.getOp(state)).setType(stateType));
  for (uint32_t i = 0; i < shared.getStructMemberCount(); i++)
    b.add(ir::Op::DebugMemberName(state, sharedBase + i, state_words ? kSharedWordNames[i] : kSharedNames[i]));
  if (stage == Stage::Vertex)
    for (uint32_t i = 0; i < vsType.getStructMemberCount(); i++)
      b.add(ir::Op::DebugMemberName(state, vsBase + i, state_words ? kVsWordNames[i] : kVsNames[i]));

  std::vector<ir::SsaDef> pushDecls;
  for (auto it = b.getDeclarations().first; it != b.getDeclarations().second; ++it)
    if (it->getOpCode() == ir::OpCode::eDclPushData)
      pushDecls.push_back(it->getDef());
  for (auto decl : pushDecls) {
    const auto& op = b.getOp(decl);
    auto stages = ir::ShaderStageMask(op.getOperand(op.getFirstLiteralOperandIndex() + 1u));
    bool local = bool(stages);
    if (local && stage != Stage::Vertex) {
      error = "pixel shader push data that is not DXVK's shared block";
      return false;
    }
    ir::Type want = local ? vs_push_type() : shared_push_type();
    if (op.getType() != want || uint32_t(op.getOperand(op.getFirstLiteralOperandIndex())) != 0u) {
      error = "push data whose type or offset is not DXVK's D3D9 block";
      return false;
    }
    uint32_t base = local ? vsBase : sharedBase;
    util::small_vector<ir::SsaDef, 64u> uses;
    b.getUses(decl, uses);
    for (auto use : uses) {
      ir::Op useOp = b.getOp(use);
      if (useOp.getOpCode() != ir::OpCode::ePushDataLoad)
        continue;
      const auto& address = b.getOpForOperand(useOp, 1u);
      if (!address || !address.isConstant() || address.getOperandCount() != 1u) {
        error = "a push data load whose address is not one constant member";
        return false;
      }
      const uint32_t m = uint32_t(address.getOperand(0u));
      auto type = useOp.getType();
      if (m >= want.getStructMemberCount() || !type.isBasicType() || want.getBaseType(m) != type.getBaseType(0u)) {
        error = "a push data load that is not a whole member";
        return false;
      }
      const Place at = place_of(local, m, base, state_words);
      auto descriptor = b.addBefore(use, ir::Op::DescriptorLoad(ir::ScalarType::eCbv, state, b.makeConstant(0u)));
      if (!at.bits) {
        b.rewriteOp(use, ir::Op::BufferLoad(type, descriptor, b.makeConstant(at.member),
                                            ir::byteSize(type.getBaseType(0u).getBaseType())));
        continue;
      }
      auto word = b.addBefore(use, ir::Op::BufferLoad(ir::ScalarType::eU32, descriptor, b.makeConstant(at.member), 4u));
      if (!lower_narrow(b, use, word, at.bit, at.bits, local ? kVsNames[m] : kSharedNames[m], error))
        return false;
    }
    remove_with_debug_names(b, decl);
  }

  // The dynamic float buffer at SDL's window.
  capped_dynamic = false;
  for (auto it = b.getDeclarations().first; it != b.getDeclarations().second; ++it) {
    if (it->getOpCode() != ir::OpCode::eDclCbv)
      continue;
    if (uint32_t(it->getOperand(1u)) != 0u || uint32_t(it->getOperand(2u)) != kCbvVsDynamic)
      continue;
    ir::Type t = it->getType();
    if (t.getArrayDimensions() == 1u && t.getArraySize(0u) * 16u > kSdlUniformWindow) {
      t.setArraySize(0u, kSdlUniformWindow / 16u);
      b.rewriteOp(it->getDef(), ir::Op(*it).setType(t));
      capped_dynamic = true;
    }
  }
  return true;
}

Built build(const Translated& shader, const ShaderProfile& profile, const Linkage* linkage) {
  Built out;
  FpEnvGuard env;
  CapturingLogger logger;
  if (!shader.ok) {
    out.error = "the shader did not translate";
    return out;
  }
  // dxvk_shader_ir.cpp:2079-2082: with EnableSampleRateShading (d3d9.forceSampleRateShading) DXVK
  // interpolates every pixel-shader input per sample, linked or not. Host 2 does not mirror that,
  // so it refuses rather than build a module DXVK would not.
  if (shader.stage == Stage::Pixel && profile.enable_sample_rate_shading) {
    out.error = "unmapped: sample-rate shading (d3d9.forceSampleRateShading, dxvk_shader_ir.cpp:2079-2082) is not mirrored";
    return out;
  }
  ir::Builder b;
  if (!deserialize(shader.ir, b)) {
    out.error = "ir::Deserializer failed";
    return out;
  }
  if (linkage) {
    if (shader.stage != Stage::Pixel || !linkage->vs || linkage->vs->stage != Stage::Vertex) {
      out.error = "a linked build is a pixel shader against a vertex shader";
      return out;
    }
    if (linkage->rt_nonidentity_swizzle_mask) {
      out.error = "unmapped: a render-target swizzle that is not identity";
      return out;
    }
    std::string faultError;
    if (active_fault(faultError) != Fault::LinkageSkipped &&
        !link_pixel_io(b, *linkage->vs, shader, *linkage, out.io_rewritten, out.error))
      return out;
    out.linked = true;
  }
  bool capped = false;
  if (!lower_sdl_abi(b, shader.stage, profile.state_words, out.error, capped))
    return out;
  Fault fault = active_fault(out.error);
  if (!out.error.empty())
    return out;

  std::vector<uint32_t> samplers;
  for (auto it = b.getDeclarations().first; it != b.getDeclarations().second; ++it) {
    if (it->getOpCode() == ir::OpCode::eDclSampler || it->getOpCode() == ir::OpCode::eDclSrv) {
      uint32_t reg = uint32_t(it->getOperand(2u));
      if (std::find(samplers.begin(), samplers.end(), reg) == samplers.end())
        samplers.push_back(reg);
    }
  }
  std::sort(samplers.begin(), samplers.end());
  std::vector<uint32_t> bindingOrder = samplers;
  if (fault == Fault::SamplersSwapped && bindingOrder.size() >= 2u)
    std::swap(bindingOrder[0], bindingOrder[1]);
  ShaderProfile faulted = profile;
  if (fault == Fault::DenormFtz) {
    faulted.independent_denorm_mode = true;
    faulted.supports_rte[1] = true;
    faulted.supports_denorm_flush[1] = true;
  }
  SdlResourceMapping mapping(shader.stage, bindingOrder);
  spirv::SpirvBuilder spirvBuilder(b, mapping, spirv_options(faulted));
  spirvBuilder.buildSpirvBinary();
  if (!mapping.error().empty()) {
    out.error = mapping.error();
    return out;
  }
  if (logger.errors) {
    out.error = "dxbc-spirv logged errors: " + logger.text;
    return out;
  }
  out.sampler_registers = samplers;
  out.num_samplers = uint32_t(samplers.size());
  out.dynamic_capped = capped;
  for (auto it = b.getDeclarations().first; it != b.getDeclarations().second; ++it) {
    if (it->getOpCode() != ir::OpCode::eDclCbv)
      continue;
    uint32_t space = uint32_t(it->getOperand(1u)), index = uint32_t(it->getOperand(2u));
    UniformUse u;
    u.bytes = it->getType().byteSize();
    bool vs = shader.stage == Stage::Vertex;
    if (space == kStateSpace) { u.slot = vs ? kVsState : kPsState; u.kind = "state"; }
    else if (space == kBorderSpace) { u.slot = kPsBorderColours; u.kind = "border_colours"; }
    else if (index == kCbvVsStatic || index == kCbvPsStatic) { u.slot = vs ? kVsStaticConstants : kPsStaticConstants; u.kind = "static"; }
    else if (index == kCbvVsDynamic) { u.slot = kVsDynamicConstants; u.kind = "dynamic"; }
    else if (index == kCbvVsClipPlanes) { u.slot = kVsClipPlanes; u.kind = "clip_planes"; }
    else { u.slot = kPsTextureStages; u.kind = "texture_stages"; }
    if (u.bytes > kSdlUniformWindow) {
      out.error = "unmapped: a " + u.kind + " buffer of " + std::to_string(u.bytes) + " bytes exceeds SDL's 4 KiB uniform window";
      return out;
    }
    out.num_uniform_buffers = std::max(out.num_uniform_buffers, u.slot + 1u);
    out.uniforms.push_back(std::move(u));
  }
  std::sort(out.uniforms.begin(), out.uniforms.end(), [](const UniformUse& a, const UniformUse& b2) { return a.slot < b2.slot; });
  out.spirv = spirvBuilder.getSpirvBinary();
  out.ok = true;
  return out;
}

}  // namespace d9g::shader
