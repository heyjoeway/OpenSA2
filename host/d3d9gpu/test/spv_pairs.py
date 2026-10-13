#!/usr/bin/env python3
"""Hand-built SPIR-V pairs, one DXVK-shaped and one host-2-shaped, for spv_canon.py's classes.

Subtitle: the gates' check of the comparer's DXVK side (R1 plan U13; review SHOULD-FIX 8 of
2026-10-04). The real DXVK modules are derived from the game's shaders and may never be tracked,
so the pytest builds small modules in each side's shape from the assembly below (the pinned
spirv-as): a pixel shader that reads a spec dword, two shared push members and the sampler of
register 0, and a vertex shader that reads the dynamically indexed float buffer. Their pairing
exercises spec_data, push_data, sampler_descriptor, set_binding, heap_capabilities,
state_layout and dynamic_cf_length on DXVK's side as well as host 2's (the eighth class,
bgra_input_swizzle, is host 2's against host 2: the pytest's BGRA variant); each named fault turns
one rule wrong on one side and must fail the comparison: the class rules, and the rules that hold
what no class explains (the execution modes, a RelaxedPrecision anywhere, a texture's set).

  fragment(side, fault=None) -> assembly text     side "dxvk" or "host2"
  vertex(side, fault=None)   -> assembly text
  FRAGMENT_FAULTS, VERTEX_FAULTS: the faults, each with the side it changes
"""

FRAGMENT_FAULTS = {
    "push_member_renamed": "host2",     # host 2 reads fogDensity where DXVK reads fogDistanceScale
    "spec_dword_moved": "host2",        # host 2 reads spec dword 1 where DXVK reads dword 0
    "sampler_register_moved": "host2",  # host 2 samples register 1 where DXVK samples register 0
    "extra_capability_on_host2": "host2",  # a capability (Float16) only host 2 declares, outside the class
    "extra_capability_on_dxvk": "dxvk",    # a capability (ImageQuery) only DXVK declares, outside the class
    "state_offset_moved": "host2",      # host 2's alphaRef at Offset 30, not 31
    "push_offset_moved": "dxvk",        # DXVK's alphaRef at Offset 2, not 3
    "heap_index_not_push": "dxvk",      # DXVK indexes its sampler heap with a constant
    "spec_offset_moved": "dxvk",        # DXVK's spec dword 0 at Offset 4
    "execution_mode_dropped": "host2",  # host 2's module without SignedZeroInfNanPreserve
    "relaxed_precision_on_value": "host2",         # a RelaxedPrecision sum
    "relaxed_precision_on_state_member": "host2",  # RelaxedPrecision on the state buffer's alphaRef
    "relaxed_precision_on_image": "host2",         # RelaxedPrecision on the image variable
    "texture_in_vertex_set": "host2",   # host 2's fragment sampler and image in set 0, SDL's vertex textures
    "image_in_sampler_set": "dxvk",     # DXVK's image in set 0, its samplers' set
}
VERTEX_FAULTS = {
    "dynamic_length_255": "host2",      # host 2's dynamic float buffer at 255, not 256
    "dynamic_length_258": "dxvk",       # DXVK's at 258, not its count + 1
}

_FRAGMENT_COMMON_TYPES = """
       %void = OpTypeVoid
         %fn = OpTypeFunction %void
      %float = OpTypeFloat 32
       %uint = OpTypeInt 32 0
     %ushort = OpTypeInt 16 0
      %uchar = OpTypeInt 8 0
    %v3uchar = OpTypeVector %uchar 3
    %v2float = OpTypeVector %float 2
    %v4float = OpTypeVector %float 4
     %uint_0 = OpConstant %uint 0
     %uint_1 = OpConstant %uint 1
     %uint_2 = OpConstant %uint 2
     %uint_3 = OpConstant %uint 3
     %uint_8 = OpConstant %uint 8
     %uint_9 = OpConstant %uint 9
    %uint_11 = OpConstant %uint 11
   %float_h = OpConstant %float 0.5
      %coord = OpConstantComposite %v2float %float_h %float_h
    %sampler = OpTypeSampler
      %image = OpTypeImage %float 2D 0 0 0 1 Unknown
    %sampled = OpTypeSampledImage %image
    %ptr_img = OpTypePointer UniformConstant %image
   %ptr_samp = OpTypePointer UniformConstant %sampler
    %ptr_out = OpTypePointer Output %v4float
        %oC0 = OpVariable %ptr_out Output
"""


