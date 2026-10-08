// Antialiased drawing shared by the panel's controls (panel_chrome) and the dashboard bar
// (dash_bar): round buttons with line icons, in the Steam Frame's own window style.
//
// Textures are RGBA8, straight alpha, tightly packed, top-down.
#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>

namespace vrm::ui {

// Pixel coverage from the signed distance at its centre, for a 1 px antialiased edge.
float coverage(float sd);

float segment_distance(float px, float py, float ax, float ay, float bx, float by);

// Straight-alpha "over": paints colour (0-255) with opacity `a` (0-1) onto pixel `p`.
void over(uint8_t* p, float r, float g, float b, float a);

// Calls fn(x, y, pixel) for every pixel whose centre is within the box around (cx, cy).
template <typename Fn>
void for_box(uint8_t* tex, uint32_t tex_w, uint32_t tex_h, float cx, float cy, float half_w, float half_h,
             Fn fn) {
  const int w = static_cast<int>(tex_w), h = static_cast<int>(tex_h);
  const int x0 = std::max(0, static_cast<int>(cx - half_w - 2)), x1 = std::min(w, static_cast<int>(cx + half_w + 2));
  const int y0 = std::max(0, static_cast<int>(cy - half_h - 2)), y1 = std::min(h, static_cast<int>(cy + half_h + 2));
  for (int y = y0; y < y1; ++y) {
    for (int x = x0; x < x1; ++x) {
      fn(x + 0.5f, y + 0.5f, tex + (static_cast<size_t>(y) * static_cast<size_t>(w) + static_cast<size_t>(x)) * 4);
    }
  }
}

// An icon returns its coverage at (x, y) relative to the button centre, for a button of radius r
// (y down).
using Icon = float (*)(float x, float y, float r);

float back_icon(float x, float y, float r);     // left-pointing chevron
float home_icon(float x, float y, float r);     // ring
float recents_icon(float x, float y, float r);  // rounded square outline
float more_icon(float x, float y, float r);     // three vertical dots
float expand_icon(float x, float y, float r);   // four corners pointing out (theater: off)
float shrink_icon(float x, float y, float r);   // four corners pointing in (theater: on)
float hide_icon(float x, float y, float r);     // eye with a slash
float phone_icon(float x, float y, float r);    // phone outline, as in the app's "phone" icon
// As the app's LineIcon.qml icons of the same names.
float recenter_icon(float x, float y, float r);        // ring with four ticks
float headphones_icon(float x, float y, float r);      // audio casting on
float headphones_off_icon(float x, float y, float r);  // audio casting off: with a slash
// Digits for the recenter countdown.
float digit_1_icon(float x, float y, float r);
float digit_2_icon(float x, float y, float r);
float digit_3_icon(float x, float y, float r);

// Idle: a dark disc with a faint rim and a white icon, readable on bright and dark scenes.
// Hot (hovered or pressed): a white disc with a dark icon. Disabled: idle with a faint icon.
void draw_round_button(uint8_t* tex, uint32_t tex_w, uint32_t tex_h, float cx, float cy, float r, bool hot,
                       Icon icon, bool enabled = true);

}  // namespace vrm::ui
