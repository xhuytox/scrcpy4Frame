// vr-mirror: shows an Android phone (scrcpy 5.0 server, H.264) as a SteamVR overlay panel and
// forwards laser input to it as touches.
//
// Protocol: docs/CONTRACTS.md, "VR mirror panel: vr-mirror helper".
//   stdin : JSON lines {"cmd":"connect"|"key"|"recenter"|"show"|"hide"|"theater"|"dashboard_button"|
//                       "audio"|"quit", ...}
//   stdout: JSON lines {"event":"ready"|"unavailable"|"connected"|"size"|"first_frame"|"stats"|
//                       "placement"|"visibility"|"theater"|"audio_cast"|"disconnected"|"error", ...}
//
// The main thread owns stdin/stdout and all OpenVR calls. The network thread (ScrcpyClient)
// decodes into a latest-frame mailbox and queues session events, waking the main loop through
// a pipe. Control messages are sent from the main thread.

#include "decoder.h"
#include "json_lite.h"
#include "log.h"
#include "scrcpy_client.h"
#include "scrcpy_protocol.h"

#if VR_MIRROR_WITH_OPENVR
#include "dash_bar.h"
#include "vr_panel.h"
#endif

#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace {

using Clock = std::chrono::steady_clock;
using vrm::scrcpy::KeyAction;
using vrm::scrcpy::TouchAction;

constexpr int kTickMs = 5;
constexpr auto kStatsInterval = std::chrono::seconds(5);
constexpr auto kTapDuration = std::chrono::milliseconds(50);
constexpr size_t kMaxLineBytes = 1u << 20;
#if VR_MIRROR_WITH_OPENVR
constexpr int kMaxConsecutiveUploadFailures = 30;
// Dashboard bar button that shows the hidden panel in front of the user (in theater, where it was).
constexpr const char* kShowPanelButton = "show_panel";
#endif
constexpr int kExitUsage = 2;
constexpr int kExitUnavailable = 3;
constexpr int kExitFatal = 4;

volatile sig_atomic_t g_stop = 0;
FILE* g_out = stdout;  // protocol channel, see isolate_protocol_stdout()

void on_signal(int) { g_stop = 1; }

void emit_line(const std::string& line) {
  std::fwrite(line.data(), 1, line.size(), g_out);  // EPIPE is ignored: stdin EOF follows
  std::fputc('\n', g_out);
  std::fflush(g_out);
}

void emit(const vrm::json::Writer& w) { emit_line(w.finish()); }

void emit_event(const char* event) { emit(vrm::json::Writer().str("event", event)); }

void emit_error(std::string_view message) {
  vrm::log_error("main", message);
  emit(vrm::json::Writer().str("event", "error").str("message", message));
}

// OpenVR and FFmpeg can print to stdout. Move the protocol to a private dup of stdout and point
// fd 1 at stderr.
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

struct Options {
  bool no_vr = false;
  std::string snapshot;        // --snapshot FILE.ppm
  bool tap = false;            // --tap X,Y
  long tap_x = 0;
  long tap_y = 0;
  std::string manifest;        // --manifest PATH (default: next to the executable)
};

void print_usage(FILE* f) {
  std::fputs(
      "usage: vr-mirror [--no-vr] [--snapshot FILE.ppm] [--tap X,Y] [--manifest PATH]\n"
      "\n"
      "JSON lines on stdin/stdout, see docs/CONTRACTS.md (\"VR mirror panel\").\n"
      "  --no-vr            no OpenVR: connect, decode and print the same events (dev/test)\n"
      "  --snapshot FILE    write the first decoded frame as a binary PPM\n"
      "  --tap X,Y          send one tap at video pixel X,Y after the first frame\n"
      "  --manifest PATH    vr-mirror.vrmanifest to register (default: next to the binary)\n",
      f);
}

bool parse_tap(const char* s, long* x, long* y) {
  char* end = nullptr;
  errno = 0;
  *x = std::strtol(s, &end, 10);
  if (errno || end == s || *end != ',' || *x < 0) return false;
  const char* s2 = end + 1;
  *y = std::strtol(s2, &end, 10);
  return !errno && end != s2 && *end == '\0' && *y >= 0;
}

