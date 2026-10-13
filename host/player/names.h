// names.h: the recording device's interface and method names by (type, slot),
// for the player's report. The type numbering is runtime/sa2_d3d9.c's
// (T_D3D9 .. T_CONSTANTTABLE); the slot order is each interface's vtable order
// in the Direct3D 9 headers.
#pragma once

#include <cstdint>

namespace names {

enum : uint16_t {
    T_D3D9 = 0, T_DEVICE, T_TEXTURE, T_CUBETEX, T_VOLTEX, T_SURFACE, T_VOLUME,
    T_VB, T_IB, T_DECL, T_VS, T_PS, T_STATEBLOCK, T_SWAPCHAIN, T_QUERY, T_CTAB, T_COUNT,
};

inline const char* type_name(uint32_t t) {
    static const char* const n[T_COUNT] = {
        "IDirect3D9", "Device", "Texture", "CubeTexture", "VolumeTexture", "Surface", "Volume",
        "VertexBuffer", "IndexBuffer", "VertexDeclaration", "VertexShader", "PixelShader",
        "StateBlock", "SwapChain", "Query", "ID3DXConstantTable"};
    return t < T_COUNT ? n[t] : "?";
}

inline const char* method_name(uint32_t t, uint32_t s) {
    static const char* const unk[] = {"QueryInterface", "AddRef", "Release"};
    static const char* const d3d9[] = {
        "QueryInterface", "AddRef", "Release", "RegisterSoftwareDevice", "GetAdapterCount",
        "GetAdapterIdentifier", "GetAdapterModeCount", "EnumAdapterModes", "GetAdapterDisplayMode",
        "CheckDeviceType", "CheckDeviceFormat", "CheckDeviceMultiSampleType", "CheckDepthStencilMatch",
        "CheckDeviceFormatConversion", "GetDeviceCaps", "GetAdapterMonitor", "CreateDevice"};
    static const char* const dev[] = {
        "QueryInterface", "AddRef", "Release", "TestCooperativeLevel", "GetAvailableTextureMem",
        "EvictManagedResources", "GetDirect3D", "GetDeviceCaps", "GetDisplayMode",
        "GetCreationParameters", "SetCursorProperties", "SetCursorPosition", "ShowCursor",
        "CreateAdditionalSwapChain", "GetSwapChain", "GetNumberOfSwapChains", "Reset", "Present",
        "GetBackBuffer", "GetRasterStatus", "SetDialogBoxMode", "SetGammaRamp", "GetGammaRamp",
        "CreateTexture", "CreateVolumeTexture", "CreateCubeTexture", "CreateVertexBuffer",
        "CreateIndexBuffer", "CreateRenderTarget", "CreateDepthStencilSurface", "UpdateSurface",
        "UpdateTexture", "GetRenderTargetData", "GetFrontBufferData", "StretchRect", "ColorFill",
        "CreateOffscreenPlainSurface", "SetRenderTarget", "GetRenderTarget", "SetDepthStencilSurface",
        "GetDepthStencilSurface", "BeginScene", "EndScene", "Clear", "SetTransform", "GetTransform",
        "MultiplyTransform", "SetViewport", "GetViewport", "SetMaterial", "GetMaterial", "SetLight",
        "GetLight", "LightEnable", "GetLightEnable", "SetClipPlane", "GetClipPlane", "SetRenderState",
        "GetRenderState", "CreateStateBlock", "BeginStateBlock", "EndStateBlock", "SetClipStatus",
        "GetClipStatus", "GetTexture", "SetTexture", "GetTextureStageState", "SetTextureStageState",
        "GetSamplerState", "SetSamplerState", "ValidateDevice", "SetPaletteEntries",
        "GetPaletteEntries", "SetCurrentTexturePalette", "GetCurrentTexturePalette", "SetScissorRect",
        "GetScissorRect", "SetSoftwareVertexProcessing", "GetSoftwareVertexProcessing",
        "SetNPatchMode", "GetNPatchMode", "DrawPrimitive", "DrawIndexedPrimitive", "DrawPrimitiveUP",
        "DrawIndexedPrimitiveUP", "ProcessVertices", "CreateVertexDeclaration",
        "SetVertexDeclaration", "GetVertexDeclaration", "SetFVF", "GetFVF", "CreateVertexShader",
        "SetVertexShader", "GetVertexShader", "SetVertexShaderConstantF", "GetVertexShaderConstantF",
        "SetVertexShaderConstantI", "GetVertexShaderConstantI", "SetVertexShaderConstantB",
        "GetVertexShaderConstantB", "SetStreamSource", "GetStreamSource", "SetStreamSourceFreq",
        "GetStreamSourceFreq", "SetIndices", "GetIndices", "CreatePixelShader", "SetPixelShader",
        "GetPixelShader", "SetPixelShaderConstantF", "GetPixelShaderConstantF",
        "SetPixelShaderConstantI", "GetPixelShaderConstantI", "SetPixelShaderConstantB",
        "GetPixelShaderConstantB", "DrawRectPatch", "DrawTriPatch", "DeletePatch", "CreateQuery"};
    static const char* const res[] = {
        "QueryInterface", "AddRef", "Release", "GetDevice", "SetPrivateData", "GetPrivateData",
        "FreePrivateData", "SetPriority", "GetPriority", "PreLoad", "GetType"};
    static const char* const basetex[] = {"SetLOD", "GetLOD", "GetLevelCount", "SetAutoGenFilterType",
                                          "GetAutoGenFilterType", "GenerateMipSubLevels"};
    static const char* const tex2d[] = {"GetLevelDesc", "GetSurfaceLevel", "LockRect", "UnlockRect",
                                        "AddDirtyRect"};
    static const char* const cube[] = {"GetLevelDesc", "GetCubeMapSurface", "LockRect", "UnlockRect",
                                       "AddDirtyRect"};
    static const char* const vol[] = {"GetLevelDesc", "GetVolumeLevel", "LockBox", "UnlockBox",
                                      "AddDirtyBox"};
    static const char* const surf[] = {"GetContainer", "GetDesc", "LockRect", "UnlockRect", "GetDC",
                                       "ReleaseDC"};
    static const char* const buf[] = {"Lock", "Unlock", "GetDesc"};
    static const char* const volume[] = {"QueryInterface", "AddRef", "Release", "GetDevice",
                                         "SetPrivateData", "GetPrivateData", "FreePrivateData",
                                         "GetContainer", "GetDesc", "LockBox", "UnlockBox"};
    static const char* const decl[] = {"QueryInterface", "AddRef", "Release", "GetDevice", "GetDeclaration"};
    static const char* const shader[] = {"QueryInterface", "AddRef", "Release", "GetDevice", "GetFunction"};
    static const char* const sb[] = {"QueryInterface", "AddRef", "Release", "GetDevice", "Capture", "Apply"};
    static const char* const sc[] = {"QueryInterface", "AddRef", "Release", "Present", "GetFrontBufferData",
                                     "GetBackBuffer", "GetRasterStatus", "GetDisplayMode", "GetDevice",
                                     "GetPresentParameters"};
    static const char* const query[] = {"QueryInterface", "AddRef", "Release", "GetDevice", "GetType",
                                        "GetDataSize", "Issue", "GetData"};
#define PICK(arr) (s < sizeof(arr) / sizeof(arr[0]) ? arr[s] : "?")
    switch (t) {
    case T_D3D9: return PICK(d3d9);
    case T_DEVICE: return PICK(dev);
    case T_TEXTURE: case T_CUBETEX: case T_VOLTEX:
        if (s <= 10) return res[s];
        if (s <= 16) return basetex[s - 11];
        if (s <= 21) return t == T_TEXTURE ? tex2d[s - 17] : t == T_CUBETEX ? cube[s - 17] : vol[s - 17];
        return "?";
    case T_SURFACE: return s <= 10 ? res[s] : s <= 16 ? surf[s - 11] : "?";
    case T_VB: case T_IB: return s <= 10 ? res[s] : s <= 13 ? buf[s - 11] : "?";
    case T_VOLUME: return PICK(volume);
    case T_DECL: return PICK(decl);
    case T_VS: case T_PS: return PICK(shader);
    case T_STATEBLOCK: return PICK(sb);
    case T_SWAPCHAIN: return PICK(sc);
    case T_QUERY: return PICK(query);
    case T_CTAB: return s < 3 ? unk[s] : "ConstantTableMethod";
    default: return "?";
    }
#undef PICK
}

}  // namespace names
