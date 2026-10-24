#include "panel_math.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <limits>

namespace vrm::panel {

namespace {

bool normalize_xz(float& x, float& z) {
  const float len = std::sqrt(x * x + z * z);
  if (len < 1e-3f) return false;
  x /= len;
  z /= len;
  return true;
}

Vec3 column(const Mat34& m, int c) { return {m.m[0][c], m.m[1][c], m.m[2][c]}; }

// The horizontal direction the HMD faces, as a unit (x, z).
void heading(const Mat34& hmd, float& fx, float& fz) {
  // HMD forward is -Z. Keep only its horizontal component.
  fx = -hmd.m[0][2];
  fz = -hmd.m[2][2];
  if (normalize_xz(fx, fz)) return;
  // Looking straight down or up, the HMD's up axis is horizontal and points forward or back.
  const float sign = -hmd.m[1][2] < 0.0f ? 1.0f : -1.0f;
  fx = sign * hmd.m[0][1];
  fz = sign * hmd.m[2][1];
  if (!normalize_xz(fx, fz)) {
    fx = 0.0f;
    fz = -1.0f;
  }
}

// The unit direction `z` with only `share` of its slope. Its heading is kept.
Vec3 scaled_slope(const Vec3& z, float share) {
  const float flat = std::sqrt(z.x * z.x + z.z * z.z);
  if (flat < 1e-6f) return z;  // straight up or down: no heading to keep
  const float slope = std::atan2(z.y, flat) * share;
  const float k = std::cos(slope) / flat;
  return {z.x * k, std::sin(slope), z.z * k};
}

// Pose at `position` with +Z pointing at `eye` and X level (see facing()), with only `share` of
// Z's slope (scaled_slope) if that's less than 1.
Mat34 aim(const Vec3& position, const Vec3& eye, const Vec3& right, float share) {
  Vec3 z{eye.x - position.x, eye.y - position.y, eye.z - position.z};
  float len = std::sqrt(z.x * z.x + z.y * z.y + z.z * z.z);
  z = len < 1e-4f ? Vec3{0.0f, 0.0f, 1.0f} : Vec3{z.x / len, z.y / len, z.z / len};
  if (share < 1.0f) z = scaled_slope(z, share);
  // X = world up x Z, level, flipped to agree with `right`.
  float xx = z.z, xz = -z.x;
  if (normalize_xz(xx, xz)) {
    if (xx * right.x + xz * right.z < 0.0f) {
      xx = -xx;
      xz = -xz;
    }
  } else {
    xx = right.x;
    xz = right.z;
    if (!normalize_xz(xx, xz)) {
      xx = 1.0f;
      xz = 0.0f;
    }
  }
  const Vec3 x{xx, 0.0f, xz};
  const Vec3 y{z.y * x.z - z.z * x.y, z.z * x.x - z.x * x.z, z.x * x.y - z.y * x.x};  // Z x X
  Mat34 r{};
  const Vec3 axes[4] = {x, y, z, position};
  for (int c = 0; c < 4; ++c) {
    r.m[0][c] = axes[c].x;
    r.m[1][c] = axes[c].y;
    r.m[2][c] = axes[c].z;
  }
  return r;
}

Mat34 face(const Vec3& position, const Vec3& eye, const Vec3& right, const Vec3& content, float share) {
  Mat34 r = aim(position, eye, right, share);
  if (content.x == 0.0f && content.y == 0.0f) return r;
  // Aim from where `content` lands with the current turn, and again: each pass shrinks the error
  // by about the content's offset over its distance, so a few are plenty.
  for (int pass = 0; pass < 4; ++pass) {
    const Vec3 at{position.x + r.m[0][0] * content.x + r.m[0][1] * content.y,
                  position.y + r.m[1][0] * content.x + r.m[1][1] * content.y,
                  position.z + r.m[2][0] * content.x + r.m[2][1] * content.y};
    r = aim(at, eye, right, share);
  }
  r.m[0][3] = position.x;
  r.m[1][3] = position.y;
  r.m[2][3] = position.z;
  return r;
}

}  // namespace

Mat34 identity() {
  Mat34 r{};
  r.m[0][0] = 1.0f;
  r.m[1][1] = 1.0f;
  r.m[2][2] = 1.0f;
  return r;
}

Vec3 translation(const Mat34& m) { return {m.m[0][3], m.m[1][3], m.m[2][3]}; }

Vec3 view_center(float tan_left, float tan_right, float tan_top, float tan_bottom) {
  const float x = (tan_left + tan_right) / 2.0f, y = (tan_top + tan_bottom) / 2.0f;
  // A centre more than 45 degrees off the axis isn't a real lens.
  if (!std::isfinite(x) || !std::isfinite(y) || std::fabs(x) > 1.0f || std::fabs(y) > 1.0f) return {0.0f, 0.0f, -1.0f};
  const float len = std::sqrt(x * x + y * y + 1.0f);
  return {x / len, y / len, -1.0f / len};
}

Mat34 place_on_gaze(const Mat34& hmd, float distance, const Vec3& view, const Vec3& content) {
  // `view` turned into world axes.
  Vec3 f{hmd.m[0][0] * view.x + hmd.m[0][1] * view.y + hmd.m[0][2] * view.z,
         hmd.m[1][0] * view.x + hmd.m[1][1] * view.y + hmd.m[1][2] * view.z,
         hmd.m[2][0] * view.x + hmd.m[2][1] * view.y + hmd.m[2][2] * view.z};
  const float len = std::sqrt(f.x * f.x + f.y * f.y + f.z * f.z);
  f = len < 1e-4f ? Vec3{-hmd.m[0][2], -hmd.m[1][2], -hmd.m[2][2]} : Vec3{f.x / len, f.y / len, f.z / len};
  const Vec3 eye = translation(hmd);
  return facing_upright({eye.x + f.x * distance, eye.y + f.y * distance, eye.z + f.z * distance}, eye,
                        column(hmd, 0), content);
}

Mat34 on_floor(const Vec3& at, float drop, const Vec3& eye, const Vec3& right, const Vec3& content) {
  const Vec3 position{at.x, drop, at.z};
  // Aimed at the eye brought down (or up) to the panel's centre, it stays upright. X is level, so
  // the content's x offset keeps that height and its y offset doesn't change the turn.
  return facing(position, {eye.x, position.y, eye.z}, right, {content.x, 0.0f, 0.0f});
}

Mat34 place_theater(const Mat34& hmd, float drop, const Vec3& content) {
  float fx = 0.0f, fz = 0.0f;
  heading(hmd, fx, fz);
  const Vec3 eye = translation(hmd);
  const float d = kTheaterDistanceMeters;
  return on_floor({eye.x + fx * d, 0.0f, eye.z + fz * d}, drop, eye, column(hmd, 0), content);
}

Mat34 turned(const Mat34& pose, float radians) {
  const float c = std::cos(radians), s = std::sin(radians);
  Mat34 turn = identity();
  turn.m[0][0] = c;
  turn.m[0][2] = s;
  turn.m[2][0] = -s;
  turn.m[2][2] = c;
  return multiply(pose, turn);
}

Mat34 offset_along_up(const Mat34& base, float dy) {
  Mat34 r = base;
  for (int row = 0; row < 3; ++row) r.m[row][3] += base.m[row][1] * dy;
  return r;
}

float panel_width_m(uint32_t w, uint32_t h, float long_side_m) {
  if (w == 0 || h == 0) return long_side_m;
  if (w >= h) return long_side_m;
  return long_side_m * static_cast<float>(w) / static_cast<float>(h);
}

float panel_height_m(uint32_t w, uint32_t h, float long_side_m) {
  if (w == 0 || h == 0) return long_side_m;
  if (h >= w) return long_side_m;
  return long_side_m * static_cast<float>(h) / static_cast<float>(w);
}

Mat34 multiply(const Mat34& a, const Mat34& b) {
  Mat34 r{};
  for (int row = 0; row < 3; ++row) {
    for (int col = 0; col < 4; ++col) {
      float v = a.m[row][0] * b.m[0][col] + a.m[row][1] * b.m[1][col] + a.m[row][2] * b.m[2][col];
      if (col == 3) v += a.m[row][3];
      r.m[row][col] = v;
    }
  }
  return r;
}

Mat34 inverse_rigid(const Mat34& m) {
  Mat34 r{};
  for (int row = 0; row < 3; ++row) {
    for (int col = 0; col < 3; ++col) r.m[row][col] = m.m[col][row];  // R^T
  }
  for (int row = 0; row < 3; ++row) {
    r.m[row][3] = -(r.m[row][0] * m.m[0][3] + r.m[row][1] * m.m[1][3] + r.m[row][2] * m.m[2][3]);
  }
  return r;
}

Mat34 facing(const Vec3& position, const Vec3& eye, const Vec3& right, const Vec3& content) {
  return face(position, eye, right, content, 1.0f);
}

bool past_upright(const Vec3& position, const Vec3& eye, const Vec3& right, const Vec3& content) {
  // Square to the line, the panel's +Z runs along it.
  return std::fabs(facing(position, eye, right, content).m[1][2]) > std::sin(kUprightWithinRadians);
}

Mat34 facing_tilted(const Vec3& position, const Vec3& eye, float share, const Vec3& right, const Vec3& content) {
  return face(position, eye, right, content, std::isfinite(share) ? std::clamp(share, 0.0f, 1.0f) : 1.0f);
}

Mat34 facing_upright(const Vec3& position, const Vec3& eye, const Vec3& right, const Vec3& content) {
  return facing_tilted(position, eye, past_upright(position, eye, right, content) ? 1.0f : 0.0f, right, content);
}

void TiltAnimation::reset(bool square) {
  square_ = square;
  progress_ = 1.0f;
}

float TiltAnimation::update(bool past, float seconds) {
  if (busy() && seconds > 0.0f) progress_ = std::min(1.0f, progress_ + seconds / kTiltSeconds);
  if (!busy() && past != square_) {  // only once the last turn is done
    square_ = past;
    progress_ = 0.0f;
  }
  const float eased = progress_ * progress_ * (3.0f - 2.0f * progress_);  // smoothstep: eases in and out
  return square_ ? eased : 1.0f - eased;
}

Mat34 push_away(const Mat34& pose, const Vec3& eye, float delta, float max_distance) {
  const float dx = pose.m[0][3] - eye.x, dy = pose.m[1][3] - eye.y, dz = pose.m[2][3] - eye.z;
  const float dist = std::sqrt(dx * dx + dy * dy + dz * dz);
  if (dist < 1e-3f) return pose;
  float next = dist + delta;
  if (next < kMinDistanceMeters) next = kMinDistanceMeters;
  if (next > max_distance) next = max_distance;
  Mat34 r = pose;
  const float k = next / dist;
  r.m[0][3] = eye.x + dx * k;
  r.m[1][3] = eye.y + dy * k;
  r.m[2][3] = eye.z + dz * k;
  return r;
}

float resize_scale(float grab_x, float grab_y, float x, float y) {
  const float g2 = grab_x * grab_x + grab_y * grab_y;
  if (!(g2 > 0.0f)) return 1.0f;
  const float s = (x * grab_x + y * grab_y) / g2;
  if (!std::isfinite(s)) return 1.0f;
  return std::max(s, 0.0f);
}

Vec3 foot(const Mat34& pose, float drop) {
  // The overlay origin is the panel centre, X right, Y up.
  return {pose.m[0][3] - pose.m[0][1] * drop, pose.m[1][3] - pose.m[1][1] * drop, pose.m[2][3] - pose.m[2][1] * drop};
}

bool ray_on_panel(const Mat34& pose, const Vec3& anchor, const Vec3& origin, const Vec3& dir,
                  float& x, float& y) {
  const float nx = pose.m[0][2], ny = pose.m[1][2], nz = pose.m[2][2];  // the panel's normal
  const float denom = dir.x * nx + dir.y * ny + dir.z * nz;
  if (std::fabs(denom) < 1e-4f) return false;
  const float t = ((anchor.x - origin.x) * nx + (anchor.y - origin.y) * ny + (anchor.z - origin.z) * nz) / denom;
  if (!(t > 0.0f) || !std::isfinite(t)) return false;
  const float dx = origin.x + t * dir.x - anchor.x;
  const float dy = origin.y + t * dir.y - anchor.y;
  const float dz = origin.z + t * dir.z - anchor.z;
  x = dx * pose.m[0][0] + dy * pose.m[1][0] + dz * pose.m[2][0];
  y = -(dx * pose.m[0][1] + dy * pose.m[1][1] + dz * pose.m[2][1]);
  return true;
}

float scroll_clicks_per_unit(float screens_per_click, float screen_long_dp, uint32_t w, uint32_t h) {
  if (!(screens_per_click > 0.0f) || !std::isfinite(screens_per_click) || w == 0 || h == 0) return 1.0f;
  const float long_dp = screen_long_dp > 0.0f && std::isfinite(screen_long_dp) ? screen_long_dp : kTypicalScreenLongDp;
  const float visible_dp = long_dp * static_cast<float>(h) / static_cast<float>(std::max(w, h));
  return screens_per_click * visible_dp / kAndroidScrollDp;
}

int countdown_left(float elapsed) {
  if (!(elapsed >= 0.0f)) elapsed = 0.0f;  // NaN or a clock step back: from the top
  if (elapsed >= kRecenterCountdownSeconds) return 0;
  return static_cast<int>(std::ceil(kRecenterCountdownSeconds - elapsed));
}

namespace {

constexpr float kStickDeadZone = 0.2f;
constexpr float kStickDepthSpeed = 1.0f;   // metres per second at full deflection

}  // namespace

float stick_depth(float axis, float seconds) {
  if (!std::isfinite(axis) || !(seconds > 0.0f)) return 0.0f;
  const float a = std::min(std::fabs(axis), 1.0f);
  if (a < kStickDeadZone) return 0.0f;
  const float u = (a - kStickDeadZone) / (1.0f - kStickDeadZone);
  return std::copysign(u * u * kStickDepthSpeed * std::min(seconds, 0.1f), axis);
}

namespace {

constexpr float kSmoothScrollMeters = 0.05f;  // per unit of smooth scroll
constexpr float kMaxPendingMeters = 0.15f;    // bounds the glide after the stick is released
constexpr float kGlideSeconds = 0.06f;        // time constant of the glide
constexpr float kMaxDepthSpeed = 2.0f;        // metres per second

}  // namespace

void DepthGlide::add(float delta) {
  if (!std::isfinite(delta)) return;
  ++events;
  max_abs_delta = std::max(max_abs_delta, std::fabs(delta));
  pending_m_ = std::clamp(pending_m_ + std::clamp(delta, -1.0f, 1.0f) * kSmoothScrollMeters, -kMaxPendingMeters,
                          kMaxPendingMeters);
}

float DepthGlide::take(float seconds) {
  if (pending_m_ == 0.0f || !(seconds > 0.0f)) return 0.0f;
  const float dt = std::min(seconds, 0.1f);
  float step = pending_m_ * (1.0f - std::exp(-dt / kGlideSeconds));
  const float cap = kMaxDepthSpeed * dt;
  step = std::clamp(step, -cap, cap);
  if (std::fabs(pending_m_ - step) < 0.0005f) step = pending_m_;  // finish instead of creeping
  pending_m_ -= step;
  travelled_m += step;
  return step;
}

void DepthGlide::reset() { *this = DepthGlide(); }

// The area the fingers may use, and the anchor moved into it. On an image too small for both
// margins of an axis, they share it in proportion.
void Pinch::place(float x, float y, uint32_t w, uint32_t h) {
  short_ = static_cast<float>(std::min(w, h));
  const float edge = kPinchEdgeShare * short_, top_edge = kPinchTopEdgeShare * short_;
  const float width = std::max(static_cast<float>(w) - 1.0f, 0.0f);
  const float height = std::max(static_cast<float>(h) - 1.0f, 0.0f);
  const float sx = edge > 0.0f ? std::min(1.0f, width / (2.0f * edge)) : 1.0f;
  const float sy = edge > 0.0f ? std::min(1.0f, height / (top_edge + edge)) : 1.0f;
  left_ = edge * sx;
  right_ = width - edge * sx;
  top_ = top_edge * sy;
  bottom_ = height - edge * sy;
  x_ = std::clamp(x, left_, right_);
  y_ = std::clamp(y, top_, bottom_);
}

void Pinch::start(float x, float y, uint32_t w, uint32_t h, bool spreading) {
  place(x, y, w, h);
  // How far the fingers can each go from the anchor along (dx, dy) and stay in the area.
  const auto room = [&](float dx, float dy) {
    float t = std::numeric_limits<float>::max();
    if (dx != 0.0f) t = std::min(t, std::min(x_ - left_, right_ - x_) / std::fabs(dx));
    if (dy != 0.0f) t = std::min(t, std::min(y_ - top_, bottom_ - y_) / std::fabs(dy));
    return std::max(t, 0.0f);
  };
  constexpr float kDiagonal = 0.70710678f;
  const float dirs[4][2] = {{1.0f, 0.0f}, {0.0f, 1.0f}, {kDiagonal, kDiagonal}, {kDiagonal, -kDiagonal}};
  float best = -1.0f;
  for (const auto& d : dirs) {
    const float r = room(d[0], d[1]);
    if (r > best) {
      best = r;
      dx_ = d[0];
      dy_ = d[1];
    }
  }
  max_ = 2.0f * best;
  min_ = std::min(kPinchMinSpanShare * short_, max_);
  restart(spreading);
}

void Pinch::start_turn(float x, float y, uint32_t w, uint32_t h, float radians) {
  place(x, y, w, h);
  // The largest circle around the anchor that stays in the area, so any angle fits.
  const float fits = 2.0f * std::max(0.0f, std::min({x_ - left_, right_ - x_, y_ - top_, bottom_ - y_}));
  span_ = min_ = max_ = std::min(kPinchTurnSpanShare * short_, fits);
  aim(radians);
}

void Pinch::aim(float radians) {
  if (!std::isfinite(radians)) return;
  dx_ = std::cos(radians);
  dy_ = std::sin(radians);
}

float Pinch::out_start() const { return std::clamp(kPinchOutStartShare * short_, min_, max_); }

void Pinch::restart(bool spreading) { span_ = spreading ? min_ : out_start(); }

void Pinch::add(float delta) {
  if (!std::isfinite(delta)) return;
  span_ = std::clamp(span_ * std::exp(kPinchPerUnit * delta), min_, max_);
}

bool Pinch::spent(float delta) const {
  constexpr float kMinStroke = 1.1f;  // a stroke shorter than this is no zoom worth repeating
  if (delta > 0.0f) return max_ > min_ * kMinStroke && span_ >= max_;
  if (delta < 0.0f) return out_start() > min_ * kMinStroke && span_ <= min_;
  return false;
}

void Pinch::finger(int i, float& x, float& y) const {
  const float half = (i == 0 ? -0.5f : 0.5f) * span_;
  x = x_ + half * dx_;
  y = y_ + half * dy_;
}

std::string format_pose(const Mat34& m) {
  std::string out;
  char buf[32];
  for (int row = 0; row < 3; ++row) {
    for (int col = 0; col < 4; ++col) {
      std::snprintf(buf, sizeof buf, "%s%.5f", out.empty() ? "" : ",", static_cast<double>(m.m[row][col]));
      out += buf;
    }
  }
  return out;
}

bool parse_pose(const std::string& text, Mat34& out) {
  Mat34 r{};
  const char* p = text.c_str();
  for (int i = 0; i < 12; ++i) {
    char* end = nullptr;
    const float v = std::strtof(p, &end);
    if (end == p || !std::isfinite(v)) return false;
    r.m[i / 4][i % 4] = v;
    p = end;
    if (i < 11) {
      if (*p != ',') return false;
      ++p;
    }
  }
  if (*p != '\0') return false;
  for (int a = 0; a < 3; ++a) {  // columns of the rotation must be orthonormal
    for (int b = 0; b < 3; ++b) {
      const float dot = r.m[0][a] * r.m[0][b] + r.m[1][a] * r.m[1][b] + r.m[2][a] * r.m[2][b];
      if (std::fabs(dot - (a == b ? 1.0f : 0.0f)) > 0.02f) return false;
    }
  }
  out = r;
  return true;
}

}  // namespace vrm::panel