// Returns -1 to continue, otherwise an exit code.
int parse_args(int argc, char** argv, Options& o) {
  for (int i = 1; i < argc; ++i) {
    const std::string a = argv[i];
    auto value = [&](const char* name) -> const char* {
      if (i + 1 >= argc) {
        std::fprintf(stderr, "vr-mirror: %s needs a value\n", name);
        return nullptr;
      }
      return argv[++i];
    };
    if (a == "--no-vr") {
      o.no_vr = true;
    } else if (a == "--snapshot") {
      const char* v = value("--snapshot");
      if (!v) return kExitUsage;
      o.snapshot = v;
    } else if (a == "--tap") {
      const char* v = value("--tap");
      if (!v) return kExitUsage;
      if (!parse_tap(v, &o.tap_x, &o.tap_y)) {
        std::fprintf(stderr, "vr-mirror: --tap expects X,Y (non-negative integers)\n");
        return kExitUsage;
      }
      o.tap = true;
    } else if (a == "--manifest") {
      const char* v = value("--manifest");
      if (!v) return kExitUsage;
      o.manifest = v;
    } else if (a == "-h" || a == "--help") {
      print_usage(stdout);
      return 0;
    } else {
      std::fprintf(stderr, "vr-mirror: unknown argument %s\n", a.c_str());
      print_usage(stderr);
      return kExitUsage;
    }
  }
  return -1;
}

#if VR_MIRROR_WITH_OPENVR
std::string executable_dir(const char* argv0) {
  char buf[4096];
  const ssize_t n = ::readlink("/proc/self/exe", buf, sizeof buf - 1);
  std::string path = n > 0 ? std::string(buf, static_cast<size_t>(n)) : std::string(argv0 ? argv0 : "");
  const size_t slash = path.rfind('/');
  return slash == std::string::npos ? "." : path.substr(0, slash);
}
#endif

// Binary PPM (P6) of an RGBA image, optionally composited over a grey background.
bool write_ppm(const std::string& path, const uint8_t* rgba, uint32_t w, uint32_t h, bool composite) {
  FILE* f = std::fopen(path.c_str(), "wb");
  if (!f) return false;
  std::fprintf(f, "P6\n%u %u\n255\n", w, h);
  std::vector<uint8_t> row(static_cast<size_t>(w) * 3);
  for (uint32_t y = 0; y < h; ++y) {
    const uint8_t* src = rgba + static_cast<size_t>(y) * w * 4;
    for (uint32_t x = 0; x < w; ++x) {
      for (int c = 0; c < 3; ++c) {
        uint8_t v = src[x * 4 + c];
        if (composite) {
          const unsigned a = src[x * 4 + 3];
          v = static_cast<uint8_t>((v * a + 128u * (255u - a)) / 255u);
        }
        row[x * 3 + c] = v;
      }
    }
    std::fwrite(row.data(), 1, row.size(), f);
  }
  const bool ok = !std::ferror(f);
  return std::fclose(f) == 0 && ok;
}

struct NetEvent {
  enum class Type { Connected, Size, Ended, Error };
  Type type = Type::Error;
  std::string text;
  uint32_t width = 0;
  uint32_t height = 0;
};

// Sink callbacks run on the network thread. The main thread reads `mailbox` and drain().
class Session : public vrm::ScrcpyClient::Sink {
 public:
  explicit Session(int notify_fd) : notify_fd_(notify_fd) {}

  bool open(std::string& error) { return decoder_.open(error); }

