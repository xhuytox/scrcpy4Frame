// vr-toast: HMD-anchored OpenVR toast overlays for scrcpy4Frame.
//
// The protocol is specified in docs/CONTRACTS.md ("Frame -> vr-toast protocol"):
//   stdin : JSON lines {"cmd":"show"|"hide"|"quit", ...}
//   stdout: JSON lines {"event":"ready"|"unavailable"|"shown"|"expired"|"vr_quit"|"error", ...}
//
// Depends only on OpenVR. The JSON and base64 code below handles just what the protocol needs.

#include <openvr.h>

#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <cctype>
#include <cerrno>
#include <chrono>
#include <thread>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

using Clock = std::chrono::steady_clock;

constexpr int kPollTimeoutMs = 11;           // ~90 Hz housekeeping tick
constexpr int kMaxToasts = 3;                // stacked, newest on top
constexpr float kWidthMeters = 0.45f;
constexpr float kDistanceMeters = 1.2f;      // in front of the eyes
constexpr float kTopCentreYMeters = -0.18f;  // centre of the newest toast, below the middle of the view
constexpr float kGapMeters = 0.015f;         // vertical gap between stacked toasts
constexpr int kFadeMs = 250;
constexpr long kDefaultDurationMs = 5000;
constexpr long kMaxDurationMs = 10L * 60L * 1000L;
constexpr uint32_t kMaxDimension = 2048;
constexpr uint64_t kMaxPixels = 1920ull * 1080ull;  // upper bound for SetOverlayRaw payloads
constexpr size_t kMaxLineBytes = 16u << 20;         // > base64(kMaxPixels * 4) plus JSON overhead
constexpr size_t kReadChunk = 256u << 10;
constexpr uint32_t kSortOrderBase = 0x40000000u;    // well above ordinary overlays
constexpr int kExitUnavailable = 3;

volatile sig_atomic_t g_stop = 0;
FILE* g_out = stdout;  // protocol channel, see isolate_protocol_stdout()

void on_signal(int) { g_stop = 1; }

void append_json_escaped(std::string& out, std::string_view s) {
  for (unsigned char c : s) {
    switch (c) {
      case '"': out += "\\\""; break;
      case '\\': out += "\\\\"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      case '\b': out += "\\b"; break;
      case '\f': out += "\\f"; break;
      default:
        if (c < 0x20) {
          char buf[8];
          std::snprintf(buf, sizeof buf, "\\u%04x", c);
          out += buf;
        } else {
          out += static_cast<char>(c);
        }
    }
  }
}

void emit_line(const std::string& line) {
  // Ignore write errors (EPIPE). If the parent is gone, stdin EOF ends us shortly.
  std::fwrite(line.data(), 1, line.size(), g_out);
  std::fputc('\n', g_out);
  std::fflush(g_out);
}

// vrclient.so may print to stdout. Keep the protocol on a private dup of stdout and point fd 1
// at stderr so stray output can't corrupt the JSON stream.
void isolate_protocol_stdout() {
  const int fd = ::fcntl(STDOUT_FILENO, F_DUPFD_CLOEXEC, 3);
  if (fd < 0) return;
  FILE* f = ::fdopen(fd, "w");
  if (!f) {
    ::close(fd);
    return;
  }
  std::fflush(stdout);
  if (::dup2(STDERR_FILENO, STDOUT_FILENO) < 0) {
    std::fclose(f);
    return;
  }
  g_out = f;
}

void emit_event(const char* event) {
  emit_line(std::string("{\"event\":\"") + event + "\"}");
}

void emit_event(const char* event, const char* key, std::string_view value) {
  std::string s = "{\"event\":\"";
  s += event;
  s += "\",\"";
  s += key;
  s += "\":\"";
  append_json_escaped(s, value);
  s += "\"}";
  emit_line(s);
}

void emit_error(std::string_view message) { emit_event("error", "message", message); }

// Minimal JSON reader for one object per line. Nested values are validated but not kept.
struct JsonValue {
  enum class Type { String, Number, Bool, Null, Composite };
  Type type = Type::Null;
  std::string str;
  double num = 0.0;
  bool boolean = false;
};

using JsonObject = std::vector<std::pair<std::string, JsonValue>>;

