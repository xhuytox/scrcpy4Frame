// A row of our own round buttons under SteamVR's dashboard bar, shown while the dashboard is
// open. The owner decides which buttons to offer and gets the ids of clicked ones back, so more
// buttons only need an id and an icon. Main thread only.
//
// It's one overlay, uploaded through the panel's SteamFrameVulkanTexture (OpenVR can't take
// textures from a second Vulkan device). OpenVR 2.x has no overlay-relative transforms, so
// every tick it reads the bar's pose and places the row under it (dash_math.h): centred, facing
// the head, pitched with the dashboard, and visible only while the bar is. It follows with the
// bar's own few frames of delay. Attaching it to the controller while the dashboard is dragged
// was tried and jittered, because the dashboard doesn't move rigidly with the hand.
//
// The bar's overlay key is not part of the OpenVR SDK. If it changes, the row never shows.
#pragma once

#include "dash_math.h"
#include "ui_draw.h"
#include "vulkan_texture.h"

#include <openvr.h>

#include <chrono>
#include <string>
#include <vector>

namespace vrm {

class DashBar {
 public:
  struct Button {
    std::string id;
    ui::Icon icon = nullptr;

    bool operator==(const Button& o) const { return id == o.id && icon == o.icon; }
    bool operator!=(const Button& o) const { return !(*this == o); }
  };

  DashBar() = default;
  ~DashBar();
  DashBar(const DashBar&) = delete;
  DashBar& operator=(const DashBar&) = delete;

  // Needs a VR_Init(VRApplication_Overlay) session. `texture` is the process's one Vulkan
  // texture uploader (VrPanel's) and must outlive the overlay. `key` must be unique among overlays.
  bool init(SteamFrameVulkanTexture* texture, const std::string& key, const std::string& name,
            std::string& error);

  // Buttons to offer, left to right. Empty hides the row.
  void set_buttons(const std::vector<Button>& buttons);

  // Call every main-loop tick: follows the bar and handles the laser. Appends the ids of clicked
  // buttons to `clicked`. Returns false if SteamVR asked us to quit (already acknowledged).
  bool tick(std::vector<std::string>& clicked);

  // Before VR_Shutdown: hides the overlay, gives the compositor time to drop its texture and
  // destroys it. The texture's Vulkan resources go with the panel's, after VR_Shutdown.
  void release_overlay();

 private:
  using Clock = std::chrono::steady_clock;

  bool handle_events(std::vector<std::string>& clicked);
  void set_pointer(int hover, int pressed);
  int highlighted() const;
  bool redraw();
  bool find_bar();
  bool place();
  void measure_tilt(const panel::Mat34& bar);
  void show(bool on);

  vr::IVROverlay* overlay_ = nullptr;
  vr::VROverlayHandle_t handle_ = vr::k_ulOverlayHandleInvalid;
  SteamFrameVulkanTexture* texture_ = nullptr;  // not owned
  bool texture_failed_ = false;
  bool uploaded_ = false;
  int upload_failures_ = 0;

  std::vector<Button> buttons_;
  dash::RowLayout layout_{};
  std::vector<uint8_t> pixels_;
  bool dirty_ = false;  // pixels need drawing and uploading
  bool shown_ = false;
  int hover_ = -1;      // button under the laser
  int pressed_ = -1;    // button the trigger went down on
  float width_m_ = 0.0f;
  Clock::time_point placed_at_{};

  vr::VROverlayHandle_t bar_ = vr::k_ulOverlayHandleInvalid;
  Clock::time_point bar_lookup_at_{};
  bool bar_missing_logged_ = false;
  float tilt_ = dash::kDefaultBarTilt;
  panel::Vec3 bar_pos_{};
  Clock::time_point bar_moved_at_{};
  panel::Vec3 eye_{};  // head position the row faces, refreshed only when it appears or the bar moves
  bool have_eye_ = false;
};

}  // namespace vrm
