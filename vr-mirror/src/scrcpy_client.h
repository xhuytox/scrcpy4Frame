// scrcpy 5.0 client over an adb forward tunnel (tcp:N -> localabstract:scrcpy_<SCID>).
// Connects video, then control, then reads the video stream on its own thread.
#pragma once

#include "scrcpy_protocol.h"

#include <atomic>
#include <cstdint>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace vrm {

class ScrcpyClient {
 public:
  // All callbacks run on the network thread.
  class Sink {
   public:
    virtual ~Sink() = default;
    virtual void on_connected(const std::string& device_name) = 0;
    virtual void on_session(uint32_t width, uint32_t height) = 0;
    // Returning false ends the session with `error` as the reason.
    virtual bool on_packet(scrcpy::StreamEvent& packet, std::string& error) = 0;
    // Called at most once, last, and never after stop().
    virtual void on_ended(const std::string& reason) = 0;
  };

  static constexpr int kConnectTimeoutMs = 10000;
  static constexpr int kRetryDelayMs = 100;

  ScrcpyClient() = default;
  ~ScrcpyClient();
  ScrcpyClient(const ScrcpyClient&) = delete;
  ScrcpyClient& operator=(const ScrcpyClient&) = delete;

  bool start(const std::string& host, uint16_t port, Sink* sink, std::string& error);

  // Closing the sockets makes the server exit. Idempotent.
  void stop();

  // Thread-safe. Returns false if not connected or the write failed.
  bool send(const std::vector<uint8_t>& message);

  bool running() const { return thread_.joinable(); }

 private:
  void run();
  int connect_video(std::string& error);
  int connect_socket(int timeout_ms, std::string& error);
  bool wait_readable(int fd, int timeout_ms, bool* woken);
  bool sleep_ms(int ms);  // false when woken by stop()
  void close_sockets();

  std::string host_;
  uint16_t port_ = 0;
  Sink* sink_ = nullptr;
  std::thread thread_;
  std::atomic<bool> stop_{false};
  int wake_pipe_[2] = {-1, -1};
  int video_fd_ = -1;    // network thread only
  std::mutex send_mutex_;
  int control_fd_ = -1;  // guarded by send_mutex_
};

}  // namespace vrm