class JsonReader {
 public:
  explicit JsonReader(std::string_view s) : s_(s) {}

  bool parse_object(JsonObject& out) {
    out.clear();
    skip_ws();
    if (!eat('{')) return false;
    skip_ws();
    if (eat('}')) return at_end();
    for (;;) {
      std::string key;
      skip_ws();
      if (!parse_string(&key)) return false;
      skip_ws();
      if (!eat(':')) return false;
      skip_ws();
      JsonValue value;
      if (!parse_value(value, 0)) return false;
      out.emplace_back(std::move(key), std::move(value));
      skip_ws();
      if (eat(',')) continue;
      if (eat('}')) return at_end();
      return false;
    }
  }

 private:
  static constexpr int kMaxDepth = 32;

  bool at_end() {
    skip_ws();
    return i_ == s_.size();
  }

  void skip_ws() {
    while (i_ < s_.size() &&
           (s_[i_] == ' ' || s_[i_] == '\t' || s_[i_] == '\n' || s_[i_] == '\r')) {
      ++i_;
    }
  }

  bool eat(char c) {
    if (i_ < s_.size() && s_[i_] == c) {
      ++i_;
      return true;
    }
    return false;
  }

  bool eat_word(const char* w) {
    const size_t n = std::strlen(w);
    if (s_.substr(i_, n) == w) {
      i_ += n;
      return true;
    }
    return false;
  }

  static int hex_value(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
  }

  bool parse_hex4(uint32_t* out) {
    if (i_ + 4 > s_.size()) return false;
    uint32_t v = 0;
    for (int k = 0; k < 4; ++k) {
      const int h = hex_value(s_[i_ + k]);
      if (h < 0) return false;
      v = (v << 4) | static_cast<uint32_t>(h);
    }
    i_ += 4;
    *out = v;
    return true;
  }

  static void append_utf8(std::string* out, uint32_t cp) {
    if (cp < 0x80) {
      *out += static_cast<char>(cp);
    } else if (cp < 0x800) {
      *out += static_cast<char>(0xC0 | (cp >> 6));
      *out += static_cast<char>(0x80 | (cp & 0x3F));
    } else if (cp < 0x10000) {
      *out += static_cast<char>(0xE0 | (cp >> 12));
      *out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
      *out += static_cast<char>(0x80 | (cp & 0x3F));
    } else {
      *out += static_cast<char>(0xF0 | (cp >> 18));
      *out += static_cast<char>(0x80 | ((cp >> 12) & 0x3F));
      *out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
      *out += static_cast<char>(0x80 | (cp & 0x3F));
    }
  }

  // Parses a string starting at '"'. With out == nullptr the string is only validated.
  bool parse_string(std::string* out) {
    if (!eat('"')) return false;
    for (;;) {
      // Copy the run of plain characters in one go (the base64 payload is one long run).
      const size_t start = i_;
      while (i_ < s_.size() && s_[i_] != '"' && s_[i_] != '\\' &&
             static_cast<unsigned char>(s_[i_]) >= 0x20) {
        ++i_;
      }
      if (out) out->append(s_.data() + start, i_ - start);
      if (i_ >= s_.size()) return false;
      const char c = s_[i_++];
      if (c == '"') return true;
      if (c != '\\') return false;  // raw control character
      if (i_ >= s_.size()) return false;
      const char e = s_[i_++];
      char decoded = 0;
      switch (e) {
        case '"': decoded = '"'; break;
        case '\\': decoded = '\\'; break;
        case '/': decoded = '/'; break;
        case 'b': decoded = '\b'; break;
        case 'f': decoded = '\f'; break;
        case 'n': decoded = '\n'; break;
        case 'r': decoded = '\r'; break;
        case 't': decoded = '\t'; break;
        case 'u': {
          uint32_t cp = 0;
          if (!parse_hex4(&cp)) return false;
          if (cp >= 0xD800 && cp <= 0xDBFF) {
            uint32_t lo = 0;
            if (!eat('\\') || !eat('u') || !parse_hex4(&lo) || lo < 0xDC00 || lo > 0xDFFF) {
              return false;
            }
            cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
          } else if (cp >= 0xDC00 && cp <= 0xDFFF) {
            return false;
          }
          if (out) append_utf8(out, cp);
          continue;
        }
        default:
          return false;
      }
      if (out) *out += decoded;
    }
  }