  void on_connected(const std::string& device_name) override {
    push({NetEvent::Type::Connected, device_name, 0, 0});
  }
  void on_session(uint32_t width, uint32_t height) override {
    push({NetEvent::Type::Size, {}, width, height});
  }
  bool on_packet(vrm::scrcpy::StreamEvent& packet, std::string& error) override {
    if (!merger_.merge(packet)) return true;  // config: prepended to the next media packet
    const Clock::time_point t0 = Clock::now();
    int frames = 0;
    if (!decoder_.decode(packet.data.data(), packet.data.size(), static_cast<int64_t>(packet.pts),
                         packet.key_frame, mailbox, &frames, error)) {
      return false;
    }
    decode_ns_ += static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now() - t0).count());
    if (frames > 0) {
      decoded_frames_ += static_cast<uint64_t>(frames);
      notify();
    }
    return true;
  }
  void on_ended(const std::string& reason) override { push({NetEvent::Type::Ended, reason, 0, 0}); }

  std::vector<NetEvent> drain() {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<NetEvent> out;
    out.swap(events_);
    return out;
  }

  uint64_t decode_ns() const { return decode_ns_; }
  uint64_t decoded_frames() const { return decoded_frames_; }

  vrm::FrameMailbox mailbox;

 private:
  void push(NetEvent ev) {
    {
      std::lock_guard<std::mutex> lock(mutex_);
      events_.push_back(std::move(ev));
    }
    notify();
  }
  void notify() {
    const char b = 1;
    (void)!::write(notify_fd_, &b, 1);  // non-blocking, a full pipe already wakes the loop
  }

  int notify_fd_;
  vrm::scrcpy::ConfigPacketMerger merger_;
  vrm::H264Decoder decoder_;
  std::mutex mutex_;
  std::vector<NetEvent> events_;
  std::atomic<uint64_t> decode_ns_{0};
  std::atomic<uint64_t> decoded_frames_{0};
};

class App {
 public:
  explicit App(Options o) : opt_(std::move(o)) {}

  int run(const char* argv0);

 private:
  enum class Flow { Continue, Quit };

  void pump_stdin();
  Flow handle_line(std::string_view line);
  Flow handle_connect(const vrm::json::Object& obj);
  void handle_key(const std::string& key);

  void handle_net_events();
  void handle_frame();
  void on_first_frame(const vrm::RgbaFrame& frame);
  void maybe_emit_stats();

  bool send(const std::vector<uint8_t>& msg) { return client_.send(msg); }
  void send_back();
  void send_key(uint32_t keycode);
  void send_touch(TouchAction action, const vrm::scrcpy::Position& pos,
                  uint64_t pointer_id = vrm::scrcpy::kPointerIdGenericFinger);

#if VR_MIRROR_WITH_OPENVR
  void handle_panel_actions();
  void init_dashbar();
  void update_dashbar();
#endif

  void request_quit(int code) {
    quit_ = true;
    exit_code_ = code;
  }

  Options opt_;
  int notify_pipe_[2] = {-1, -1};
  std::unique_ptr<Session> session_;
  vrm::ScrcpyClient client_;
#if VR_MIRROR_WITH_OPENVR
  std::unique_ptr<vrm::VrPanel> panel_;
  std::unique_ptr<vrm::DashBar> dashbar_;  // optional; needs panel_'s OpenVR session
#endif

  bool quit_ = false;
  int exit_code_ = 0;
  bool stdin_open_ = true;
  std::string line_buf_;
  bool discarding_ = false;

  bool turn_screen_off_ = false;
  bool dashboard_button_ = false;
  bool connected_ = false;
  bool first_frame_ = false;
  uint32_t session_w_ = 0;
  uint32_t session_h_ = 0;
  std::optional<Clock::time_point> tap_up_at_;
  vrm::scrcpy::Position tap_pos_;
#if VR_MIRROR_WITH_OPENVR
  int upload_failures_ = 0;
#endif

  Clock::time_point stats_at_{};
  uint64_t stats_frames_ = 0;
  uint64_t stats_upload_ns_ = 0;
  uint64_t stats_uploads_ = 0;
  uint64_t stats_decode_ns_base_ = 0;
  uint64_t stats_decoded_base_ = 0;
};

