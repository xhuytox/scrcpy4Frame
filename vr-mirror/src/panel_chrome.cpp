#include "panel_chrome.h"

#include "ui_draw.h"

#include <algorithm>
#include <cmath>
#include <cstring>

namespace vrm::chrome {

namespace {

constexpr float kBarFraction = 0.09f;       // bar row height / short side
constexpr float kStrokeFraction = 0.08f;    // pill and bracket half thickness / bar row height
constexpr float kPillCenterFraction = 0.45f;  // pill centre / bar row height, just above middle
constexpr float kBracketGapFraction = 0.55f;  // bracket gap from the image edges / bar row height
constexpr float kPillWidthFraction = 0.34f;
constexpr float kArmFraction = 0.11f;       // bracket arm / short side
constexpr float kArcFraction = 0.4f;        // bracket corner radius / arm length
constexpr uint8_t kIdleAlpha = 140;
constexpr uint8_t kActiveAlpha = 245;

constexpr float kButtonFraction = 0.13f;    // button diameter / short side
constexpr float kButtonRowScale = 1.4f;     // row height / diameter
constexpr float kButtonCenterFraction = 0.55f;  // button centre / row height
constexpr float kButtonSpacing = 1.2f;      // between button centres in a group, in diameters
constexpr float kGroupSpacing = 1.5f;       // between the two groups' facing buttons, in diameters
constexpr float kButtonHitScale = 1.2f;     // laser jitter: accept hits slightly outside the circle
constexpr float kTrayPadFraction = 0.14f;   // menu tray beyond its buttons / button diameter
constexpr float kTrayAlpha = 0.78f;
constexpr float kTrayRimAlpha = 0.22f;

using ui::coverage;
using ui::over;
using ui::segment_distance;

template <typename Fn>
void for_box(uint8_t* tex, const Layout& l, float cx, float cy, float half_w, float half_h, Fn fn) {
  ui::for_box(tex, l.width(), l.height(), cx, cy, half_w, half_h, fn);
}

void draw_pill(uint8_t* tex, const Layout& l, bool active) {
  const float r = l.stroke_r;
  const float half_len = std::max(r, l.short_side * kPillWidthFraction / 2.0f - r);
  const float cx = l.image_w / 2.0f, cy = l.bar_cy;
  const float alpha = (active ? kActiveAlpha : kIdleAlpha) / 255.0f;
  for_box(tex, l, cx, cy, half_len + r, r, [&](float x, float y, uint8_t* p) {
    const float along = std::clamp(x - cx, -half_len, half_len);
    over(p, 255, 255, 255, alpha * coverage(std::hypot(x - cx - along, y - cy) - r));
  });
}

void draw_bracket(uint8_t* tex, const Layout& l, bool active) {
  const float cx = l.corner_x, cy = l.corner_y, arm = l.arm, rc = arm * kArcFraction;
  const float alpha = (active ? kActiveAlpha : kIdleAlpha) / 255.0f;
  for_box(tex, l, cx - arm / 2.0f, cy - arm / 2.0f, arm / 2.0f + l.stroke_r, arm / 2.0f + l.stroke_r,
          [&](float x, float y, uint8_t* p) {
            float d = std::min(segment_distance(x, y, cx - arm, cy, cx - rc, cy),
                               segment_distance(x, y, cx, cy - arm, cx, cy - rc));
            const float qx = x - (cx - rc), qy = y - (cy - rc);
            if (qx >= 0.0f && qy >= 0.0f) d = std::min(d, std::fabs(std::hypot(qx, qy) - rc));
            over(p, 255, 255, 255, alpha * coverage(d - l.stroke_r));
          });
}

void draw_button(uint8_t* tex, const Layout& l, float bcx, bool hot, ui::Icon icon) {
  ui::draw_round_button(tex, l.width(), l.height(), bcx, l.button_cy, l.button_r, hot, icon);
}

// The open menu's tray: a dark capsule from More down to the menu's row, then along it past the
// last button, with a faint rim. What's under it is cleared first, so nothing shows through.
void draw_tray(uint8_t* tex, const Layout& l) {
  const float cx = l.more_cx, top = l.button_cy, row = l.menu_cy(), right = l.menu_cx(kMenuSize - 1);
  const float r = l.button_r + l.tray_pad;
  const auto tray = [&](float x, float y, uint8_t* p) {
    const float d = std::min(segment_distance(x, y, cx, top, cx, row), segment_distance(x, y, cx, row, right, row)) - r;
    const float a = coverage(d);
    if (a <= 0.0f) return;
    if (a >= 1.0f) std::memset(p, 0, 4);
    over(p, 12, 12, 14, kTrayAlpha * a);
    over(p, 255, 255, 255, kTrayRimAlpha * coverage(std::fabs(d + 1.0f) - 0.75f));
  };
  for_box(tex, l, (cx + right) / 2.0f, (top + row) / 2.0f, (right - cx) / 2.0f + r, (row - top) / 2.0f + r, tray);
}

void draw_menu(uint8_t* tex, const Layout& l, const State& s) {
  for (uint32_t i = 0; i < kMenuSize; ++i) {
    const float cx = l.menu_cx(i), cy = l.menu_cy(), r = l.button_r;
    switch (kMenu[i]) {
      case Zone::Theater:  // shows what pressing it does: grow into theater, or shrink back
        ui::draw_round_button(tex, l.width(), l.height(), cx, cy, r, s.theater,
                              s.theater_on ? ui::shrink_icon : ui::expand_icon);
        break;
      case Zone::Recenter:
        ui::draw_round_button(tex, l.width(), l.height(), cx, cy, r, s.recenter, ui::recenter_icon);
        break;
      case Zone::Audio:  // shows what's true now, as Home's button does
        ui::draw_round_button(tex, l.width(), l.height(), cx, cy, r, s.audio,
                              s.audio_on ? ui::headphones_icon : ui::headphones_off_icon, s.audio_enabled);
        break;
      default:
        break;
    }
  }
}

}  // namespace

Layout layout(uint32_t w, uint32_t h) {
  Layout l;
  l.image_w = w;
  l.image_h = h;
  l.short_side = std::min(w, h);
  const float s = static_cast<float>(l.short_side);
  const float diameter = std::max(28.0f, s * kButtonFraction);
  l.button_r = diameter / 2.0f;
  l.button_h = static_cast<uint32_t>(std::lround(diameter * kButtonRowScale));
  l.button_cy = h + l.button_h * kButtonCenterFraction;
  // Two groups centred under the image: the device keys (Back, Home, Recents), then after a wider
  // gap the panel controls (Hide, More). The menu's row starts under More, at the end.
  const float step = diameter * kButtonSpacing, group_step = diameter * kGroupSpacing;
  l.back_cx = w / 2.0f - (3.0f * step + group_step) / 2.0f;
  l.home_cx = l.back_cx + step;
  l.recents_cx = l.home_cx + step;
  l.hide_cx = l.recents_cx + group_step;
  l.more_cx = l.hide_cx + step;
  l.menu_step = step;
  l.tray_pad = diameter * kTrayPadFraction;
  l.bar_h = std::max<uint32_t>(24, static_cast<uint32_t>(std::lround(s * kBarFraction)));
  // Room under the bar row, and right of the image, for the tray and the menu buttons' hit circles.
  const float menu_reach = std::max(l.button_r * kButtonHitScale, l.button_r + l.tray_pad) + 2.0f;
  const float menu_bottom = l.menu_cy() + menu_reach;
  const float bar_bottom = static_cast<float>(h + l.button_h + l.bar_h);
  l.menu_h = menu_bottom > bar_bottom ? static_cast<uint32_t>(std::ceil(menu_bottom - bar_bottom)) : 0;
  const float menu_right = l.menu_cx(kMenuSize - 1) + menu_reach;
  l.bar_cy = static_cast<float>(h + l.button_h) + l.bar_h * kPillCenterFraction;
  l.stroke_r = std::max(3.0f, l.bar_h * kStrokeFraction);
  const float gap = l.bar_h * kBracketGapFraction;
  l.corner_x = w + gap;
  l.corner_y = h + gap;
  l.arm = std::max(12.0f, s * kArmFraction);
  l.margin_r = static_cast<uint32_t>(std::ceil(std::max(gap + l.stroke_r + 2.0f, menu_right - w)));
  return l;
}

bool is_menu_item(Zone z) {
  for (const Zone item : kMenu) {
    if (z == item) return true;
  }
  return false;
}

Zone zone(const Layout& l, float mouse_x, float mouse_y, bool menu_open) {
  const float x = mouse_x, y = static_cast<float>(l.height()) - mouse_y;  // top-down
  const float image_w = static_cast<float>(l.image_w), image_h = static_cast<float>(l.image_h);
  if (x < image_w && y < image_h) return Zone::Phone;
  const float hit = l.button_r * kButtonHitScale;
  const auto on = [&](float cx, float cy) { return std::hypot(x - cx, y - cy) <= hit; };
  if (menu_open) {  // over the drag bar
    for (uint32_t i = 0; i < kMenuSize; ++i) {
      if (on(l.menu_cx(i), l.menu_cy())) return kMenu[i];
    }
  }
  // Bracket hit box. The image was already excluded, so this is effectively an L.
  const float reach = l.arm + 2.0f * l.stroke_r;
  if (x >= l.corner_x - reach && y >= l.corner_y - reach && y < l.corner_y + l.arm * 0.4f) return Zone::Resize;
  if (y < image_h) return Zone::Empty;  // margin column beside the image
  if (y >= image_h + static_cast<float>(l.button_h)) return Zone::Bar;  // the menu's rows too
  if (on(l.back_cx, l.button_cy)) return Zone::Back;
  if (on(l.home_cx, l.button_cy)) return Zone::Home;
  if (on(l.recents_cx, l.button_cy)) return Zone::Recents;
  if (on(l.more_cx, l.button_cy)) return Zone::More;
  if (on(l.hide_cx, l.button_cy)) return Zone::Hide;
  return Zone::Empty;
}

void draw(uint8_t* texture, const Layout& l, const State& s) {
  const size_t row_bytes = static_cast<size_t>(l.width()) * 4;
  if (l.margin_r > 0) {
    for (uint32_t y = 0; y < l.image_h; ++y) {
      std::memset(texture + y * row_bytes + static_cast<size_t>(l.image_w) * 4, 0, static_cast<size_t>(l.margin_r) * 4);
    }
  }
  std::memset(texture + l.image_h * row_bytes, 0, l.strip_h() * row_bytes);
  if (l.image_w == 0 || l.image_h == 0) return;
  draw_pill(texture, l, s.bar);
  draw_bracket(texture, l, s.resize);
  draw_button(texture, l, l.back_cx, s.back, ui::back_icon);
  draw_button(texture, l, l.home_cx, s.home, ui::home_icon);
  draw_button(texture, l, l.recents_cx, s.recents, ui::recents_icon);
  if (s.menu_open) draw_tray(texture, l);
  draw_button(texture, l, l.more_cx, s.more, ui::more_icon);
  draw_button(texture, l, l.hide_cx, s.hide, ui::hide_icon);
  if (s.menu_open) draw_menu(texture, l, s);
}

}  // namespace vrm::chrome
