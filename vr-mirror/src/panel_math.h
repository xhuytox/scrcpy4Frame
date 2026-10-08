// Panel placement math with no OpenVR dependency. Mat34 has the same layout as
// vr::HmdMatrix34_t (row-major float[3][4], columns X, Y, Z, translation) so they can be memcpy'd.
#pragma once

#include <cstdint>
#include <string>

namespace vrm::panel {

struct Mat34 {
  float m[3][4];
};

struct Vec3 {
  float x, y, z;
};

constexpr float kDefaultLongSideMeters = 0.30f;  // portrait height / landscape width
constexpr float kDefaultTheaterLongSideMeters = 3.5f;   // a big screen 3.8 m away, standing on the floor
constexpr float kDistanceMeters = 0.65f;         // along the line of sight on first placement / recenter
constexpr float kMinDistanceMeters = 0.25f;      // depth limits while carrying
constexpr float kMaxDistanceMeters = 3.0f;
constexpr float kMaxTheaterDistanceMeters = 8.0f;  // in theater mode
constexpr float kMinLongSideMeters = 0.12f;      // resize limits for the long side
constexpr float kMaxLongSideMeters = 2.0f;
constexpr float kMaxTheaterLongSideMeters = 8.0f;  // in theater mode
constexpr float kTheaterDistanceMeters = 3.8f;   // straight ahead in theater mode

Mat34 identity();
Vec3 translation(const Mat34& m);

// Pose at `position` facing `eye` with X level, so it never rolls: square to the line from `eye`
// to `content`, a point on the panel given in its own axes from `position` (z ignored). That is
// the middle of what the user looks at, the panel's own centre by default; the VR panel uses the
// phone screen's, which sits above and left of the overlay's. Of the two level X axes, the one
// closer to `right` is used, so a panel overhead isn't upside down for someone lying back. If
// `eye` is directly above or below, X is `right` made level, or world X if `right` is zero or
// vertical.
Mat34 facing(const Vec3& position, const Vec3& eye, const Vec3& right = {0.0f, 0.0f, 0.0f},
             const Vec3& content = {0.0f, 0.0f, 0.0f});

// The panel faces the user the way the Steam Frame's dashboard does: turned about the vertical
// only, standing upright, while the line from `eye` to `content` is within this angle of level,
// and square to that line, as facing makes it, past it. It turns from one to the other over
// kTiltSeconds (TiltAnimation). A tilt eased in with the angle looked too little tilted in the
// headset.
constexpr float kUprightWithinRadians = 25.0f * 3.14159265f / 180.0f;

// How long the panel takes to turn between upright and square. Tune by feel.
constexpr float kTiltSeconds = 0.3f;

// Whether the line from `eye` to `content` (as for facing) is more than kUprightWithinRadians
// from level.
bool past_upright(const Vec3& position, const Vec3& eye, const Vec3& right = {0.0f, 0.0f, 0.0f},
                  const Vec3& content = {0.0f, 0.0f, 0.0f});

// As facing, but with only `share` (0 to 1) of the slope of the line to the eye: 0 stands it
// upright, turned about the vertical only, and 1 is facing.
Mat34 facing_tilted(const Vec3& position, const Vec3& eye, float share, const Vec3& right = {0.0f, 0.0f, 0.0f},
                    const Vec3& content = {0.0f, 0.0f, 0.0f});

// As it rests: facing_tilted, upright or square by past_upright.
Mat34 facing_upright(const Vec3& position, const Vec3& eye, const Vec3& right = {0.0f, 0.0f, 0.0f},
                     const Vec3& content = {0.0f, 0.0f, 0.0f});

// The panel's turn between upright and square over time. Crossing kUprightWithinRadians starts it
// turning, eased in and out over kTiltSeconds. Crossing back meanwhile changes nothing until that
// turn is done; it then turns back if the panel is still on the other side, so crossing again
// before then cancels that.
class TiltAnimation {
 public:
  // At rest, square or upright.
  void reset(bool square);
  // `seconds` on, with the panel `past` kUprightWithinRadians or not (past_upright): the share of
  // the slope to give it (facing_tilted).
  float update(bool past, float seconds);
  bool busy() const { return progress_ < 1.0f; }