  bool parse_number(double* out) {
    const size_t start = i_;
    if (i_ < s_.size() && s_[i_] == '-') ++i_;
    while (i_ < s_.size() && (std::isdigit(static_cast<unsigned char>(s_[i_])) || s_[i_] == '.' ||
                              s_[i_] == 'e' || s_[i_] == 'E' || s_[i_] == '+' || s_[i_] == '-')) {
      ++i_;
    }
    if (i_ == start) return false;
    const std::string token(s_.substr(start, i_ - start));
    char* end = nullptr;
    errno = 0;
    const double v = std::strtod(token.c_str(), &end);
    if (end != token.c_str() + token.size() || errno == ERANGE || !std::isfinite(v)) return false;
    *out = v;
    return true;
  }

  bool parse_value(JsonValue& v, int depth) {
    if (i_ >= s_.size()) return false;
    const char c = s_[i_];
    if (c == '"') {
      v.type = JsonValue::Type::String;
      return parse_string(depth == 0 ? &v.str : nullptr);
    }
    if (c == '{' || c == '[') {
      v.type = JsonValue::Type::Composite;
      return skip_composite(depth + 1);
    }
    if (eat_word("true")) {
      v.type = JsonValue::Type::Bool;
      v.boolean = true;
      return true;
    }
    if (eat_word("false")) {
      v.type = JsonValue::Type::Bool;
      v.boolean = false;
      return true;
    }
    if (eat_word("null")) {
      v.type = JsonValue::Type::Null;
      return true;
    }
    v.type = JsonValue::Type::Number;
    return parse_number(&v.num);
  }

  bool skip_composite(int depth) {
    if (depth > kMaxDepth) return false;
    const char open = s_[i_++];
    const char close = open == '{' ? '}' : ']';
    skip_ws();
    if (eat(close)) return true;
    for (;;) {
      skip_ws();
      if (open == '{') {
        if (!parse_string(nullptr)) return false;
        skip_ws();
        if (!eat(':')) return false;
        skip_ws();
      }
      JsonValue ignored;
      if (!parse_value(ignored, depth)) return false;
      skip_ws();
      if (eat(',')) continue;
      return eat(close);
    }
  }

  std::string_view s_;
  size_t i_ = 0;
};

const JsonValue* find_field(const JsonObject& obj, std::string_view key) {
  // Last occurrence wins, like most JSON parsers.
  for (auto it = obj.rbegin(); it != obj.rend(); ++it) {
    if (it->first == key) return &it->second;
  }
  return nullptr;
}

const std::string* string_field(const JsonObject& obj, std::string_view key) {
  const JsonValue* v = find_field(obj, key);
  return (v && v->type == JsonValue::Type::String) ? &v->str : nullptr;
}

bool integer_field(const JsonObject& obj, std::string_view key, long* out) {
  const JsonValue* v = find_field(obj, key);
  if (!v || v->type != JsonValue::Type::Number) return false;
  if (v->num != std::floor(v->num) || std::fabs(v->num) > 9.0e15) return false;
  *out = static_cast<long>(v->num);
  return true;
}

// Accepts the standard and URL-safe alphabets, with or without padding.
bool base64_decode(std::string_view in, std::vector<uint8_t>& out) {
  static const std::array<int8_t, 256> table = [] {
    std::array<int8_t, 256> t{};
    t.fill(-1);
    const char* alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    for (int k = 0; k < 64; ++k) t[static_cast<unsigned char>(alphabet[k])] = static_cast<int8_t>(k);
    t[static_cast<unsigned char>('-')] = 62;
    t[static_cast<unsigned char>('_')] = 63;
    return t;
  }();

  out.clear();
  out.reserve(in.size() / 4 * 3 + 3);
  uint32_t acc = 0;
  int bits = 0;
  bool padding = false;
  for (unsigned char c : in) {
    if (c == ' ' || c == '\n' || c == '\r' || c == '\t') continue;
    if (c == '=') {
      padding = true;
      continue;
    }
    if (padding) return false;  // data after '='
    const int8_t v = table[c];
    if (v < 0) return false;
    acc = (acc << 6) | static_cast<uint32_t>(v);
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      out.push_back(static_cast<uint8_t>((acc >> bits) & 0xFFu));
      acc &= (1u << bits) - 1u;
    }
  }
  return bits != 6;  // a lone trailing sextet cannot encode a byte
}

