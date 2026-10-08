#include "decoder.h"

#include "log.h"

#include <cstdlib>
#include <cstring>
#include <utility>

extern "C" {
#include <libavcodec/avcodec.h>
#include <libavutil/error.h>
#include <libavutil/frame.h>
#include <libavutil/log.h>
#include <libavutil/pixfmt.h>
#include <libswscale/swscale.h>
}

namespace vrm {

namespace {

std::string av_error_string(int err) {
  char buf[AV_ERROR_MAX_STRING_SIZE] = {0};
  av_strerror(err, buf, sizeof buf);
  return buf;
}

}  // namespace

void FrameMailbox::publish() {
  std::lock_guard<std::mutex> lock(mutex_);
  if (fresh_) ++dropped_;
  std::swap(back_, ready_);
  fresh_ = true;
}

const RgbaFrame* FrameMailbox::take() {
  std::lock_guard<std::mutex> lock(mutex_);
  if (!fresh_) return nullptr;
  std::swap(front_, ready_);
  fresh_ = false;
  return &slots_[front_];
}

uint64_t FrameMailbox::dropped() const {
  std::lock_guard<std::mutex> lock(mutex_);
  return dropped_;
}

H264Decoder::~H264Decoder() { close(); }

bool H264Decoder::open(std::string& error) {
  close();
  const char* debug = std::getenv("VR_MIRROR_DEBUG");
  av_log_set_level(debug && *debug && std::strcmp(debug, "0") != 0 ? AV_LOG_INFO : AV_LOG_ERROR);

  const AVCodec* codec = avcodec_find_decoder(AV_CODEC_ID_H264);
  if (!codec) {
    error = "FFmpeg has no H.264 decoder";
    return false;
  }
  ctx_ = avcodec_alloc_context3(codec);
  packet_ = av_packet_alloc();
  frame_ = av_frame_alloc();
  if (!ctx_ || !packet_ || !frame_) {
    error = "out of memory allocating the decoder";
    close();
    return false;
  }
  ctx_->flags |= AV_CODEC_FLAG_LOW_DELAY;  // output each picture immediately, like scrcpy
  ctx_->thread_count = 1;                  // frame threading would add a frame of latency
  const int r = avcodec_open2(ctx_, codec, nullptr);
  if (r < 0) {
    error = "avcodec_open2 failed: " + av_error_string(r);
    close();
    return false;
  }
  return true;
}

void H264Decoder::close() {
  if (sws_) sws_freeContext(sws_);
  sws_ = nullptr;
  if (frame_) av_frame_free(&frame_);
  if (packet_) av_packet_free(&packet_);
  if (ctx_) avcodec_free_context(&ctx_);
  sws_w_ = sws_h_ = 0;
  sws_fmt_ = sws_colorspace_ = sws_full_range_ = -1;
}

bool H264Decoder::decode(const uint8_t* data, size_t size, int64_t pts, bool key_frame,
                         FrameMailbox& mailbox, int* frames, std::string& error) {
  *frames = 0;
  if (!ctx_) {
    error = "decoder not open";
    return false;
  }
  if (size == 0 || size > static_cast<size_t>(INT32_MAX - AV_INPUT_BUFFER_PADDING_SIZE)) return true;

  av_packet_unref(packet_);
  if (av_new_packet(packet_, static_cast<int>(size)) < 0) {  // zeroed padding included
    error = "out of memory allocating a packet";
    return false;
  }
  std::memcpy(packet_->data, data, size);
  packet_->pts = pts;
  packet_->dts = pts;
  if (key_frame) packet_->flags |= AV_PKT_FLAG_KEY;

  int r = avcodec_send_packet(ctx_, packet_);
  av_packet_unref(packet_);
  if (r == AVERROR(ENOMEM)) {
    error = "decoder out of memory";
    return false;
  }
  if (r < 0 && r != AVERROR(EAGAIN)) {
    // Corrupt data, or no key frame yet. Skip it.
    if (errors_++ % 100 == 0) log_warn("decoder", "avcodec_send_packet: " + av_error_string(r));
    return true;
  }

  for (;;) {
    r = avcodec_receive_frame(ctx_, frame_);
    if (r == AVERROR(EAGAIN) || r == AVERROR_EOF) break;
    if (r < 0) {
      if (errors_++ % 100 == 0) log_warn("decoder", "avcodec_receive_frame: " + av_error_string(r));
      break;
    }
    bool published = false;
    const bool ok = convert(mailbox, &published, error);
    av_frame_unref(frame_);
    if (!ok) return false;
    if (published) ++*frames;
  }
  return true;
}

bool H264Decoder::convert(FrameMailbox& mailbox, bool* published, std::string& error) {
  *published = false;
  const int w = frame_->width;
  const int h = frame_->height;
  const int fmt = frame_->format;
  if (w <= 0 || h <= 0 || w > 16384 || h > 16384) return true;  // nothing sensible to show

  int colorspace = SWS_CS_DEFAULT;  // BT.601, what Android encoders use unless told otherwise
  if (frame_->colorspace == AVCOL_SPC_BT709) colorspace = SWS_CS_ITU709;
  else if (frame_->colorspace == AVCOL_SPC_BT2020_NCL || frame_->colorspace == AVCOL_SPC_BT2020_CL)
    colorspace = SWS_CS_BT2020;
  const int full_range =
      (frame_->color_range == AVCOL_RANGE_JPEG || fmt == AV_PIX_FMT_YUVJ420P) ? 1 : 0;

  if (!sws_ || w != sws_w_ || h != sws_h_ || fmt != sws_fmt_ || colorspace != sws_colorspace_ ||
      full_range != sws_full_range_) {
    // Rebuilt on rotation or format change.
    sws_ = sws_getCachedContext(sws_, w, h, static_cast<AVPixelFormat>(fmt), w, h, AV_PIX_FMT_RGBA,
                                SWS_BILINEAR, nullptr, nullptr, nullptr);
    if (!sws_) {
      error = "swscale cannot convert pixel format " + std::to_string(fmt) + " to RGBA";
      return false;
    }
    sws_setColorspaceDetails(sws_, sws_getCoefficients(colorspace), full_range,
                             sws_getCoefficients(SWS_CS_DEFAULT), 1, 0, 1 << 16, 1 << 16);
    sws_w_ = w;
    sws_h_ = h;
    sws_fmt_ = fmt;
    sws_colorspace_ = colorspace;
    sws_full_range_ = full_range;
  }

  RgbaFrame& out = mailbox.back();
  out.pixels.resize(static_cast<size_t>(w) * static_cast<size_t>(h) * 4u);
  out.width = static_cast<uint32_t>(w);
  out.height = static_cast<uint32_t>(h);
  out.sequence = ++sequence_;
  uint8_t* dst[4] = {out.pixels.data(), nullptr, nullptr, nullptr};
  int dst_stride[4] = {w * 4, 0, 0, 0};
  const int rows = sws_scale(sws_, frame_->data, frame_->linesize, 0, h, dst, dst_stride);
  if (rows != h) {
    if (errors_++ % 100 == 0) log_warn("decoder", "sws_scale converted " + std::to_string(rows) + " rows");
    return true;  // drop this picture, keep going
  }
  mailbox.publish();
  *published = true;
  return true;
}

}  // namespace vrm
