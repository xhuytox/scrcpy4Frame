#include "scrcpy_client.h"

#include "log.h"

#include <arpa/inet.h>
#include <fcntl.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <cstdio>
#include <chrono>
#include <cstring>

namespace vrm {

namespace {

using Clock = std::chrono::steady_clock;

#ifdef MSG_NOSIGNAL
constexpr int kSendFlags = MSG_NOSIGNAL;
#else
constexpr int kSendFlags = 0;  // macOS: SO_NOSIGPIPE is set on the socket instead
#endif

constexpr size_t kRecvChunk = 256u << 10;
constexpr int kMinAttemptMs = 250;

std::string errno_string(int err) { return std::strerror(err); }

void set_cloexec(int fd) {
  const int flags = ::fcntl(fd, F_GETFD);
  if (flags >= 0) ::fcntl(fd, F_SETFD, flags | FD_CLOEXEC);
}

bool set_nonblocking(int fd, bool on) {
  const int flags = ::fcntl(fd, F_GETFL);
  if (flags < 0) return false;
  return ::fcntl(fd, F_SETFL, on ? (flags | O_NONBLOCK) : (flags & ~O_NONBLOCK)) == 0;
}

int remaining_ms(Clock::time_point deadline) {
  const auto left = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - Clock::now());
  return static_cast<int>(std::max<long long>(0, left.count()));
}

const char* codec_name(uint32_t id) {
  switch (id) {
    case scrcpy::kCodecIdH264: return "h264";
    case scrcpy::kCodecIdH265: return "h265";
    case scrcpy::kCodecIdAv1: return "av1";
    default: return "unknown";
  }
}

}  // namespace

ScrcpyClient::~ScrcpyClient() { stop(); }

bool ScrcpyClient::start(const std::string& host, uint16_t port, Sink* sink, std::string& error) {
  if (thread_.joinable()) {
    error = "already started";
    return false;
  }
  if (::pipe(wake_pipe_) != 0) {
    error = "pipe: " + errno_string(errno);
    return false;
  }
  for (int fd : wake_pipe_) {
    set_cloexec(fd);
    set_nonblocking(fd, true);
  }
  host_ = host;
  port_ = port;
  sink_ = sink;
  stop_ = false;
  thread_ = std::thread([this] { run(); });
  return true;
}

void ScrcpyClient::stop() {
  stop_ = true;
  if (wake_pipe_[1] >= 0) {
    const char b = 'x';
    (void)!::write(wake_pipe_[1], &b, 1);
  }
  {
    // Unblock a network thread stuck in recv/connect on the control socket.
    std::lock_guard<std::mutex> lock(send_mutex_);
    if (control_fd_ >= 0) ::shutdown(control_fd_, SHUT_RDWR);
  }
  if (thread_.joinable()) thread_.join();
  close_sockets();
  for (int& fd : wake_pipe_) {
    if (fd >= 0) ::close(fd);
    fd = -1;
  }
}

bool ScrcpyClient::send(const std::vector<uint8_t>& message) {
  std::lock_guard<std::mutex> lock(send_mutex_);
  if (control_fd_ < 0) return false;
  size_t off = 0;
  while (off < message.size()) {
    const ssize_t n = ::send(control_fd_, message.data() + off, message.size() - off, kSendFlags);
    if (n < 0) {
      if (errno == EINTR) continue;
      log_warn("control", "send failed: " + errno_string(errno));
      return false;
    }
    off += static_cast<size_t>(n);
  }
  return true;
}

void ScrcpyClient::close_sockets() {
  if (video_fd_ >= 0) ::close(video_fd_);
  video_fd_ = -1;
  std::lock_guard<std::mutex> lock(send_mutex_);
  if (control_fd_ >= 0) ::close(control_fd_);
  control_fd_ = -1;
}

