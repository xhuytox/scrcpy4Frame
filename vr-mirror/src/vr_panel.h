// Phone panel as a single SteamVR overlay. Main thread only.
//
// The overlay texture is the phone image plus the controls from panel_chrome.h. The drag bar
// carries the panel with the controller (stick for depth), turning it to face the user as it
// goes, and the corner bracket resizes it around its centre. While either is
// active the laser is switched off and input comes from SteamVR Input (see begin_laser_free).
// The More button opens a menu with Theater, which stands the panel on the floor 3.8 m ahead at a
// larger size, Recenter, and audio casting. Recenter counts down first (countdown.h).
#pragma once

#include "decoder.h"
#include "panel_chrome.h"
#include "panel_math.h"
#include "scrcpy_protocol.h"
#include "vulkan_texture.h"

#include <openvr.h>

#include <chrono>
#include <memory>
#include <string>
#include <vector>

namespace vrm {

class Countdown;

// `pos` is in pixels of the frame currently shown, whose size is the w x h the server expects.
// AudioCast asks the app to turn audio casting `on` or off.
struct PanelAction {
  enum class Type { TouchDown, TouchMove, TouchUp, Scroll, Back, Home, AppSwitch, AudioCast };
  Type type = Type::TouchMove;
  scrcpy::Position pos;
  float hscroll = 0.0f;
  float vscroll = 0.0f;
  bool on = false;
};

class VrPanel {
 public:
  // Lets the visible overlay take the laser without opening the dashboard. It also blocks
  // controller input to a running game while the panel is visible. false makes the panel
  // view-only outside the dashboard.
  static constexpr bool kMakeInteractiveIfVisible = true;
  static constexpr const char* kAppKey = "io.github.xhuytox.scrcpy4frame.mirror";

  enum class InitResult { Ok, Unavailable, Fatal };

  VrPanel();
  ~VrPanel();
  VrPanel(const VrPanel&) = delete;
  VrPanel& operator=(const VrPanel&) = delete;

  // Never launches SteamVR. Loads vr-mirror-actions.json from the manifest's directory if present.
  // On failure `message` is the VRInitError or overlay error symbol (Unavailable) or a
  // description (Fatal).
  InitResult init(const std::string& manifest_path, std::string& message);

  // Sleeps so the compositor can finish any pending texture copy before OpenVR and Vulkan are
  // torn down.
  void shutdown();

  void set_title(const std::string& title);
  // Clamped to the limits of the current mode (theater allows larger).
  void set_long_side(float meters);
  float long_side() const { return long_side_m_; }
  // Long side when theater mode starts.
  void set_theater_long_side(float meters);
  // What the menu's audio button shows: casting or not, and whether it can be pressed.
  void set_audio(bool on, bool enabled);
  // Scroll speed, see panel::scroll_clicks_per_unit.
  void set_scroll_speed(float screens_per_click, float screen_long_dp);
  // Pose from a previous session, used for the first placement instead of in front of the user.
  void set_saved_pose(const panel::Mat34& pose);

  // On a size change the panel is reshaped around its centre. The first frame places and shows it.
  bool show_frame(const RgbaFrame& frame, std::string& error);

  // Appends phone actions to `out`. Returns false if SteamVR requested quit (already acknowledged).
  bool poll(std::vector<PanelAction>& out);

  // After a countdown, moves the panel onto the user's line of sight, facing them, unhiding it if
  // needed. Keeps its size, and theater mode if it's on (farther away then). Hiding, carrying,
  // resizing or entering theater meanwhile cancels it.
  void recenter();
  // The same at once, with no countdown.
  void recenter_now();
  // Showing a hidden panel (Home's show button, the dashboard button): recenter_now, except in
  // theater, where it shows where it was hidden.
  void reveal();

  // Streaming continues while hidden. Showing re-uploads the latest frame.
  void set_hidden(bool hidden);
  bool hidden() const { return hidden_; }

  // Theater on or off, as the More menu's Theater button does, showing the panel if it's hidden
  // (Home's Theater button).
  void set_theater(bool on);

  // The process's one Vulkan uploader, for other overlays (see vulkan_texture.h).
  SteamFrameVulkanTexture* texture() const { return texture_.get(); }

  // True once after any move, resize, recenter or theater, with where the next session should
  // open: `pose` at `long_side`. Theater never carries over, so while it's on `in_front` is set
  // instead of a pose (in front of the user) and `long_side` is the size from before theater.
  bool take_moved(panel::Mat34& pose, bool& in_front, float& long_side);

  // True once after the panel was hidden or shown.
  bool take_visibility(bool& visible);
  // True once after theater turned on or off.
  bool take_theater(bool& on);

