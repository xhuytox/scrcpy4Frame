// Logs go to stderr because stdout carries the JSON protocol.
#pragma once

#include <string_view>

namespace vrm {

enum class LogLevel { Debug, Info, Warning, Error };

// Thread-safe, one fwrite per line. Debug lines need VR_MIRROR_DEBUG set.
void log(LogLevel level, std::string_view tag, std::string_view message);

inline void log_debug(std::string_view tag, std::string_view message) {
  log(LogLevel::Debug, tag, message);
}
inline void log_info(std::string_view tag, std::string_view message) {
  log(LogLevel::Info, tag, message);
}
inline void log_warn(std::string_view tag, std::string_view message) {
  log(LogLevel::Warning, tag, message);
}
inline void log_error(std::string_view tag, std::string_view message) {
  log(LogLevel::Error, tag, message);
}

}  // namespace vrm