int App::run(const char* argv0) {
  if (!opt_.no_vr) {
#if VR_MIRROR_WITH_OPENVR
    panel_ = std::make_unique<vrm::VrPanel>();
    const std::string manifest =
        opt_.manifest.empty() ? executable_dir(argv0) + "/vr-mirror.vrmanifest" : opt_.manifest;
    std::string message;
    switch (panel_->init(manifest, message)) {
      case vrm::VrPanel::InitResult::Ok:
        break;
      case vrm::VrPanel::InitResult::Unavailable:
        panel_.reset();
        emit(vrm::json::Writer().str("event", "unavailable").str("reason", message));
        return kExitUnavailable;
      case vrm::VrPanel::InitResult::Fatal:
        panel_.reset();
        emit_error(message);
        return kExitFatal;
    }
    init_dashbar();
#else
    (void)argv0;
    emit(vrm::json::Writer()
             .str("event", "unavailable")
             .str("reason", "built without OpenVR support (run with --no-vr)"));
    return kExitUnavailable;
#endif
  }

  if (::pipe(notify_pipe_) != 0) {
    emit_error(std::string("pipe: ") + std::strerror(errno));
    return kExitFatal;
  }
  for (int fd : notify_pipe_) {
    ::fcntl(fd, F_SETFL, ::fcntl(fd, F_GETFL) | O_NONBLOCK);
    ::fcntl(fd, F_SETFD, FD_CLOEXEC);
  }
  emit_event("ready");

  while (!g_stop && !quit_) {
    pollfd pfds[2] = {{notify_pipe_[0], POLLIN, 0}, {STDIN_FILENO, POLLIN, 0}};
    const int r = ::poll(pfds, stdin_open_ ? 2 : 1, kTickMs);
    if (r < 0 && errno != EINTR) break;
    if (r > 0 && pfds[0].revents) {
      char drain[256];
      while (::read(notify_pipe_[0], drain, sizeof drain) > 0) {
      }
    }
    if (r > 0 && stdin_open_ && pfds[1].revents) pump_stdin();
    if (!stdin_open_) break;  // EOF or quit
    if (quit_) break;

    handle_net_events();
#if VR_MIRROR_WITH_OPENVR
    if (panel_) update_dashbar();  // first, so a click's placement and visibility go out this tick
    if (panel_ && !quit_) handle_panel_actions();
#endif
    if (quit_) break;
    handle_frame();

    if (tap_up_at_ && Clock::now() >= *tap_up_at_) {
      tap_up_at_.reset();
      send_touch(TouchAction::Up, tap_pos_);
    }
    maybe_emit_stats();
  }

  // Closing the sockets makes the server exit.
  client_.stop();
#if VR_MIRROR_WITH_OPENVR
  if (dashbar_) dashbar_->release_overlay();  // before VR_Shutdown
  if (panel_) panel_->shutdown();
#endif
  for (int fd : notify_pipe_) {
    if (fd >= 0) ::close(fd);
  }
  return exit_code_;
}

void App::pump_stdin() {
  char chunk[64 * 1024];
  const ssize_t n = ::read(STDIN_FILENO, chunk, sizeof chunk);
  if (n == 0 || (n < 0 && errno != EINTR && errno != EAGAIN)) {
    stdin_open_ = false;
    return;
  }
  if (n < 0) return;
  std::string_view data(chunk, static_cast<size_t>(n));
  while (!data.empty()) {
    const size_t nl = data.find('\n');
    if (nl == std::string_view::npos) {
      if (!discarding_) line_buf_.append(data.data(), data.size());
      if (line_buf_.size() > kMaxLineBytes) {
        emit_error("input line longer than " + std::to_string(kMaxLineBytes) + " bytes, dropped");
        line_buf_.clear();
        discarding_ = true;
      }
      return;
    }
    if (!discarding_) {
      line_buf_.append(data.data(), nl);
      const std::string line = std::move(line_buf_);
      line_buf_.clear();
      if (handle_line(line) == Flow::Quit) {
        stdin_open_ = false;
        return;
      }
    }
    discarding_ = false;
    data.remove_prefix(nl + 1);
  }
}

