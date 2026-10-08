// Placement and layout for our button row under SteamVR's dashboard bar (dash_bar.h), with no
// OpenVR dependency. Poses use panel::Mat34 (same layout as vr::HmdMatrix34_t).
//
// The bar (valve.steam.gamepadui.bar) leans back about 40 degrees from the dashboard panel, like
// a shelf. The dashboard itself pitches to face the head when it's placed high or low. So the row
// is placed in the dashboard's frame (the bar's frame with that lean undone): centred under the
// bar, a little out toward the user, then turned to face the head. The bar's own width can't be
// read (it's a scene-graph panel that grows with open tabs), so only its centre is used.
//
// The distances were tuned in the headset on the Steam Frame, where the bar's transform carries a
// scale of kReferenceBarScale. Distances and sizes follow that scale if it changes.
#pragma once

#include "panel_math.h"

#include <cstdint>

namespace vrm::dash {

using panel::Mat34;
using panel::Vec3;

constexpr float kReferenceBarScale = 0.369f;
constexpr float kDefaultBarTilt = 0.6981317f;    // 40 degrees, until measured against the panel
constexpr float kBelowBarMeters = 0.11f;         // down the dashboard plane from the bar's centre
constexpr float kOutMeters = 0.06f;              // out of the dashboard plane, toward the user
constexpr float kTowardHeadMeters = 0.02f;       // then along the line to the head
constexpr float kButtonDiameterMeters = 0.06f;
constexpr float kButtonPitch = 1.25f;            // between button centres, in diameters

// Splits a transform with a uniform scale (OpenVR reports the bar's that way) into a rigid pose
// and the scale. False if the rotation part is degenerate.
bool split_scale(const Mat34& m, Mat34& rigid, float& scale);

// Distance and size multiplier for a bar transform scale, kept within [0.5, 2].
float size_factor(float bar_scale);

// How far the bar leans back from the dashboard panel, in radians: the rotation about their
// shared X axis that takes the panel's +Z (toward the user) to the bar's. Both poses rigid.
float bar_tilt(const Mat34& bar, const Mat34& dashboard);

// The dashboard's frame at the bar's centre: `bar` turned back by `tilt` about its X axis.
Mat34 dashboard_frame(const Mat34& bar, float tilt);

// Centre of the row for the dashboard frame from dashboard_frame(), with the head at `eye` and
// distances multiplied by `k` (size_factor).
Vec3 row_center(const Mat34& dashboard, const Vec3& eye, float k);

// Texture layout of a row of round buttons, left to right.
struct RowLayout {
  uint32_t count = 0;
  float radius = 0.0f;  // px
  float pitch = 0.0f;   // px between button centres
  uint32_t cell = 0;    // px, square cell around each button

  uint32_t width() const;
  uint32_t height() const { return cell; }
  float center_x(uint32_t i) const { return cell / 2.0f + pitch * static_cast<float>(i); }
  float center_y() const { return cell / 2.0f; }
  // Overlay width in metres at k = 1.
  float width_m() const;
};

RowLayout row_layout(uint32_t count);

// Index of the button under the laser, or -1. OpenVR mouse coordinates span the texture with Y
// bottom-up.
int hit(const RowLayout& l, float mouse_x, float mouse_y);

}  // namespace vrm::dash
