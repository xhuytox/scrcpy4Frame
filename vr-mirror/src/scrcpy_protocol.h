// scrcpy 5.0 wire format, client side. No I/O here.
//
// Video socket (forward tunnel), see scrcpy app/src/server.c and app/src/demuxer.c:
//   [dummy byte 0x00]                     only on the first socket, before anything else
//   [device name: 64 bytes, NUL-padded]
//   [codec id: u32 BE]                    "h264" = 0x68323634; 0 = disabled, 1 = config error
//   then 12-byte headers, each followed by an optional payload:
//     session: MSB set   [u32 flags (bit31 session, bit0 client-resized)][u32 width][u32 height]
//     media:   MSB clear [u64 pts|flags (bit62 config, bit61 key frame)][u32 size] + payload
//
// Control socket: big-endian messages, see scrcpy app/src/control_msg.c.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace vrm::scrcpy {

constexpr uint32_t kCodecIdH264 = 0x68323634;  // "h264"
constexpr uint32_t kCodecIdH265 = 0x68323635;  // "h265"
constexpr uint32_t kCodecIdAv1 = 0x00617631;   // "av1"
constexpr uint32_t kCodecIdDisabled = 0;
constexpr uint32_t kCodecIdConfigError = 1;

constexpr size_t kDeviceNameFieldLength = 64;
constexpr size_t kPacketHeaderSize = 12;
constexpr uint64_t kPacketFlagSession = UINT64_C(1) << 63;
constexpr uint64_t kPacketFlagConfig = UINT64_C(1) << 62;
constexpr uint64_t kPacketFlagKeyFrame = UINT64_C(1) << 61;
constexpr uint64_t kPacketPtsMask = kPacketFlagKeyFrame - 1;
constexpr uint32_t kMaxPacketSize = 32u << 20;  // sanity limit, far above any real access unit

// Same results as scrcpy's util/binary.h.
void write16be(uint8_t* buf, uint16_t value);
void write32be(uint8_t* buf, uint32_t value);
void write64be(uint8_t* buf, uint64_t value);
uint16_t read16be(const uint8_t* buf);
uint32_t read32be(const uint8_t* buf);
uint64_t read64be(const uint8_t* buf);

// [0, 1] -> u16 fixed point, 1.0 -> 0xffff. Clamps out-of-range input and NaN.
uint16_t float_to_u16fp(float f);
// [-1, 1] -> i16 fixed point, 1.0 -> 0x7fff, -1.0 -> -0x8000. Clamps out-of-range input.
int16_t float_to_i16fp(float f);

struct StreamEvent {
  enum class Type { DeviceName, CodecId, Session, Packet };
  Type type = Type::Packet;
  std::string device_name;          // DeviceName
  uint32_t codec_id = 0;            // CodecId
  uint32_t width = 0;               // Session
  uint32_t height = 0;              // Session
  bool client_resized = false;      // Session
  bool config = false;              // Packet: codec config (SPS/PPS), pts is meaningless
  bool key_frame = false;           // Packet
  uint64_t pts = 0;                 // Packet, microseconds. 0 for config packets.
  std::vector<uint8_t> data;        // Packet payload
};

// Push parser for the video socket. Accepts arbitrary chunk boundaries.
class StreamDemuxer {
 public:
  // expect_dummy_byte: the caller hasn't already read the forward-tunnel dummy byte.
  explicit StreamDemuxer(bool expect_dummy_byte);

  // Appends completed events to `out`. Returns false once the stream is malformed, after which
  // error() says why and all further input is rejected.
  bool feed(const uint8_t* data, size_t size, std::vector<StreamEvent>& out);

  bool failed() const { return state_ == State::Failed; }
  const std::string& error() const { return error_; }

 private:
  enum class State { DummyByte, DeviceName, CodecId, Header, Payload, Failed };

  bool fail(std::string message);
  bool handle_field(std::vector<StreamEvent>& out);  // field_ is complete for the current state
  void expect(State state, size_t field_size);

  State state_;
  std::vector<uint8_t> field_;  // accumulates fixed-size fields
  size_t field_size_ = 0;
  StreamEvent packet_;          // media packet being assembled (State::Payload)
  size_t payload_size_ = 0;
  bool seen_session_ = false;
  std::string error_;
};

// Prepends config packets (SPS/PPS) to the next media packet, like scrcpy's packet_merger.c.
// merge() returns false for a config packet, which is held, and true for a packet to decode.
class ConfigPacketMerger {
 public:
  bool merge(StreamEvent& packet);
  bool has_pending_config() const { return has_config_; }

 private:
  std::vector<uint8_t> config_;
  bool has_config_ = false;
};

// Control messages, client -> device.
enum class ControlType : uint8_t {
  InjectKeycode = 0,
  InjectTouchEvent = 2,
  InjectScrollEvent = 3,
  BackOrScreenOn = 4,
  SetDisplayPower = 10,
};

enum class TouchAction : uint8_t { Down = 0, Up = 1, Move = 2 };
enum class KeyAction : uint8_t { Down = 0, Up = 1 };

constexpr uint64_t kPointerIdMouse = UINT64_C(0xFFFFFFFFFFFFFFFF);          // -1
constexpr uint64_t kPointerIdGenericFinger = UINT64_C(0xFFFFFFFFFFFFFFFE);  // -2

// Android key codes used by the panel.
constexpr uint32_t kKeycodeHome = 3;
constexpr uint32_t kKeycodeBack = 4;
constexpr uint32_t kKeycodePower = 26;
constexpr uint32_t kKeycodeAppSwitch = 187;

constexpr size_t kTouchMessageSize = 32;
constexpr size_t kScrollMessageSize = 21;
constexpr size_t kBackMessageSize = 2;
constexpr size_t kKeycodeMessageSize = 14;
constexpr size_t kDisplayPowerMessageSize = 2;

// A point in video coordinates plus the video size it refers to. The server drops events whose
// size doesn't match the current capture, which makes rotation races harmless.
struct Position {
  int32_t x = 0;
  int32_t y = 0;
  uint16_t width = 0;
  uint16_t height = 0;
};

std::vector<uint8_t> serialize_touch(TouchAction action, uint64_t pointer_id, const Position& pos,
                                     float pressure, uint32_t action_button = 0,
                                     uint32_t buttons = 0);
// hscroll/vscroll are wheel clicks in [-16, 16], sent as i16 fixed point of value/16.
std::vector<uint8_t> serialize_scroll(const Position& pos, float hscroll, float vscroll,
                                      uint32_t buttons = 0);
std::vector<uint8_t> serialize_back_or_screen_on(KeyAction action);
std::vector<uint8_t> serialize_keycode(KeyAction action, uint32_t keycode, uint32_t repeat = 0,
                                       uint32_t metastate = 0);
std::vector<uint8_t> serialize_display_power(bool on);

// Clamps (x, y) into [0, width-1] x [0, height-1] and attaches the size.
Position make_position(int64_t x, int64_t y, uint32_t width, uint32_t height);

// Maps an OpenVR laser-mouse point to a clamped touch position. With the overlay mouse scale set
// to the video size, mouse coordinates are pixels from the bottom-left, so y is flipped.
Position map_overlay_point(float mouse_x, float mouse_y, uint32_t width, uint32_t height);

}  // namespace vrm::scrcpy