bool file_exists(const std::string& path) {
  struct stat st {};
  return ::stat(path.c_str(), &st) == 0 && S_ISREG(st.st_mode);
}

// In a Flatpak sandbox XDG_CONFIG_HOME is ~/.var/app/<app-id>/config, where OpenVR won't find
// SteamVR's openvrpaths.vrpath. Fall back to ~/.config/openvr if it exists. An explicit
// VR_PATHREG_OVERRIDE wins. No-op on the host.
void point_openvr_at_host_registry() {
  if (std::getenv("VR_PATHREG_OVERRIDE")) return;
  const char* xdg = std::getenv("XDG_CONFIG_HOME");
  const char* home = std::getenv("HOME");
  if (!xdg || !*xdg || !home || !*home) return;
  if (file_exists(std::string(xdg) + "/openvr/openvrpaths.vrpath")) return;
  const std::string host = std::string(home) + "/.config/openvr/openvrpaths.vrpath";
  if (file_exists(host)) ::setenv("VR_PATHREG_OVERRIDE", host.c_str(), 1);
}

// Rotation from a frame looking down the middle of the view to the HMD's axes. The middle comes
// from both eyes' raw projections (OpenVR's "bottom" is the upward bound): the Steam Frame's
// lenses reach further down than up, so it is about 15 degrees below the HMD's forward axis.
// Same as vr-mirror's panel::view_center. No rotation if the values make no sense.
vr::HmdMatrix34_t view_rotation() {
  float x = 0.0f, y = 0.0f;
  for (const vr::EVREye eye : {vr::Eye_Left, vr::Eye_Right}) {
    float l = 0.0f, r = 0.0f, t = 0.0f, b = 0.0f;
    vr::VRSystem()->GetProjectionRaw(eye, &l, &r, &t, &b);
    x += (l + r) / 4.0f;
    y += (t + b) / 4.0f;
  }
  vr::HmdMatrix34_t m{};
  m.m[0][0] = m.m[1][1] = m.m[2][2] = 1.0f;
  if (!std::isfinite(x) || !std::isfinite(y) || std::fabs(x) > 1.0f || std::fabs(y) > 1.0f) return m;
  // Z points back out of the middle of the view, X stays level in the HMD's frame, Y = Z x X.
  float len = std::sqrt(x * x + y * y + 1.0f);
  const float zx = -x / len, zy = -y / len, zz = 1.0f / len;
  len = std::sqrt(zx * zx + zz * zz);
  const float xx = zz / len, xz = -zx / len;
  const float axes[3][3] = {{xx, 0.0f, xz}, {zy * xz, zz * xx - zx * xz, -zy * xx}, {zx, zy, zz}};
  for (int c = 0; c < 3; ++c) {
    for (int r = 0; r < 3; ++r) m.m[r][c] = axes[c][r];
  }
  return m;
}

// HMD-relative pose: centred, at height `y` from the middle of the view, kDistanceMeters along it,
// tilted about X to face the eye. `view` is from view_rotation().
vr::HmdMatrix34_t hmd_relative_pose(const vr::HmdMatrix34_t& view, float y) {
  const float z = -kDistanceMeters;
  const float a = std::atan2(-y, -z);
  const float c = std::cos(a);
  const float s = std::sin(a);
  vr::HmdMatrix34_t m{};
  m.m[0][0] = 1.0f; m.m[0][1] = 0.0f; m.m[0][2] = 0.0f; m.m[0][3] = 0.0f;
  m.m[1][0] = 0.0f; m.m[1][1] = c;    m.m[1][2] = s;    m.m[1][3] = y;
  m.m[2][0] = 0.0f; m.m[2][1] = -s;   m.m[2][2] = c;    m.m[2][3] = z;
  vr::HmdMatrix34_t r{};  // view * m: both turn about the eyes, so there's no translation to add
  for (int row = 0; row < 3; ++row) {
    for (int col = 0; col < 4; ++col) {
      r.m[row][col] = view.m[row][0] * m.m[0][col] + view.m[row][1] * m.m[1][col] + view.m[row][2] * m.m[2][col];
    }
  }
  return r;
}