App::Flow App::handle_line(std::string_view line) {
  while (!line.empty() && (line.back() == '\r' || line.back() == ' ' || line.back() == '\t')) {
    line.remove_suffix(1);
  }
  if (line.find_first_not_of(" \t") == std::string_view::npos) return Flow::Continue;

  vrm::json::Object obj;
  if (!vrm::json::Reader(line).parse_object(obj)) {
    emit_error("malformed JSON line");
    return Flow::Continue;
  }
  const std::string* cmd = vrm::json::string_field(obj, "cmd");
  if (!cmd) {
    emit_error("missing string field \"cmd\"");
    return Flow::Continue;
  }
  if (*cmd == "connect") return handle_connect(obj);
  if (*cmd == "key") {
    const std::string* key = vrm::json::string_field(obj, "key");
    if (!key) {
      emit_error("key: missing string field \"key\"");
    } else {
      handle_key(*key);
    }
    return Flow::Continue;
  }
  if (*cmd == "recenter") {
    // "now": Home's show button, at once with no countdown (only Recenter itself counts down). In
    // theater a hidden panel shows where it was instead.
    bool now = false;
    if (vrm::json::find(obj, "now") && !vrm::json::bool_field(obj, "now", &now)) {
      emit_error("recenter: \"now\" must be a boolean");
      return Flow::Continue;
    }
#if VR_MIRROR_WITH_OPENVR
    if (panel_ && now) {
      panel_->reveal();
    } else if (panel_) {
      panel_->recenter();
    }
#endif
    return Flow::Continue;
  }
  if (*cmd == "show" || *cmd == "hide") {
#if VR_MIRROR_WITH_OPENVR
    if (panel_) panel_->set_hidden(*cmd == "hide");
#endif
    return Flow::Continue;
  }
  if (*cmd == "theater") {
    bool on = false;
    if (!vrm::json::bool_field(obj, "on", &on)) {
      emit_error("theater: \"on\" must be a boolean");
    } else {
#if VR_MIRROR_WITH_OPENVR
      if (panel_) panel_->set_theater(on);
#endif
    }
    return Flow::Continue;
  }
  if (*cmd == "audio") {
    bool on = false, enabled = true;
    if (!vrm::json::bool_field(obj, "on", &on) ||
        (vrm::json::find(obj, "enabled") && !vrm::json::bool_field(obj, "enabled", &enabled))) {
      emit_error("audio: \"on\" and \"enabled\" must be booleans");
    } else {
#if VR_MIRROR_WITH_OPENVR
      if (panel_) panel_->set_audio(on, enabled);
#endif
    }
    return Flow::Continue;
  }
  if (*cmd == "dashboard_button") {
    bool enabled = false;
    if (!vrm::json::bool_field(obj, "enabled", &enabled)) {
      emit_error("dashboard_button: \"enabled\" must be a boolean");
    } else {
      dashboard_button_ = enabled;
    }
    return Flow::Continue;
  }
  if (*cmd == "quit") return Flow::Quit;
  emit_error("unknown cmd \"" + *cmd + "\"");
  return Flow::Continue;
}

