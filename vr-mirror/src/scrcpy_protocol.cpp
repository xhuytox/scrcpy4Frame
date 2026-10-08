#include "scrcpy_protocol.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <utility>

namespace vrm::scrcpy {

void write16be(uint8_t* buf, uint16_t value) {
  buf[0] = static_cast<uint8_t>(value >> 8);
  buf[1] = static_cast<uint8_t>(value);
}

void write32be(uint8_t* buf, uint32_t value) {
  buf[0] = static_cast<uint8_t>(value >> 24);
  buf[1] = static_cast<uint8_t>(value >> 16);
  buf[2] = static_cast<uint8_t>(value >> 8);
  buf[3] = static_cast<uint8_t>(value);
}

void write64be(uint8_t* buf, uint64_t value) {
  write32be(buf, static_cast<uint32_t>(value >> 32));
  write32be(buf + 4, static_cast<uint32_t>(value));
}

uint16_t read16be(const uint8_t* buf) {
  return static_cast<uint16_t>((buf[0] << 8) | buf[1]);
}

uint32_t read32be(const uint8_t* buf) {
  return (static_cast<uint32_t>(buf[0]) << 24) | (static_cast<uint32_t>(buf[1]) << 16) |
         (static_cast<uint32_t>(buf[2]) << 8) | static_cast<uint32_t>(buf[3]);
}

uint64_t read64be(const uint8_t* buf) {
  return (static_cast<uint64_t>(read32be(buf)) << 32) | read32be(buf + 4);
}

uint16_t float_to_u16fp(float f) {
  if (!(f >= 0.0f)) f = 0.0f;  // also catches NaN
  if (f > 1.0f) f = 1.0f;
  uint32_t u = static_cast<uint32_t>(f * 65536.0f);  // f * 2^16, truncated like scrcpy
  if (u >= 0xffff) u = 0xffff;                      // 1.0 -> 0x10000 -> 0xffff
  return static_cast<uint16_t>(u);
}

int16_t float_to_i16fp(float f) {
  if (!(f >= -1.0f)) f = -1.0f;  // also catches NaN
  if (f > 1.0f) f = 1.0f;
  int32_t i = static_cast<int32_t>(f * 32768.0f);  // f * 2^15, truncated toward zero like scrcpy
  if (i >= 0x7fff) i = 0x7fff;                     // 1.0 -> 0x8000 -> 0x7fff
  return static_cast<int16_t>(i);                  // -1.0 -> -0x8000
}

StreamDemuxer::StreamDemuxer(bool expect_dummy_byte) {
  if (expect_dummy_byte) {
    expect(State::DummyByte, 1);
  } else {
    expect(State::DeviceName, kDeviceNameFieldLength);
  }
}

void StreamDemuxer::expect(State state, size_t field_size) {
  state_ = state;
  field_size_ = field_size;
  field_.clear();
  field_.reserve(field_size);
}

bool StreamDemuxer::fail(std::string message) {
  state_ = State::Failed;
  error_ = std::move(message);
  field_.clear();
  packet_ = StreamEvent{};
  return false;
}

bool StreamDemuxer::feed(const uint8_t* data, size_t size, std::vector<StreamEvent>& out) {
  if (state_ == State::Failed) return false;
  size_t i = 0;
  while (i < size) {
    if (state_ == State::Payload) {
      const size_t want = payload_size_ - packet_.data.size();
      const size_t n = std::min(want, size - i);
      packet_.data.insert(packet_.data.end(), data + i, data + i + n);
      i += n;
      if (packet_.data.size() == payload_size_) {
        out.push_back(std::move(packet_));
        packet_ = StreamEvent{};
        expect(State::Header, kPacketHeaderSize);
      }
      continue;
    }
    const size_t want = field_size_ - field_.size();
    const size_t n = std::min(want, size - i);
    field_.insert(field_.end(), data + i, data + i + n);
    i += n;
    if (field_.size() == field_size_ && !handle_field(out)) return false;
  }
  return true;
}

bool StreamDemuxer::handle_field(std::vector<StreamEvent>& out) {
  switch (state_) {
    case State::DummyByte:
      // Value ignored, as in scrcpy.
      expect(State::DeviceName, kDeviceNameFieldLength);
      return true;

    case State::DeviceName: {
      StreamEvent ev;
      ev.type = StreamEvent::Type::DeviceName;
      // Force NUL termination like scrcpy.
      field_[kDeviceNameFieldLength - 1] = 0;
      const auto nul = std::find(field_.begin(), field_.end(), uint8_t{0});
      ev.device_name.assign(field_.begin(), nul);
      out.push_back(std::move(ev));
      expect(State::CodecId, 4);
      return true;
    }

    case State::CodecId: {
      StreamEvent ev;
      ev.type = StreamEvent::Type::CodecId;
      ev.codec_id = read32be(field_.data());
      out.push_back(std::move(ev));
      expect(State::Header, kPacketHeaderSize);
      return true;
    }

    case State::Header: {
      const uint8_t* h = field_.data();
      if (h[0] & 0x80) {
        StreamEvent ev;
        ev.type = StreamEvent::Type::Session;
        ev.client_resized = (h[3] & 1) != 0;
        ev.width = read32be(h + 4);
        ev.height = read32be(h + 8);
        if (ev.width == 0 || ev.height == 0 || ev.width > 0xFFFF || ev.height > 0xFFFF) {
          return fail("invalid session video size " + std::to_string(ev.width) + "x" +
                      std::to_string(ev.height));
        }
        seen_session_ = true;
        out.push_back(std::move(ev));
        expect(State::Header, kPacketHeaderSize);
        return true;
      }
      if (!seen_session_) return fail("media packet before the first session packet");
      const uint64_t pts_flags = read64be(h);
      const uint32_t len = read32be(h + 8);
      if (len == 0) return fail("invalid packet length 0");
      if (len > kMaxPacketSize) {
        return fail("packet length " + std::to_string(len) + " exceeds the sanity limit");
      }
      packet_ = StreamEvent{};
      packet_.type = StreamEvent::Type::Packet;
      packet_.config = (pts_flags & kPacketFlagConfig) != 0;
      packet_.key_frame = (pts_flags & kPacketFlagKeyFrame) != 0;
      packet_.pts = packet_.config ? 0 : (pts_flags & kPacketPtsMask);
      packet_.data.reserve(len);
      payload_size_ = len;
      state_ = State::Payload;
      field_.clear();
      return true;
    }

    case State::Payload:
    case State::Failed:
      break;
  }
  return fail("internal demuxer state error");
}

bool ConfigPacketMerger::merge(StreamEvent& packet) {
  if (packet.config) {
    config_ = packet.data;
    has_config_ = true;
    return false;
  }
  if (has_config_) {
    packet.data.insert(packet.data.begin(), config_.begin(), config_.end());
    config_.clear();
    has_config_ = false;
  }
  return true;
}

namespace {

void write_position(uint8_t* buf, const Position& pos) {
  write32be(buf, static_cast<uint32_t>(pos.x));
  write32be(buf + 4, static_cast<uint32_t>(pos.y));
  write16be(buf + 8, pos.width);
  write16be(buf + 10, pos.height);
}

}  // namespace

std::vector<uint8_t> serialize_touch(TouchAction action, uint64_t pointer_id, const Position& pos,
                                     float pressure, uint32_t action_button, uint32_t buttons) {
  std::vector<uint8_t> b(kTouchMessageSize);
  b[0] = static_cast<uint8_t>(ControlType::InjectTouchEvent);
  b[1] = static_cast<uint8_t>(action);
  write64be(&b[2], pointer_id);
  write_position(&b[10], pos);
  write16be(&b[22], float_to_u16fp(pressure));
  write32be(&b[24], action_button);
  write32be(&b[28], buttons);
  return b;
}

std::vector<uint8_t> serialize_scroll(const Position& pos, float hscroll, float vscroll,
                                      uint32_t buttons) {
  std::vector<uint8_t> b(kScrollMessageSize);
  b[0] = static_cast<uint8_t>(ControlType::InjectScrollEvent);
  write_position(&b[1], pos);
  const float h = std::clamp(hscroll / 16.0f, -1.0f, 1.0f);
  const float v = std::clamp(vscroll / 16.0f, -1.0f, 1.0f);
  write16be(&b[13], static_cast<uint16_t>(float_to_i16fp(h)));
  write16be(&b[15], static_cast<uint16_t>(float_to_i16fp(v)));
  write32be(&b[17], buttons);
  return b;
}

std::vector<uint8_t> serialize_back_or_screen_on(KeyAction action) {
  return {static_cast<uint8_t>(ControlType::BackOrScreenOn), static_cast<uint8_t>(action)};
}

std::vector<uint8_t> serialize_keycode(KeyAction action, uint32_t keycode, uint32_t repeat,
                                       uint32_t metastate) {
  std::vector<uint8_t> b(kKeycodeMessageSize);
  b[0] = static_cast<uint8_t>(ControlType::InjectKeycode);
  b[1] = static_cast<uint8_t>(action);
  write32be(&b[2], keycode);
  write32be(&b[6], repeat);
  write32be(&b[10], metastate);
  return b;
}

std::vector<uint8_t> serialize_display_power(bool on) {
  return {static_cast<uint8_t>(ControlType::SetDisplayPower), static_cast<uint8_t>(on ? 1 : 0)};
}

Position make_position(int64_t x, int64_t y, uint32_t width, uint32_t height) {
  Position p;
  p.width = static_cast<uint16_t>(std::min<uint32_t>(width, 0xFFFF));
  p.height = static_cast<uint16_t>(std::min<uint32_t>(height, 0xFFFF));
  if (p.width == 0 || p.height == 0) return p;
  p.x = static_cast<int32_t>(std::clamp<int64_t>(x, 0, p.width - 1));
  p.y = static_cast<int32_t>(std::clamp<int64_t>(y, 0, p.height - 1));
  return p;
}

Position map_overlay_point(float mouse_x, float mouse_y, uint32_t width, uint32_t height) {
  if (width == 0 || height == 0) return make_position(0, 0, width, height);
  const float max_x = static_cast<float>(width - 1);
  const float max_y = static_cast<float>(height - 1);
  float x = std::isfinite(mouse_x) ? mouse_x : 0.0f;
  float y = std::isfinite(mouse_y) ? static_cast<float>(height) - mouse_y : 0.0f;
  x = std::clamp(x, 0.0f, max_x);  // clamp before converting: no float->int overflow
  y = std::clamp(y, 0.0f, max_y);
  return make_position(static_cast<int64_t>(std::floor(x)), static_cast<int64_t>(std::floor(y)),
                       width, height);
}

}  // namespace vrm::scrcpy
