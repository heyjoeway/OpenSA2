// The link shims host 2's translator needs beside DXVK's units (R1 U2): the only definitions of
// DXVK names that are not DXVK's own code. The build lists every unresolved DXVK symbol of the
// translator's objects (shader/build_translator.sh, `nm -u`) and fails if one appears that this
// file does not define, so a new dependency is seen rather than silently stubbed.
//
// dxvk::Logger::err: DXVK's analysis and constant-layout units log refusals through it
// (src/util/log/log.h). Host 2 keeps the message per thread and fails the translation on it.
#include "d9g_shader_internal.h"

#include "../util/log/log.h"

namespace d9g::shader {
thread_local std::string g_dxvk_errors;
}

namespace dxvk {

void Logger::err(const std::string& message) {
  if (!d9g::shader::g_dxvk_errors.empty())
    d9g::shader::g_dxvk_errors += "; ";
  d9g::shader::g_dxvk_errors += message;
}

}  // namespace dxvk
