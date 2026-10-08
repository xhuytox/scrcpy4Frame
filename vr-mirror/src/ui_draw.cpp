#include "ui_draw.h"

#include <cmath>

namespace vrm::ui {

namespace {

float stroke_of(float r) { return std::max(2.0f, r * 0.10f); }

}  // namespace

float coverage(float sd) { return std::clamp(0.5f - sd, 0.0f, 1.0f); }

float segment_distance(float px, float py, float ax, float ay, float bx, float by) {
  const float vx = bx - ax, vy = by - ay;
  const float t = std::clamp(((px - ax) * vx + (py - ay) * vy) / (vx * vx + vy * vy), 0.0f, 1.0f);
  return std::hypot(px - ax - t * vx, py - ay - t * vy);
}

void over(uint8_t* p, float r, float g, float b, float a) {
  if (a <= 0.0f) return;
  const float da = p[3] / 255.0f;
  const float oa = a + da * (1.0f - a);
  const float src[3] = {r, g, b};
  for (int c = 0; c < 3; ++c) {
    p[c] = static_cast<uint8_t>(std::lround((src[c] * a + p[c] * da * (1.0f - a)) / oa));
  }
  p[3] = static_cast<uint8_t>(std::lround(oa * 255.0f));
}

float back_icon(float x, float y, float r) {
  const float s = r * 0.36f, stroke = stroke_of(r);
  const float tip_x = -s * 0.55f;
  const float d = std::min(segment_distance(x, y, tip_x, 0.0f, tip_x + s, -s),
                           segment_distance(x, y, tip_x, 0.0f, tip_x + s, s));
  return coverage(d - stroke / 2.0f);
}

// As on Android's navigation bar.
float home_icon(float x, float y, float r) {
  return coverage(std::fabs(std::hypot(x, y) - r * 0.31f) - stroke_of(r) / 2.0f);
}

// As on Android's navigation bar.
float recents_icon(float x, float y, float r) {
  const float half = r * 0.27f, rc = r * 0.08f;  // half side, corner radius
  const float qx = std::fabs(x) - (half - rc), qy = std::fabs(y) - (half - rc);
  const float d = std::hypot(std::max(qx, 0.0f), std::max(qy, 0.0f)) + std::min(std::max(qx, qy), 0.0f) - rc;
  return coverage(std::fabs(d) - stroke_of(r) / 2.0f);
}

// As in Android's overflow menus.
float more_icon(float x, float y, float r) {
  const float dot_r = stroke_of(r) * 1.15f, gap = r * 0.32f;
  return coverage(std::min({std::hypot(x, y + gap), std::hypot(x, y), std::hypot(x, y - gap)}) - dot_r);
}

namespace {

// Four L-shaped corners of a wide frame, with each L's vertex at its corner of the frame
// (`inward` false) or turned to face the centre (`inward` true).
float corners_icon(float x, float y, float r, bool inward) {
  const float ox = r * 0.50f, oy = r * 0.36f;  // the frame's half width / height
  const float ix = r * 0.26f, iy = r * 0.12f;  // where the arms end
  const float ax = std::fabs(x), ay = std::fabs(y);  // the four corners are mirror images
  const float vx = inward ? ix : ox, vy = inward ? iy : oy;
  const float d = std::min(segment_distance(ax, ay, vx, vy, inward ? ox : ix, vy),
                           segment_distance(ax, ay, vx, vy, vx, inward ? oy : iy));
  return coverage(d - stroke_of(r) / 2.0f);
}

}  // namespace

float expand_icon(float x, float y, float r) { return corners_icon(x, y, r, false); }

float shrink_icon(float x, float y, float r) { return corners_icon(x, y, r, true); }

float hide_icon(float x, float y, float r) {
  const float ew = r * 0.56f, eh = r * 0.30f;  // half width / height of the eye
  const float stroke = stroke_of(r);
  // The eye's outline is the lens where two circles overlap.
  const float d = (ew * ew - eh * eh) / (2.0f * eh), big = eh + d;
  const float lens = std::max(std::hypot(x, y - d) - big, std::hypot(x, y + d) - big);
  float eye = std::max(coverage(std::fabs(lens) - stroke / 2.0f), coverage(std::hypot(x, y) - eh * 0.55f));
  // Cut a gap around the slash so it reads at small sizes.
  const float s = r * 0.5f;
  const float slash = segment_distance(x, y, -s, -s, s, s);
  eye *= 1.0f - coverage(slash - stroke * 1.4f);
  return std::max(eye, coverage(slash - stroke / 2.0f));
}

// Rounded rectangle with a short line near the bottom edge (LineIcon.qml's "phone").
float phone_icon(float x, float y, float r) {
  const float stroke = stroke_of(r);
  const float hw = r * 0.25f, hh = r * 0.43f, rc = r * 0.1f;  // half width / height, corner radius
  const float qx = std::fabs(x) - (hw - rc), qy = std::fabs(y) - (hh - rc);
  const float d = std::hypot(std::max(qx, 0.0f), std::max(qy, 0.0f)) + std::min(std::max(qx, qy), 0.0f) - rc;
  const float outline = coverage(std::fabs(d) - stroke / 2.0f);
  const float line_y = hh * 0.66f, line_hw = hw * 0.36f;
  return std::max(outline, coverage(segment_distance(x, y, -line_hw, line_y, line_hw, line_y) - stroke / 2.0f));
}

namespace {

// The app's icons are drawn on a 24-unit grid centred on (12, 12). The phone's 19 units are
// 0.86 r tall here, as phone_icon is.
constexpr float kGridUnit = 0.045f;

// Distance to the arc of radius `rad` around (cx, cy) from angle a0 to a1 (radians, y down,
// clockwise on screen from a0 to a1).
float arc_distance(float x, float y, float cx, float cy, float rad, float a0, float a1) {
  const float a = std::atan2(y - cy, x - cx);
  for (const float turn : {-6.2831853f, 0.0f, 6.2831853f}) {
    if (a + turn >= a0 && a + turn <= a1) return std::fabs(std::hypot(x - cx, y - cy) - rad);
  }
  return std::min(std::hypot(x - cx - rad * std::cos(a0), y - cy - rad * std::sin(a0)),
                  std::hypot(x - cx - rad * std::cos(a1), y - cy - rad * std::sin(a1)));
}

// Signed distance to a rounded rectangle centred at (cx, cy).
float round_rect(float x, float y, float cx, float cy, float hw, float hh, float rc) {
  const float qx = std::fabs(x - cx) - (hw - rc), qy = std::fabs(y - cy) - (hh - rc);
  return std::hypot(std::max(qx, 0.0f), std::max(qy, 0.0f)) + std::min(std::max(qx, qy), 0.0f) - rc;
}

// The headband and both ear cups (LineIcon.qml's "headphones"), as a distance to the strokes.
float headphones_distance(float x, float y, float r) {
  const float u = r * kGridUnit;
  const float side = 7.5f * u, top = 0.5f * u, end = 5.0f * u;  // band: x = ±7.5, y 12.5 -> 17
  float d = std::min(segment_distance(std::fabs(x), y, side, top, side, end),
                     arc_distance(x, y, 0.0f, top, side, -3.1415927f, 0.0f));
  // Cups: x 4.5..8 (mirrored), y 14..20, corner radius 1.5.
  d = std::min(d, std::fabs(round_rect(std::fabs(x), y, 6.25f * u, 5.0f * u, 1.75f * u, 3.0f * u, 1.5f * u)));
  return d;
}

// The countdown's digits are 0.8 r tall, a little bolder than the icons.
float digit_stroke(float r) { return std::max(3.0f, r * 0.13f); }

}  // namespace

float recenter_icon(float x, float y, float r) {
  const float u = r * kGridUnit, stroke = stroke_of(r);
  const float ax = std::fabs(x), ay = std::fabs(y);
  float d = std::fabs(std::hypot(x, y) - 3.5f * u);
  d = std::min({d, segment_distance(ax, ay, 0.0f, 5.5f * u, 0.0f, 9.5f * u),
                segment_distance(ax, ay, 5.5f * u, 0.0f, 9.5f * u, 0.0f)});
  return coverage(d - stroke / 2.0f);
}

float headphones_icon(float x, float y, float r) {
  return coverage(headphones_distance(x, y, r) - stroke_of(r) / 2.0f);
}

float headphones_off_icon(float x, float y, float r) {
  const float u = r * kGridUnit, stroke = stroke_of(r);
  // As hide_icon: a gap around the slash so it reads at small sizes.
  const float slash = segment_distance(x, y, -8.5f * u, -8.5f * u, 8.5f * u, 8.5f * u);
  const float phones = headphones_icon(x, y, r) * (1.0f - coverage(slash - stroke * 1.4f));
  return std::max(phones, coverage(slash - stroke / 2.0f));
}

float digit_1_icon(float x, float y, float r) {
  const float h = r * 0.4f, w = r * 0.26f, sx = w * 0.15f;
  const float d = std::min(segment_distance(x, y, sx, -h, sx, h),            // stem
                           segment_distance(x, y, sx, -h, sx - w * 0.75f, -h + w * 0.6f));  // flag
  return coverage(d - digit_stroke(r) / 2.0f);
}

float digit_2_icon(float x, float y, float r) {
  const float h = r * 0.4f, w = r * 0.24f;
  // A bowl over the top from the upper left round to the right, then down to the bottom left
  // and along the base.
  const float cy = -h + w, end = 0.6f;  // radians below the bowl's rightmost point
  const float ex = w * std::cos(end), ey = cy + w * std::sin(end);
  const float d = std::min({arc_distance(x, y, 0.0f, cy, w, -2.8f, end), segment_distance(x, y, ex, ey, -w, h),
                            segment_distance(x, y, -w, h, w, h)});
  return coverage(d - digit_stroke(r) / 2.0f);
}

float digit_3_icon(float x, float y, float r) {
  const float h = r * 0.4f, bowl = h / 2.0f;
  // Two bowls open to the left, meeting in the middle.
  const float d = std::min(arc_distance(x, y, 0.0f, -bowl, bowl, -2.6f, 1.5707963f),
                           arc_distance(x, y, 0.0f, bowl, bowl, -1.5707963f, 2.6f));
  return coverage(d - digit_stroke(r) / 2.0f);
}

void draw_round_button(uint8_t* tex, uint32_t tex_w, uint32_t tex_h, float cx, float cy, float r, bool hot,
                       Icon icon, bool enabled) {
  for_box(tex, tex_w, tex_h, cx, cy, r, r, [&](float x, float y, uint8_t* p) {
    const float px = x - cx, py = y - cy, dist = std::hypot(px, py);
    const float disc = coverage(dist - r);
    if (disc <= 0.0f) return;
    const float ink = icon(px, py, r);
    if (hot && enabled) {
      over(p, 255, 255, 255, 0.95f * disc);
      over(p, 24, 24, 24, ink);
    } else {
      over(p, 16, 16, 16, 0.62f * disc);
      over(p, 255, 255, 255, 0.3f * coverage(std::fabs(dist - (r - 1.0f)) - 0.75f));
      over(p, 255, 255, 255, (enabled ? 0.92f : 0.35f) * ink);
    }
  });
}

}  // namespace vrm::ui