const char* overlay_error_name(vr::EVROverlayError err) {
  vr::IVROverlay* ov = vr::VROverlay();
  return ov ? ov->GetOverlayErrorNameFromEnum(err) : "VROverlayError_Unknown";
}

long elapsed_ms(Clock::time_point from, Clock::time_point to) {
  return static_cast<long>(std::chrono::duration_cast<std::chrono::milliseconds>(to - from).count());
}

struct Toast {
  vr::VROverlayHandle_t handle = vr::k_ulOverlayHandleInvalid;
  bool active = false;
  std::string id;
  uint64_t seq = 0;  // larger = newer
  float height_m = 0.0f;
  Clock::time_point fade_in_at;   // alpha ramps 0 -> 1 from here
  Clock::time_point expires_at;   // fade-out starts here unless hidden earlier
  bool fading_out = false;
  Clock::time_point fade_out_at;
  float fade_out_from = 1.0f;     // alpha when the fade-out started
  float alpha = 0.0f;             // last alpha sent to the compositor
};

class Toaster {
 public:
  bool init(std::string* error) {
    view_ = view_rotation();
    vr::IVROverlay* ov = vr::VROverlay();
    const long pid = static_cast<long>(::getpid());
    for (int k = 0; k < kMaxToasts; ++k) {
      char key[vr::k_unVROverlayMaxKeyLength];
      char name[64];
      std::snprintf(key, sizeof key, "io.github.xhuytox.scrcpy4frame.toast.%ld.%d", pid, k);
      std::snprintf(name, sizeof name, "scrcpy4Frame notification %d", k + 1);
      const vr::EVROverlayError err = ov->CreateOverlay(key, name, &slots_[k].handle);
      if (err != vr::VROverlayError_None) {
        *error = overlay_error_name(err);
        shutdown();
        return false;
      }
      ov->SetOverlayWidthInMeters(slots_[k].handle, kWidthMeters);
      ov->SetOverlayAlpha(slots_[k].handle, 0.0f);
      ov->HideOverlay(slots_[k].handle);
    }
    return true;
  }

  void shutdown() {
    vr::IVROverlay* ov = vr::VROverlay();
    bool had_overlays = false;
    for (Toast& t : slots_) {
      if (ov && t.handle != vr::k_ulOverlayHandleInvalid) {
        ov->HideOverlay(t.handle);
        ov->ClearOverlayTexture(t.handle);
        had_overlays = true;
      }
    }
    // The compositor copies SetOverlayRaw uploads from our memory asynchronously. Exiting with one
    // pending crashes it (SIGBUS), so give it time before DestroyOverlay and VR_Shutdown.
    if (had_overlays) std::this_thread::sleep_for(std::chrono::milliseconds(300));
    for (Toast& t : slots_) {
      if (ov && t.handle != vr::k_ulOverlayHandleInvalid) ov->DestroyOverlay(t.handle);
      t = Toast{};
    }
    if (had_overlays) std::this_thread::sleep_for(std::chrono::milliseconds(300));
  }

  void show(const std::string& id, uint32_t width, uint32_t height, std::vector<uint8_t>& rgba,
            long duration_ms) {
    vr::IVROverlay* ov = vr::VROverlay();
    const Clock::time_point now = Clock::now();
    Toast* t = find(id);
    const bool reused = t != nullptr;
    if (!t) t = free_slot();
    if (!t) t = &evict_oldest();

    const vr::EVROverlayError err = ov->SetOverlayRaw(t->handle, rgba.data(), width, height, 4);
    if (err != vr::VROverlayError_None) {
      emit_error("show " + id + ": SetOverlayRaw failed: " + overlay_error_name(err));
      return;
    }
    ov->SetOverlayWidthInMeters(t->handle, kWidthMeters);

    if (reused) {
      // Keep the current opacity so that re-showing a fading toast does not flash.
      const float current = t->alpha;
      t->fade_in_at = now - std::chrono::milliseconds(static_cast<long>(current * kFadeMs));
    } else {
      t->fade_in_at = now;
      t->alpha = 0.0f;
      ov->SetOverlayAlpha(t->handle, 0.0f);
    }
    t->active = true;
    t->id = id;
    t->seq = next_seq_++;
    t->height_m = kWidthMeters * static_cast<float>(height) / static_cast<float>(width);
    t->expires_at = now + std::chrono::milliseconds(duration_ms);
    t->fading_out = false;

    layout();
    ov->ShowOverlay(t->handle);
    emit_event("shown", "id", id);
  }