def fragment(side, fault=None):
    assert side in ("dxvk", "host2") and (fault is None or FRAGMENT_FAULTS[fault] == side)
    if side == "dxvk":
        alpha_off = 2 if fault == "push_offset_moved" else 3
        spec0_off = 4 if fault == "spec_offset_moved" else 0
        extra_cap = "               OpCapability ImageQuery\n" if fault == "extra_capability_on_dxvk" else ""
        heap_index = ("        %sidx = OpUConvert %uint %uint_0" if fault == "heap_index_not_push" else
                      "       %psidx = OpInBoundsAccessChain %ptr_push_ushort %push_data %uint_3\n"
                      "       %sidx16 = OpLoad %ushort %psidx\n"
                      "        %sidx = OpUConvert %uint %sidx16")
        img_set = 0 if fault == "image_in_sampler_set" else 2
        return f"""               OpCapability Shader
{extra_cap}               OpCapability RuntimeDescriptorArray
               OpCapability Int16
               OpCapability Int8
               OpCapability SignedZeroInfNanPreserve
               OpMemoryModel Logical GLSL450
               OpEntryPoint Fragment %main "main" %oC0 %push_data %specData %heap %img
               OpExecutionMode %main OriginUpperLeft
               OpExecutionMode %main SignedZeroInfNanPreserve 32
               OpName %main "main"
               OpName %push_data "push_data"
               OpMemberName %push_t 0 "fogColor"
               OpMemberName %push_t 1 "alphaRef"
               OpMemberName %push_t 2 "fogDistanceScale"
               OpMemberName %push_t 3 "s0_g_Tex"
               OpMemberName %push_t 4 "sPad"
               OpName %specData "commonState"
               OpName %heap "samplers"
               OpName %img "s0_g_Tex_2d"
               OpName %oC0 "oC0_color"
               OpDecorate %push_t Block
               OpMemberDecorate %push_t 0 Offset 0
               OpMemberDecorate %push_t 1 Offset {alpha_off}
               OpMemberDecorate %push_t 2 Offset 4
               OpMemberDecorate %push_t 3 Offset 32
               OpMemberDecorate %push_t 4 Offset 34
               OpDecorate %spec_t Block
               OpMemberDecorate %spec_t 0 Offset {spec0_off}
               OpDecorate %specData DescriptorSet 7
               OpDecorate %specData Binding 0
               OpDecorate %heap DescriptorSet 0
               OpDecorate %heap Binding 0
               OpDecorate %img DescriptorSet {img_set}
               OpDecorate %img Binding 0
               OpDecorate %oC0 Location 0
{_FRAGMENT_COMMON_TYPES}
     %push_t = OpTypeStruct %v3uchar %uchar %float %ushort %ushort
   %ptr_push = OpTypePointer PushConstant %push_t
  %push_data = OpVariable %ptr_push PushConstant
%ptr_push_uchar = OpTypePointer PushConstant %uchar
%ptr_push_float = OpTypePointer PushConstant %float
%ptr_push_ushort = OpTypePointer PushConstant %ushort
     %spec_t = OpTypeStruct %uint
   %ptr_spec = OpTypePointer Uniform %spec_t
  %ptr_specu = OpTypePointer Uniform %uint
   %specData = OpVariable %ptr_spec Uniform
     %heap_t = OpTypeRuntimeArray %sampler
   %ptr_heap = OpTypePointer UniformConstant %heap_t
       %heap = OpVariable %ptr_heap UniformConstant
        %img = OpVariable %ptr_img UniformConstant
       %main = OpFunction %void None %fn
      %entry = OpLabel
     %palpha = OpInBoundsAccessChain %ptr_push_uchar %push_data %uint_1
      %alpha = OpLoad %uchar %palpha
     %pscale = OpInBoundsAccessChain %ptr_push_float %push_data %uint_2
      %scale = OpLoad %float %pscale
     %pspec0 = OpAccessChain %ptr_specu %specData %uint_0
      %spec0 = OpLoad %uint %pspec0
{heap_index}
      %psamp = OpAccessChain %ptr_samp %heap %sidx
       %samp = OpLoad %sampler %psamp
        %imv = OpLoad %image %img
         %si = OpSampledImage %sampled %imv %samp
          %c = OpImageSampleImplicitLod %v4float %si %coord
     %alphaf = OpConvertUToF %float %alpha
      %specf = OpConvertUToF %float %spec0
       %sum1 = OpFAdd %float %alphaf %scale
       %sum2 = OpFAdd %float %sum1 %specf
        %out = OpVectorTimesScalar %v4float %c %sum2
               OpStore %oC0 %out
               OpReturn
               OpFunctionEnd
"""
    reg = 1 if fault == "sampler_register_moved" else 0
    alpha_off = 30 if fault == "state_offset_moved" else 31
    scale_member = "%uint_11" if fault == "push_member_renamed" else "%uint_9"
    spec_member = "%uint_1" if fault == "spec_dword_moved" else "%uint_0"
    heap_cap = "               OpCapability Float16\n" if fault == "extra_capability_on_host2" else ""
    float_controls = "" if fault == "execution_mode_dropped" else "               OpExecutionMode %main SignedZeroInfNanPreserve 32\n"
    tex_set = 0 if fault == "texture_in_vertex_set" else 2
    relaxed = {"relaxed_precision_on_value": "               OpDecorate %sum1 RelaxedPrecision\n",
               "relaxed_precision_on_state_member": "               OpMemberDecorate %state_t 8 RelaxedPrecision\n",
               "relaxed_precision_on_image": "               OpDecorate %img RelaxedPrecision\n"}.get(fault, "")
    return f"""               OpCapability Shader
{heap_cap}               OpCapability Int16
               OpCapability Int8
               OpCapability SignedZeroInfNanPreserve
               OpMemoryModel Logical GLSL450
               OpEntryPoint Fragment %main "main" %oC0 %specData %samp_var %img
               OpExecutionMode %main OriginUpperLeft
{float_controls}               OpName %main "main"
               OpName %specData "specData"
               OpMemberName %state_t 0 "commonState"
               OpMemberName %state_t 7 "fogColor"
               OpMemberName %state_t 8 "alphaRef"
               OpMemberName %state_t 9 "fogDistanceScale"
               OpMemberName %state_t 10 "fogDistanceEnd"
               OpMemberName %state_t 11 "fogDensity"
               OpName %samp_var "s{reg}"
               OpName %img "s{reg}_2d"
               OpName %oC0 "oC0_color"
               OpDecorate %state_t Block
               OpMemberDecorate %state_t 0 Offset 0
               OpMemberDecorate %state_t 1 Offset 4
               OpMemberDecorate %state_t 2 Offset 8
               OpMemberDecorate %state_t 3 Offset 12
               OpMemberDecorate %state_t 4 Offset 16
               OpMemberDecorate %state_t 5 Offset 20
               OpMemberDecorate %state_t 6 Offset 24
               OpMemberDecorate %state_t 7 Offset 28
               OpMemberDecorate %state_t 8 Offset {alpha_off}
               OpMemberDecorate %state_t 9 Offset 32
               OpMemberDecorate %state_t 10 Offset 36
               OpMemberDecorate %state_t 11 Offset 40
               OpDecorate %specData DescriptorSet 3
               OpDecorate %specData Binding 1
               OpDecorate %samp_var DescriptorSet {tex_set}
               OpDecorate %samp_var Binding 0
               OpDecorate %img DescriptorSet {tex_set}
               OpDecorate %img Binding 0
               OpDecorate %oC0 Location 0
{relaxed}
{_FRAGMENT_COMMON_TYPES}
    %state_t = OpTypeStruct %uint %uint %uint %uint %uint %uint %uint %v3uchar %uchar %float %float %float
  %ptr_state = OpTypePointer Uniform %state_t
 %ptr_su8 = OpTypePointer Uniform %uchar
 %ptr_sf32 = OpTypePointer Uniform %float
 %ptr_su32 = OpTypePointer Uniform %uint
   %specData = OpVariable %ptr_state Uniform
   %samp_var = OpVariable %ptr_samp UniformConstant
        %img = OpVariable %ptr_img UniformConstant
       %main = OpFunction %void None %fn
      %entry = OpLabel
     %palpha = OpAccessChain %ptr_su8 %specData %uint_8
      %alpha = OpLoad %uchar %palpha
     %pscale = OpAccessChain %ptr_sf32 %specData {scale_member}
      %scale = OpLoad %float %pscale
     %pspec0 = OpAccessChain %ptr_su32 %specData {spec_member}
      %spec0 = OpLoad %uint %pspec0
       %samp = OpLoad %sampler %samp_var
        %imv = OpLoad %image %img
         %si = OpSampledImage %sampled %imv %samp
          %c = OpImageSampleImplicitLod %v4float %si %coord
     %alphaf = OpConvertUToF %float %alpha
      %specf = OpConvertUToF %float %spec0
       %sum1 = OpFAdd %float %alphaf %scale
       %sum2 = OpFAdd %float %sum1 %specf
        %out = OpVectorTimesScalar %v4float %c %sum2
               OpStore %oC0 %out
               OpReturn
               OpFunctionEnd
"""


