#include "countdown.h"

#include "log.h"
#include "ui_draw.h"

#include <algorithm>
#include <cstring>
#include <thread>

namespace vrm {

namespace {

constexpr uint32_t kTexturePx = 192;
constexpr float kDistanceMeters = 1.0f;  // along the middle of the view
constexpr float kWidthMeters = 0.09f;    // about 5 degrees across
constexpr uint32_t kSortOrder = 1;       // over the panel, which keeps the default 0

// See VrPanel: the compositor may still be reading the texture.
constexpr auto kCompositorDrainDelay = std::chrono::milliseconds(300);

}  // namespace

Countdown::~Countdown() { release_overlay(); }

bool Countdown::init(SteamFrameVulkanTexture* texture, const std::string& key, const panel::Vec3& view,
                     std::string& error) {
  overlay_ = vr::VROverlay();
  texture_ = texture;
  if (!overlay_ || !texture_) {
    error = overlay_ ? "no Vulkan texture" : "IVROverlay unavailable";
    overlay_ = nullptr;
    return false;
  }
  const vr::EVROverlayError e = overlay_->CreateOverlay(key.c_str(), "scrcpy4Frame recenter", &handle_);
  if (e != vr::VROverlayError_None) {
    const char* n = overlay_->GetOverlayErrorNameFromEnum(e);
    error = std::string("CreateOverlay failed: ") + (n ? n : "?");
    overlay_ = nullptr;
    handle_ = vr::k_ulOverlayHandleInvalid;
    return false;
  }
  // In the HMD's own axes, square to the eye. It never moves relative to the head.
  const panel::Mat34 pose = panel::facing({view.x * kDistanceMeters, view.y * kDistanceMeters, view.z * kDistanceMeters},
                                          {0.0f, 0.0f, 0.0f}, {1.0f, 0.0f, 0.0f});
  vr::HmdMatrix34_t m;
  static_assert(sizeof m == sizeof pose, "layout mismatch");
  std::memcpy(&m, &pose, sizeof m);
  overlay_->SetOverlayTransformTrackedDeviceRelative(handle_, vr::k_unTrackedDeviceIndex_Hmd, &m);
  overlay_->SetOverlayWidthInMeters(handle_, kWidthMeters);
  overlay_->SetOverlaySortOrder(handle_, kSortOrder);
  overlay_->HideOverlay(handle_);
  pixels_.assign(static_cast<size_t>(kTexturePx) * kTexturePx * 4, 0);
  return true;
}

void Countdown::start() {
  if (running_) return;
  running_ = true;
  started_ = Clock::now();
}

void Countdown::cancel() {
  running_ = false;
  if (shown_ != 0 && overlay_) overlay_->HideOverlay(handle_);
  shown_ = 0;
}

bool Countdown::tick() {
  if (!running_) return false;
  const int left = panel::countdown_left(std::chrono::duration<float>(Clock::now() - started_).count());
  if (left == 0) {
    cancel();
    return true;
  }
  if (left != shown_ && overlay_) {
    if (draw(left)) overlay_->ShowOverlay(handle_);
    shown_ = left;
  }
  return false;
}

bool Countdown::draw(int digit) {
  std::fill(pixels_.begin(), pixels_.end(), 0);
  const ui::Icon icon = digit >= 3 ? ui::digit_3_icon : digit == 2 ? ui::digit_2_icon : ui::digit_1_icon;
  const float c = kTexturePx / 2.0f;
  ui::draw_round_button(pixels_.data(), kTexturePx, kTexturePx, c, c, c - 2.0f, false, icon);
  std::string error;
  if (!texture_->Update(overlay_, handle_, pixels_.data(), kTexturePx, kTexturePx, error)) {
    log_warn("countdown", "texture upload failed: " + error);
    return false;
  }
  uploaded_ = true;
  return true;
}

void Countdown::release_overlay() {
  if (!overlay_ || handle_ == vr::k_ulOverlayHandleInvalid) return;
  overlay_->HideOverlay(handle_);
  if (uploaded_) {
    overlay_->ClearOverlayTexture(handle_);
    std::this_thread::sleep_for(kCompositorDrainDelay);
  }
  overlay_->DestroyOverlay(handle_);
  handle_ = vr::k_ulOverlayHandleInvalid;
  overlay_ = nullptr;
  running_ = false;
  shown_ = 0;
}

}  // namespace vrm