App::Flow App::handle_connect(const vrm::json::Object& obj) {
  if (session_) {
    emit_error("connect: already connected");
    return Flow::Continue;
  }
  std::string host = "127.0.0.1";
  if (const vrm::json::Value* v = vrm::json::find(obj, "host")) {
    if (v->type != vrm::json::Value::Type::String || v->str.empty()) {
      emit_error("connect: \"host\" must be a non-empty string");
      return Flow::Continue;
    }
    host = v->str;
  }
  long port = 0;
  if (!vrm::json::integer_field(obj, "port", &port) || port < 1 || port > 65535) {
    emit_error("connect: \"port\" must be an integer in 1..65535");
    return Flow::Continue;
  }
  bool screen_off = false;
  if (vrm::json::find(obj, "turn_screen_off") && !vrm::json::bool_field(obj, "turn_screen_off", &screen_off)) {
    emit_error("connect: \"turn_screen_off\" must be a boolean");
    return Flow::Continue;
  }
  bool dashboard_button = false;
  if (vrm::json::find(obj, "dashboard_button") && !vrm::json::bool_field(obj, "dashboard_button", &dashboard_button)) {
    emit_error("connect: \"dashboard_button\" must be a boolean");
    return Flow::Continue;
  }
  // Optional positive numbers, clamped to [lo, hi].
  const auto positive_field = [&obj](const char* name, double lo, double hi, double* out) {
    if (!vrm::json::find(obj, name)) return true;
    if (!vrm::json::number_field(obj, name, out) || *out <= 0.0) {
      emit_error(std::string("connect: \"") + name + "\" must be a positive number");
      return false;
    }
    *out = std::clamp(*out, lo, hi);
    return true;
  };
  double panel_size = 0.0, theater_size = 0.0, scroll_share = 0.0, screen_dp = 0.0;
  if (!positive_field("panel_size_m", 0.10, 2.0, &panel_size) ||
      !positive_field("theater_size_m", 0.10, 8.0, &theater_size) ||
      !positive_field("scroll_screens_per_click", 0.001, 1.0, &scroll_share) ||
      !positive_field("screen_long_dp", 100.0, 10000.0, &screen_dp)) {
    return Flow::Continue;
  }
  turn_screen_off_ = screen_off;
  dashboard_button_ = dashboard_button;
#if VR_MIRROR_WITH_OPENVR
  if (panel_) {
    if (const std::string* title = vrm::json::string_field(obj, "title")) panel_->set_title(*title);
    if (panel_size > 0.0) panel_->set_long_side(static_cast<float>(panel_size));
    if (theater_size > 0.0) panel_->set_theater_long_side(static_cast<float>(theater_size));
    panel_->set_scroll_speed(static_cast<float>(scroll_share), static_cast<float>(screen_dp));
    // Last session's pose, ignored if invalid.
    vrm::panel::Mat34 saved;
    const std::string* pose = vrm::json::string_field(obj, "pose");
    if (pose && vrm::panel::parse_pose(*pose, saved)) panel_->set_saved_pose(saved);
  }
#endif

  session_ = std::make_unique<Session>(notify_pipe_[1]);
  std::string error;
  if (!session_->open(error)) {
    session_.reset();
    emit_error("decoder: " + error);
    request_quit(kExitFatal);
    return Flow::Continue;
  }
  if (!client_.start(host, static_cast<uint16_t>(port), session_.get(), error)) {
    session_.reset();
    emit_error("connect: " + error);
    return Flow::Continue;
  }
  stats_at_ = Clock::now();
  return Flow::Continue;
}

void App::handle_key(const std::string& key) {
  if (!connected_) {
    emit_error("key: not connected");
    return;
  }
  if (key == "back") {
    send_back();
  } else if (key == "home") {
    send_key(vrm::scrcpy::kKeycodeHome);
  } else if (key == "app_switch") {
    send_key(vrm::scrcpy::kKeycodeAppSwitch);
  } else if (key == "power") {
    send_key(vrm::scrcpy::kKeycodePower);
  } else {
    emit_error("key: unknown key \"" + key + "\" (back, home, app_switch, power)");
  }
}

void App::handle_net_events() {
  if (!session_) return;
  for (NetEvent& ev : session_->drain()) {
    switch (ev.type) {
      case NetEvent::Type::Connected:
        connected_ = true;
        emit(vrm::json::Writer().str("event", "connected").str("device", ev.text));
        break;
      case NetEvent::Type::Size:
        if (ev.width != session_w_ || ev.height != session_h_) {
          session_w_ = ev.width;
          session_h_ = ev.height;
          emit(vrm::json::Writer()
                   .str("event", "size")
                   .integer("width", ev.width)
                   .integer("height", ev.height));
        }
        break;
      case NetEvent::Type::Ended:
        emit(vrm::json::Writer().str("event", "disconnected").str("reason", ev.text));
        request_quit(0);
        return;
      case NetEvent::Type::Error:
        emit_error(ev.text);
        break;
    }
  }
}