def vertex(side, fault=None):
    assert side in ("dxvk", "host2") and (fault is None or VERTEX_FAULTS[fault] == side)
    if side == "dxvk":
        length, binding = (258 if fault == "dynamic_length_258" else 257), 4
    else:
        length, binding = (255 if fault == "dynamic_length_255" else 256), 1
    return f"""               OpCapability Shader
               OpMemoryModel Logical GLSL450
               OpEntryPoint Vertex %main "main" %pos %cF
               OpName %main "main"
               OpName %cF "cF"
               OpDecorate %pos BuiltIn Position
               OpDecorate %arr ArrayStride 16
               OpDecorate %cf_t Block
               OpMemberDecorate %cf_t 0 Offset 0
               OpDecorate %cF DescriptorSet 1
               OpDecorate %cF Binding {binding}
       %void = OpTypeVoid
         %fn = OpTypeFunction %void
      %float = OpTypeFloat 32
       %uint = OpTypeInt 32 0
    %v4float = OpTypeVector %float 4
     %uint_0 = OpConstant %uint 0
     %uint_2 = OpConstant %uint 2
     %length = OpConstant %uint {length}
        %arr = OpTypeArray %v4float %length
       %cf_t = OpTypeStruct %arr
     %ptr_cf = OpTypePointer Uniform %cf_t
     %ptr_v4 = OpTypePointer Uniform %v4float
         %cF = OpVariable %ptr_cf Uniform
    %ptr_out = OpTypePointer Output %v4float
        %pos = OpVariable %ptr_out Output
       %main = OpFunction %void None %fn
      %entry = OpLabel
         %pc = OpAccessChain %ptr_v4 %cF %uint_0 %uint_2
          %v = OpLoad %v4float %pc
               OpStore %pos %v
               OpReturn
               OpFunctionEnd
"""


