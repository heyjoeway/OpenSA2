// d9g_ni.h: what the generated COM stubs (gen_com.py's d9g_com_stubs.h) call for a
// method the device does not implement: counted by interface index (gen_com.py's
// INTERFACES order) and slot in the device's report, and answered with the stub's
// default (D3DERR_INVALIDCALL for an HRESULT).
#pragma once

namespace d9g {
void not_implemented(int iface, unsigned slot);
}
