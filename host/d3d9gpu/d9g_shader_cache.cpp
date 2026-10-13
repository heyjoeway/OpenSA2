// Host 2's shader cache (R1 U5). One file per key under a root inside the repository's private/
// tree (the game's shaders are shipped bytes, and so is everything translated from them, so the
// cache may never live where a commit could carry it). An entry holds its key's text and the
// sha256 of its SPIR-V; one that does not read back whole is corrupt, counted, and recompiled.
// Off by default; in verify mode every hit is recompiled and compared byte for byte.
#include "d9g_shader.h"

#include "sha256.h"

#include <cerrno>
#include <climits>
#include <filesystem>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <sstream>
#include <sys/stat.h>
#include <unistd.h>

namespace d9g::shader {

namespace {

const char kMagic[] = "SA2-D3D9GPU-SHADER-CACHE/1\n";

#ifdef _WIN32
// Windows has no realpath: the canonical path (it must exist, as realpath's must), with '/'
// separators, so the comparisons below and the entries' paths read as they do on Linux.
std::string real(const std::string& path) {
  std::error_code ec;
  const std::filesystem::path p = std::filesystem::canonical(std::filesystem::path(path), ec);
  return ec ? std::string() : p.generic_string();
}

// A drive-letter path ("C:/...") has no POSIX root to build from: create_directories walks it.
bool make_dirs(const std::string& path) {
  std::error_code ec;
  std::filesystem::create_directories(std::filesystem::path(path), ec);
  return !ec && std::filesystem::is_directory(std::filesystem::path(path), ec);
}
#else
std::string real(const std::string& path) {
  char buf[PATH_MAX];
  return realpath(path.c_str(), buf) ? std::string(buf) : std::string();
}

bool make_dirs(const std::string& path) {
  std::string cur;
  std::stringstream ss(path);
  std::string part;
  if (!path.empty() && path[0] == '/')
    cur = "/";
  while (std::getline(ss, part, '/')) {
    if (part.empty())
      continue;
    cur += part + "/";
    if (mkdir(cur.c_str(), 0755) != 0 && errno != EEXIST)
      return false;
  }
  return true;
}
#endif

// The payload: the bindings' length (4 bytes), the bindings padded to 4, then the SPIR-V.
std::string payload(const CachedModule& m) {
  std::string p(4, '\0');
  uint32_t n = uint32_t(m.bindings.size());
  std::memcpy(&p[0], &n, 4);
  p += m.bindings;
  p.resize((p.size() + 3u) & ~size_t(3u), '\0');
  p.append(reinterpret_cast<const char*>(m.spirv.data()), m.spirv.size() * sizeof(uint32_t));
  return p;
}

std::string sha_of(const std::string& s) {
  Sha256 h;
  h.update(s.data(), s.size());
  return h.hex();
}

}  // namespace

Cache::Cache(const std::string& root, const std::string& repo_root, bool verify) : m_verify(verify) {
  // A translator built without its identity (D9G_TRANSLATOR_ID, from build_translator.sh) would
  // key every entry the same, so a cache written by one translator would serve another's modules.
  if (translator_identity() == "unbuilt") {
    m_error = "the translator was built without D9G_TRANSLATOR_ID; a cache keyed by \"unbuilt\" is refused";
    return;
  }
  // A translator fault (SA2_D3D9GPU_SHADER_FAULT, a test run's control) changes the module while
  // the key stays the clean one's: a cache under it would keep a faulted module for a clean key, or
  // serve a clean module to the fault's control. Refused whatever the fault's name.
  if (const char* f = std::getenv("SA2_D3D9GPU_SHADER_FAULT"); f && *f) {
    m_error = std::string("a translator fault is set (SA2_D3D9GPU_SHADER_FAULT=") + f + "); a cache is refused under one";
    return;
  }
  std::string priv = real(repo_root + "/private");
  if (priv.empty()) {
    m_error = "the repository has no private/ directory";
    return;
  }
  // Inside private/ before anything is made: the root as it would resolve (its existing part's
  // symlinks followed, the rest read as written), so a refused root leaves no directory behind.
  std::error_code ec;
#ifdef _WIN32
  const std::string planned = std::filesystem::weakly_canonical(std::filesystem::path(root), ec).generic_string();
#else
  const std::string planned = std::filesystem::weakly_canonical(std::filesystem::path(root), ec).string();
#endif
  if (ec || planned.empty() || (planned != priv && planned.compare(0, priv.size() + 1, priv + "/") != 0)) {
    m_error = "the cache root " + root + " is not inside " + priv;
    return;
  }
  if (!make_dirs(root)) {
    m_error = "cannot create the cache root " + root;
    return;
  }
  std::string r = real(root);
  if (r.empty() || (r != priv && r.compare(0, priv.size() + 1, priv + "/") != 0)) {
    m_error = "the cache root " + root + " is not inside " + priv;
    return;
  }
  m_root = r;
  m_ok = true;
}

std::string Cache::path_for(const CacheKey& key) const {
  std::string sha = key.sha256();
  return m_root + "/" + sha.substr(0, 2) + "/" + sha + ".spv";
}

bool Cache::load(const CacheKey& key, CachedModule& module) {
  if (!m_ok)
    return false;
  std::ifstream in(path_for(key), std::ios::binary);
  if (!in)
    return false;
  std::string data((std::istreambuf_iterator<char>(in)), {});
  std::string want = std::string(kMagic) + key.text();
  size_t header = want.size() + 65u;  // the key's text, then the payload's sha256 and a newline
  if (data.size() < header + 4u || data.compare(0, want.size(), want) != 0 || data[header - 1] != '\n') {
    m_stats.corrupt++;
    return false;
  }
  std::string body = data.substr(header);
  if (sha_of(body) != data.substr(want.size(), 64u)) {
    m_stats.corrupt++;
    return false;
  }
  uint32_t n = 0;
  std::memcpy(&n, body.data(), 4);
  size_t spirvAt = (4u + size_t(n) + 3u) & ~size_t(3u);
  if (spirvAt > body.size() || (body.size() - spirvAt) % 4u) {
    m_stats.corrupt++;
    return false;
  }
  module.bindings = body.substr(4, n);
  module.spirv.resize((body.size() - spirvAt) / 4u);
  std::memcpy(module.spirv.data(), body.data() + spirvAt, body.size() - spirvAt);
  return true;
}

bool Cache::store(const CacheKey& key, const CachedModule& module) {
  if (!m_ok)
    return false;
  std::string path = path_for(key);
  if (!make_dirs(path.substr(0, path.rfind('/'))))
    return false;
  std::string tmp = path + ".tmp" + std::to_string(getpid());
  {
    std::ofstream out(tmp, std::ios::binary | std::ios::trunc);
    std::string body = payload(module);
    std::string head = std::string(kMagic) + key.text() + sha_of(body) + "\n";
    out.write(head.data(), std::streamsize(head.size()));
    out.write(body.data(), std::streamsize(body.size()));
    if (!out)
      return false;
  }
#ifdef _WIN32
  // The C library's rename refuses an existing destination on Windows; filesystem::rename
  // replaces it, as rename(2) does.
  std::error_code ec;
  std::filesystem::rename(std::filesystem::path(tmp), std::filesystem::path(path), ec);
  return !ec;
#else
  return std::rename(tmp.c_str(), path.c_str()) == 0;
#endif
}

// The binding fields of a Built as text lines, so a cache hit gives the device everything a
// build would.
std::string encode_bindings(const Built& b) {
  std::ostringstream s;
  s << "samplers";
  for (auto r : b.sampler_registers) s << " " << r;
  s << "\nnum " << b.num_samplers << " " << b.num_uniform_buffers << " " << int(b.dynamic_capped) << "\n";
  for (const auto& u : b.uniforms) s << "uniform " << u.slot << " " << u.bytes << " " << u.kind << "\n";
  return s.str();
}

bool decode_bindings(const std::string& text, Built& b) {
  std::istringstream in(text);
  std::string line;
  b.sampler_registers.clear();
  b.uniforms.clear();
  bool sawSamplers = false, sawNum = false;
  while (std::getline(in, line)) {
    std::istringstream l(line);
    std::string tag;
    l >> tag;
    if (tag == "samplers") {
      uint32_t r;
      while (l >> r) b.sampler_registers.push_back(r);
      sawSamplers = true;
    } else if (tag == "num") {
      int capped = 0;
      if (!(l >> b.num_samplers >> b.num_uniform_buffers >> capped)) return false;
      b.dynamic_capped = capped != 0;
      sawNum = true;
    } else if (tag == "uniform") {
      UniformUse u;
      if (!(l >> u.slot >> u.bytes >> u.kind)) return false;
      b.uniforms.push_back(u);
    } else if (!tag.empty()) {
      return false;
    }
  }
  return sawSamplers && sawNum;
}

}  // namespace d9g::shader