  void hide(const std::string& id) {
    Toast* t = find(id);
    if (t) begin_fade_out(*t, Clock::now());
  }

  // Advances fades and expiry.
  void tick() {
    vr::IVROverlay* ov = vr::VROverlay();
    const Clock::time_point now = Clock::now();
    bool removed = false;
    for (Toast& t : slots_) {
      if (!t.active) continue;
      if (!t.fading_out && now >= t.expires_at) begin_fade_out(t, t.expires_at);
      float alpha;
      if (t.fading_out) {
        const float progress = static_cast<float>(elapsed_ms(t.fade_out_at, now)) / kFadeMs;
        if (progress >= 1.0f) {
          retire(t);
          removed = true;
          continue;
        }
        alpha = t.fade_out_from * (1.0f - std::max(0.0f, progress));
      } else {
        alpha = std::min(1.0f, static_cast<float>(elapsed_ms(t.fade_in_at, now)) / kFadeMs);
      }
      // Skip negligible updates, but always land exactly on fully opaque.
      if (std::fabs(alpha - t.alpha) >= 0.004f || (alpha >= 1.0f && t.alpha < 1.0f)) {
        t.alpha = alpha;
        ov->SetOverlayAlpha(t.handle, alpha);
      }
    }
    if (removed) layout();
  }

  // Overlay events aren't used, but drain the queues so they never fill up.
  void drain_overlay_events() {
    vr::IVROverlay* ov = vr::VROverlay();
    vr::VREvent_t ev{};
    for (Toast& t : slots_) {
      if (t.handle == vr::k_ulOverlayHandleInvalid) continue;
      while (ov->PollNextOverlayEvent(t.handle, &ev, sizeof ev)) {
      }
    }
  }

 private:
  Toast* find(const std::string& id) {
    for (Toast& t : slots_) {
      if (t.active && t.id == id) return &t;
    }
    return nullptr;
  }

  Toast* free_slot() {
    for (Toast& t : slots_) {
      if (!t.active) return &t;
    }
    return nullptr;
  }

  Toast& evict_oldest() {
    Toast* oldest = &slots_[0];
    for (Toast& t : slots_) {
      if (t.seq < oldest->seq) oldest = &t;
    }
    retire(*oldest);
    return *oldest;
  }

  void begin_fade_out(Toast& t, Clock::time_point at) {
    if (t.fading_out) return;
    t.fading_out = true;
    t.fade_out_at = at;
    t.fade_out_from = t.alpha;
  }

  // Every `shown` gets exactly one `expired`: on timeout, hide, or eviction by a newer toast.
  void retire(Toast& t) {
    vr::VROverlay()->HideOverlay(t.handle);
    vr::VROverlay()->SetOverlayAlpha(t.handle, 0.0f);
    const std::string id = std::move(t.id);
    const vr::VROverlayHandle_t handle = t.handle;
    t = Toast{};
    t.handle = handle;
    emit_event("expired", "id", id);
  }

  // Newest toast closest to the centre of view, older ones stacked below it.
  void layout() {
    std::array<Toast*, kMaxToasts> order{};
    int n = 0;
    for (Toast& t : slots_) {
      if (t.active) order[n++] = &t;
    }
    std::sort(order.begin(), order.begin() + n,
              [](const Toast* a, const Toast* b) { return a->seq > b->seq; });
    vr::IVROverlay* ov = vr::VROverlay();
    float y = kTopCentreYMeters;
    for (int k = 0; k < n; ++k) {
      if (k > 0) y -= order[k - 1]->height_m / 2.0f + kGapMeters + order[k]->height_m / 2.0f;
      const vr::HmdMatrix34_t pose = hmd_relative_pose(view_, y);
      ov->SetOverlayTransformTrackedDeviceRelative(order[k]->handle, vr::k_unTrackedDeviceIndex_Hmd,
                                                   &pose);
      ov->SetOverlaySortOrder(order[k]->handle, kSortOrderBase + static_cast<uint32_t>(kMaxToasts - k));
    }
  }