# ---- the linked half (spv_canon.py --linked, --linked-batch): DXVK's modules as the capture layer
# records them. A captured module's descriptors sit where its pipeline's layout puts them (other
# sets and bindings than the dump's), its push members at the layout's offsets (the shared group at
# 16, the sampler indices at 208, as DXVK 3.1.1's layouts put them on this machine), and in the
# linked variant its spec dwords stay OpSpecConstant (the driver specialises them). Each fault breaks
# one of the rules --linked holds.
LINKED_FAULTS = {
    "linked_spec_id_moved": "dxvk_linked",       # DXVK's spec constant SpecId 1 where host 2 reads dword 0
    "linked_push_member_moved": "dxvk_linked",   # DXVK's alphaRef at the group's base + 2, not + 3
}
FS_DEBUG = "fs.0123456789abcdef0123456789abcdef"
VS_DEBUG = "vs.fedcba9876543210fedcba9876543210"


def fragment_linked(fault=None, debug=FS_DEBUG):
    assert fault is None or fault in LINKED_FAULTS
    alpha_off = 18 if fault == "linked_push_member_moved" else 19
    spec_id = 1 if fault == "linked_spec_id_moved" else 0
    return f"""               OpCapability Shader
               OpCapability RuntimeDescriptorArray
               OpCapability Int16
               OpCapability Int8
               OpCapability SignedZeroInfNanPreserve
               OpMemoryModel Logical GLSL450
               OpEntryPoint Fragment %main "main" %oC0 %push_data %heap %img
               OpExecutionMode %main OriginUpperLeft
               OpExecutionMode %main SignedZeroInfNanPreserve 32
        %dbg = OpString "{debug}"
               OpSource Unknown 0 %dbg
               OpName %main "main"
               OpName %push_data "push_data"
               OpMemberName %push_t 0 "fogColor"
               OpMemberName %push_t 1 "alphaRef"
               OpMemberName %push_t 2 "fogDistanceScale"
               OpMemberName %push_t 3 "s0_g_Tex"
               OpMemberName %push_t 4 "sPad"
               OpName %commonState "commonState"
               OpName %heap "samplers"
               OpName %img "s0_g_Tex_2d"
               OpName %oC0 "oC0_color"
               OpDecorate %push_t Block
               OpMemberDecorate %push_t 0 Offset 16
               OpMemberDecorate %push_t 1 Offset {alpha_off}
               OpMemberDecorate %push_t 2 Offset 20
               OpMemberDecorate %push_t 3 Offset 208
               OpMemberDecorate %push_t 4 Offset 210
               OpDecorate %commonState SpecId {spec_id}
               OpDecorate %heap DescriptorSet 0
               OpDecorate %heap Binding 0
               OpDecorate %img DescriptorSet 1
               OpDecorate %img Binding 0
               OpDecorate %oC0 Location 0
{_FRAGMENT_COMMON_TYPES}
     %push_t = OpTypeStruct %v3uchar %uchar %float %ushort %ushort
   %ptr_push = OpTypePointer PushConstant %push_t
  %push_data = OpVariable %ptr_push PushConstant
%ptr_push_uchar = OpTypePointer PushConstant %uchar
%ptr_push_float = OpTypePointer PushConstant %float
%ptr_push_ushort = OpTypePointer PushConstant %ushort
%commonState = OpSpecConstant %uint 0
     %heap_t = OpTypeRuntimeArray %sampler
   %ptr_heap = OpTypePointer UniformConstant %heap_t
       %heap = OpVariable %ptr_heap UniformConstant
        %img = OpVariable %ptr_img UniformConstant
       %main = OpFunction %void None %fn
      %entry = OpLabel
     %palpha = OpInBoundsAccessChain %ptr_push_uchar %push_data %uint_1
      %alpha = OpLoad %uchar %palpha
     %pscale = OpInBoundsAccessChain %ptr_push_float %push_data %uint_2
      %scale = OpLoad %float %pscale
       %psidx = OpInBoundsAccessChain %ptr_push_ushort %push_data %uint_3
       %sidx16 = OpLoad %ushort %psidx
        %sidx = OpUConvert %uint %sidx16
      %psamp = OpAccessChain %ptr_samp %heap %sidx
       %samp = OpLoad %sampler %psamp
        %imv = OpLoad %image %img
         %si = OpSampledImage %sampled %imv %samp
          %c = OpImageSampleImplicitLod %v4float %si %coord
     %alphaf = OpConvertUToF %float %alpha
      %specf = OpConvertUToF %float %commonState
       %sum1 = OpFAdd %float %alphaf %scale
       %sum2 = OpFAdd %float %sum1 %specf
        %out = OpVectorTimesScalar %v4float %c %sum2
               OpStore %oC0 %out
               OpReturn
               OpFunctionEnd
"""


def vertex_linked(debug=VS_DEBUG, side="dxvk"):
    """vertex(side) with a debug name, and, for DXVK's captured module, its dynamic float buffer at
    another set and binding than the dump's (set 15, binding 2)."""
    text = vertex("dxvk" if side in ("dxvk", "dxvk_captured") else side)
    text = text.replace("               OpName %main \"main\"\n",
                        f"        %dbg = OpString \"{debug}\"\n               OpSource Unknown 0 %dbg\n"
                        "               OpName %main \"main\"\n", 1)
    if side == "dxvk_captured":
        text = text.replace("OpDecorate %cF DescriptorSet 1", "OpDecorate %cF DescriptorSet 15")
        text = text.replace("OpDecorate %cF Binding 4", "OpDecorate %cF Binding 2")
    return text


def with_debug(text, debug):
    """A module's text with dxbc-spirv's debug name (OpString, OpSource) after its execution modes,
    as DXVK's modules carry it: the name a captured module is paired by."""
    at = text.rindex("               OpExecutionMode")
    end = text.index("\n", at) + 1
    return text[:end] + f"        %dbg = OpString \"{debug}\"\n               OpSource Unknown 0 %dbg\n" + text[end:]
