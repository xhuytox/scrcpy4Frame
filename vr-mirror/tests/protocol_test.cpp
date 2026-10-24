// Unit tests for vr-mirror's I/O-free code. Control messages are checked byte for byte against
// scrcpy 5.0's own test vectors. Exit status 0 means every check passed.

#include "dash_math.h"
#include "panel_chrome.h"
#include "json_lite.h"
#include "panel_math.h"
#include "scrcpy_protocol.h"
#include "ui_draw.h"

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <limits>
#include <random>
#include <string>
#include <vector>

namespace {

int g_checks = 0;
int g_failures = 0;

#define CHECK(cond)                                                              \
  do {                                                                           \
    ++g_checks;                                                                  \
    if (!(cond)) {                                                               \
      ++g_failures;                                                              \
      std::fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
    }                                                                            \
  } while (0)

#define CHECK_EQ(a, b)                                                                     \
  do {                                                                                     \
    ++g_checks;                                                                            \
    const auto va_ = (a);                                                                  \
    const auto vb_ = (b);                                                                  \
    if (!(va_ == vb_)) {                                                                   \
      ++g_failures;                                                                        \
      std::fprintf(stderr, "%s:%d: CHECK_EQ failed: %s == %s (%lld vs %lld)\n", __FILE__, \
                   __LINE__, #a, #b, static_cast<long long>(va_), static_cast<long long>(vb_)); \
    }                                                                                      \
  } while (0)

#define CHECK_NEAR(a, b, eps)                                                                  \
  do {                                                                                         \
    ++g_checks;                                                                                \
    const double va_ = (a);                                                                    \
    const double vb_ = (b);                                                                    \
    if (!(std::fabs(va_ - vb_) <= (eps))) {                                                    \
      ++g_failures;                                                                            \
      std::fprintf(stderr, "%s:%d: CHECK_NEAR failed: %s ~ %s (%g vs %g)\n", __FILE__, __LINE__, \
                   #a, #b, va_, vb_);                                                          \
    }                                                                                          \
  } while (0)

std::string hex(const std::vector<uint8_t>& v) {
  std::string s;
  char buf[4];
  for (uint8_t b : v) {
    std::snprintf(buf, sizeof buf, "%02x", b);
    s += buf;
  }
  return s;
}

#define CHECK_BYTES(actual, ...)                                                             \
  do {                                                                                       \
    ++g_checks;                                                                              \
    const std::vector<uint8_t> act_ = (actual);                                              \
    const std::vector<uint8_t> exp_ = __VA_ARGS__;                                           \
    if (act_ != exp_) {                                                                      \
      ++g_failures;                                                                          \
      std::fprintf(stderr, "%s:%d: bytes differ\n  actual:   %s\n  expected: %s\n", __FILE__, \
                   __LINE__, hex(act_).c_str(), hex(exp_).c_str());                          \
    }                                                                                        \
  } while (0)

using namespace vrm;
using scrcpy::StreamEvent;

void test_fixed_point() {
  // Vectors from scrcpy 5.0 app/tests/test_binary.c.
  CHECK_EQ(scrcpy::float_to_u16fp(0.0f), 0);
  CHECK_EQ(scrcpy::float_to_u16fp(0.03125f), 0x800);
  CHECK_EQ(scrcpy::float_to_u16fp(0.0625f), 0x1000);
  CHECK_EQ(scrcpy::float_to_u16fp(0.125f), 0x2000);
  CHECK_EQ(scrcpy::float_to_u16fp(0.25f), 0x4000);
  CHECK_EQ(scrcpy::float_to_u16fp(0.5f), 0x8000);
  CHECK_EQ(scrcpy::float_to_u16fp(0.75f), 0xc000);
  CHECK_EQ(scrcpy::float_to_u16fp(1.0f), 0xffff);

  CHECK_EQ(scrcpy::float_to_i16fp(0.0f), 0);
  CHECK_EQ(scrcpy::float_to_i16fp(0.03125f), 0x400);
  CHECK_EQ(scrcpy::float_to_i16fp(0.0625f), 0x800);
  CHECK_EQ(scrcpy::float_to_i16fp(0.125f), 0x1000);
  CHECK_EQ(scrcpy::float_to_i16fp(0.25f), 0x2000);
  CHECK_EQ(scrcpy::float_to_i16fp(0.5f), 0x4000);
  CHECK_EQ(scrcpy::float_to_i16fp(0.75f), 0x6000);
  CHECK_EQ(scrcpy::float_to_i16fp(1.0f), 0x7fff);
  CHECK_EQ(scrcpy::float_to_i16fp(-0.03125f), -0x400);
  CHECK_EQ(scrcpy::float_to_i16fp(-0.5f), -0x4000);
  CHECK_EQ(scrcpy::float_to_i16fp(-0.75f), -0x6000);
  CHECK_EQ(scrcpy::float_to_i16fp(-1.0f), -0x8000);  // not -0x7fff, matches scrcpy

  // scrcpy asserts on out-of-range input. We clamp.
  CHECK_EQ(scrcpy::float_to_u16fp(2.0f), 0xffff);
  CHECK_EQ(scrcpy::float_to_u16fp(-1.0f), 0);
  CHECK_EQ(scrcpy::float_to_u16fp(std::numeric_limits<float>::quiet_NaN()), 0);
  CHECK_EQ(scrcpy::float_to_i16fp(5.0f), 0x7fff);
  CHECK_EQ(scrcpy::float_to_i16fp(-5.0f), -0x8000);
}

void test_byte_order() {
  uint8_t b[8];
  scrcpy::write16be(b, 0xABCD);
  CHECK_EQ(b[0], 0xAB);
  CHECK_EQ(b[1], 0xCD);
  CHECK_EQ(scrcpy::read16be(b), 0xABCD);
  scrcpy::write32be(b, 0xABCD1234);
  CHECK_EQ(scrcpy::read32be(b), 0xABCD1234u);
  scrcpy::write64be(b, UINT64_C(0xABCD1234567890EF));
  CHECK(scrcpy::read64be(b) == UINT64_C(0xABCD1234567890EF));
  CHECK_EQ(b[0], 0xAB);
  CHECK_EQ(b[7], 0xEF);
}

void put32(std::vector<uint8_t>& v, uint32_t x) {
  uint8_t b[4];
  scrcpy::write32be(b, x);
  v.insert(v.end(), b, b + 4);
}

void put64(std::vector<uint8_t>& v, uint64_t x) {
  uint8_t b[8];
  scrcpy::write64be(b, x);
  v.insert(v.end(), b, b + 8);
}

void put_session(std::vector<uint8_t>& v, uint32_t w, uint32_t h, bool resized) {
  put32(v, 0x80000000u | (resized ? 1u : 0u));
  put32(v, w);
  put32(v, h);
}

void put_packet(std::vector<uint8_t>& v, uint64_t pts_flags, const std::vector<uint8_t>& payload) {
  put64(v, pts_flags);
  put32(v, static_cast<uint32_t>(payload.size()));
  v.insert(v.end(), payload.begin(), payload.end());
}

std::vector<uint8_t> payload(uint8_t seed, size_t n) {
  std::vector<uint8_t> p(n);
  for (size_t i = 0; i < n; ++i) p[i] = static_cast<uint8_t>(seed + i * 7);
  return p;
}

// A full video stream as the scrcpy 5.0 server writes it over the forward tunnel.
std::vector<uint8_t> sample_stream(bool with_dummy) {
  std::vector<uint8_t> s;
  if (with_dummy) s.push_back(0x00);
  const char name[] = "Pixel 8";
  std::vector<uint8_t> field(64, 0);
  std::memcpy(field.data(), name, sizeof name - 1);
  s.insert(s.end(), field.begin(), field.end());
  put32(s, scrcpy::kCodecIdH264);
  put_session(s, 864, 1920, false);
  put_packet(s, scrcpy::kPacketFlagConfig, payload(1, 23));                    // SPS/PPS
  put_packet(s, scrcpy::kPacketFlagKeyFrame | 1000, payload(2, 5000));         // IDR
  put_packet(s, 34333, payload(3, 777));                                       // P frame
  put_session(s, 1920, 864, true);                                             // rotation
  put_packet(s, scrcpy::kPacketFlagConfig, payload(4, 19));
  put_packet(s, scrcpy::kPacketFlagKeyFrame | UINT64_C(123456789012), payload(5, 70000));
  put_packet(s, 2, payload(6, 1));
  return s;
}

void check_sample_events(const std::vector<StreamEvent>& ev) {
  CHECK_EQ(ev.size(), 10u);
  if (ev.size() != 10) return;
  CHECK(ev[0].type == StreamEvent::Type::DeviceName);
  CHECK(ev[0].device_name == "Pixel 8");
  CHECK(ev[1].type == StreamEvent::Type::CodecId);
  CHECK_EQ(ev[1].codec_id, scrcpy::kCodecIdH264);
  CHECK(ev[2].type == StreamEvent::Type::Session);
  CHECK_EQ(ev[2].width, 864u);
  CHECK_EQ(ev[2].height, 1920u);
  CHECK(!ev[2].client_resized);
  CHECK(ev[3].type == StreamEvent::Type::Packet);
  CHECK(ev[3].config);
  CHECK(!ev[3].key_frame);
  CHECK(ev[3].data == payload(1, 23));
  CHECK(ev[4].type == StreamEvent::Type::Packet);
  CHECK(!ev[4].config);
  CHECK(ev[4].key_frame);
  CHECK_EQ(ev[4].pts, 1000u);
  CHECK(ev[4].data == payload(2, 5000));
  CHECK(!ev[5].config);
  CHECK(!ev[5].key_frame);
  CHECK_EQ(ev[5].pts, 34333u);
  CHECK(ev[5].data == payload(3, 777));
  CHECK(ev[6].type == StreamEvent::Type::Session);
  CHECK_EQ(ev[6].width, 1920u);
  CHECK_EQ(ev[6].height, 864u);
  CHECK(ev[6].client_resized);
  CHECK(ev[7].config);
  CHECK(ev[7].data == payload(4, 19));
  CHECK(ev[8].key_frame);
  CHECK(ev[8].pts == UINT64_C(123456789012));
  CHECK(ev[8].data == payload(5, 70000));
  CHECK_EQ(ev[9].pts, 2u);
  CHECK(ev[9].data == payload(6, 1));
}

void test_demuxer_whole_and_split() {
  const std::vector<uint8_t> s = sample_stream(true);

  {  // all at once
    scrcpy::StreamDemuxer d(true);
    std::vector<StreamEvent> ev;
    CHECK(d.feed(s.data(), s.size(), ev));
    CHECK(!d.failed());
    check_sample_events(ev);
  }
  {  // byte by byte
    scrcpy::StreamDemuxer d(true);
    std::vector<StreamEvent> ev;
    bool ok = true;
    for (uint8_t b : s) ok = d.feed(&b, 1, ev) && ok;
    CHECK(ok);
    check_sample_events(ev);
  }
  // Random chunk boundaries, 1 byte to several KiB, fixed seed.
  std::mt19937 rng(12345);
  for (int run = 0; run < 300; ++run) {
    scrcpy::StreamDemuxer d(true);
    std::vector<StreamEvent> ev;
    size_t pos = 0;
    bool ok = true;
    const size_t max_chunk = run % 3 == 0 ? 16 : (run % 3 == 1 ? 300 : 9000);
    std::uniform_int_distribution<size_t> dist(1, max_chunk);
    while (pos < s.size()) {
      const size_t n = std::min(dist(rng), s.size() - pos);
      ok = d.feed(s.data() + pos, n, ev) && ok;
      pos += n;
    }
    CHECK(ok);
    if (run % 50 == 0 || ev.size() != 10) check_sample_events(ev);
    else CHECK(ev.size() == 10 && ev[8].data == payload(5, 70000) && ev[6].width == 1920u);
  }
  {  // dummy byte already consumed by the connect logic
    scrcpy::StreamDemuxer d(false);
    std::vector<StreamEvent> ev;
    const std::vector<uint8_t> s2 = sample_stream(false);
    CHECK(d.feed(s2.data(), s2.size(), ev));
    check_sample_events(ev);
  }
  {  // partial input produces nothing until complete
    scrcpy::StreamDemuxer d(true);
    std::vector<StreamEvent> ev;
    CHECK(d.feed(s.data(), 1 + 63, ev));
    CHECK(ev.empty());
    CHECK(d.feed(s.data() + 64, 1, ev));
    CHECK_EQ(ev.size(), 1u);
  }
}

void test_demuxer_device_name() {
  // 64 non-NUL bytes: the last is forced to NUL like scrcpy, so 63 are kept.
  std::vector<uint8_t> s(64, 'A');
  scrcpy::StreamDemuxer d(false);
  std::vector<StreamEvent> ev;
  CHECK(d.feed(s.data(), s.size(), ev));
  CHECK_EQ(ev.size(), 1u);
  if (!ev.empty()) CHECK_EQ(ev[0].device_name.size(), 63u);
}

void test_demuxer_errors() {
  auto prefix = [] {
    std::vector<uint8_t> s(64, 0);
    s[0] = 'X';
    put32(s, scrcpy::kCodecIdH264);
    return s;
  };
  {  // media packet before the first session packet
    std::vector<uint8_t> s = prefix();
    put_packet(s, 1, payload(0, 4));
    scrcpy::StreamDemuxer d(false);
    std::vector<StreamEvent> ev;
    CHECK(!d.feed(s.data(), s.size(), ev));
    CHECK(d.failed());
    CHECK(!d.error().empty());
    const uint8_t more = 0;
    CHECK(!d.feed(&more, 1, ev));  // stays failed
  }
  {  // zero-length packet
    std::vector<uint8_t> s = prefix();
    put_session(s, 100, 200, false);
    put64(s, 5);
    put32(s, 0);
    scrcpy::StreamDemuxer d(false);
    std::vector<StreamEvent> ev;
    CHECK(!d.feed(s.data(), s.size(), ev));
  }
  {  // invalid session size
    std::vector<uint8_t> s = prefix();
    put_session(s, 0, 200, false);
    scrcpy::StreamDemuxer d(false);
    std::vector<StreamEvent> ev;
    CHECK(!d.feed(s.data(), s.size(), ev));
  }
  {  // absurd packet size
    std::vector<uint8_t> s = prefix();
    put_session(s, 100, 200, false);
    put64(s, 5);
    put32(s, 0xFFFFFFF0u);
    scrcpy::StreamDemuxer d(false);
    std::vector<StreamEvent> ev;
    CHECK(!d.feed(s.data(), s.size(), ev));
  }
  {  // codec id "disabled" (0) is reported, not fatal for the demuxer
    std::vector<uint8_t> s(64, 0);
    put32(s, 0);
    scrcpy::StreamDemuxer d(false);
    std::vector<StreamEvent> ev;
    CHECK(d.feed(s.data(), s.size(), ev));
    CHECK_EQ(ev.size(), 2u);
    if (ev.size() == 2) CHECK_EQ(ev[1].codec_id, 0u);
  }
}

void test_config_merger() {
  scrcpy::ConfigPacketMerger m;
  StreamEvent config;
  config.config = true;
  config.data = {0, 0, 0, 1, 0x67, 0x42};
  CHECK(!m.merge(config));
  CHECK(m.has_pending_config());

  StreamEvent media;
  media.key_frame = true;
  media.pts = 10;
  media.data = {0, 0, 0, 1, 0x65, 0x88};
  CHECK(m.merge(media));
  CHECK_BYTES(media.data, {0, 0, 0, 1, 0x67, 0x42, 0, 0, 0, 1, 0x65, 0x88});
  CHECK(!m.has_pending_config());

  StreamEvent next;
  next.data = {0, 0, 0, 1, 0x41};
  CHECK(m.merge(next));
  CHECK_BYTES(next.data, {0, 0, 0, 1, 0x41});

  // A newer config replaces an older pending one.
  StreamEvent c1, c2, f;
  c1.config = c2.config = true;
  c1.data = {1};
  c2.data = {2};
  f.data = {9};
  m.merge(c1);
  m.merge(c2);
  CHECK(m.merge(f));
  CHECK_BYTES(f.data, {2, 9});
}

void test_serialize_touch() {
  using scrcpy::TouchAction;
  // scrcpy 5.0 app/tests/test_control_msg_serialize.c, test_serialize_inject_touch_event.
  scrcpy::Position p{100, 200, 1080, 1920};
  CHECK_BYTES(scrcpy::serialize_touch(TouchAction::Down, UINT64_C(0x1234567887654321), p, 1.0f, 1, 1),
              {0x02, 0x00, 0x12, 0x34, 0x56, 0x78, 0x87, 0x65, 0x43, 0x21, 0x00, 0x00, 0x00, 0x64,
               0x00, 0x00, 0x00, 0xc8, 0x04, 0x38, 0x07, 0x80, 0xff, 0xff, 0x00, 0x00, 0x00, 0x01,
               0x00, 0x00, 0x00, 0x01});

  // What vr-mirror sends: generic finger (-2), no buttons, pressure 1 / 0.
  scrcpy::Position q{100, 200, 360, 640};
  const auto down = scrcpy::serialize_touch(TouchAction::Down, scrcpy::kPointerIdGenericFinger, q, 1.0f);
  CHECK_EQ(down.size(), scrcpy::kTouchMessageSize);
  CHECK_BYTES(down, {0x02, 0x00, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xfe, 0x00, 0x00, 0x00,
                     0x64, 0x00, 0x00, 0x00, 0xc8, 0x01, 0x68, 0x02, 0x80, 0xff, 0xff, 0x00, 0x00,
                     0x00, 0x00, 0x00, 0x00, 0x00, 0x00});
  CHECK_BYTES(scrcpy::serialize_touch(TouchAction::Up, scrcpy::kPointerIdGenericFinger, q, 0.0f),
              {0x02, 0x01, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xfe, 0x00, 0x00, 0x00, 0x64,
               0x00, 0x00, 0x00, 0xc8, 0x01, 0x68, 0x02, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
               0x00, 0x00, 0x00, 0x00});
  scrcpy::Position m{863, 1919, 864, 1920};
  CHECK_BYTES(scrcpy::serialize_touch(TouchAction::Move, scrcpy::kPointerIdGenericFinger, m, 1.0f),
              {0x02, 0x02, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xfe, 0x00, 0x00, 0x03, 0x5f,
               0x00, 0x00, 0x07, 0x7f, 0x03, 0x60, 0x07, 0x80, 0xff, 0xff, 0x00, 0x00, 0x00, 0x00,
               0x00, 0x00, 0x00, 0x00});
  // A pinch's second finger is scrcpy's virtual finger (-3).
  CHECK_BYTES(scrcpy::serialize_touch(TouchAction::Down, scrcpy::kPointerIdVirtualFinger, q, 1.0f),
              {0x02, 0x00, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xfd, 0x00, 0x00, 0x00, 0x64,
               0x00, 0x00, 0x00, 0xc8, 0x01, 0x68, 0x02, 0x80, 0xff, 0xff, 0x00, 0x00, 0x00, 0x00,
               0x00, 0x00, 0x00, 0x00});
}

void test_serialize_scroll() {
  // scrcpy 5.0 test_serialize_inject_scroll_event.
  scrcpy::Position p{260, 1026, 1080, 1920};
  CHECK_BYTES(scrcpy::serialize_scroll(p, 16.0f, -16.0f, 1),
              {0x03, 0x00, 0x00, 0x01, 0x04, 0x00, 0x00, 0x04, 0x02, 0x04, 0x38, 0x07, 0x80, 0x7F,
               0xFF, 0x80, 0x00, 0x00, 0x00, 0x00, 0x01});
  // One wheel notch (OpenVR ydelta = 1) is 1/16 -> 0x0800. Beyond 16 clamps.
  const auto s = scrcpy::serialize_scroll(p, 0.0f, 1.0f);
  CHECK_EQ(s.size(), scrcpy::kScrollMessageSize);
  CHECK_BYTES(s, {0x03, 0x00, 0x00, 0x01, 0x04, 0x00, 0x00, 0x04, 0x02, 0x04, 0x38, 0x07, 0x80,
                  0x00, 0x00, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00});
  const auto big = scrcpy::serialize_scroll(p, -100.0f, 100.0f);
  CHECK_EQ(big[13], 0x80);
  CHECK_EQ(big[14], 0x00);
  CHECK_EQ(big[15], 0x7f);
  CHECK_EQ(big[16], 0xff);
}

void test_serialize_keys() {
  using scrcpy::KeyAction;
  CHECK_BYTES(scrcpy::serialize_back_or_screen_on(KeyAction::Down), {0x04, 0x00});
  CHECK_BYTES(scrcpy::serialize_back_or_screen_on(KeyAction::Up), {0x04, 0x01});
  // scrcpy 5.0 test_serialize_inject_keycode: ENTER (66), up, repeat 5, SHIFT_ON|SHIFT_LEFT_ON.
  CHECK_BYTES(scrcpy::serialize_keycode(KeyAction::Up, 66, 5, 0x41),
              {0x00, 0x01, 0x00, 0x00, 0x00, 0x42, 0x00, 0x00, 0x00, 0x05, 0x00, 0x00, 0x00, 0x41});
  CHECK_BYTES(scrcpy::serialize_keycode(KeyAction::Down, scrcpy::kKeycodeHome),
              {0x00, 0x00, 0x00, 0x00, 0x00, 0x03, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00});
  CHECK_BYTES(scrcpy::serialize_keycode(KeyAction::Up, scrcpy::kKeycodeAppSwitch),
              {0x00, 0x01, 0x00, 0x00, 0x00, 0xbb, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00});
  CHECK_EQ(scrcpy::serialize_keycode(KeyAction::Down, scrcpy::kKeycodePower)[5], 26);
  CHECK_BYTES(scrcpy::serialize_display_power(true), {0x0a, 0x01});
  CHECK_BYTES(scrcpy::serialize_display_power(false), {0x0a, 0x00});
}

void test_overlay_mapping() {
  const uint32_t w = 864, h = 1920;
  // OpenVR mouse origin is bottom-left: y_touch = h - y.
  scrcpy::Position p = scrcpy::map_overlay_point(0.0f, static_cast<float>(h), w, h);  // top-left
  CHECK_EQ(p.x, 0);
  CHECK_EQ(p.y, 0);
  CHECK_EQ(p.width, 864);
  CHECK_EQ(p.height, 1920);
  p = scrcpy::map_overlay_point(0.0f, 0.0f, w, h);  // bottom-left -> clamped to the last row
  CHECK_EQ(p.x, 0);
  CHECK_EQ(p.y, 1919);
  p = scrcpy::map_overlay_point(static_cast<float>(w), 0.0f, w, h);  // bottom-right corner
  CHECK_EQ(p.x, 863);
  CHECK_EQ(p.y, 1919);
  p = scrcpy::map_overlay_point(432.5f, 1500.25f, w, h);
  CHECK_EQ(p.x, 432);
  CHECK_EQ(p.y, 419);  // floor(1920 - 1500.25) = 419
  p = scrcpy::map_overlay_point(-50.0f, 99999.0f, w, h);  // outside: clamped
  CHECK_EQ(p.x, 0);
  CHECK_EQ(p.y, 0);
  p = scrcpy::map_overlay_point(1e30f, -1e30f, w, h);
  CHECK_EQ(p.x, 863);
  CHECK_EQ(p.y, 1919);
  p = scrcpy::map_overlay_point(std::numeric_limits<float>::quiet_NaN(),
                                std::numeric_limits<float>::infinity(), w, h);
  CHECK_EQ(p.x, 0);
  CHECK_EQ(p.y, 0);
  p = scrcpy::map_overlay_point(10.0f, 10.0f, 0, 0);  // no frame yet
  CHECK_EQ(p.width, 0);

  p = scrcpy::make_position(-5, 5000, 360, 640);
  CHECK_EQ(p.x, 0);
  CHECK_EQ(p.y, 639);
  p = scrcpy::make_position(100, 200, 360, 640);
  CHECK_EQ(p.x, 100);
  CHECK_EQ(p.y, 200);
}

panel::Mat34 rot_y(float deg, float x, float y, float z) {
  const float a = deg * 3.14159265f / 180.0f;
  panel::Mat34 m = panel::identity();
  m.m[0][0] = std::cos(a);
  m.m[0][2] = std::sin(a);
  m.m[2][0] = -std::sin(a);
  m.m[2][2] = std::cos(a);
  m.m[0][3] = x;
  m.m[1][3] = y;
  m.m[2][3] = z;
  return m;
}

panel::Mat34 rot_x(float deg, float y) {
  const float a = deg * 3.14159265f / 180.0f;
  panel::Mat34 m = panel::identity();
  m.m[1][1] = std::cos(a);
  m.m[1][2] = -std::sin(a);
  m.m[2][1] = std::sin(a);
  m.m[2][2] = std::cos(a);
  m.m[1][3] = y;
  return m;
}

panel::Mat34 rot_z(float deg) {
  const float a = deg * 3.14159265f / 180.0f;
  panel::Mat34 m = panel::identity();
  m.m[0][0] = std::cos(a);
  m.m[0][1] = -std::sin(a);
  m.m[1][0] = std::sin(a);
  m.m[1][1] = std::cos(a);
  return m;
}

void check_upright(const panel::Mat34& p) {
  CHECK_NEAR(p.m[0][1], 0.0, 1e-5);  // Y axis = world up
  CHECK_NEAR(p.m[1][1], 1.0, 1e-5);
  CHECK_NEAR(p.m[2][1], 0.0, 1e-5);
  CHECK_NEAR(p.m[1][0], 0.0, 1e-5);  // no roll/pitch
  CHECK_NEAR(p.m[1][2], 0.0, 1e-5);
}

void check_rigid_near(const panel::Mat34& a, const panel::Mat34& b) {
  for (int r = 0; r < 3; ++r) {
    for (int c = 0; c < 4; ++c) CHECK_NEAR(a.m[r][c], b.m[r][c], 1e-4);
  }
}

// a in b's frame: (a - origin(b)) along b's axes.
panel::Vec3 in_frame(const panel::Mat34& b, const panel::Vec3& a) {
  const float d[3] = {a.x - b.m[0][3], a.y - b.m[1][3], a.z - b.m[2][3]};
  float o[3];
  for (int c = 0; c < 3; ++c) o[c] = b.m[0][c] * d[0] + b.m[1][c] * d[1] + b.m[2][c] * d[2];
  return {o[0], o[1], o[2]};
}

// Dot product of column `a` of `p` with column `b` of `q`.
double axis_dot(const panel::Mat34& p, int a, const panel::Mat34& q, int b) {
  return p.m[0][a] * q.m[0][b] + p.m[1][a] * q.m[1][b] + p.m[2][a] * q.m[2][b];
}

// Orthonormal and right-handed (X x Y = Z).
void check_rotation(const panel::Mat34& p) {
  for (int a = 0; a < 3; ++a) {
    for (int b = 0; b < 3; ++b) CHECK_NEAR(axis_dot(p, a, p, b), a == b ? 1.0 : 0.0, 1e-5);
  }
  CHECK_NEAR(p.m[1][0] * p.m[2][1] - p.m[2][0] * p.m[1][1], p.m[0][2], 1e-5);
  CHECK_NEAR(p.m[2][0] * p.m[0][1] - p.m[0][0] * p.m[2][1], p.m[1][2], 1e-5);
  CHECK_NEAR(p.m[0][0] * p.m[1][1] - p.m[1][0] * p.m[0][1], p.m[2][2], 1e-5);
}

void test_panel_math() {
  const float d = panel::kDistanceMeters;
  {  // looking down -Z from (0, 1.6, 0)
    const panel::Mat34 p = panel::place_on_gaze(rot_y(0, 0, 1.6f, 0), d);
    CHECK_NEAR(p.m[0][3], 0.0, 1e-5);
    CHECK_NEAR(p.m[1][3], 1.6, 1e-5);   // centred on the line of sight
    CHECK_NEAR(p.m[2][3], -d, 1e-5);
    CHECK_NEAR(p.m[0][0], 1.0, 1e-5);  // X axis = (1,0,0)
    CHECK_NEAR(p.m[2][2], 1.0, 1e-5);  // Z axis points back at the user
    check_upright(p);
  }
  {  // turned 90 degrees left (looking down -X), standing at (1, 1.7, 2)
    const panel::Mat34 p = panel::place_on_gaze(rot_y(90, 1.0f, 1.7f, 2.0f), d);
    CHECK_NEAR(p.m[0][3], 1.0 - d, 1e-5);
    CHECK_NEAR(p.m[1][3], 1.7, 1e-5);
    CHECK_NEAR(p.m[2][3], 2.0, 1e-5);
    CHECK_NEAR(p.m[0][2], 1.0, 1e-5);  // Z axis = +X, towards the user
    CHECK_NEAR(p.m[2][0], -1.0, 1e-5); // X axis = -Z (user's right)
    check_upright(p);
  }
  {  // head pitched down 20 degrees and turned: on the line of sight, upright, facing back along
     // the head's heading
    const panel::Mat34 hp = panel::multiply(rot_y(-45, 0, 1.5f, 0), rot_x(-20, 0));
    const panel::Mat34 p = panel::place_on_gaze(hp, d);
    for (int r = 0; r < 3; ++r) CHECK_NEAR(p.m[r][3], hp.m[r][3] - hp.m[r][2] * d, 1e-5);  // forward is the HMD's -Z
    CHECK_NEAR(p.m[1][3], 1.5 - d * std::sin(20 * 3.14159265 / 180), 1e-5);
    check_upright(p);
    CHECK_NEAR(axis_dot(p, 2, rot_y(-45, 0, 0, 0), 2), 1.0, 1e-5);
    CHECK(axis_dot(p, 0, hp, 0) > 0.99);
    check_rotation(p);
  }
  {  // pitched down 60 degrees, well past the threshold: tilted back square to the eye
    const panel::Mat34 hp = panel::multiply(rot_y(-45, 0, 1.5f, 0), rot_x(-60, 0));
    const panel::Mat34 p = panel::place_on_gaze(hp, d);
    for (int r = 0; r < 3; ++r) CHECK_NEAR(p.m[r][3], hp.m[r][3] - hp.m[r][2] * d, 1e-5);
    const double tilt = std::asin(p.m[1][2]) * 180 / 3.14159265;
    CHECK_NEAR(tilt, 60.0, 1e-3);
    CHECK_NEAR(p.m[1][0], 0.0, 1e-6);  // X level
    CHECK(axis_dot(p, 0, hp, 0) > 0.99);
    check_rotation(p);
  }
  {  // head rolled: the panel isn't
    const panel::Mat34 p = panel::place_on_gaze(panel::multiply(rot_y(20, 0, 1.6f, 0), rot_z(35)), d);
    check_upright(p);
    CHECK_NEAR(axis_dot(p, 2, rot_y(20, 0, 0, 0), 2), 1.0, 1e-5);
  }
  {  // lying back, looking straight up: overhead, with its top toward the crown of the head
    for (const float yaw : {0.0f, 180.0f, 70.0f}) {
      const panel::Mat34 hmd = panel::multiply(rot_y(yaw, 0.2f, 0.5f, 0), rot_x(90, 0));
      const panel::Mat34 p = panel::place_on_gaze(hmd, d);
      CHECK_NEAR(p.m[1][3], 0.5 + d, 1e-5);
      CHECK_NEAR(p.m[1][2], -1.0, 1e-5);       // facing down at the user
      CHECK(axis_dot(p, 1, hmd, 1) > 0.99);   // its up is the head's up
      check_rotation(p);
    }
  }
  {  // lying back and looking a little past straight up: still not upside down
    const panel::Mat34 hmd = panel::multiply(rot_y(180, 0, 0.5f, 0), rot_x(100, 0));
    const panel::Mat34 p = panel::place_on_gaze(hmd, d);
    CHECK_NEAR(p.m[1][0], 0.0, 1e-6);         // X level
    CHECK(axis_dot(p, 0, hmd, 0) > 0.99);
    CHECK(axis_dot(p, 1, hmd, 1) > 0.99);
    check_rotation(p);
  }
  {  // the middle of the view, from the lenses: the Steam Frame's (both eyes averaged, measured)
    // reach 49.6 degrees up and 59.9 down, so it's about 15 degrees below the forward axis
    const panel::Vec3 frame = panel::view_center(-1.43535f, 1.44165f, -1.71955f, 1.17705f);
    const double below = std::asin(-frame.y) * 180 / 3.14159265;
    CHECK_NEAR(below, 15.18, 0.05);
    CHECK_NEAR(frame.x, 0.0, 0.01);
    CHECK(frame.z < -0.95f);
    const panel::Vec3 even = panel::view_center(-1.2f, 1.2f, -1.3f, 1.3f);   // symmetric lenses
    CHECK_NEAR(even.x, 0.0, 1e-6);
    CHECK_NEAR(even.y, 0.0, 1e-6);
    CHECK_NEAR(even.z, -1.0, 1e-6);
    const panel::Vec3 junk = panel::view_center(std::nanf(""), 1.0f, -5.0f, 1.0f);
    CHECK_NEAR(junk.z, -1.0, 1e-6);
    // Placed on that line: below eye level for a level head, but not by enough to tilt it.
    const panel::Mat34 p = panel::place_on_gaze(rot_y(0, 0, 1.6f, 0), d, frame);
    const double a = below * 3.14159265 / 180;
    CHECK(a < panel::kUprightWithinRadians);
    CHECK_NEAR(p.m[1][3], 1.6 - d * std::sin(a), 1e-3);
    CHECK_NEAR(p.m[2][3], -d * std::cos(a), 1e-3);
    CHECK_NEAR(p.m[2][2], 1.0, 1e-5);
    check_upright(p);
    check_rotation(p);
    // Turned and pitched heads carry the offset with them.
    const panel::Mat34 hp = panel::multiply(rot_y(70, 0.3f, 1.5f, -0.2f), rot_x(20, 0));
    const panel::Mat34 q = panel::place_on_gaze(hp, d, frame);
    const panel::Vec3 rel = in_frame(hp, panel::translation(q));
    CHECK_NEAR(rel.x / d, frame.x, 1e-4);
    CHECK_NEAR(rel.y / d, frame.y, 1e-4);
    CHECK_NEAR(rel.z / d, frame.z, 1e-4);
  }
  {  // theater: 3.8 m straight ahead, upright on the floor, facing the user, whatever the head's pitch
    const float t = panel::kTheaterDistanceMeters;
    const float h = 0.8f;   // the phone screen's bottom edge, below the overlay's centre
    CHECK_NEAR(t, 3.8, 1e-6);
    CHECK(t > panel::kMaxDistanceMeters);           // farther than a normal panel goes
    CHECK(t < panel::kMaxTheaterDistanceMeters);    // with room to push it back
    CHECK_NEAR(panel::kDefaultTheaterLongSideMeters, 3.5, 1e-6);
    CHECK(panel::kDefaultTheaterLongSideMeters > panel::kMaxLongSideMeters);        // beyond a normal panel
    CHECK(panel::kDefaultTheaterLongSideMeters < panel::kMaxTheaterLongSideMeters);  // with room to grow
    panel::Mat34 p = panel::place_theater(rot_y(0, 0, 1.6f, 0), h);
    CHECK_NEAR(p.m[0][3], 0.0, 1e-5);
    CHECK_NEAR(p.m[1][3], h, 1e-5);
    CHECK_NEAR(panel::foot(p, h).y, 0.0, 1e-5);   // the phone screen's bottom edge on the floor
    CHECK_NEAR(p.m[2][3], -t, 1e-5);
    CHECK_NEAR(p.m[2][2], 1.0, 1e-5);       // facing the user
    check_upright(p);
    p = panel::place_theater(rot_y(90, 0.5f, 1.7f, 1.0f), h);     // looking down -X
    CHECK_NEAR(p.m[0][3], 0.5 - t, 1e-5);
    CHECK_NEAR(p.m[1][3], h, 1e-5);
    CHECK_NEAR(p.m[2][3], 1.0, 1e-5);
    check_upright(p);
    p = panel::place_theater(panel::multiply(rot_y(0, 0, 1.6f, 0), rot_x(-40, 0)), h);  // looking down
    CHECK_NEAR(p.m[1][3], h, 1e-5);
    CHECK_NEAR(p.m[2][3], -t, 1e-5);
    check_upright(p);
    p = panel::place_theater(rot_x(-90, 1.6f), h);                 // straight down: ahead is the HMD's up
    CHECK_NEAR(p.m[2][3], -t, 1e-4);
    check_upright(p);
    p = panel::place_theater(rot_y(0, 0, 1.6f, 0), 2.1f);          // centre above the eye: still on the floor
    CHECK_NEAR(p.m[1][3], 2.1, 1e-5);
    check_upright(p);
    // Square to the level line to the phone screen's centre, left of and above the overlay's.
    const panel::Vec3 content{-0.3f, 0.25f, 0.0f};
    p = panel::place_theater(rot_y(30, 0.2f, 1.6f, -0.1f), h, content);
    check_upright(p);
    check_rotation(p);
    CHECK_NEAR(p.m[1][3], h, 1e-5);
    const float sx = p.m[0][3] + p.m[0][0] * content.x, sz = p.m[2][3] + p.m[2][0] * content.x;
    const float lx = 0.2f - sx, lz = -0.1f - sz, len = std::sqrt(lx * lx + lz * lz);
    CHECK_NEAR(p.m[0][2], lx / len, 1e-4);
    CHECK_NEAR(p.m[2][2], lz / len, 1e-4);
    // on_floor: where it's carried to, at any height and with the eye right above it.
    p = panel::on_floor({1.0f, 5.0f, -2.0f}, h, {0.0f, 1.6f, 0.0f}, {1.0f, 0.0f, 0.0f});
    CHECK_NEAR(p.m[0][3], 1.0, 1e-5);
    CHECK_NEAR(p.m[1][3], h, 1e-5);
    CHECK_NEAR(p.m[2][3], -2.0, 1e-5);
    check_upright(p);
    p = panel::on_floor({0.0f, 0.0f, 0.0f}, h, {0.0f, 1.6f, 0.0f}, {1.0f, 0.0f, 0.0f});
    CHECK_NEAR(p.m[1][3], h, 1e-5);
    check_upright(p);
    check_rotation(p);
  }
  {  // phone scrolling: the same share of the screen per click as the app's pages (64 dp a click)
    const float share = 144.0f / 968.0f;
    CHECK_NEAR(panel::scroll_clicks_per_unit(share, 914.0f, 864, 1920), share * 914.0 / 64.0, 1e-4);   // portrait
    CHECK_NEAR(panel::scroll_clicks_per_unit(share, 914.0f, 1920, 864), share * 914.0 * 0.45 / 64.0, 1e-4);  // landscape
    CHECK_NEAR(panel::scroll_clicks_per_unit(share, 0.0f, 864, 1920), share * panel::kTypicalScreenLongDp / 64.0, 1e-4);
    CHECK_EQ(panel::scroll_clicks_per_unit(0.0f, 914.0f, 864, 1920), 1.0f);   // not told: one click per unit
    CHECK_EQ(panel::scroll_clicks_per_unit(std::nanf(""), 914.0f, 864, 1920), 1.0f);
    CHECK_EQ(panel::scroll_clicks_per_unit(share, 914.0f, 0, 0), 1.0f);
  }
  // Size: the long side is fixed; rotation swaps which side that is.
  CHECK_NEAR(panel::panel_height_m(864, 1920, 0.30f), 0.30, 1e-6);
  CHECK_NEAR(panel::panel_width_m(864, 1920, 0.30f), 0.135, 1e-6);
  CHECK_NEAR(panel::panel_width_m(1920, 864, 0.30f), 0.30, 1e-6);
  CHECK_NEAR(panel::panel_height_m(1920, 864, 0.30f), 0.135, 1e-6);
}

void test_panel_chrome() {
  const uint32_t w = 864, h = 1920;
  const chrome::Layout l = chrome::layout(w, h);
  CHECK_EQ(l.bar_h, 78u);                              // 9% of the short side
  CHECK_EQ(chrome::layout(1920, 864).bar_h, 78u);      // same in landscape
  CHECK_EQ(chrome::layout(100, 50).bar_h, 24u);        // never thinner than 24 px
  CHECK_EQ(l.image_w, w);
  CHECK(l.margin_r > 0);
  CHECK(chrome::layout(1920, 864).margin_r < 80);      // room for the bracket's vertical arm
  CHECK_EQ(l.width(), w + l.margin_r);
  CHECK_EQ(l.height(), h + l.button_h + l.bar_h + l.menu_h);
  CHECK_EQ(l.strip_h(), l.button_h + l.bar_h + l.menu_h);
  // Buttons under the image, drag bar under them, bracket around the image's bottom-right
  // corner at equal distance from both edges.
  CHECK(l.button_cy - l.button_r > h);
  CHECK(l.button_cy + l.button_r < h + l.button_h);
  CHECK(l.bar_cy > h + l.button_h && l.bar_cy < l.height());
  CHECK_NEAR(l.corner_x - w, l.corner_y - h, 1e-6);
  CHECK(l.corner_y > h && l.corner_y < l.button_cy);
  CHECK(l.corner_x - l.arm - l.stroke_r > l.more_cx + l.button_r * 1.2f);  // clear of More
  // Back, Home, Recents, then More, Hide from left to right, centred under the phone, not
  // touching. A wider gap separates the device keys from the panel controls.
  CHECK_NEAR((l.back_cx + l.more_cx) / 2.0f, w / 2.0f, 1e-3);
  const float step = l.home_cx - l.back_cx;
  CHECK_NEAR(l.recents_cx - l.home_cx, step, 1e-3);
  CHECK_NEAR(l.more_cx - l.hide_cx, step, 1e-3);
  CHECK(l.hide_cx - l.recents_cx > step + l.button_r * 0.5f);
  // The menu is a row of buttons under More, the first right under it, going right, over the
  // bar row. Its rows and the margin past its last button are part of the texture.
  CHECK_EQ(chrome::kMenuSize, 3u);
  CHECK(chrome::kMenu[0] == chrome::Zone::Theater);
  CHECK(chrome::kMenu[1] == chrome::Zone::Recenter);
  CHECK(chrome::kMenu[2] == chrome::Zone::Audio);
  CHECK_NEAR(l.menu_cx(0), l.more_cx, 1e-6);
  CHECK_NEAR(l.menu_cx(1) - l.menu_cx(0), step, 1e-3);
  CHECK_NEAR(l.menu_cx(2) - l.menu_cx(1), step, 1e-3);
  CHECK_NEAR(l.menu_cy() - l.button_cy, step, 1e-3);
  CHECK(l.menu_cy() - l.button_r < h + l.button_h + l.bar_h);
  CHECK(l.menu_h > 0);
  CHECK(l.menu_cy() + l.button_r * 1.2f <= static_cast<float>(l.height()));
  CHECK(l.menu_cx(chrome::kMenuSize - 1) + l.button_r + l.tray_pad <= static_cast<float>(l.width()));
  const chrome::Layout wide = chrome::layout(1920, 864);   // in landscape it fits under the image
  CHECK(wide.menu_cx(chrome::kMenuSize - 1) + wide.button_r + wide.tray_pad < 1920.0f);
  CHECK(wide.margin_r < 60);
  CHECK(l.back_cx + l.button_r < l.home_cx - l.button_r);
  CHECK(l.back_cx - l.button_r > 0);

  const uint32_t tw = l.width(), th = l.height();
  std::vector<uint8_t> idle(static_cast<size_t>(tw) * th * 4, 7), hot(idle.size(), 7);
  chrome::draw(idle.data(), l, chrome::State{});
  chrome::State all;
  all.bar = all.resize = all.back = all.home = all.recents = all.more = all.hide = true;
  chrome::draw(hot.data(), l, all);
  auto px = [&](const std::vector<uint8_t>& im, float x, float y, int c) {
    return im[(static_cast<size_t>(y) * tw + static_cast<size_t>(x)) * 4 + static_cast<size_t>(c)];
  };
  CHECK_EQ(px(idle, 10, 10, 0), 7);                    // the phone image is not touched
  CHECK_EQ(px(idle, w - 1, h - 1, 3), 7);
  CHECK_EQ(px(idle, w + 1, 10, 3), 0);                 // margin column cleared,
  CHECK_EQ(px(idle, 2, h + 2, 3), 0);                  // transparent outside the controls,
  CHECK_EQ(px(idle, w / 2, h + 1, 3), 0);              // a gap between the image and the buttons
  CHECK_EQ(px(idle, w / 2, th - 2, 3), 0);             // and below the pill
  CHECK_EQ(px(idle, w / 2, l.bar_cy, 0), 255);         // pill: white,
  CHECK(px(idle, w / 2, l.bar_cy, 3) > 100);           // translucent when idle,
  CHECK(px(hot, w / 2, l.bar_cy, 3) > px(idle, w / 2, l.bar_cy, 3));  // brighter when active
  CHECK_EQ(px(idle, w / 2 + 160, l.bar_cy, 3), 0);     // ~34% of the short side wide
  CHECK(px(idle, w / 2 + 130, l.bar_cy, 3) > 100);
  // Bracket: both arms drawn, brighter when active, nothing beyond its ends.
  CHECK(px(idle, l.corner_x - l.arm + 4, l.corner_y, 3) > 100);   // horizontal arm
  CHECK(px(idle, l.corner_x, l.corner_y - l.arm + 4, 3) > 100);   // vertical arm (margin column)
  CHECK(px(hot, l.corner_x - l.arm + 4, l.corner_y, 3) > px(idle, l.corner_x - l.arm + 4, l.corner_y, 3));
  CHECK_EQ(px(idle, l.corner_x - l.arm - l.stroke_r - 3, l.corner_y, 3), 0);
  CHECK_EQ(px(idle, l.corner_x, l.corner_y - l.arm - l.stroke_r - 3, 3), 0);
  // Buttons: a dark disc with a white icon, white with a dark icon when highlighted.
  for (const float cx : {l.back_cx, l.home_cx, l.recents_cx, l.hide_cx, l.more_cx}) {
    CHECK(px(idle, cx + l.button_r * 0.8f, l.button_cy, 3) > 120);
    CHECK(px(idle, cx + l.button_r * 0.8f, l.button_cy, 0) < 60);
    CHECK(px(hot, cx + l.button_r * 0.8f, l.button_cy, 0) > 200);
    CHECK_EQ(px(idle, cx + l.button_r + 3, l.button_cy, 3), 0);
  }
  CHECK(px(idle, l.home_cx + l.button_r * 0.31f, l.button_cy, 0) > 200);     // Home's ring,
  CHECK(px(idle, l.home_cx, l.button_cy, 0) < 60);                            // hollow
  CHECK(px(idle, l.recents_cx + l.button_r * 0.27f, l.button_cy, 0) > 200);  // Recents' square,
  CHECK(px(idle, l.recents_cx, l.button_cy, 0) < 60);                         // hollow
  CHECK(px(idle, l.more_cx, l.button_cy, 0) > 200);   // More's middle dot,
  CHECK(px(idle, l.more_cx, l.button_cy - l.button_r * 0.32f, 0) > 200);  // the top one
  CHECK(px(idle, l.more_cx, l.button_cy + l.button_r * 0.16f, 0) < 60);  // and a gap
  CHECK(px(idle, l.hide_cx, l.button_cy, 0) > 200);      // the eye's pupil
  CHECK(px(hot, l.hide_cx, l.button_cy, 0) < 60);

  // The menu: drawn only while open, over the drag pill. Theater shows what pressing it does:
  // expand (corners at the frame's corners) while off, shrink (corners turned in) while on.
  const float mx = l.menu_cx(0), my = l.menu_cy(), r = l.button_r;
  CHECK_EQ(px(idle, mx + r * 0.8f, my + r * 0.6f, 3), 0);
  std::vector<uint8_t> open(idle.size(), 7), open_hot(idle.size(), 7), open_on(idle.size(), 7);
  chrome::State menu;
  menu.menu_open = true;
  chrome::draw(open.data(), l, menu);
  menu.theater_on = true;
  chrome::draw(open_on.data(), l, menu);
  menu.theater = true;
  chrome::draw(open_hot.data(), l, menu);
  CHECK(px(open, mx + r * 0.8f, my, 3) > 120);           // a dark disc
  CHECK(px(open, mx + r * 0.8f, my, 0) < 60);
  for (const float sx : {-1.0f, 1.0f}) {
    for (const float sy : {-1.0f, 1.0f}) {
      const float outer_x = mx + sx * r * 0.50f, outer_y = my + sy * r * 0.36f;
      const float inner_x = mx + sx * r * 0.26f, inner_y = my + sy * r * 0.12f;
      CHECK(px(open, outer_x, outer_y, 0) > 200);        // expand: the frame's corner,
      CHECK(px(open, inner_x, inner_y, 0) < 60);
      CHECK(px(open_on, inner_x, inner_y, 0) > 200);     // shrink: turned in,
      CHECK(px(open_on, outer_x, outer_y, 0) < 60);
      CHECK(px(open_on, mx + sx * r * 0.40f, inner_y, 0) > 200);  // its arms pointing out
      CHECK(px(open_on, inner_x, my + sy * r * 0.26f, 0) > 200);
    }
  }
  CHECK(px(open, mx, my, 0) < 60);                       // hollow middle
  CHECK(px(open_on, mx, my, 0) < 60);
  CHECK_EQ(px(open_on, mx + r * 0.8f, my, 0), px(open, mx + r * 0.8f, my, 0));  // same dark disc when on
  CHECK(px(open_hot, mx + r * 0.8f, my, 0) > 200);       // hovered: white
  // The tray: dark and nearly opaque between More and the menu, with nothing of it while the
  // menu is closed.
  const float gap_y = (l.button_cy + my) / 2.0f;
  CHECK(px(open, mx, gap_y, 3) > 180);
  CHECK(px(open, mx, gap_y, 0) < 40);
  CHECK(px(open, mx + r * 0.5f, l.bar_cy, 0) < 40);
  CHECK_EQ(px(idle, mx, gap_y, 3), 0);
  CHECK(px(open, mx + r + l.tray_pad * 0.5f, l.button_cy, 3) > 150);   // around More too

  // Zoom mode's anchor: a white ring and dot edged in dark over the phone image, hollow between
  // them, and never past the image's edge.
  {
    std::vector<uint8_t> a(idle.size(), 128);
    const float ax = w / 2.0f, ay = h / 2.0f, ring = l.button_r * 0.45f, reach = chrome::anchor_reach(l);
    chrome::draw_anchor(a.data(), l, ax, ay);
    CHECK(px(a, ax, ay, 0) > 240);                     // the dot,
    CHECK(px(a, ax + ring, ay, 0) > 240);              // the ring,
    CHECK(px(a, ax, ay - ring, 0) > 240);
    CHECK_EQ(px(a, ax + ring / 2.0f, ay, 0), 128);     // the image showing between them,
    CHECK(px(a, ax + ring + l.stroke_r / 2.0f + 1.5f, ay, 0) < 100);  // a dark edge outside,
    CHECK_EQ(px(a, ax + reach + 1.0f, ay, 0), 128);    // nothing past its reach
    std::vector<uint8_t> edge(idle.size(), 128);
    chrome::draw_anchor(edge.data(), l, w - 1.0f, h - 1.0f);
    CHECK(px(edge, w - 1, h - 1, 0) > 240);
    CHECK_EQ(px(edge, w + 1, h - 3, 0), 128);          // not on the margin column
    CHECK_EQ(px(edge, w - 3, h + 1, 0), 128);          // nor the controls under the image
  }
  CHECK_EQ(px(open, mx - r - l.tray_pad - 3, my, 3), 0);                // and no further left
  CHECK(px(open, mx, my + r + l.tray_pad * 0.5f, 3) > 150);             // below the row
  const float last = l.menu_cx(chrome::kMenuSize - 1);
  CHECK(px(open, (l.menu_cx(1) + last) / 2.0f, my - r - l.tray_pad * 0.5f, 3) > 150);  // along it
  CHECK(px(open, last + r + l.tray_pad * 0.5f, my, 3) > 150);           // past the last button
  CHECK(static_cast<float>(l.height()) >= my + r + l.tray_pad);
  CHECK(px(open, l.back_cx + r * 0.8f, l.button_cy, 3) > 120);  // the other buttons are unchanged
  CHECK(px(open, l.hide_cx + r * 0.8f, l.button_cy, 0) < 60);   // Hide too, left of the tray
  CHECK(px(open, l.hide_cx, l.button_cy, 0) > 200);
  // Recenter: a ring with four ticks.
  const float rx = l.menu_cx(1);
  CHECK(px(open, rx + r * 0.8f, my, 3) > 120);            // a dark disc
  CHECK(px(open, rx + r * 0.045f * 3.5f, my, 0) > 200);   // the ring
  CHECK(px(open, rx, my, 0) < 60);                        // hollow
  CHECK(px(open, rx, my - r * 0.045f * 7.5f, 0) > 200);   // a tick
  // Audio shows what's true now, as Home does: headphones while casting, with a slash when not,
  // never tinted. Faint while it can't be pressed.
  const float ax = l.menu_cx(2), u = r * 0.045f;
  std::vector<uint8_t> audio_on(idle.size(), 7), audio_busy(idle.size(), 7), audio_hot(idle.size(), 7);
  chrome::State a_on;
  a_on.menu_open = true;
  a_on.audio_on = true;
  chrome::draw(audio_on.data(), l, a_on);
  chrome::State a_busy = a_on;
  a_busy.audio_enabled = false;
  a_busy.audio = true;   // hovered but disabled: not highlighted
  chrome::draw(audio_busy.data(), l, a_busy);
  chrome::State a_hot = a_on;
  a_hot.audio = true;
  chrome::draw(audio_hot.data(), l, a_hot);
  CHECK(px(audio_on, ax, my - 7.0f * u, 0) > 200);                            // the band's top
  CHECK(px(audio_on, ax - 7.5f * u, my + 2.0f * u, 0) > 200);                 // its side
  CHECK(px(audio_on, ax + 2.0f * u, my + 2.0f * u, 0) < 60);                  // no slash
  CHECK(px(open, ax + 2.0f * u, my + 2.0f * u, 0) > 200);                     // a slash when off
  CHECK(px(audio_on, ax, my - 7.0f * u, 0) == px(audio_on, ax, my - 7.0f * u, 1));   // white, not tinted
  CHECK(px(audio_on, ax, my - 7.0f * u, 1) == px(audio_on, ax, my - 7.0f * u, 2));
  CHECK(px(audio_busy, ax, my - 7.0f * u, 0) < 160);                          // faint
  CHECK(px(audio_busy, ax + r * 0.8f, my, 0) < 60);                           // and not highlighted
  CHECK(px(audio_hot, ax + r * 0.8f, my, 0) > 200);

  // Laser zones. Mouse Y is bottom-up over the whole texture.
  auto z = [&](float x, float y_top_down, bool menu_open = false) {
    return chrome::zone(l, x, th - y_top_down, menu_open);
  };
  CHECK(z(10, 10) == chrome::Zone::Phone);
  CHECK(z(w - 1, h - 1) == chrome::Zone::Phone);
  CHECK(z(w / 2, th - 2) == chrome::Zone::Bar);
  CHECK(z(5, h + l.button_h + 1) == chrome::Zone::Bar);
  CHECK(z(w / 2, h + 2) == chrome::Zone::Empty);                     // above the buttons
  CHECK(z(l.corner_x, l.corner_y) == chrome::Zone::Resize);
  CHECK(z(l.corner_x - l.arm, l.corner_y) == chrome::Zone::Resize);   // along the arms
  CHECK(z(w + 2, l.corner_y - l.arm + 2) == chrome::Zone::Resize);
  CHECK(z(tw - 1, th - 1) == chrome::Zone::Bar);                      // the bar row is all drag bar
  CHECK(z(w + 2, l.corner_y + l.arm) == chrome::Zone::Empty);         // below the bracket
  CHECK(z(w + 2, l.corner_y - l.arm - 3 * l.stroke_r - 5) == chrome::Zone::Empty);  // margin above it
  CHECK(z(w - 5, h - 5) == chrome::Zone::Phone);                      // never steals the phone's corner
  CHECK(z(l.back_cx, l.button_cy) == chrome::Zone::Back);
  CHECK(z(l.home_cx, l.button_cy) == chrome::Zone::Home);
  CHECK(z(l.recents_cx, l.button_cy) == chrome::Zone::Recents);
  CHECK(z(l.more_cx, l.button_cy) == chrome::Zone::More);
  CHECK(z(l.hide_cx + l.button_r, l.button_cy) == chrome::Zone::Hide);  // a little forgiving
  CHECK(z((l.recents_cx + l.hide_cx) / 2, l.button_cy) == chrome::Zone::Empty);  // between the groups
  // The menu's buttons only while it's open. Otherwise that's drag bar, down to the bottom row.
  CHECK(z(l.menu_cx(0), l.menu_cy()) == chrome::Zone::Bar);
  CHECK(z(l.menu_cx(2), l.menu_cy()) == chrome::Zone::Bar);
  CHECK(z(l.menu_cx(0), l.menu_cy(), true) == chrome::Zone::Theater);
  CHECK(z(l.menu_cx(0) - l.button_r * 1.1f, l.menu_cy(), true) == chrome::Zone::Theater);
  CHECK(z(l.menu_cx(1), l.menu_cy(), true) == chrome::Zone::Recenter);
  CHECK(z(l.menu_cx(2), l.menu_cy(), true) == chrome::Zone::Audio);
  CHECK(z(l.menu_cx(2) + l.button_r * 1.1f, l.menu_cy(), true) == chrome::Zone::Audio);
  CHECK(z(l.more_cx, l.button_cy, true) == chrome::Zone::More);
  CHECK(z(l.hide_cx, l.button_cy, true) == chrome::Zone::Hide);
  CHECK(z(l.menu_cx(0) - l.button_r * 1.5f, l.menu_cy(), true) == chrome::Zone::Bar);
  CHECK(z(l.menu_cx(0), l.menu_cy() + l.button_r * 1.5f, true) == chrome::Zone::Bar);
  CHECK(z(10, 10, true) == chrome::Zone::Phone);
  CHECK(z(l.back_cx, l.button_cy, true) == chrome::Zone::Back);
  CHECK(z(5, th - 1) == chrome::Zone::Bar);
  CHECK(chrome::is_menu_item(chrome::Zone::Theater));
  CHECK(chrome::is_menu_item(chrome::Zone::Recenter));
  CHECK(chrome::is_menu_item(chrome::Zone::Audio));
  CHECK(!chrome::is_menu_item(chrome::Zone::More));
  // All of More's forgiving hit circle is More, none of it the bracket's grab area.
  for (int i = 0; i < 16; ++i) {
    const float a = static_cast<float>(i) * 3.14159265f / 8.0f, reach = l.button_r * 1.15f;
    CHECK(z(l.more_cx + reach * std::cos(a), l.button_cy + reach * std::sin(a)) == chrome::Zone::More);
  }
  CHECK(z(5, l.button_cy) == chrome::Zone::Empty);

  // The countdown's digits: drawn, and each different from the others.
  const float dr = 90.0f;
  std::vector<std::vector<float>> inks;
  for (const ui::Icon digit : {ui::digit_1_icon, ui::digit_2_icon, ui::digit_3_icon}) {
    std::vector<float> ink;
    float total = 0.0f;
    for (float y = -dr; y < dr; y += 3.0f) {
      for (float x = -dr; x < dr; x += 3.0f) {
        ink.push_back(digit(x, y, dr));
        total += ink.back();
      }
    }
    CHECK(total > 40.0f);
    inks.push_back(ink);
  }
  for (size_t i = 0; i < inks.size(); ++i) {
    for (size_t j = i + 1; j < inks.size(); ++j) {
      float diff = 0.0f;
      for (size_t k = 0; k < inks[i].size(); ++k) diff += std::fabs(inks[i][k] - inks[j][k]);
      CHECK(diff > 20.0f);
    }
  }
  CHECK(ui::digit_1_icon(dr * 0.04f, 0.0f, dr) > 0.9f);    // 1: its stem
  CHECK(ui::digit_1_icon(-dr * 0.2f, dr * 0.2f, dr) < 0.1f);
  CHECK(ui::digit_3_icon(dr * 0.2f, -dr * 0.2f, dr) > 0.9f);   // 3: both bowls' right sides,
  CHECK(ui::digit_3_icon(dr * 0.2f, dr * 0.2f, dr) > 0.9f);
  CHECK(ui::digit_3_icon(-dr * 0.2f, -dr * 0.03f, dr) < 0.1f);  // open on the left
  CHECK(ui::digit_2_icon(0.0f, dr * 0.4f, dr) > 0.9f);         // 2: its base
}

void test_pinch() {
  const auto fingers = [](const panel::Pinch& p, float& x0, float& y0, float& x1, float& y1) {
    p.finger(0, x0, y0);
    p.finger(1, x1, y1);
  };
  float x0, y0, x1, y1;
  // Portrait 864 x 1920 (a Pixel 7's stream). The fingers keep 0.12 of the short side (103.68 px)
  // from the sides and bottom and 0.22 (190.08 px) from the top, where the notification shade
  // is: the area they use is x 103.68..759.32, y 190.08..1815.32.
  const double edge = 0.12 * 864, top = 0.22 * 864, min_span = 0.45 * 864;
  // Anchored in the middle: one above the other (the most room), centred on the anchor. Zooming
  // in starts at their closest.
  panel::Pinch p;
  p.start(432.0f, 960.0f, 864, 1920, true);
  fingers(p, x0, y0, x1, y1);
  CHECK_NEAR(p.span(), min_span, 1e-3);
  CHECK_NEAR(x0, 432.0, 1e-3);
  CHECK_NEAR(x1, 432.0, 1e-3);
  CHECK_NEAR((y0 + y1) / 2.0f, 960.0, 1e-3);
  CHECK_NEAR(y1 - y0, p.span(), 1e-3);
  // Forward spreads by exp(kPinchPerUnit) per unit, back closes as much.
  p.add(1.0f);
  CHECK_NEAR(p.span(), min_span * std::exp(panel::kPinchPerUnit), 1e-2);
  p.add(-1.0f);
  CHECK_NEAR(p.span(), min_span, 1e-2);
  // At their closest, closing further is past the end of the stroke; spreading isn't.
  CHECK(p.spent(-0.1f));
  CHECK(!p.spent(0.1f));
  // Spread as far as they go: the top one stops at the top margin, the bottom one as far from
  // the anchor. Then the stroke is spent and another starts closest.
  for (int i = 0; i < 100; ++i) p.add(1.0f);
  fingers(p, x0, y0, x1, y1);
  CHECK_NEAR(y0, top, 1e-2);
  CHECK_NEAR(y1, 960.0 + (960.0 - top), 1e-2);
  CHECK(y1 <= 1919.0 - edge);
  CHECK(p.spent(0.1f));
  CHECK(!p.spent(-0.1f));
  CHECK(!p.spent(0.0f));
  p.restart(true);
  CHECK_NEAR(p.span(), min_span, 1e-3);
  // Zooming out starts 0.6 of the short side apart, not at their widest, which would put the top
  // finger on an app's toolbar. Closed to their closest, the stroke is spent and starts over.
  p.start(432.0f, 960.0f, 864, 1920, false);
  fingers(p, x0, y0, x1, y1);
  CHECK_NEAR(p.span(), 0.6 * 864, 1e-2);
  CHECK_NEAR(y0, 960.0 - 0.3 * 864, 1e-2);
  CHECK(!p.spent(-0.1f));
  for (int i = 0; i < 100; ++i) p.add(-1.0f);
  CHECK_NEAR(p.span(), min_span, 1e-2);
  CHECK(p.spent(-0.1f));
  p.restart(false);
  CHECK_NEAR(p.span(), 0.6 * 864, 1e-2);
  // Where 0.6 doesn't fit, as wide as fits: 150 px past the side margin, with no room vertically.
  p.start(253.68f, 190.08f, 864, 1920, false);
  CHECK_NEAR(p.span(), 300.0, 1e-2);
  p.add(std::nanf(""));
  CHECK(std::isfinite(p.span()));
  // Near the top edge: the anchor moves down off it, and the fingers go side by side.
  p.start(432.0f, 30.0f, 864, 1920, true);
  fingers(p, x0, y0, x1, y1);
  CHECK_NEAR(y0, top, 1e-3);
  CHECK_NEAR(y1, top, 1e-3);
  CHECK_NEAR(p.span(), min_span, 1e-3);
  // Near the bottom edge: moved up by the smaller margin.
  p.start(432.0f, 1915.0f, 864, 1920, true);
  fingers(p, x0, y0, x1, y1);
  CHECK_NEAR(y0, 1919.0 - edge, 1e-2);
  // In a corner nothing fits: both fingers on the (moved) anchor, and no strokes to repeat.
  p.start(10.0f, 10.0f, 864, 1920, true);
  fingers(p, x0, y0, x1, y1);
  CHECK_NEAR(p.span(), 0.0, 1e-3);
  CHECK_NEAR(x0, edge, 1e-3);
  CHECK_NEAR(y1, top, 1e-3);
  p.add(5.0f);
  CHECK(!p.spent(1.0f));
  CHECK(!p.spent(-1.0f));
  // Landscape: the short side is the height, and side by side has the most room.
  p.start(960.0f, 432.0f, 1920, 864, true);
  fingers(p, x0, y0, x1, y1);
  CHECK_NEAR(p.span(), min_span, 1e-3);
  CHECK_NEAR(y0, 432.0, 1e-3);
  // Outside the image (a rotation race): the anchor is moved onto it, off the edges.
  p.start(5000.0f, -5.0f, 864, 1920, false);
  fingers(p, x0, y0, x1, y1);
  CHECK(x0 <= 759.33f && x1 <= 759.33f && y0 >= 190.07f && y1 >= 190.07f);
  // Turning: fingers 0.6 of the short side across, centred on the anchor, along the angle, and
  // the same span at any angle, all off the edges.
  p.start_turn(432.0f, 960.0f, 864, 1920, 0.0f);
  fingers(p, x0, y0, x1, y1);
  CHECK_NEAR(p.span(), 0.6 * 864, 1e-3);
  CHECK_NEAR(y0, 960.0, 1e-3);
  CHECK_NEAR(x1 - x0, 0.6 * 864, 1e-3);
  p.aim(1.5707963f);  // a quarter turn clockwise on screen (y down): one above the other
  fingers(p, x0, y0, x1, y1);
  CHECK_NEAR(x0, 432.0, 1e-2);
  CHECK_NEAR(y1 - y0, 0.6 * 864, 1e-2);
  CHECK_NEAR(p.span(), 0.6 * 864, 1e-3);
  for (float a = 0.0f; a < 6.3f; a += 0.1f) {
    p.aim(a);
    fingers(p, x0, y0, x1, y1);
    CHECK(x0 >= edge - 1e-2 && x1 <= 863.0 - edge + 1e-2 && y0 >= top - 1e-2 && y1 >= top - 1e-2);
  }
  p.aim(std::nanf(""));
  fingers(p, x0, y0, x1, y1);
  CHECK(std::isfinite(x0) && std::isfinite(y1));
  // Turning, like zooming, never strokes on: the stick can't move a turn's fingers.
  CHECK(!p.spent(1.0f));
  p.add(3.0f);
  CHECK_NEAR(p.span(), 0.6 * 864, 1e-3);
  // Near a side the circle shrinks to fit: 50 px past the margin leaves 100 px across.
  p.start_turn(static_cast<float>(edge) + 50.0f, 960.0f, 864, 1920, 0.0f);
  CHECK_NEAR(p.span(), 100.0, 1e-2);

  // An image too small for the margins: they share it, and nothing is NaN.
  p.start(1.0f, 1.0f, 3, 2, true);
  fingers(p, x0, y0, x1, y1);
  CHECK(std::isfinite(x0) && std::isfinite(y0) && x0 >= 0.0f && x0 <= 2.0f && y0 >= 0.0f && y0 <= 1.0f);
}

void test_depth_glide() {
  // Smooth events glide over several ticks and sum to what was asked, 0.05 m per unit.
  panel::DepthGlide g;
  g.add(0.5f);
  g.add(0.5f);
  float total = 0.0f, first = g.take(0.005f);
  CHECK(first > 0.0f && first < 0.01f);                // a small first step, not a jump
  total += first;
  for (int i = 0; i < 400; ++i) total += g.take(0.005f);
  CHECK_NEAR(total, 0.05, 1e-5);
  CHECK_EQ(g.take(0.005f), 0.0f);
  CHECK_EQ(g.events, 2u);
  CHECK_NEAR(g.travelled_m, 0.05, 1e-5);
  // Negative is nearer. Huge deltas are clamped per event and in total.
  g.reset();
  for (int i = 0; i < 20; ++i) g.add(-5.0f);
  total = 0.0f;
  for (int i = 0; i < 1000; ++i) total += g.take(0.005f);
  CHECK_NEAR(total, -0.15, 1e-5);
  CHECK_NEAR(g.max_abs_delta, 5.0, 1e-6);
  // Speed limit: at most 2 m/s.
  g.reset();
  g.add(1.0f);
  g.add(1.0f);
  g.add(1.0f);
  CHECK(g.take(0.01f) <= 0.02f + 1e-6f);
  CHECK_EQ(g.take(0.0f), 0.0f);
  g.reset();
  g.add(std::nanf(""));
  CHECK_EQ(g.take(0.005f), 0.0f);
  CHECK_EQ(g.events, 0u);
}

void test_facing() {
  {  // faces the eye with X level: no roll, even when looking up at it
    const panel::Vec3 at{0.2f, 1.0f, -0.8f}, eye{0.0f, 1.6f, 0.0f};
    const panel::Mat34 f = panel::facing(at, eye);
    const float dx = eye.x - at.x, dy = eye.y - at.y, dz = eye.z - at.z, len = std::sqrt(dx * dx + dy * dy + dz * dz);
    CHECK_NEAR(f.m[0][2], dx / len, 1e-5);             // +Z at the eye
    CHECK_NEAR(f.m[1][2], dy / len, 1e-5);
    CHECK_NEAR(f.m[2][2], dz / len, 1e-5);
    CHECK_NEAR(f.m[1][0], 0.0, 1e-6);                  // X level
    CHECK(f.m[1][1] > 0.5f);                           // Y up-ish
    CHECK_NEAR(f.m[0][3], at.x, 1e-6);                 // at `at`
    CHECK_NEAR(f.m[1][3], at.y, 1e-6);
    CHECK_NEAR(f.m[2][3], at.z, 1e-6);
    check_rotation(f);
    // A `right` that agrees changes nothing; one that disagrees turns it upside down.
    const panel::Mat34 same = panel::facing(at, eye, {1.0f, 0.3f, 0.0f});
    check_rigid_near(same, f);
    const panel::Mat34 flipped = panel::facing(at, eye, {-1.0f, 0.0f, 0.0f});
    CHECK_NEAR(axis_dot(flipped, 0, f, 0), -1.0, 1e-5);
    CHECK_NEAR(axis_dot(flipped, 1, f, 1), -1.0, 1e-5);
    CHECK_NEAR(axis_dot(flipped, 2, f, 2), 1.0, 1e-5);
    check_rotation(flipped);
  }
  {  // the eye straight above: X is `right` made level, else world X
    const panel::Vec3 at{0.2f, 1.0f, -0.8f};
    const panel::Mat34 above = panel::facing(at, {at.x, at.y + 1.0f, at.z});
    CHECK_NEAR(above.m[1][2], 1.0, 1e-5);
    CHECK_NEAR(above.m[0][0], 1.0, 1e-5);
    const panel::Mat34 hinted = panel::facing(at, {at.x, at.y + 1.0f, at.z}, {0.0f, 0.5f, -2.0f});
    CHECK_NEAR(hinted.m[2][0], -1.0, 1e-5);
    check_rotation(hinted);
  }
  {  // same as a fresh placement straight ahead
    const panel::Mat34 front = panel::place_on_gaze(rot_y(30, 0.1f, 1.6f, 0.2f), 0.65f);
    check_rigid_near(panel::facing(panel::translation(front), {0.1f, 1.6f, 0.2f}), front);
  }
  {  // turned about its own vertical axis: positive brings the left side toward the viewer
    const panel::Mat34 front = panel::place_on_gaze(rot_y(25, 0.2f, 1.6f, 0.1f), 0.65f);
    const panel::Mat34 t = panel::turned(front, 2.0f * 3.14159265f / 180.0f);
    check_rotation(t);
    CHECK_NEAR(t.m[0][3], front.m[0][3], 1e-6);        // same centre
    CHECK_NEAR(t.m[1][0], 0.0, 1e-6);                  // still level: no roll
    const panel::Vec3 eye{0.2f, 1.6f, 0.1f};
    const auto dist = [&](const panel::Mat34& p, float side) {
      const float x = p.m[0][3] + p.m[0][0] * side * 0.1f, z = p.m[2][3] + p.m[2][0] * side * 0.1f;
      return std::hypot(x - eye.x, z - eye.z);
    };
    CHECK(dist(t, -1.0f) < dist(front, -1.0f));        // left edge nearer,
    CHECK(dist(t, 1.0f) > dist(front, 1.0f));          // right edge farther
    CHECK_NEAR(axis_dot(t, 2, front, 2), std::cos(2.0 * 3.14159265 / 180.0), 1e-6);
  }
  {  // square to the line to a point off the centre (the phone screen, up and left of the
     // overlay's centre), not to the centre itself; the position doesn't change
    const panel::Vec3 at{0.1f, 1.3f, -0.7f}, eye{0.0f, 1.6f, 0.1f}, screen{-0.004f, 0.023f, 0.0f};
    const panel::Mat34 f = panel::facing(at, eye, {1.0f, 0.0f, 0.0f}, screen);
    CHECK_NEAR(f.m[0][3], at.x, 1e-6);
    CHECK_NEAR(f.m[1][3], at.y, 1e-6);
    CHECK_NEAR(f.m[2][3], at.z, 1e-6);
    const panel::Vec3 c{at.x + f.m[0][0] * screen.x + f.m[0][1] * screen.y,
                        at.y + f.m[1][0] * screen.x + f.m[1][1] * screen.y,
                        at.z + f.m[2][0] * screen.x + f.m[2][1] * screen.y};
    const float dx = eye.x - c.x, dy = eye.y - c.y, dz = eye.z - c.z, len = std::sqrt(dx * dx + dy * dy + dz * dz);
    CHECK_NEAR(f.m[0][2], dx / len, 1e-5);
    CHECK_NEAR(f.m[1][2], dy / len, 1e-5);
    CHECK_NEAR(f.m[2][2], dz / len, 1e-5);
    CHECK_NEAR(f.m[1][0], 0.0, 1e-6);                  // still no roll
    check_rotation(f);
    // The eye is straight out from the screen's centre, where facing the overlay's centre put it
    // straight out from that (so the screen's top and left leaned away, about 2 and 0.3 degrees).
    const panel::Vec3 eye_in = in_frame(f, eye), eye_plain = in_frame(panel::facing(at, eye), eye);
    CHECK_NEAR(eye_in.x, screen.x, 1e-5);
    CHECK_NEAR(eye_in.y, screen.y, 1e-5);
    CHECK_NEAR(eye_plain.x, 0.0, 1e-5);
    CHECK_NEAR(eye_plain.y, 0.0, 1e-5);
    const panel::Mat34 g = panel::place_on_gaze(rot_y(0, 0, 1.6f, 0), 0.65f, {0.0f, 0.0f, -1.0f}, screen);
    CHECK_NEAR(g.m[1][3], 1.6, 1e-6);                  // still centred on the line of sight
    CHECK_NEAR(g.m[2][3], -0.65, 1e-6);
    check_upright(g);                                  // the screen above it is nearly level with the eye
  }
  {  // facing_upright, like SteamVR's dashboard: turned about the vertical only near eye level,
     // tilting toward the eye only past kUprightWithinRadians above or below it
    const double pi = 3.14159265, limit = panel::kUprightWithinRadians;
    CHECK(limit > 0.2 && limit < 0.6);
    const panel::Vec3 eye{0.3f, 1.6f, -0.2f};
    const float yaw = 0.7f, dist = 0.8f;
    // Elevation `e` radians above the eye (negative = below), along the heading `yaw`.
    const auto at = [&](double e) {
      return panel::Vec3{eye.x - static_cast<float>(dist * std::cos(e) * std::sin(yaw)),
                         eye.y + static_cast<float>(dist * std::sin(e)),
                         eye.z - static_cast<float>(dist * std::cos(e) * std::cos(yaw))};
    };
    // Its slope in radians: positive tilts it back, to look up at it.
    const auto slope = [](const panel::Mat34& p) { return std::asin(static_cast<double>(p.m[1][2])); };
    const auto heading_ok = [&](const panel::Mat34& p) {  // +Z's level part points back at the eye
      const double f = std::hypot(p.m[0][2], p.m[2][2]);
      CHECK_NEAR(p.m[0][2] / f, std::sin(yaw), 1e-4);
      CHECK_NEAR(p.m[2][2] / f, std::cos(yaw), 1e-4);
    };
    for (const double e : {0.0, 0.2, -0.2, limit - 0.01, -(limit - 0.01)}) {
      const panel::Mat34 p = panel::facing_upright(at(e), eye, {1.0f, 0.0f, 0.0f});
      check_upright(p);
      heading_ok(p);
      check_rotation(p);
      CHECK_NEAR(p.m[1][3], at(e).y, 1e-6);  // kept where it was put
    }
    // Past it, at rest, it's square to the eye, as facing makes it, up to straight above or below.
    for (const double e : {limit + 0.01, 0.6, 1.0, 1.55, pi / 2}) {
      for (const double s : {1.0, -1.0}) {
        const panel::Mat34 p = panel::facing_upright(at(s * e), eye, {1.0f, 0.0f, 0.0f});
        check_rigid_near(p, panel::facing(at(s * e), eye, {1.0f, 0.0f, 0.0f}));
        CHECK_NEAR(slope(p), -s * e, 1e-4);            // above the eye it tips forward, down at it
        CHECK_NEAR(p.m[1][0], 0.0, 1e-6);              // never rolls
        if (e < pi / 2) heading_ok(p);
        check_rotation(p);
      }
    }
    // Which side of it the line to the eye is.
    CHECK(!panel::past_upright(at(0.0), eye));
    CHECK(!panel::past_upright(at(limit - 0.01), eye));
    CHECK(!panel::past_upright(at(-(limit - 0.01)), eye));
    CHECK(panel::past_upright(at(limit + 0.01), eye));
    CHECK(panel::past_upright(at(-(limit + 0.01)), eye));
    CHECK(panel::past_upright(at(pi / 2), eye));
    // Turning between the two: a share of the slope, about the same level X, never rolling.
    for (const double e : {0.6, -0.6, 1.2}) {
      const panel::Mat34 square = panel::facing(at(e), eye, {1.0f, 0.0f, 0.0f});
      check_rigid_near(panel::facing_tilted(at(e), eye, 1.0f, {1.0f, 0.0f, 0.0f}), square);
      check_upright(panel::facing_tilted(at(e), eye, 0.0f, {1.0f, 0.0f, 0.0f}));
      const panel::Mat34 half = panel::facing_tilted(at(e), eye, 0.5f, {1.0f, 0.0f, 0.0f});
      CHECK_NEAR(slope(half), -e / 2, 1e-4);
      CHECK_NEAR(half.m[1][0], 0.0, 1e-6);
      heading_ok(half);
      check_rotation(half);
    }
    check_upright(panel::facing_tilted(at(0.6), eye, -3.0f));  // clamped
    CHECK_NEAR(slope(panel::facing_tilted(at(0.6), eye, 7.0f)), -0.6, 1e-4);
    // facing itself always squares to the eye.
    CHECK_NEAR(slope(panel::facing(at(limit + 0.2), eye)), -(limit + 0.2), 1e-4);
    // With the phone screen above the overlay's centre: upright, turned square to the level line
    // to the screen's centre.
    const panel::Vec3 screen{-0.004f, 0.023f, 0.0f};
    const panel::Mat34 f = panel::facing_upright(at(0.3), eye, {1.0f, 0.0f, 0.0f}, screen);
    check_upright(f);
    const float cx = f.m[0][3] + f.m[0][0] * screen.x, cz = f.m[2][3] + f.m[2][0] * screen.x;
    const float lx = eye.x - cx, lz = eye.z - cz, len = std::hypot(lx, lz);
    CHECK_NEAR(f.m[0][2], lx / len, 1e-5);
    CHECK_NEAR(f.m[2][2], lz / len, 1e-5);
  }
}

void test_tilt_animation() {
  const float t = panel::kTiltSeconds;
  CHECK(t > 0.05f && t < 2.0f);
  {  // at rest it stays as it is
    panel::TiltAnimation a;
    CHECK_NEAR(a.update(false, 0.1f), 0.0, 1e-6);
    CHECK(!a.busy());
    a.reset(true);
    CHECK_NEAR(a.update(true, 0.1f), 1.0, 1e-6);
    CHECK(!a.busy());
  }
  {  // crossing the threshold turns it over kTiltSeconds, eased in and out, from no tilt at first
    panel::TiltAnimation a;
    CHECK_NEAR(a.update(true, 0.1f), 0.0, 1e-6);
    CHECK(a.busy());
    CHECK_NEAR(a.update(true, t / 4), 0.15625, 1e-5);  // smoothstep(1/4)
    CHECK_NEAR(a.update(true, t / 4), 0.5, 1e-5);
    CHECK(a.busy());
    CHECK_NEAR(a.update(true, t), 1.0, 1e-6);         // done, and no further
    CHECK(!a.busy());
    // ...and back the same way, down from square.
    CHECK_NEAR(a.update(false, 0.1f), 1.0, 1e-6);
    CHECK_NEAR(a.update(false, t / 2), 0.5, 1e-5);
    CHECK_NEAR(a.update(false, t), 0.0, 1e-6);
    CHECK(!a.busy());
  }
  {  // crossing back mid-turn: the turn finishes first, then it turns back
    panel::TiltAnimation a;
    a.update(true, 0.0f);
    CHECK_NEAR(a.update(false, t / 2), 0.5, 1e-5);   // still turning to square
    CHECK_NEAR(a.update(false, t / 4), 0.84375, 1e-5);
    CHECK_NEAR(a.update(false, t), 1.0, 1e-6);       // square, and the turn back starts from there
    CHECK(a.busy());
    CHECK_NEAR(a.update(false, t / 2), 0.5, 1e-5);
    CHECK_NEAR(a.update(false, t), 0.0, 1e-6);
    CHECK(!a.busy());
  }
  {  // crossing back and then over again before the turn is done: the turn back never starts
    panel::TiltAnimation a;
    a.update(true, 0.0f);
    a.update(false, t / 4);
    a.update(true, t / 4);
    CHECK_NEAR(a.update(true, t), 1.0, 1e-6);
    CHECK(!a.busy());
    CHECK_NEAR(a.update(true, t), 1.0, 1e-6);
  }
  {  // a bad clock doesn't move it; a long gap just finishes the turn
    panel::TiltAnimation a;
    a.update(true, 0.0f);
    CHECK_NEAR(a.update(true, std::nanf("")), 0.0, 1e-6);
    CHECK_NEAR(a.update(true, -1.0f), 0.0, 1e-6);
    CHECK_NEAR(a.update(true, 1e9f), 1.0, 1e-6);
    CHECK(!a.busy());
  }
  {  // reset puts it at rest, mid-turn or not
    panel::TiltAnimation a;
    a.update(true, 0.0f);
    a.update(true, t / 2);
    a.reset(false);
    CHECK(!a.busy());
    CHECK_NEAR(a.update(false, 0.1f), 0.0, 1e-6);
  }
}

void test_laser_free_math() {
  // The pointer's ray onto an upright panel facing +Z (towards the user) at z = -1.
  panel::Mat34 pose = panel::identity();
  pose.m[0][3] = 0.0f;
  pose.m[1][3] = 1.5f;
  pose.m[2][3] = -1.0f;
  const panel::Vec3 centre = panel::translation(pose);
  float x = 0.0f, y = 0.0f;
  CHECK(panel::ray_on_panel(pose, centre, panel::Vec3{0.0f, 1.5f, 0.0f}, panel::Vec3{0.0f, 0.0f, -1.0f}, x, y));
  CHECK_NEAR(x, 0.0, 1e-5);                            // the centre itself
  CHECK_NEAR(y, 0.0, 1e-5);
  CHECK(panel::ray_on_panel(pose, centre, panel::Vec3{0.05f, 1.4f, 0.0f}, panel::Vec3{0.0f, 0.0f, -2.0f}, x, y));
  CHECK_NEAR(x, 0.05, 1e-5);
  CHECK_NEAR(y, 0.1, 1e-5);                            // lower = further down the panel
  const panel::Vec3 foot = panel::foot(pose, 0.2f);   // from another point on the panel
  CHECK(panel::ray_on_panel(pose, foot, panel::Vec3{0.05f, 1.4f, 0.0f}, panel::Vec3{0.0f, 0.0f, -2.0f}, x, y));
  CHECK_NEAR(x, 0.05, 1e-5);
  CHECK_NEAR(y, -0.1, 1e-5);                           // above the bottom edge
  CHECK(!panel::ray_on_panel(pose, centre, panel::Vec3{0, 1.5f, 0}, panel::Vec3{0, 0, 1}, x, y));   // pointing away
  CHECK(!panel::ray_on_panel(pose, centre, panel::Vec3{0, 1.5f, 0}, panel::Vec3{1, 0, 0}, x, y));   // along the plane
  // Turned panels: same answer in the panel's own axes.
  const panel::Mat34 turned = panel::place_on_gaze(panel::multiply(rot_y(70, 0.4f, 1.6f, 0.3f), rot_x(-10, 0)), 0.8f);
  const panel::Vec3 c = panel::translation(turned);
  const panel::Vec3 eye{c.x + turned.m[0][2] * 0.5f + turned.m[0][0] * 0.03f, c.y + turned.m[1][2] * 0.5f,
                        c.z + turned.m[2][2] * 0.5f + turned.m[2][0] * 0.03f};
  CHECK(panel::ray_on_panel(turned, c, eye, panel::Vec3{-turned.m[0][2], -turned.m[1][2], -turned.m[2][2]}, x, y));
  CHECK_NEAR(x, 0.03, 1e-4);                           // 3 cm right of the centre
  CHECK_NEAR(y, 0.0, 1e-4);

  // Stick depth: dead zone, sign, gentle near the centre, full speed (1 m/s) at the edge.
  CHECK_EQ(panel::stick_depth(0.15f, 0.01f), 0.0f);
  CHECK_EQ(panel::stick_depth(-0.19f, 0.01f), 0.0f);
  CHECK_NEAR(panel::stick_depth(1.0f, 0.01f), 0.01, 1e-6);
  CHECK_NEAR(panel::stick_depth(-1.0f, 0.01f), -0.01, 1e-6);
  CHECK_NEAR(panel::stick_depth(0.6f, 0.01f), 0.01 * 0.25, 1e-6);     // halfway past the dead zone
  CHECK(panel::stick_depth(0.3f, 0.01f) < panel::stick_depth(0.6f, 0.01f) / 4);
  CHECK_NEAR(panel::stick_depth(1.0f, 5.0f), 0.1, 1e-6);              // a long stall doesn't jump
  CHECK_EQ(panel::stick_depth(std::nanf(""), 0.01f), 0.0f);
  CHECK_EQ(panel::stick_depth(1.0f, 0.0f), 0.0f);
}

void test_resize() {
  // The bracket of a 0.2 x 0.4 m panel, grabbed at its bottom-right corner, with the centre fixed.
  CHECK_NEAR(panel::resize_scale(0.1f, 0.2f, 0.1f, 0.2f), 1.0, 1e-6);
  CHECK_NEAR(panel::resize_scale(0.1f, 0.2f, 0.15f, 0.3f), 1.5, 1e-5);    // out from the centre
  CHECK_NEAR(panel::resize_scale(0.1f, 0.2f, 0.05f, 0.1f), 0.5, 1e-5);
  // Grabbed elsewhere, e.g. the bracket above the controls: the grabbed point follows the pointer.
  CHECK_NEAR(panel::resize_scale(0.09f, 0.12f, 0.135f, 0.18f), 1.5, 1e-5);
  // Moving off the line from the centre: projected onto it (aspect ratio is fixed).
  CHECK_NEAR(panel::resize_scale(0.1f, 0.2f, 0.3f, 0.2f), (0.3 * 0.1 + 0.2 * 0.2) / (0.1 * 0.1 + 0.2 * 0.2), 1e-5);
  CHECK_EQ(panel::resize_scale(0.1f, 0.2f, 0.0f, 0.0f), 0.0f);         // at the centre: as small as it goes
  CHECK_EQ(panel::resize_scale(0.1f, 0.2f, -0.1f, -0.2f), 0.0f);       // and past it, not back to full size
  CHECK_EQ(panel::resize_scale(0.0f, 0.0f, 1.0f, 1.0f), 1.0f);         // grabbed on the centre itself
  CHECK_EQ(panel::resize_scale(0.1f, 0.2f, std::nanf(""), 0.2f), 1.0f);

  // The foot, below the centre along the panel's own Y.
  const panel::Vec3 down = panel::foot(panel::identity(), 0.2f);
  CHECK_NEAR(down.x, 0.0, 1e-6);
  CHECK_NEAR(down.y, -0.2, 1e-6);
  const panel::Vec3 tipped_foot = panel::foot(rot_x(-30, 1.0f), 0.2f);
  CHECK_NEAR(tipped_foot.y, 1.0 - 0.2 * std::cos(30 * 3.14159265 / 180), 1e-5);
  CHECK_NEAR(tipped_foot.z, 0.2 * std::sin(30 * 3.14159265 / 180), 1e-5);

  // After a resize the panel faces the user again about its centre, which stays where it was in
  // theater too, upright or tilted as it now needs.
  const panel::Vec3 eye{0.3f, 1.6f, 0.2f};
  const panel::Mat34 floor = panel::place_theater(rot_y(-25, eye.x, eye.y, eye.z), 1.2f);
  const panel::Mat34 low = panel::turned(
      panel::facing_upright(panel::translation(floor), eye, {1.0f, 0.0f, 0.0f}), 2.0f * 3.14159265f / 180.0f);
  check_upright(low);                                  // 3.8 m away, its centre below the eye
  CHECK_NEAR(low.m[0][3], floor.m[0][3], 1e-6);
  CHECK_NEAR(low.m[1][3], floor.m[1][3], 1e-6);
  CHECK_NEAR(low.m[2][3], floor.m[2][3], 1e-6);
  // Grown on the floor 2 m away until its centre is well above the eye: tipped toward it about
  // the centre, which stays put.
  const panel::Vec3 near_b{eye.x, 0.0f, eye.z - 2.0f};
  const panel::Mat34 tall = panel::on_floor(near_b, 4.5f, eye);
  const panel::Mat34 tipped = panel::facing_upright(panel::translation(tall), eye, {1.0f, 0.0f, 0.0f});
  CHECK(tipped.m[1][2] < -0.5f);
  CHECK_NEAR(tipped.m[2][3], near_b.z, 1e-6);          // still 2 m in front of the user
  CHECK(panel::foot(tipped, 4.5f).z < near_b.z);       // its foot swings away, not its top over the user
}

void test_grab_math() {
  // inverse_rigid(c) * c is identity, and a grab released without moving keeps the pose.
  const panel::Mat34 head = rot_y(30, 0.2f, 1.6f, -0.4f);
  const panel::Mat34 pose = panel::place_on_gaze(head, 0.65f);
  const panel::Mat34 controller = rot_x(-35, 1.2f);
  const panel::Mat34 id = panel::multiply(panel::inverse_rigid(controller), controller);
  for (int r = 0; r < 3; ++r) {
    for (int c = 0; c < 4; ++c) CHECK_NEAR(id.m[r][c], (r == c ? 1.0 : 0.0), 1e-5);
  }
  const panel::Mat34 relative = panel::multiply(panel::inverse_rigid(controller), pose);
  const panel::Mat34 back = panel::multiply(controller, relative);
  for (int r = 0; r < 3; ++r) {
    for (int c = 0; c < 4; ++c) CHECK_NEAR(back.m[r][c], pose.m[r][c], 1e-5);
  }
  // Depth with the stick: along the eye->panel line, clamped.
  const panel::Vec3 eye = panel::translation(head);
  auto dist = [&](const panel::Mat34& m) {
    const panel::Vec3 t = panel::translation(m);
    return std::sqrt((t.x - eye.x) * (t.x - eye.x) + (t.y - eye.y) * (t.y - eye.y) + (t.z - eye.z) * (t.z - eye.z));
  };
  const double d0 = dist(pose);
  CHECK_NEAR(dist(panel::push_away(pose, eye, 0.2f)), d0 + 0.2, 1e-4);
  CHECK_NEAR(dist(panel::push_away(pose, eye, -5.0f)), panel::kMinDistanceMeters, 1e-4);
  CHECK_NEAR(dist(panel::push_away(pose, eye, 50.0f)), panel::kMaxDistanceMeters, 1e-4);
  CHECK_NEAR(panel::push_away(pose, eye, 0.2f).m[0][0], pose.m[0][0], 1e-6);  // orientation kept
  // In theater it can go much farther.
  CHECK(panel::kMaxTheaterDistanceMeters > panel::kMaxDistanceMeters);
  CHECK_NEAR(dist(panel::push_away(pose, eye, 50.0f, panel::kMaxTheaterDistanceMeters)),
             panel::kMaxTheaterDistanceMeters, 1e-4);
  CHECK_NEAR(dist(panel::push_away(pose, eye, 2.0f, panel::kMaxTheaterDistanceMeters)), d0 + 2.0, 1e-4);

  // Recenter's countdown: 3, 2, 1, then over.
  CHECK_NEAR(panel::kRecenterCountdownSeconds, 3.0, 1e-6);
  CHECK_EQ(panel::countdown_left(0.0f), 3);
  CHECK_EQ(panel::countdown_left(0.99f), 3);
  CHECK_EQ(panel::countdown_left(1.0f), 2);
  CHECK_EQ(panel::countdown_left(2.5f), 1);
  CHECK_EQ(panel::countdown_left(3.0f), 0);
  CHECK_EQ(panel::countdown_left(60.0f), 0);
  CHECK_EQ(panel::countdown_left(-1.0f), 3);
  CHECK_EQ(panel::countdown_left(std::nanf("")), 3);
  // Saved pose round trip, and rejection of garbage.
  panel::Mat34 parsed{};
  CHECK(panel::parse_pose(panel::format_pose(pose), parsed));
  for (int r = 0; r < 3; ++r) {
    for (int c = 0; c < 4; ++c) CHECK_NEAR(parsed.m[r][c], pose.m[r][c], 1e-4);
  }
  CHECK(!panel::parse_pose("", parsed));
  CHECK(!panel::parse_pose("1,0,0,0,0,1,0,0,0,0,1", parsed));             // 11 numbers
  CHECK(!panel::parse_pose("1,0,0,0,0,1,0,0,0,0,1,0,9", parsed));         // 13 numbers
  CHECK(!panel::parse_pose("2,0,0,0,0,1,0,0,0,0,1,0", parsed));           // not orthonormal
  CHECK(!panel::parse_pose("1,0,0,nan,0,1,0,0,0,0,1,0", parsed));
  CHECK(panel::parse_pose("1,0,0,0.5,0,1,0,1.6,0,0,1,-0.7", parsed));
  CHECK_NEAR(parsed.m[1][3], 1.6, 1e-6);
}

void test_dash_math() {
  const float deg = 3.14159265f / 180.0f;
  {  // the bar's transform carries the dashboard scale
    panel::Mat34 scaled = rot_y(30, 0.3f, 0.6f, -1.1f);
    for (int r = 0; r < 3; ++r) {
      for (int c = 0; c < 3; ++c) scaled.m[r][c] *= 0.369f;
    }
    panel::Mat34 rigid;
    float scale = 0.0f;
    CHECK(dash::split_scale(scaled, rigid, scale));
    CHECK_NEAR(scale, 0.369, 1e-5);
    check_rigid_near(rigid, rot_y(30, 0.3f, 0.6f, -1.1f));
    panel::Mat34 flat = panel::identity();
    flat.m[1][1] = 0.0f;
    CHECK(!dash::split_scale(flat, rigid, scale));
  }
  CHECK_NEAR(dash::size_factor(dash::kReferenceBarScale), 1.0, 1e-6);
  CHECK_NEAR(dash::size_factor(0.738f), 2.0, 1e-5);
  CHECK_NEAR(dash::size_factor(10.0f), 2.0, 1e-6);    // clamped
  CHECK_NEAR(dash::size_factor(0.0f), 1.0, 1e-6);     // nonsense: unscaled
  CHECK_NEAR(dash::size_factor(std::numeric_limits<float>::quiet_NaN()), 1.0, 1e-6);

  // The bar leans back 40 degrees from the dashboard: measuring and undoing that gives the
  // dashboard's frame back, whatever the dashboard's own heading and pitch.
  for (const float pitch : {0.0f, 60.0f, -50.0f}) {
    const panel::Mat34 dashboard = panel::multiply(rot_y(25, 0.3f, 0.6f, -1.1f), rot_x(pitch, 0.0f));
    const panel::Mat34 bar = panel::multiply(dashboard, rot_x(-40, 0.0f));
    CHECK_NEAR(dash::bar_tilt(bar, dashboard), 40 * deg, 1e-4);
    check_rigid_near(dash::dashboard_frame(bar, dash::bar_tilt(bar, dashboard)), dashboard);
    // Below the bar and out toward the user in the dashboard's own frame, then toward the head,
    // here a metre straight out from the row.
    const panel::Mat34 at_bar = dash::dashboard_frame(bar, 40 * deg);
    panel::Mat34 head = panel::identity();
    head.m[1][3] = -dash::kBelowBarMeters;
    head.m[2][3] = dash::kOutMeters + 1.0f;
    const panel::Vec3 eye = panel::translation(panel::multiply(at_bar, head));
    const panel::Vec3 rel = in_frame(at_bar, dash::row_center(at_bar, eye, 1.0f));
    CHECK_NEAR(rel.x, 0.0, 1e-5);
    CHECK_NEAR(rel.y, -dash::kBelowBarMeters, 1e-5);
    CHECK_NEAR(rel.z, dash::kOutMeters + dash::kTowardHeadMeters, 1e-5);
  }
  {  // distances follow the bar's scale
    const panel::Mat34 at_bar = panel::identity();
    const panel::Vec3 c = dash::row_center(at_bar, {0.0f, -2 * dash::kBelowBarMeters, 2.0f}, 2.0f);
    CHECK_NEAR(c.x, 0.0, 1e-5);
    CHECK_NEAR(c.y, -2 * dash::kBelowBarMeters, 1e-5);
    CHECK_NEAR(c.z, 2 * (dash::kOutMeters + dash::kTowardHeadMeters), 1e-5);
  }
  {  // row layout and laser hits (mouse Y bottom-up)
    const dash::RowLayout one = dash::row_layout(1);
    CHECK_EQ(one.width(), 128u);
    CHECK_EQ(one.height(), 128u);
    CHECK_NEAR(one.width_m(), 128 * dash::kButtonDiameterMeters / (2 * one.radius), 1e-6);
    CHECK_EQ(dash::hit(one, 64, 64), 0);
    CHECK_EQ(dash::hit(one, 64 + one.radius * 1.1f, 64), 0);   // a little forgiving
    CHECK_EQ(dash::hit(one, 2, 2), -1);                        // corner of the cell
    const dash::RowLayout three = dash::row_layout(3);
    CHECK_EQ(three.width(), 128u + 2 * static_cast<uint32_t>(three.pitch));
    CHECK(three.pitch > 2 * three.radius * 1.2f);               // hit areas don't overlap
    CHECK_EQ(dash::hit(three, three.center_x(2), 64), 2);
    CHECK_EQ(dash::hit(three, (three.center_x(0) + three.center_x(1)) / 2, 64), -1);
    CHECK_EQ(dash::hit(dash::row_layout(0), 64, 64), -1);
  }
  {  // the dashboard button: the panel controls' style, with a phone outline
    const dash::RowLayout l = dash::row_layout(1);
    std::vector<uint8_t> idle(static_cast<size_t>(l.width()) * l.height() * 4, 0), hot(idle.size(), 0);
    ui::draw_round_button(idle.data(), l.width(), l.height(), l.center_x(0), l.center_y(), l.radius, false,
                          ui::phone_icon);
    ui::draw_round_button(hot.data(), l.width(), l.height(), l.center_x(0), l.center_y(), l.radius, true,
                          ui::phone_icon);
    auto px = [&](const std::vector<uint8_t>& im, float x, float y, int c) {
      return im[(static_cast<size_t>(y) * l.width() + static_cast<size_t>(x)) * 4 + static_cast<size_t>(c)];
    };
    const float cx = l.center_x(0), cy = l.center_y(), r = l.radius;
    CHECK(px(idle, cx, cy, 3) > 120);                    // dark disc,
    CHECK(px(idle, cx, cy, 0) < 60);                     // hollow phone
    CHECK(px(idle, cx + r * 0.25f, cy, 0) > 200);        // white outline (side)
    CHECK(px(idle, cx, cy - r * 0.43f, 0) > 200);        // (top)
    CHECK(px(hot, cx, cy, 0) > 200);                     // white disc when highlighted,
    CHECK(px(hot, cx + r * 0.25f, cy, 0) < 60);          // dark outline
    CHECK_EQ(px(idle, 1, 1, 3), 0);                      // transparent outside the disc
  }
}

void test_json() {
  json::Object o;
  CHECK(json::Reader(R"({"cmd":"connect","host":"127.0.0.1","port":40123,"title":"Pixel é",)"
                     R"("turn_screen_off":true,"panel_size_m":0.30,"extra":{"a":[1,2]}})")
            .parse_object(o));
  long port = 0;
  CHECK(json::integer_field(o, "port", &port));
  CHECK_EQ(port, 40123);
  bool off = false;
  CHECK(json::bool_field(o, "turn_screen_off", &off));
  CHECK(off);
  double size = 0;
  CHECK(json::number_field(o, "panel_size_m", &size));
  CHECK_NEAR(size, 0.30, 1e-12);
  CHECK(*json::string_field(o, "title") == "Pixel \xc3\xa9");
  CHECK(!json::Reader("{\"cmd\":").parse_object(o));
  CHECK(!json::Reader("not json").parse_object(o));
  CHECK(!json::Reader("{\"a\":1} trailing").parse_object(o));
  CHECK(!json::Reader("{\"a\":1.5}").parse_object(o) || !json::integer_field(o, "a", &port));

  const std::string line = json::Writer()
                               .str("event", "connected")
                               .str("device", std::string("SM-\"G\"\n\x01\xff", 9))
                               .integer("width", 864)
                               .number("fps", 59.84, 1)
                               .boolean("ok", true)
                               .finish();
  CHECK(line == "{\"event\":\"connected\",\"device\":\"SM-\\\"G\\\"\\n\\u0001\xEF\xBF\xBD\","
                "\"width\":864,\"fps\":59.8,\"ok\":true}");
}

}  // namespace

int main() {
  test_fixed_point();
  test_byte_order();
  test_demuxer_whole_and_split();
  test_demuxer_device_name();
  test_demuxer_errors();
  test_config_merger();
  test_serialize_touch();
  test_serialize_scroll();
  test_serialize_keys();
  test_overlay_mapping();
  test_panel_math();
  test_panel_chrome();
  test_depth_glide();
  test_pinch();
  test_resize();
  test_laser_free_math();
  test_facing();
  test_tilt_animation();
  test_grab_math();
  test_dash_math();
  test_json();
  if (g_failures) {
    std::fprintf(stderr, "protocol_test: %d of %d checks FAILED\n", g_failures, g_checks);
    return 1;
  }
  std::printf("protocol_test: all %d checks passed\n", g_checks);
  return 0;
}