void App::handle_frame() {
  if (!session_) return;
  const vrm::RgbaFrame* frame = session_->mailbox.take();
  if (!frame || frame->pixels.empty()) return;

#if VR_MIRROR_WITH_OPENVR
  if (panel_) {
    const Clock::time_point t0 = Clock::now();
    std::string error;
    if (!panel_->show_frame(*frame, error)) {
      if (upload_failures_++ == 0) emit_error("frame upload failed: " + error);
      if (upload_failures_ >= kMaxConsecutiveUploadFailures) {
        emit_error("giving up after " + std::to_string(upload_failures_) +
                   " consecutive frame upload failures");
        request_quit(kExitFatal);
      }
      return;
    }
    upload_failures_ = 0;
    stats_upload_ns_ += static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now() - t0).count());
    ++stats_uploads_;
  }
#endif
  ++stats_frames_;
  if (!first_frame_) {
    first_frame_ = true;
    on_first_frame(*frame);
  }
}

void App::on_first_frame(const vrm::RgbaFrame& frame) {
  emit_event("first_frame");
  if (!opt_.snapshot.empty()) {
    if (write_ppm(opt_.snapshot, frame.pixels.data(), frame.width, frame.height, false)) {
      vrm::log_info("main", "snapshot written to " + opt_.snapshot);
    } else {
      emit_error("cannot write snapshot " + opt_.snapshot);
    }
  }
  if (turn_screen_off_ && !send(vrm::scrcpy::serialize_display_power(false))) {
    emit_error("could not send display power off");
  }
  if (opt_.tap) {
    tap_pos_ = vrm::scrcpy::make_position(opt_.tap_x, opt_.tap_y, frame.width, frame.height);
    send_touch(TouchAction::Down, tap_pos_);
    tap_up_at_ = Clock::now() + kTapDuration;
  }
}

void App::maybe_emit_stats() {
  if (!session_ || !connected_) return;
  const Clock::time_point now = Clock::now();
  if (now - stats_at_ < kStatsInterval) return;
  const double seconds = std::chrono::duration<double>(now - stats_at_).count();
  const uint64_t decode_ns = session_->decode_ns();
  const uint64_t decoded = session_->decoded_frames();
  const uint64_t d_ns = decode_ns - stats_decode_ns_base_;
  const uint64_t d_frames = decoded - stats_decoded_base_;
  emit(vrm::json::Writer()
           .str("event", "stats")
           .number("fps", seconds > 0 ? static_cast<double>(stats_frames_) / seconds : 0.0, 1)
           .number("decode_ms", d_frames ? static_cast<double>(d_ns) / 1e6 / static_cast<double>(d_frames) : 0.0, 1)
           .number("upload_ms",
                   stats_uploads_ ? static_cast<double>(stats_upload_ns_) / 1e6 / static_cast<double>(stats_uploads_) : 0.0,
                   1));
  stats_at_ = now;
  stats_frames_ = 0;
  stats_upload_ns_ = 0;
  stats_uploads_ = 0;
  stats_decode_ns_base_ = decode_ns;
  stats_decoded_base_ = decoded;
}

void App::send_back() {
  send(vrm::scrcpy::serialize_back_or_screen_on(KeyAction::Down));
  send(vrm::scrcpy::serialize_back_or_screen_on(KeyAction::Up));
}

void App::send_key(uint32_t keycode) {
  send(vrm::scrcpy::serialize_keycode(KeyAction::Down, keycode));
  send(vrm::scrcpy::serialize_keycode(KeyAction::Up, keycode));
}

void App::send_touch(TouchAction action, const vrm::scrcpy::Position& pos, uint64_t pointer_id) {
  const float pressure = action == TouchAction::Up ? 0.0f : 1.0f;
  send(vrm::scrcpy::serialize_touch(action, pointer_id, pos, pressure));
}

