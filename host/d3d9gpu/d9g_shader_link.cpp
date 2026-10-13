// Host 2's per-pair linkage (R1 U4): the pixel-shader half of DxvkIrShader::getCode(bindings,
// &linkage) (src/dxvk/dxvk_shader_ir.cpp:2033-2075), with the linkage DxvkGraphicsPipeline builds
// (dxvk_graphics.cpp:880-915). The compatibility test is DXVK's own code
// (DxvkShaderIo::checkStageCompatibility, src/dxvk/dxvk_shader_io.cpp, compiled unmodified); the
// two I/O conversions it needs are ported below, each from the cited lines, because DXVK keeps
// them inside classes that bring its whole device with them.
#include "d9g_shader.h"
#include "d9g_shader_internal.h"

#include "../dxvk/dxvk_shader_io.h"

#include <optional>

namespace d9g::shader {

using namespace dxbc_spv;

namespace {

// DxvkIrLowerBindingModelPass::convertBuiltIn (dxvk_shader_ir.cpp:1835-1905).
std::optional<spv::BuiltIn> to_spv_builtin(ir::BuiltIn builtIn, ir::ShaderStage stage) {
  switch (builtIn) {
    case ir::BuiltIn::ePosition: return stage == ir::ShaderStage::ePixel ? spv::BuiltInFragCoord : spv::BuiltInPosition;
    case ir::BuiltIn::eClipDistance: return spv::BuiltInClipDistance;
    case ir::BuiltIn::eCullDistance: return spv::BuiltInCullDistance;
    case ir::BuiltIn::eVertexId: return spv::BuiltInVertexIndex;
    case ir::BuiltIn::eInstanceId: return spv::BuiltInInstanceIndex;
    case ir::BuiltIn::ePrimitiveId: return spv::BuiltInPrimitiveId;
    case ir::BuiltIn::eLayerIndex: return spv::BuiltInLayer;
    case ir::BuiltIn::eViewportIndex: return spv::BuiltInViewportIndex;
    case ir::BuiltIn::eGsVertexCountIn: return std::nullopt;
    case ir::BuiltIn::eGsInstanceId: return spv::BuiltInInvocationId;
    case ir::BuiltIn::eTessControlPointCountIn: return spv::BuiltInPatchVertices;
    case ir::BuiltIn::eTessControlPointId: return spv::BuiltInInvocationId;
    case ir::BuiltIn::eTessCoord: return spv::BuiltInTessCoord;
    case ir::BuiltIn::eTessFactorInner: return spv::BuiltInTessLevelInner;
    case ir::BuiltIn::eTessFactorOuter: return spv::BuiltInTessLevelOuter;
    case ir::BuiltIn::eSampleCount: return std::nullopt;
    case ir::BuiltIn::eSampleId: return spv::BuiltInSampleId;
    case ir::BuiltIn::eSamplePosition: return spv::BuiltInSamplePosition;
    case ir::BuiltIn::eSampleMask: return spv::BuiltInSampleMask;
    case ir::BuiltIn::eIsFrontFace: return spv::BuiltInFrontFacing;
    case ir::BuiltIn::eDepth: return spv::BuiltInFragDepth;
    case ir::BuiltIn::eStencilRef: return spv::BuiltInFragStencilRefEXT;
    case ir::BuiltIn::eIsFullyCovered: return spv::BuiltInFullyCoveredEXT;
    case ir::BuiltIn::eWorkgroupId: return spv::BuiltInWorkgroupId;
    case ir::BuiltIn::eGlobalThreadId: return spv::BuiltInGlobalInvocationId;
    case ir::BuiltIn::eLocalThreadId: return spv::BuiltInLocalInvocationId;
    case ir::BuiltIn::eLocalThreadIndex: return spv::BuiltInLocalInvocationIndex;
    case ir::BuiltIn::ePointSize: return spv::BuiltInPointSize;
    case ir::BuiltIn::eTessFactorLimit: return std::nullopt;
    case ir::BuiltIn::ePointCoord: return spv::BuiltInPointCoord;
    default: return std::nullopt;  // the legacy D3D9 inputs (:1895-1904)
  }
}

// DxvkIrShader::convertBuiltIn (dxvk_shader_ir.cpp:2412-2470).
std::optional<ir::BuiltIn> to_ir_builtin(spv::BuiltIn builtIn, VkShaderStageFlagBits stage) {
  switch (builtIn) {
    case spv::BuiltInFragCoord:
    case spv::BuiltInPosition: return ir::BuiltIn::ePosition;
    case spv::BuiltInClipDistance: return ir::BuiltIn::eClipDistance;
    case spv::BuiltInCullDistance: return ir::BuiltIn::eCullDistance;
    case spv::BuiltInVertexId:
    case spv::BuiltInVertexIndex: return ir::BuiltIn::eVertexId;
    case spv::BuiltInInstanceId:
    case spv::BuiltInInstanceIndex: return ir::BuiltIn::eInstanceId;
    case spv::BuiltInPrimitiveId: return ir::BuiltIn::ePrimitiveId;
    case spv::BuiltInLayer: return ir::BuiltIn::eLayerIndex;
    case spv::BuiltInViewportIndex: return ir::BuiltIn::eViewportIndex;
    case spv::BuiltInInvocationId:
      if (stage == VK_SHADER_STAGE_GEOMETRY_BIT) return ir::BuiltIn::eGsInstanceId;
      if (stage == VK_SHADER_STAGE_TESSELLATION_CONTROL_BIT) return ir::BuiltIn::eTessControlPointId;
      return std::nullopt;
    case spv::BuiltInPatchVertices: return ir::BuiltIn::eTessControlPointCountIn;
    case spv::BuiltInTessCoord: return ir::BuiltIn::eTessCoord;
    case spv::BuiltInTessLevelInner: return ir::BuiltIn::eTessFactorInner;
    case spv::BuiltInTessLevelOuter: return ir::BuiltIn::eTessFactorOuter;
    case spv::BuiltInSampleId: return ir::BuiltIn::eSampleId;
    case spv::BuiltInSamplePosition: return ir::BuiltIn::eSamplePosition;
    case spv::BuiltInSampleMask: return ir::BuiltIn::eSampleMask;
    case spv::BuiltInFrontFacing: return ir::BuiltIn::eIsFrontFace;
    case spv::BuiltInFragDepth: return ir::BuiltIn::eDepth;
    case spv::BuiltInFragStencilRefEXT: return ir::BuiltIn::eStencilRef;
    case spv::BuiltInFullyCoveredEXT: return ir::BuiltIn::eIsFullyCovered;
    case spv::BuiltInWorkgroupId: return ir::BuiltIn::eWorkgroupId;
    case spv::BuiltInGlobalInvocationId: return ir::BuiltIn::eGlobalThreadId;
    case spv::BuiltInLocalInvocationId: return ir::BuiltIn::eLocalThreadId;
    case spv::BuiltInLocalInvocationIndex: return ir::BuiltIn::eLocalThreadIndex;
    case spv::BuiltInPointSize: return ir::BuiltIn::ePointSize;
    case spv::BuiltInPointCoord: return ir::BuiltIn::ePointCoord;
    default: return std::nullopt;
  }
}

// DxvkIrLowerBindingModelPass::convertIoMap (dxvk_shader_ir.cpp:1796-1830); D3D9 always sets
// SemanticIo (d3d9_device.cpp:7876), so semantics are always carried.
dxvk::DxvkShaderIo to_dxvk_io(const ir::IoMap& io, ir::ShaderStage stage) {
  dxvk::DxvkShaderIo map;
  for (const auto& e : io) {
    dxvk::DxvkShaderIoVar var = {};
    if (e.getType() == ir::IoEntryType::eBuiltIn) {
      auto builtIn = to_spv_builtin(e.getBuiltIn(), stage);
      if (!builtIn)
        continue;
      var.builtIn = *builtIn;
      var.location = 0u;
      var.componentIndex = 0u;
      var.componentCount = e.computeComponentCount();
      var.isPatchConstant = *builtIn == spv::BuiltInTessLevelInner || *builtIn == spv::BuiltInTessLevelOuter;
    } else {
      var.builtIn = spv::BuiltInMax;
      var.location = e.getLocationIndex();
      var.componentIndex = e.getFirstComponentIndex();
      var.componentCount = e.computeComponentCount();
      var.isPatchConstant = e.getType() == ir::IoEntryType::ePerPatch;
      auto semantic = io.getSemanticForEntry(e);
      var.semanticName = semantic.name;
      var.semanticIndex = semantic.index;
    }
    map.add(std::move(var));
  }
  return map;
}

// DxvkIrShader::convertIoMap (dxvk_shader_ir.cpp:2379-2409).
ir::IoMap to_ir_io(const dxvk::DxvkShaderIo& io, VkShaderStageFlagBits stage) {
  ir::IoMap map = {};
  for (uint32_t i = 0u; i < io.getVarCount(); i++) {
    const auto& var = io.getVar(i);
    if (var.builtIn != spv::BuiltInMax) {
      auto builtIn = to_ir_builtin(var.builtIn, stage);
      if (builtIn)
        map.add(ir::IoLocation(*builtIn, uint8_t((1u << var.componentCount) - 1u)), ir::IoSemantic());
    } else {
      auto type = var.isPatchConstant ? ir::IoEntryType::ePerPatch : ir::IoEntryType::ePerVertex;
      ir::IoSemantic semantic = {};
      semantic.name = var.semanticName;
      semantic.index = var.semanticIndex;
      map.add(ir::IoLocation(type, var.location, ((1u << var.componentCount) - 1u) << var.componentIndex),
              std::move(semantic));
    }
  }
  return map;
}

bool io_of(const Translated& t, bool outputs, dxvk::DxvkShaderIo& out) {
  ir::Builder b;
  if (!deserialize(t.ir, b))
    return false;
  auto stage = t.stage == Stage::Vertex ? ir::ShaderStage::eVertex : ir::ShaderStage::ePixel;
  out = to_dxvk_io(outputs ? ir::IoMap::forOutputs(b, 0u) : ir::IoMap::forInputs(b), stage);
  return true;
}

}  // namespace

bool stages_compatible(const Translated& vs, const Translated& ps, bool match_semantics) {
  dxvk::DxvkShaderIo vsOut, psIn;
  if (!io_of(vs, true, vsOut) || !io_of(ps, false, psIn))
    return false;
  return dxvk::DxvkShaderIo::checkStageCompatibility(VK_SHADER_STAGE_FRAGMENT_BIT, psIn,
                                                     VK_SHADER_STAGE_VERTEX_BIT, vsOut, match_semantics);
}

bool link_pixel_io(ir::Builder& ps, const Translated& vs, const Translated& psInfo, const Linkage& linkage,
                   bool& io_rewritten, std::string& error) {
  dxvk::DxvkShaderIo vsOut;
  if (!io_of(vs, true, vsOut)) {
    error = "the vertex shader's IR does not deserialize";
    return false;
  }
  dxvk::DxvkShaderIo psIn = to_dxvk_io(ir::IoMap::forInputs(ps), ir::ShaderStage::ePixel);
  ir::LowerIoPass ioPass(ps);
  // :2037-2038, with linkage->fsFlatShading as dxvk_graphics.cpp:891 sets it
  if (linkage.flat_shade_mode && psInfo.reflection.flat_shading_inputs)
    ioPass.enableFlatInterpolation(psInfo.reflection.flat_shading_inputs);
  // :2049-2057: identity render-target swizzles only (build() refuses any other); swizzleOutputs
  // with all-identity swizzles returns without a change (ir_pass_lower_io.cpp:693-706).
  // :2059-2069: semanticIo is the vertex shader's flag and the pixel shader's (both D3D9: set).
  bool matchSemantics = true;
  if (!dxvk::DxvkShaderIo::checkStageCompatibility(VK_SHADER_STAGE_FRAGMENT_BIT, psIn,
                                                   VK_SHADER_STAGE_VERTEX_BIT, vsOut, matchSemantics)) {
    auto prevStageIoMap = to_ir_io(vsOut, VK_SHADER_STAGE_VERTEX_BIT);
    if (matchSemantics)
      ioPass.resolveSemanticIo(prevStageIoMap);
    ioPass.resolveMismatchedIo(ir::ShaderStage::eVertex, prevStageIoMap);
    io_rewritten = true;
  }
  // :2079-2082: EnableSampleRateShading is off (d3d9.forceSampleRateShading is not set).
  return true;
}

}  // namespace d9g::shader