bool ScrcpyClient::wait_readable(int fd, int timeout_ms, bool* woken) {
  *woken = false;
  pollfd pfds[2] = {{fd, POLLIN, 0}, {wake_pipe_[0], POLLIN, 0}};
  for (;;) {
    const int r = ::poll(pfds, 2, timeout_ms);
    if (r < 0 && errno == EINTR) {
      if (stop_) {
        *woken = true;
        return false;
      }
      continue;
    }
    if (r <= 0) return false;
    if (pfds[1].revents) {
      *woken = true;
      return false;
    }
    return pfds[0].revents != 0;
  }
}

bool ScrcpyClient::sleep_ms(int ms) {
  pollfd pfd{wake_pipe_[0], POLLIN, 0};
  const int r = ::poll(&pfd, 1, ms);
  return !(r > 0 || stop_);
}

// Tries each resolved address within `timeout_ms`, interruptible by stop(). Returns a blocking
// socket or -1.
int ScrcpyClient::connect_socket(int timeout_ms, std::string& error) {
  addrinfo hints{};
  hints.ai_family = AF_UNSPEC;
  hints.ai_socktype = SOCK_STREAM;
  hints.ai_flags = AI_NUMERICSERV;
  addrinfo* res = nullptr;
  const std::string service = std::to_string(port_);
  const int gai = ::getaddrinfo(host_.c_str(), service.c_str(), &hints, &res);
  if (gai != 0) {
    error = std::string("cannot resolve ") + host_ + ": " + ::gai_strerror(gai);
    return -1;
  }
  const Clock::time_point deadline = Clock::now() + std::chrono::milliseconds(timeout_ms);
  int result = -1;
  for (addrinfo* ai = res; ai && result < 0 && !stop_; ai = ai->ai_next) {
    const int fd = ::socket(ai->ai_family, ai->ai_socktype, ai->ai_protocol);
    if (fd < 0) {
      error = "socket: " + errno_string(errno);
      continue;
    }
    set_cloexec(fd);
#ifdef SO_NOSIGPIPE
    const int one = 1;
    ::setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof one);
#endif
    set_nonblocking(fd, true);
    int r = ::connect(fd, ai->ai_addr, ai->ai_addrlen);
    if (r < 0 && errno == EINPROGRESS) {
      pollfd pfds[2] = {{fd, POLLOUT, 0}, {wake_pipe_[0], POLLIN, 0}};
      int pr;
      do {
        pr = ::poll(pfds, 2, remaining_ms(deadline));
      } while (pr < 0 && errno == EINTR && !stop_);
      if (pr > 0 && pfds[1].revents == 0) {
        int so_error = 0;
        socklen_t len = sizeof so_error;
        ::getsockopt(fd, SOL_SOCKET, SO_ERROR, &so_error, &len);
        r = so_error == 0 ? 0 : -1;
        errno = so_error;
      } else {
        r = -1;
        errno = pr == 0 ? ETIMEDOUT : EINTR;
      }
    }
    if (r == 0 && set_nonblocking(fd, false)) {
      result = fd;
    } else {
      error = "connect: " + errno_string(errno);
      ::close(fd);
    }
  }
  ::freeaddrinfo(res);
  return result;
}

// adb accepts the forwarded connection even before the server listens, then closes it without
// sending anything. Retry until the server's dummy byte arrives.
int ScrcpyClient::connect_video(std::string& error) {
  const Clock::time_point deadline = Clock::now() + std::chrono::milliseconds(kConnectTimeoutMs);
  std::string last_error = "no attempt";
  int attempts = 0;
  while (!stop_) {
    ++attempts;
    // At least kMinAttemptMs so the last attempt reports the real error, not a deadline timeout.
    const int budget = std::max(remaining_ms(deadline), kMinAttemptMs);
    const int fd = connect_socket(budget, last_error);
    if (fd >= 0) {
      bool woken = false;
      if (wait_readable(fd, std::max(remaining_ms(deadline), kMinAttemptMs), &woken)) {
        uint8_t dummy = 0;
        ssize_t n;
        do {
          n = ::recv(fd, &dummy, 1, 0);
        } while (n < 0 && errno == EINTR);
        if (n == 1) {
          log_info("net", "video socket connected after " + std::to_string(attempts) + " attempt(s)");
          return fd;
        }
        last_error = n == 0 ? "connection closed before the server's first byte (server not ready)"
                            : "recv: " + errno_string(errno);
      } else if (!woken) {
        last_error = "timed out waiting for the server's first byte";
      }
      ::close(fd);
    }
    if (stop_ || remaining_ms(deadline) == 0) break;
    if (!sleep_ms(std::min(kRetryDelayMs, remaining_ms(deadline)))) break;
  }
  error = "could not connect to " + host_ + ":" + std::to_string(port_) + " within " +
          std::to_string(kConnectTimeoutMs / 1000) + " s (" + std::to_string(attempts) +
          " attempts, last error: " + last_error + ")";
  return -1;
}