#if VR_MIRROR_WITH_OPENVR
void App::handle_panel_actions() {
  std::vector<vrm::PanelAction> actions;
  if (!panel_->poll(actions)) {
    // SteamVR is shutting down (quit already acknowledged).
    emit(vrm::json::Writer().str("event", "disconnected").str("reason", "SteamVR requested quit"));
    request_quit(0);
    return;
  }
  for (const vrm::PanelAction& a : actions) {
    switch (a.type) {
      case vrm::PanelAction::Type::TouchDown: send_touch(TouchAction::Down, a.pos, a.pointer_id); break;
      case vrm::PanelAction::Type::TouchMove: send_touch(TouchAction::Move, a.pos, a.pointer_id); break;
      case vrm::PanelAction::Type::TouchUp: send_touch(TouchAction::Up, a.pos, a.pointer_id); break;
      case vrm::PanelAction::Type::Scroll:
        send(vrm::scrcpy::serialize_scroll(a.pos, a.hscroll, a.vscroll));
        break;
      case vrm::PanelAction::Type::Back: send_back(); break;
      case vrm::PanelAction::Type::Home: send_key(vrm::scrcpy::kKeycodeHome); break;
      case vrm::PanelAction::Type::AppSwitch: send_key(vrm::scrcpy::kKeycodeAppSwitch); break;
      case vrm::PanelAction::Type::AudioCast:  // the app casts, and tells us with "audio"
        emit(vrm::json::Writer().str("event", "audio_cast").boolean("on", a.on));
        break;
    }
  }
  // Report pose and size so the app can restore them next session. No pose: in front of the user.
  vrm::panel::Mat34 pose;
  bool in_front = false;
  float long_side = 0.0f;
  if (panel_->take_moved(pose, in_front, long_side)) {
    emit(vrm::json::Writer()
             .str("event", "placement")
             .str("pose", in_front ? "" : vrm::panel::format_pose(pose))
             .number("panel_size_m", long_side, 3));
  }
  bool visible = true;
  if (panel_->take_visibility(visible)) {
    emit(vrm::json::Writer().str("event", "visibility").boolean("visible", visible));
  }
  bool theater = false;
  if (panel_->take_theater(theater)) {
    emit(vrm::json::Writer().str("event", "theater").boolean("on", theater));
  }
}

void App::init_dashbar() {
  auto bar = std::make_unique<vrm::DashBar>();
  std::string error;
  // pid in the key, as for the panel.
  const std::string key = std::string(vrm::VrPanel::kAppKey) + "." + std::to_string(static_cast<long>(::getpid())) +
                          ".dashbar";
  if (!bar->init(panel_->texture(), key, "scrcpy4Frame dashboard buttons", error)) {
    vrm::log_warn("dashbar", "no dashboard buttons: " + error);
    return;
  }
  dashbar_ = std::move(bar);
}

// The button is offered while the session streams with the panel hidden. A click shows the
// panel in front of the user, like the app's Recenter but at once: it's a quick view. In theater
// it shows where it was hidden.
void App::update_dashbar() {
  if (!dashbar_) return;
  static const std::vector<vrm::DashBar::Button> kShowPanel{{kShowPanelButton, vrm::ui::phone_icon}}, kNone;
  const bool offer = dashboard_button_ && first_frame_ && panel_->hidden();
  dashbar_->set_buttons(offer ? kShowPanel : kNone);
  std::vector<std::string> clicked;
  if (!dashbar_->tick(clicked)) {
    emit(vrm::json::Writer().str("event", "disconnected").str("reason", "SteamVR requested quit"));
    request_quit(0);
    return;
  }
  for (const std::string& id : clicked) {
    if (id == kShowPanelButton) {
      vrm::log_info("dashbar", "show panel clicked");
      panel_->reveal();
    }
  }
}
#endif

}  // namespace

int main(int argc, char** argv) {
  Options opt;
  const int early = parse_args(argc, argv, opt);
  if (early >= 0) return early;

  isolate_protocol_stdout();
  install_signal_handlers();
  App app(std::move(opt));
  return app.run(argc > 0 ? argv[0] : nullptr);
}