  std::array<Toast, kMaxToasts> slots_{};
  uint64_t next_seq_ = 1;
  vr::HmdMatrix34_t view_{};  // see view_rotation()
};

enum class Flow { Continue, Quit };

Flow handle_show(const JsonObject& obj, Toaster& toaster) {
  const std::string* id = string_field(obj, "id");
  if (!id) {
    emit_error("show: missing string field \"id\"");
    return Flow::Continue;
  }
  long width = 0;
  long height = 0;
  if (!integer_field(obj, "width", &width) || !integer_field(obj, "height", &height) || width <= 0 ||
      height <= 0 || width > static_cast<long>(kMaxDimension) ||
      height > static_cast<long>(kMaxDimension) ||
      static_cast<uint64_t>(width) * static_cast<uint64_t>(height) > kMaxPixels) {
    emit_error("show " + *id + ": width/height missing or out of range (max " +
               std::to_string(kMaxDimension) + " per side, " + std::to_string(kMaxPixels) +
               " pixels)");
    return Flow::Continue;
  }
  const std::string* rgba_b64 = string_field(obj, "rgba");
  if (!rgba_b64) {
    emit_error("show " + *id + ": missing string field \"rgba\"");
    return Flow::Continue;
  }
  std::vector<uint8_t> rgba;
  if (!base64_decode(*rgba_b64, rgba)) {
    emit_error("show " + *id + ": rgba is not valid base64");
    return Flow::Continue;
  }
  const uint64_t expected = static_cast<uint64_t>(width) * static_cast<uint64_t>(height) * 4u;
  if (rgba.size() != expected) {
    emit_error("show " + *id + ": rgba has " + std::to_string(rgba.size()) + " bytes, expected " +
               std::to_string(expected) + " (width*height*4)");
    return Flow::Continue;
  }
  long duration_ms = kDefaultDurationMs;
  if (find_field(obj, "duration_ms") && !integer_field(obj, "duration_ms", &duration_ms)) {
    emit_error("show " + *id + ": duration_ms must be an integer");
    return Flow::Continue;
  }
  duration_ms = std::clamp(duration_ms, static_cast<long>(kFadeMs), kMaxDurationMs);
  toaster.show(*id, static_cast<uint32_t>(width), static_cast<uint32_t>(height), rgba, duration_ms);
  return Flow::Continue;
}

Flow handle_line(std::string_view line, Toaster& toaster) {
  while (!line.empty() && (line.back() == '\r' || line.back() == ' ' || line.back() == '\t')) {
    line.remove_suffix(1);
  }
  if (line.find_first_not_of(" \t") == std::string_view::npos) return Flow::Continue;

  JsonObject obj;
  if (!JsonReader(line).parse_object(obj)) {
    emit_error("malformed JSON line");
    return Flow::Continue;
  }
  const std::string* cmd = string_field(obj, "cmd");
  if (!cmd) {
    emit_error("missing string field \"cmd\"");
    return Flow::Continue;
  }
  if (*cmd == "show") return handle_show(obj, toaster);
  if (*cmd == "hide") {
    const std::string* id = string_field(obj, "id");
    if (!id) {
      emit_error("hide: missing string field \"id\"");
    } else {
      toaster.hide(*id);
    }
    return Flow::Continue;
  }
  if (*cmd == "quit") return Flow::Quit;
  emit_error("unknown cmd \"" + *cmd + "\"");
  return Flow::Continue;
}

class LineReader {
 public:
  enum class Status { Ok, Eof, Quit };

  // Reads whatever is available (poll() said readable) and dispatches complete lines.
  Status pump(int fd, Toaster& toaster) {
    chunk_.resize(kReadChunk);
    const ssize_t n = ::read(fd, chunk_.data(), chunk_.size());
    if (n == 0) return Status::Eof;
    if (n < 0) return (errno == EINTR || errno == EAGAIN) ? Status::Ok : Status::Eof;

    std::string_view data(chunk_.data(), static_cast<size_t>(n));
    if (discarding_) {
      const size_t nl = data.find('\n');
      if (nl == std::string_view::npos) return Status::Ok;
      data.remove_prefix(nl + 1);
      discarding_ = false;
    }
    buf_.append(data.data(), data.size());

    size_t start = 0;
    for (;;) {
      const size_t nl = buf_.find('\n', scan_from_);
      if (nl == std::string::npos) break;
      const std::string_view line(buf_.data() + start, nl - start);
      start = nl + 1;
      scan_from_ = start;
      if (handle_line(line, toaster) == Flow::Quit) return Status::Quit;
    }
    buf_.erase(0, start);
    scan_from_ = buf_.size();

    if (buf_.size() > kMaxLineBytes) {
      emit_error("input line longer than " + std::to_string(kMaxLineBytes) + " bytes, dropped");
      buf_.clear();
      scan_from_ = 0;
      discarding_ = true;
    }
    return Status::Ok;
  }

