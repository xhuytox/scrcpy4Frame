// The recenter countdown: a round badge showing 3, 2, 1 in the middle of the view, where the
// panel lands when it ends, so the user can look where they want it first. Main thread only.
//
// It's one overlay attached to the HMD, uploaded through the panel's SteamFrameVulkanTexture
// (OpenVR can't take textures from a second Vulkan device). It takes no input, so the laser
// passes through it, and draws over the panel.
#pragma once

#include "panel_math.h"
#include "vulkan_texture.h"

#include <openvr.h>

#include <chrono>
#include <string>
#include <vector>

namespace vrm {

class Countdown {
 public:
  Countdown() = default;
  ~Countdown();
  Countdown(const Countdown&) = delete;
  Countdown& operator=(const Countdown&) = delete;

  // Needs a VR_Init(VRApplication_Overlay) session. `texture` is the process's one Vulkan
  // texture uploader (VrPanel's) and must outlive the overlay. `view` is the middle of the view
  // in the HMD's axes (panel::view_center). `key` must be unique among overlays.
  bool init(SteamFrameVulkanTexture* texture, const std::string& key, const panel::Vec3& view, std::string& error);

  // Starts from the top, unless it's already counting.
  void start();
  void cancel();
  bool running() const { return running_; }

  // Call every main-loop tick. True once, when the countdown is over (and hidden).
  bool tick();

  // Before VR_Shutdown: hides the overlay, gives the compositor time to drop its texture and
  // destroys it. The texture's Vulkan resources go with the panel's, after VR_Shutdown.
  void release_overlay();

 private:
  using Clock = std::chrono::steady_clock;

  bool draw(int digit);

  vr::IVROverlay* overlay_ = nullptr;
  vr::VROverlayHandle_t handle_ = vr::k_ulOverlayHandleInvalid;
  SteamFrameVulkanTexture* texture_ = nullptr;  // not owned
  bool uploaded_ = false;
  std::vector<uint8_t> pixels_;
  bool running_ = false;
  int shown_ = 0;  // digit on the overlay, 0: hidden
  Clock::time_point started_{};
};

}  // namespace vrm