 private:
  bool try_place();
  void show_panel();
  void apply_geometry();
  bool upload_composite(std::string& error);
  void set_chrome(const chrome::State& state);
  void update_hover();
  float meters_per_pixel() const;
  float height_m() const;
  float foot_m() const;
  float max_long_side() const;
  panel::Vec3 screen_offset() const;
  void set_pose(const panel::Mat34& pose);
  void release_pointer(std::vector<PanelAction>& out);
  void handle_event(const vr::VREvent_t& ev, std::vector<PanelAction>& out);
  vr::TrackedDeviceIndex_t resolve_pointer_device(vr::TrackedDeviceIndex_t device) const;
  bool device_pose(vr::TrackedDeviceIndex_t device, panel::Mat34& pose) const;
  void begin_grab(vr::TrackedDeviceIndex_t device);
  void end_grab();
  void update_grab(float push_m, float seconds);
  panel::Mat34 aimed(const panel::Vec3& at, float seconds);
  void begin_resize(vr::TrackedDeviceIndex_t device);
  void resize_pointer(float& x, float& y) const;
  void update_resize();
  void update_resize_to(float x, float y);
  void end_resize();
  void set_carrying(bool carrying);
  void finish_action();
  void init_input(const std::string& actions_path);
  void begin_laser_free(vr::TrackedDeviceIndex_t device);
  void end_laser_free();
  float poll_laser_free(float seconds);
  void set_menu_open(bool open);
  void toggle_theater();
  void register_manifest(const std::string& manifest_path);
  scrcpy::Position phone_point(float mouse_x, float mouse_y) const;

  vr::IVROverlay* overlay_ = nullptr;
  std::unique_ptr<SteamFrameVulkanTexture> texture_;
  bool vr_initialized_ = false;
  vr::VROverlayHandle_t panel_ = vr::k_ulOverlayHandleInvalid;
  std::unique_ptr<Countdown> countdown_;  // optional

  float long_side_m_ = panel::kDefaultLongSideMeters;
  float theater_long_m_ = panel::kDefaultTheaterLongSideMeters;
  bool theater_ = false;
  bool theater_changed_ = false;
  float pre_theater_long_m_ = 0.0f;  // long side to go back to when theater ends
  bool menu_open_ = false;
  bool audio_on_ = false;
  bool audio_enabled_ = true;
  bool dashboard_was_visible_ = false;  // while the menu is open
  panel::Vec3 view_{0.0f, 0.0f, -1.0f};    // middle of the view in the HMD's axes (view_center)
  float scroll_screens_per_click_ = 0.0f;  // 0: one click per unit of smooth scroll
  float screen_long_dp_ = 0.0f;
  uint32_t shown_w_ = 0;  // frame size that touch positions are relative to
  uint32_t shown_h_ = 0;
  chrome::Layout chrome_{};
  chrome::State chrome_state_{};
  bool chrome_dirty_ = false;
  std::vector<uint8_t> composite_;  // chrome_.width() x chrome_.height() RGBA

  bool has_frame_ = false;
  bool placed_ = false;
  bool visible_ = false;   // ShowOverlay is in effect
  bool hidden_ = false;    // hidden by the user
  bool visibility_changed_ = false;
  bool has_saved_pose_ = false;
  panel::Mat34 saved_pose_{};
  std::chrono::steady_clock::time_point first_frame_at_{};
  panel::Mat34 panel_pose_{};
  bool moved_ = false;

  vr::TrackedDeviceIndex_t grab_device_ = vr::k_unTrackedDeviceIndexInvalid;
  panel::Mat34 grab_relative_{};  // panel pose in the grabbing controller's space
  panel::DepthGlide glide_;
  panel::TiltAnimation tilt_;
  panel::Vec3 aim_eye_{};    // the head the panel last turned to face, and its right
  panel::Vec3 aim_right_{};
  std::chrono::steady_clock::time_point last_tick_{};
  std::chrono::steady_clock::time_point grab_started_{};

  bool resizing_ = false;
  float resize_long0_ = 0.0f;  // long side when the corner was grabbed
  float resize_grab_x_ = 0.0f;  // pointer at grab, in metres from the anchor (x right, y down)
  float resize_grab_y_ = 0.0f;
  panel::Vec3 resize_anchor_{};  // world position of the centre when the corner was grabbed, which stays put

  chrome::Zone pressed_ = chrome::Zone::Empty;  // button the trigger went down on

  // SteamVR Input (vr-mirror-actions.json), used only while carrying or resizing.
  vr::VRActionSetHandle_t action_set_ = vr::k_ulInvalidActionSetHandle;
  vr::VRActionHandle_t hold_action_ = vr::k_ulInvalidActionHandle;
  vr::VRActionHandle_t depth_action_ = vr::k_ulInvalidActionHandle;
  vr::VRActionHandle_t aim_action_ = vr::k_ulInvalidActionHandle;
  vr::VRInputValueHandle_t hands_[2] = {vr::k_ulInvalidInputValueHandle, vr::k_ulInvalidInputValueHandle};
  bool input_ready_ = false;
  int laser_free_misses_ = 0;   // drags where the trigger never showed up as held
  bool laser_free_ = false;     // laser off, input from SteamVR Input
  bool laser_free_held_ = false;
  vr::VRInputValueHandle_t laser_free_hand_ = vr::k_ulInvalidInputValueHandle;
  std::chrono::steady_clock::time_point laser_free_since_{};
  bool aim_started_ = false;    // aim_x0_/aim_y0_ hold the first aim point of this resize
  float aim_x0_ = 0.0f;
  float aim_y0_ = 0.0f;

  bool pointer_down_ = false;
  bool has_mouse_ = false;
  float mouse_x_ = 0.0f;
  float mouse_y_ = 0.0f;
  std::vector<PanelAction> pending_;
};

}  // namespace vrm
