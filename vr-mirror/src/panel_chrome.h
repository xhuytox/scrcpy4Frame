// Controls drawn around the phone image: round buttons, a drag bar, a resize bracket and the
// More (overflow) menu. They live in transparent margins of the panel's own texture, so no
// second overlay is needed.
//
// Texture layout (top-down):
//
//   +-------------+---+
//   |             |   |   phone image (w x h) at top left, with a
//   |    phone    |   |   margin column on the right for the
//   |             |  ||   bracket's vertical arm
//   +-------------+__||
//   |(<)(o)(□) (ø)(⋮) |      button row: Back, Home, Recents, then Hide, More
//   |    =====    (▭)(▭)(▭)  bar row: drag pill. The open menu is a tray hanging from More with
//   |                 |      its buttons in a row going right, over the bar row and into rows
//   +-----------------+      kept for it at the bottom (drag bar when closed)
//
// The menu's rows, and the margin column's width past the last menu button, are always there,
// so opening it never changes the texture size: a resize makes the overlay blink (see
// vulkan_texture.cpp).
#pragma once

#include <cstdint>

namespace vrm::chrome {

// Positions are texture pixels, top-down from the texture's top-left corner.
struct Layout {
  uint32_t image_w = 0;   // phone image
  uint32_t image_h = 0;
  uint32_t margin_r = 0;  // column right of the image
  uint32_t button_h = 0;  // button row, directly under the image
  uint32_t bar_h = 0;     // drag-bar row, at the bottom
  uint32_t short_side = 0;
  float button_r = 0.0f;
  float button_cy = 0.0f;
  float back_cx = 0.0f;
  float home_cx = 0.0f;
  float recents_cx = 0.0f;
  float hide_cx = 0.0f;
  float more_cx = 0.0f;
  float menu_step = 0.0f;  // from More down to the menu's row, and between its buttons
  float tray_pad = 0.0f;   // the menu's tray reaches this far past its buttons
  uint32_t menu_h = 0;     // rows under the bar row, for the open menu
  float bar_cy = 0.0f;    // pill centre line
  float stroke_r = 0.0f;  // half thickness of the pill and bracket
  float corner_x = 0.0f;  // where the bracket's arms meet
  float corner_y = 0.0f;
  float arm = 0.0f;       // bracket arm length from the corner

  uint32_t width() const { return image_w + margin_r; }
  uint32_t height() const { return image_h + strip_h(); }
  uint32_t strip_h() const { return button_h + bar_h + menu_h; }  // rows under the image
  // The menu's buttons: one row under More, the first right under it.
  float menu_cx(uint32_t i) const { return more_cx + menu_step * static_cast<float>(i); }
  float menu_cy() const { return button_cy + menu_step; }
};

// Sizes scale with the image's short side so controls look the same in either orientation.
Layout layout(uint32_t w, uint32_t h);

enum class Zone { Phone, Bar, Resize, Back, Home, Recents, More, Hide, Theater, Recenter, Audio, Empty };

// The More menu's buttons, left to right.
constexpr Zone kMenu[] = {Zone::Theater, Zone::Recenter, Zone::Audio};
constexpr uint32_t kMenuSize = sizeof kMenu / sizeof kMenu[0];

bool is_menu_item(Zone z);

// Hit test in OpenVR mouse coordinates, which span the whole texture with Y bottom-up. Menu
// buttons are only hit while the menu is open.
Zone zone(const Layout& l, float mouse_x, float mouse_y, bool menu_open);

// Highlighted controls (hovered, pressed or in use), plus what the menu shows.
struct State {
  bool bar = false;
  bool resize = false;
  bool back = false;
  bool home = false;
  bool recents = false;
  bool more = false;
  bool hide = false;
  bool menu_open = false;
  bool theater = false;
  bool theater_on = false;  // its button shows shrink instead of expand
  bool recenter = false;
  bool audio = false;
  bool audio_on = false;       // casting: headphones, otherwise headphones with a slash
  bool audio_enabled = true;   // false while it changes or if the device can't cast

  bool operator==(const State& o) const {
    return bar == o.bar && resize == o.resize && back == o.back && home == o.home && recents == o.recents &&
           more == o.more && hide == o.hide && menu_open == o.menu_open && theater == o.theater &&
           theater_on == o.theater_on && recenter == o.recenter && audio == o.audio && audio_on == o.audio_on &&
           audio_enabled == o.audio_enabled;
  }
  bool operator!=(const State& o) const { return !(*this == o); }
};

// Redraws everything outside the phone image. `texture` is l.width() x l.height() RGBA8,
// straight alpha, tightly packed, top-down. Phone image pixels are left untouched.
void draw(uint8_t* texture, const Layout& l, const State& s);

// Zoom mode's anchor, drawn over the phone image at (x, y) in texture pixels: a white ring with a
// dot in it, edged in dark so it shows on any content. Pixels outside the image are left untouched.
// anchor_reach is how far its pixels go from (x, y).
float anchor_reach(const Layout& l);
void draw_anchor(uint8_t* texture, const Layout& l, float x, float y);

}  // namespace vrm::chrome
