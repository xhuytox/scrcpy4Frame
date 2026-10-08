// H.264 -> RGBA decoding, and the mailbox that hands frames from the network thread to the
// main (OpenVR) thread.
#pragma once

#include <cstddef>
#include <cstdint>
#include <mutex>
#include <string>
#include <vector>

struct AVCodecContext;
struct AVFrame;
struct AVPacket;
struct SwsContext;

namespace vrm {

struct RgbaFrame {
  std::vector<uint8_t> pixels;  // width * height * 4, RGBA8, top-down, tightly packed
  uint32_t width = 0;
  uint32_t height = 0;
  uint64_t sequence = 0;        // 1, 2, 3... in decode order
};

// SPSC triple buffer, latest frame wins. Producer and consumer each own a slot and the newest
// finished frame waits in the third. The lock is only held for an index swap.
class FrameMailbox {
 public:
  // Producer side.
  RgbaFrame& back() { return slots_[back_]; }
  void publish();

  // Consumer side. Returns the newest unseen frame or nullptr, valid until the next take().
  const RgbaFrame* take();

  uint64_t dropped() const;  // published frames the consumer never took

 private:
  mutable std::mutex mutex_;
  RgbaFrame slots_[3];
  int back_ = 0;
  int ready_ = 1;
  int front_ = 2;
  bool fresh_ = false;
  uint64_t dropped_ = 0;
};

class H264Decoder {
 public:
  H264Decoder() = default;
  ~H264Decoder();
  H264Decoder(const H264Decoder&) = delete;
  H264Decoder& operator=(const H264Decoder&) = delete;

  bool open(std::string& error);
  void close();

  // Decodes one Annex B access unit, config already merged in, and publishes each picture to
  // `mailbox`. `frames` gets the publish count. Corrupt input is skipped and the next key frame
  // recovers. Returns false only on unrecoverable errors like OOM.
  bool decode(const uint8_t* data, size_t size, int64_t pts, bool key_frame, FrameMailbox& mailbox,
              int* frames, std::string& error);

 private:
  bool convert(FrameMailbox& mailbox, bool* published, std::string& error);

  AVCodecContext* ctx_ = nullptr;
  AVPacket* packet_ = nullptr;
  AVFrame* frame_ = nullptr;
  SwsContext* sws_ = nullptr;
  int sws_w_ = 0;
  int sws_h_ = 0;
  int sws_fmt_ = -1;
  int sws_colorspace_ = -1;
  int sws_full_range_ = -1;
  uint64_t sequence_ = 0;
  uint64_t errors_ = 0;
};

}  // namespace vrm