void ScrcpyClient::run() {
  std::string reason;
  auto end = [&](const std::string& why) {
    close_sockets();
    if (!stop_) sink_->on_ended(why);
  };

  video_fd_ = connect_video(reason);
  if (video_fd_ < 0) return end(reason);

  // The server sends nothing more on video until control is connected.
  const int control = connect_socket(3000, reason);
  if (control < 0) return end("control socket: " + reason);
  {
    const int one = 1;
    ::setsockopt(control, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
    timeval tv{};
    tv.tv_sec = 1;  // never block the main thread for long if the device stops reading
    ::setsockopt(control, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
    std::lock_guard<std::mutex> lock(send_mutex_);
    control_fd_ = control;
  }
  if (stop_) return end("stopped");

  scrcpy::StreamDemuxer demuxer(/*expect_dummy_byte=*/false);
  std::vector<scrcpy::StreamEvent> events;
  std::vector<uint8_t> buf(kRecvChunk);
  bool control_open = true;

  while (!stop_) {
    pollfd pfds[3] = {{video_fd_, POLLIN, 0}, {wake_pipe_[0], POLLIN, 0}, {control, POLLIN, 0}};
    const int r = ::poll(pfds, control_open ? 3 : 2, -1);
    if (r < 0) {
      if (errno == EINTR) continue;
      return end("poll: " + errno_string(errno));
    }
    if (pfds[1].revents) break;  // stop()

    if (control_open && pfds[2].revents) {
      // Drain unused device messages (clipboard, acks) so the server's sender never blocks.
      const ssize_t n = ::recv(control, buf.data(), buf.size(), 0);
      if (n == 0 || (n < 0 && errno != EINTR && errno != EAGAIN)) {
        log_info("net", "control socket closed by the device");
        control_open = false;
      }
    }

    if (pfds[0].revents == 0) continue;
    const ssize_t n = ::recv(video_fd_, buf.data(), buf.size(), 0);
    if (n == 0) return end("connection closed by the device");
    if (n < 0) {
      if (errno == EINTR || errno == EAGAIN) continue;
      return end("video socket: " + errno_string(errno));
    }
    events.clear();
    const bool ok = demuxer.feed(buf.data(), static_cast<size_t>(n), events);
    for (scrcpy::StreamEvent& ev : events) {
      if (stop_) break;
      switch (ev.type) {
        case scrcpy::StreamEvent::Type::DeviceName:
          sink_->on_connected(ev.device_name);
          break;
        case scrcpy::StreamEvent::Type::CodecId:
          if (ev.codec_id == scrcpy::kCodecIdDisabled) return end("video disabled by the device");
          if (ev.codec_id == scrcpy::kCodecIdConfigError) {
            return end("video configuration error on the device");
          }
          if (ev.codec_id != scrcpy::kCodecIdH264) {
            char hex[16];
            std::snprintf(hex, sizeof hex, "0x%08x", ev.codec_id);
            return end(std::string("unsupported video codec ") + codec_name(ev.codec_id) + " (" + hex +
                       "); start the server with video_codec=h264");
          }
          break;
        case scrcpy::StreamEvent::Type::Session:
          sink_->on_session(ev.width, ev.height);
          break;
        case scrcpy::StreamEvent::Type::Packet: {
          std::string error;
          if (!sink_->on_packet(ev, error)) return end(error);
          break;
        }
      }
    }
    if (!ok) return end("protocol error: " + demuxer.error());
  }
  end("stopped");
}

}  // namespace vrm
