#include "dash_math.h"

#include <algorithm>
#include <cmath>

namespace vrm::dash {

namespace {

constexpr uint32_t kCellPx = 128;
constexpr float kRadiusPx = 60.0f;      // 2 px of the cell left for the antialiased edge
constexpr float kHitScale = 1.2f;       // laser jitter: accept hits slightly outside the circle

Vec3 column(const Mat34& m, int c) { return {m.m[0][c], m.m[1][c], m.m[2][c]}; }
float dot(const Vec3& a, const Vec3& b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
float length(const Vec3& v) { return std::sqrt(dot(v, v)); }

void set_column(Mat34& m, int c, const Vec3& v) {
  m.m[0][c] = v.x;
  m.m[1][c] = v.y;
  m.m[2][c] = v.z;
}

}  // namespace

bool split_scale(const Mat34& m, Mat34& rigid, float& scale) {
  rigid = m;
  for (int c = 0; c < 3; ++c) {
    const Vec3 v = column(m, c);
    const float s = length(v);
    if (!std::isfinite(s) || s < 1e-6f) return false;
    set_column(rigid, c, {v.x / s, v.y / s, v.z / s});
    if (c == 0) scale = s;
  }
  return true;
}

float size_factor(float bar_scale) {
  if (!std::isfinite(bar_scale) || bar_scale <= 0.0f) return 1.0f;
  return std::clamp(bar_scale / kReferenceBarScale, 0.5f, 2.0f);
}

float bar_tilt(const Mat34& bar, const Mat34& dashboard) {
  const Vec3 z = column(bar, 2);
  return std::atan2(dot(z, column(dashboard, 1)), dot(z, column(dashboard, 2)));
}

Mat34 dashboard_frame(const Mat34& bar, float tilt) {
  const float c = std::cos(tilt), s = std::sin(tilt);
  const Vec3 y = column(bar, 1), z = column(bar, 2);
  Mat34 r = bar;
  set_column(r, 1, {c * y.x + s * z.x, c * y.y + s * z.y, c * y.z + s * z.z});
  set_column(r, 2, {c * z.x - s * y.x, c * z.y - s * y.y, c * z.z - s * y.z});
  return r;
}

Vec3 row_center(const Mat34& dashboard, const Vec3& eye, float k) {
  const Vec3 o = panel::translation(dashboard), y = column(dashboard, 1), z = column(dashboard, 2);
  const float down = kBelowBarMeters * k, out = kOutMeters * k;
  const Vec3 p{o.x - y.x * down + z.x * out, o.y - y.y * down + z.y * out, o.z - y.z * down + z.z * out};
  const Vec3 d{eye.x - p.x, eye.y - p.y, eye.z - p.z};
  const float len = length(d);
  if (len < 1e-4f) return p;
  const float t = kTowardHeadMeters * k / len;
  return {p.x + d.x * t, p.y + d.y * t, p.z + d.z * t};
}

uint32_t RowLayout::width() const {
  return count == 0 ? 0 : cell + static_cast<uint32_t>(std::lround(pitch * static_cast<float>(count - 1)));
}

float RowLayout::width_m() const {
  return radius > 0.0f ? static_cast<float>(width()) * kButtonDiameterMeters / (2.0f * radius) : 0.0f;
}

RowLayout row_layout(uint32_t count) {
  RowLayout l;
  l.count = count;
  l.cell = kCellPx;
  l.radius = kRadiusPx;
  l.pitch = std::round(2.0f * kRadiusPx * kButtonPitch);
  return l;
}

int hit(const RowLayout& l, float mouse_x, float mouse_y) {
  const float y = static_cast<float>(l.height()) - mouse_y;  // top-down
  for (uint32_t i = 0; i < l.count; ++i) {
    if (std::hypot(mouse_x - l.center_x(i), y - l.center_y()) <= l.radius * kHitScale) return static_cast<int>(i);
  }
  return -1;
}

}  // namespace vrm::dash
