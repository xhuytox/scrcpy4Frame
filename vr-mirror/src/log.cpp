#include "log.h"

#include <cstdio>
#include <cstdlib>
#include <string>

namespace vrm {

namespace {

bool debug_enabled() {
  static const bool enabled = [] {
    const char* v = std::getenv("VR_MIRROR_DEBUG");
    return v && *v && std::string(v) != "0";
  }();
  return enabled;
}

const char* level_name(LogLevel level) {
  switch (level) {
    case LogLevel::Debug: return "debug";
    case LogLevel::Info: return "info";
    case LogLevel::Warning: return "warn";
    case LogLevel::Error: return "error";
  }
  return "?";
}

}  // namespace

void log(LogLevel level, std::string_view tag, std::string_view message) {
  if (level == LogLevel::Debug && !debug_enabled()) return;
  std::string line = "vr-mirror ";
  line += level_name(level);
  line += " [";
  line.append(tag.data(), tag.size());
  line += "] ";
  line.append(message.data(), message.size());
  line += '\n';
  std::fwrite(line.data(), 1, line.size(), stderr);
}

}  // namespace vrm