 private:
  std::vector<char> chunk_;
  std::string buf_;
  size_t scan_from_ = 0;
  bool discarding_ = false;
};

void install_signal_handlers() {
  struct sigaction ignore {};
  ignore.sa_handler = SIG_IGN;
  sigemptyset(&ignore.sa_mask);
  sigaction(SIGPIPE, &ignore, nullptr);

  struct sigaction stop {};
  stop.sa_handler = on_signal;
  sigemptyset(&stop.sa_mask);
  stop.sa_flags = 0;  // no SA_RESTART: poll() returns EINTR and the loop notices g_stop
  sigaction(SIGTERM, &stop, nullptr);
  sigaction(SIGINT, &stop, nullptr);
  sigaction(SIGHUP, &stop, nullptr);
}

// Returns true when SteamVR asked us to quit (already acknowledged and reported).
bool pump_vr_events() {
  vr::VREvent_t ev{};
  while (vr::VRSystem()->PollNextEvent(&ev, sizeof ev)) {
    if (ev.eventType == vr::VREvent_Quit) {
      vr::VRSystem()->AcknowledgeQuit_Exiting();
      emit_event("vr_quit");
      return true;
    }
  }
  return false;
}

}  // namespace

int main() {
  isolate_protocol_stdout();
  install_signal_handlers();
  point_openvr_at_host_registry();

  // Probe first: Background init fails instead of launching SteamVR when it isn't running.
  vr::EVRInitError init_error = vr::VRInitError_None;
  vr::VR_Init(&init_error, vr::VRApplication_Background);
  if (init_error != vr::VRInitError_None) {
    emit_event("unavailable", "reason", vr::VR_GetVRInitErrorAsSymbol(init_error));
    return kExitUnavailable;
  }
  vr::VR_Shutdown();

  init_error = vr::VRInitError_None;
  vr::VR_Init(&init_error, vr::VRApplication_Overlay);
  if (init_error != vr::VRInitError_None) {
    emit_event("unavailable", "reason", vr::VR_GetVRInitErrorAsSymbol(init_error));
    return kExitUnavailable;
  }
  if (!vr::VRSystem() || !vr::VROverlay()) {
    vr::VR_Shutdown();
    emit_event("unavailable", "reason",
               vr::VR_GetVRInitErrorAsSymbol(vr::VRInitError_Init_InterfaceNotFound));
    return kExitUnavailable;
  }

  Toaster toaster;
  std::string overlay_error;
  if (!toaster.init(&overlay_error)) {
    vr::VR_Shutdown();
    emit_event("unavailable", "reason", overlay_error);
    return kExitUnavailable;
  }
  emit_event("ready");

  LineReader reader;
  bool stdin_open = true;
  while (!g_stop) {
    if (stdin_open) {
      pollfd pfd{STDIN_FILENO, POLLIN, 0};
      const int r = ::poll(&pfd, 1, kPollTimeoutMs);
      if (r < 0 && errno != EINTR) break;
      if (r > 0) {
        if (pfd.revents & (POLLIN | POLLHUP | POLLERR)) {
          const LineReader::Status st = reader.pump(STDIN_FILENO, toaster);
          if (st != LineReader::Status::Ok) stdin_open = false;
        } else if (pfd.revents & POLLNVAL) {
          stdin_open = false;
        }
      }
      if (!stdin_open) break;  // EOF or "quit"
    }

    if (pump_vr_events()) break;  // SteamVR is shutting down: already acknowledged
    toaster.drain_overlay_events();
    toaster.tick();
  }

  toaster.shutdown();
  vr::VR_Shutdown();
  return 0;
}
