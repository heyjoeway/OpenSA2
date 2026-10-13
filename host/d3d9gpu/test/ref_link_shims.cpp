// ref_link_shims.cpp: what the six linked DXVK translation units (d3d9_util.cpp,
// d3d9_format.cpp, dxvk_constant_state.cpp, dxvk_format.cpp, dxvk_util.cpp, vulkan_names.cpp;
// host/d3d9gpu/build.sh) reference outside themselves on the paths the state check runs
// (PLAN.md section 1.2), as four functions, each of which only prints: DXVK's logger's info,
// warn and err, which the check keeps (a line is printed, never acted on), and D3D9Format's
// stream operator, which d3d9_format.cpp's log lines use. Nothing else is stubbed.
#include "d3d9_format.h"
#include "../util/log/log.h"

#include <cstdio>

namespace dxvk {

void Logger::info(const std::string& message) { std::fprintf(stderr, "dxvk info: %s\n", message.c_str()); }
void Logger::warn(const std::string& message) { std::fprintf(stderr, "dxvk warn: %s\n", message.c_str()); }
void Logger::err(const std::string& message) { std::fprintf(stderr, "dxvk err: %s\n", message.c_str()); }

std::ostream& operator<<(std::ostream& os, D3D9Format format) { return os << uint32_t(format); }

}  // namespace dxvk