 private:
  bool square_ = false;    // where the current or last turn goes
  float progress_ = 1.0f;  // 0 to 1 through it
};

// The direction of the middle of the view, in the HMD's own axes (x right, y up, -z forward),
// from a raw projection (IVRSystem::GetProjectionRaw tangents, both eyes averaged). OpenVR's
// "bottom" is the upward bound. Headsets whose lenses reach further down than up, like the Steam
// Frame (about 15 degrees), see the middle of their view below the HMD's forward axis. Straight
// ahead if the values make no sense.
Vec3 view_center(float tan_left, float tan_right, float tan_top, float tan_bottom);

// Centred on the user's line of sight `distance` m ahead, facing the head as facing_upright
// does (`content` as for facing). The line of sight is `view` (from view_center) in the HMD's
// axes.
Mat34 place_on_gaze(const Mat34& hmd_pose, float distance, const Vec3& view = {0.0f, 0.0f, -1.0f},
                    const Vec3& content = {0.0f, 0.0f, 0.0f});

// Standing on the floor: upright, its foot (see foot(), `drop` m below its centre) on the floor
// (y = 0) above `at` (whose y is ignored), turned about the vertical to face `eye`. `right` and
// `content` as for facing. The theater screen starts like this.
Mat34 on_floor(const Vec3& at, float drop, const Vec3& eye, const Vec3& right = {0.0f, 0.0f, 0.0f},
               const Vec3& content = {0.0f, 0.0f, 0.0f});

// Theater: kTheaterDistanceMeters straight ahead of the HMD along its heading (level), standing
// on the floor (see on_floor). Handles looking straight up or down.
Mat34 place_theater(const Mat34& hmd_pose, float drop, const Vec3& content = {0.0f, 0.0f, 0.0f});

Mat34 offset_along_up(const Mat34& base, float dy);

// `pose` turned about its own vertical axis through its centre by `radians`: positive brings its
// left side toward the viewer it faces.
Mat34 turned(const Mat34& pose, float radians);

// Panel size in metres for a w x h pixel image whose long side is `long_side_m`.
float panel_width_m(uint32_t w, uint32_t h, float long_side_m);
float panel_height_m(uint32_t w, uint32_t h, float long_side_m);

// a * b for rigid 3x4 transforms.
Mat34 multiply(const Mat34& a, const Mat34& b);

// Requires an orthonormal rotation part.
Mat34 inverse_rigid(const Mat34& m);

// Moves `pose` along the ray from `eye` by `delta` m (negative = closer), clamping the distance
// to [kMinDistanceMeters, max_distance].
Mat34 push_away(const Mat34& pose, const Vec3& eye, float delta, float max_distance = kMaxDistanceMeters);

// Recenter waits this long, counting down, so the user can look where the panel should go.
constexpr float kRecenterCountdownSeconds = 3.0f;
// The number to show `elapsed` seconds into the countdown: 3, 2, 1, then 0 when it's over.
int countdown_left(float elapsed);

// Scale for dragging a point of the panel with another point fixed and the aspect ratio kept.
// Positions are metres from the fixed point in the panel plane (x right, y down): the grab
// started at (grab_x, grab_y) and the pointer is now at (x, y). The grabbed point follows the
// pointer projected onto the line from the fixed point through it. 0 once the pointer reaches or
// passes the fixed point, 1 if the grab is on it.
float resize_scale(float grab_x, float grab_y, float x, float y);

// The panel's foot, the point `drop` m below its centre along its own Y: the middle of its
// bottom edge for half its height. The VR panel stands on the phone screen's bottom edge, with
// the controls under it below the floor.
Vec3 foot(const Mat34& pose, float drop);

// Intersects a ray with the plane of `pose`. (x, y) are metres from `anchor`, a point on the
// panel, x right, y down. Returns false if the ray is near-parallel to the plane or points away
// from it.
bool ray_on_panel(const Mat34& pose, const Vec3& anchor, const Vec3& origin, const Vec3& dir,
                  float& x, float& y);

// Android scrolls this far per wheel click (config_verticalScrollFactor), in dp.
constexpr float kAndroidScrollDp = 64.0f;
// Long side of a typical phone screen, in dp, for when the real one is unknown.
constexpr float kTypicalScreenLongDp = 900.0f;

// Wheel clicks to send the device per unit of smooth scroll, so it scrolls the same share of
// its screen as the app's own pages do: `screens_per_click` of a page per click (the app sends
// it), on a device whose screen's long side is `screen_long_dp` (0: unknown), shown at w x h.
// 1 if `screens_per_click` isn't positive.
float scroll_clicks_per_unit(float screens_per_click, float screen_long_dp, uint32_t w, uint32_t h);

// Depth change in metres (positive = farther) for stick Y `axis` held for `seconds`. Dead zone,
// then a quadratic response for fine control near centre.
float stick_depth(float axis, float seconds);

// Smooths smooth-scroll events into depth motion while carrying. Each event adds to a pending
// distance that take() drains exponentially, so motion doesn't depend on the event rate.
class DepthGlide {
 public:
  void add(float delta);
  // Metres to move this tick (positive = farther). `seconds` is the time since the last call.
  float take(float seconds);
  void reset();

  // Per-drag stats, logged on release.
  unsigned events = 0;
  float max_abs_delta = 0.0f;
  float travelled_m = 0.0f;

 private:
  float pending_m_ = 0.0f;
};

// Mat34 <-> 12 comma-separated row-major floats. parse_pose rejects input unless all 12 parse
// and the rotation is orthonormal, so a corrupt saved pose is ignored.
std::string format_pose(const Mat34& m);
bool parse_pose(const std::string& text, Mat34& out);

}  // namespace vrm::panel
