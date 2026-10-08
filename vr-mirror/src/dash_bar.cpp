#include "dash_bar.h"

#include "log.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <thread>

namespace vrm {

namespace {

constexpr const char* kSteamBarKey = "valve.steam.gamepadui.bar";
constexpr const char* kSteamPanelKey = "valve.steam.gamepadui.main";  // visible on Steam's own tab

// Steam recreates its overlays when its UI restarts, so a hidden or missing bar is looked up
// again this often.
constexpr auto kLookupInterval = std::chrono::seconds(1);
// The compositor updates at 90 Hz, so placing the row more often than this gains nothing.
constexpr auto kPlaceInterval = std::chrono::milliseconds(4);
// While the row is hidden (dashboard closed), how often to check whether the bar is back.
constexpr auto kHiddenPollInterval = std::chrono::milliseconds(50);
// The bar counts as moving until it has been still this long. Meanwhile the row keeps turning
// to face the head.
constexpr auto kMoveSettle = std::chrono::milliseconds(150);
constexpr float kMoveEpsilonMeters = 0.0005f;
// Accept a measured lean only in this range (radians); anything else isn't the layout we know.
constexpr float kMinTilt = 0.17f, kMaxTilt = 1.4f;
// See VrPanel: the compositor copies overlay textures asynchronously.
constexpr auto kCompositorDrainDelay = std::chrono::milliseconds(300);
constexpr int kMaxUploadFailures = 3;

vr::HmdMatrix34_t to_vr(const panel::Mat34& m) {
  static_assert(sizeof(vr::HmdMatrix34_t) == sizeof(panel::Mat34), "layout mismatch");
  vr::HmdMatrix34_t r;
  std::memcpy(&r, &m, sizeof r);
  return r;
}

panel::Mat34 from_vr(const vr::HmdMatrix34_t& m) {
  panel::Mat34 r;
  std::memcpy(&r, &m, sizeof r);
  return r;
}

// The overlay's pose at the centre of its mouse area, split into a rigid pose and its scale.
bool centre_pose(vr::IVROverlay* ov, vr::VROverlayHandle_t h, panel::Mat34& pose, float& scale) {
  vr::HmdVector2_t mouse{};
  if (ov->GetOverlayMouseScale(h, &mouse) != vr::VROverlayError_None || !(mouse.v[0] > 0.0f) ||
      !(mouse.v[1] > 0.0f)) {
    return false;
  }
  vr::HmdMatrix34_t m;
  if (ov->GetTransformForOverlayCoordinates(h, vr::TrackingUniverseStanding, {{mouse.v[0] / 2, mouse.v[1] / 2}},
                                            &m) != vr::VROverlayError_None) {
    return false;
  }
  return dash::split_scale(from_vr(m), pose, scale);
}

float distance(const panel::Vec3& a, const panel::Vec3& b) {
  return std::sqrt((a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y) + (a.z - b.z) * (a.z - b.z));
}

}  // namespace

DashBar::~DashBar() { release_overlay(); }

bool DashBar::init(SteamFrameVulkanTexture* texture, const std::string& key, const std::string& name,
                   std::string& error) {
  overlay_ = vr::VROverlay();
  texture_ = texture;
  if (!overlay_ || !texture_) {
    error = overlay_ ? "no Vulkan texture" : "IVROverlay unavailable";
    overlay_ = nullptr;
    return false;
  }
  const vr::EVROverlayError e = overlay_->CreateOverlay(key.c_str(), name.c_str(), &handle_);
  if (e != vr::VROverlayError_None) {
    const char* n = overlay_->GetOverlayErrorNameFromEnum(e);
    error = std::string("CreateOverlay failed: ") + (n ? n : "?");
    overlay_ = nullptr;
    handle_ = vr::k_ulOverlayHandleInvalid;
    return false;
  }
  // No MakeOverlaysInteractiveIfVisible: the row only shows with the dashboard, which brings its
  // own laser.
  overlay_->SetOverlayInputMethod(handle_, vr::VROverlayInputMethod_Mouse);
  overlay_->HideOverlay(handle_);
  return true;
}

void DashBar::set_buttons(const std::vector<Button>& buttons) {
  if (buttons == buttons_) return;
  buttons_ = buttons;
  hover_ = pressed_ = -1;
  if (buttons_.empty()) {
    show(false);
    return;
  }
  layout_ = dash::row_layout(static_cast<uint32_t>(buttons_.size()));
  pixels_.assign(static_cast<size_t>(layout_.width()) * layout_.height() * 4, 0);
  dirty_ = true;
  width_m_ = 0.0f;  // set on the next placement
  if (overlay_ && handle_ != vr::k_ulOverlayHandleInvalid) {
    const vr::HmdVector2_t scale{{static_cast<float>(layout_.width()), static_cast<float>(layout_.height())}};
    overlay_->SetOverlayMouseScale(handle_, &scale);
  }
}

bool DashBar::tick(std::vector<std::string>& clicked) {
  if (!overlay_ || handle_ == vr::k_ulOverlayHandleInvalid) return true;
  if (!handle_events(clicked)) return false;
  if (buttons_.empty()) return true;
  if (dirty_ && !redraw()) {
    show(false);
    return true;
  }
  const Clock::time_point now = Clock::now();
  if (now - placed_at_ < (shown_ ? kPlaceInterval : kHiddenPollInterval)) return true;
  placed_at_ = now;
  show(place());
  return true;
}

bool DashBar::handle_events(std::vector<std::string>& clicked) {
  vr::VREvent_t ev{};
  while (overlay_->PollNextOverlayEvent(handle_, &ev, sizeof ev)) {
    switch (ev.eventType) {
      case vr::VREvent_Quit:
        vr::VRSystem()->AcknowledgeQuit_Exiting();
        return false;
      case vr::VREvent_DriverRequestedQuit:
        return false;
      case vr::VREvent_MouseMove:
        set_pointer(dash::hit(layout_, ev.data.mouse.x, ev.data.mouse.y), pressed_);
        break;
      case vr::VREvent_MouseButtonDown:
        if (ev.data.mouse.button & vr::VRMouseButton_Left) {
          const int b = dash::hit(layout_, ev.data.mouse.x, ev.data.mouse.y);
          set_pointer(b, b);
        }
        break;
      case vr::VREvent_MouseButtonUp:
        if (ev.data.mouse.button & vr::VRMouseButton_Left) {
          // A button acts on release, and only if the laser is still on it.
          const int b = dash::hit(layout_, ev.data.mouse.x, ev.data.mouse.y);
          if (pressed_ >= 0 && b == pressed_ && b < static_cast<int>(buttons_.size())) {
            clicked.push_back(buttons_[static_cast<size_t>(b)].id);
          }
          set_pointer(b, -1);
        }
        break;
      case vr::VREvent_FocusLeave:
      case vr::VREvent_OverlayHidden:
        set_pointer(-1, -1);
        break;
      default:
        break;
    }
  }
  return true;
}

// While a button is pressed, the others don't highlight.
int DashBar::highlighted() const { return hover_ >= 0 && (pressed_ < 0 || pressed_ == hover_) ? hover_ : -1; }

void DashBar::set_pointer(int hover, int pressed) {
  const int before = highlighted();
  hover_ = hover;
  pressed_ = pressed;
  if (highlighted() != before) dirty_ = true;
}

bool DashBar::redraw() {
  if (texture_failed_) return false;
  std::fill(pixels_.begin(), pixels_.end(), 0);
  for (size_t i = 0; i < buttons_.size(); ++i) {
    const auto b = static_cast<uint32_t>(i);
    ui::draw_round_button(pixels_.data(), layout_.width(), layout_.height(), layout_.center_x(b), layout_.center_y(),
                          layout_.radius, highlighted() == static_cast<int>(i), buttons_[i].icon);
  }
  std::string error;
  if (!texture_->Update(overlay_, handle_, pixels_.data(), layout_.width(), layout_.height(), error)) {
    if (++upload_failures_ >= kMaxUploadFailures) {
      log_warn("dashbar", "texture upload keeps failing, no dashboard buttons: " + error);
      texture_failed_ = true;
    }
    return false;
  }
  upload_failures_ = 0;
  uploaded_ = true;
  dirty_ = false;
  return true;
}

bool DashBar::find_bar() {
  if (bar_ != vr::k_ulOverlayHandleInvalid && overlay_->IsOverlayVisible(bar_)) return true;
  const Clock::time_point now = Clock::now();
  if (now - bar_lookup_at_ < kLookupInterval) return false;
  bar_lookup_at_ = now;
  vr::VROverlayHandle_t h = vr::k_ulOverlayHandleInvalid;
  bar_ = overlay_->FindOverlay(kSteamBarKey, &h) == vr::VROverlayError_None ? h : vr::k_ulOverlayHandleInvalid;
  if (bar_ == vr::k_ulOverlayHandleInvalid && !bar_missing_logged_) {
    bar_missing_logged_ = true;
    log_info("dashbar", std::string("no ") + kSteamBarKey + " overlay yet; the buttons show once it appears");
  }
  return bar_ != vr::k_ulOverlayHandleInvalid && overlay_->IsOverlayVisible(bar_);
}

bool DashBar::place() {
  if (!find_bar()) return false;
  panel::Mat34 bar;
  float scale = 1.0f;
  if (!centre_pose(overlay_, bar_, bar, scale)) return false;

  // The bar's lean is part of Steam's layout, so measuring it when the row appears is enough.
  if (!shown_) measure_tilt(bar);
  // When the row appears and while the dashboard moves, keep facing the head. Otherwise the row
  // holds still, so it doesn't swivel when the head moves.
  const Clock::time_point now = Clock::now();
  const panel::Vec3 at = panel::translation(bar);
  if (distance(at, bar_pos_) > kMoveEpsilonMeters) {
    bar_pos_ = at;
    bar_moved_at_ = now;
  }
  if (!shown_ || now - bar_moved_at_ < kMoveSettle) {
    vr::TrackedDevicePose_t hmd{};
    vr::VRSystem()->GetDeviceToAbsoluteTrackingPose(vr::TrackingUniverseStanding, 0.0f, &hmd, 1);
    if (hmd.bPoseIsValid) {
      eye_ = panel::translation(from_vr(hmd.mDeviceToAbsoluteTracking));
      have_eye_ = true;
    }
  }
  const panel::Mat34 frame = dash::dashboard_frame(bar, tilt_);
  if (!have_eye_) {
    // No head pose yet: face out of the dashboard as if the head were a metre in front of it.
    eye_ = {at.x + frame.m[0][2], at.y + frame.m[1][2], at.z + frame.m[2][2]};
  }
  const float k = dash::size_factor(scale);
  const vr::HmdMatrix34_t m = to_vr(panel::facing(dash::row_center(frame, eye_, k), eye_));
  overlay_->SetOverlayTransformAbsolute(handle_, vr::TrackingUniverseStanding, &m);
  const float width = layout_.width_m() * k;
  if (std::fabs(width - width_m_) > 1e-4f) {
    overlay_->SetOverlayWidthInMeters(handle_, width);
    width_m_ = width;
  }
  return true;
}

// Only Steam's own tab exposes the dashboard panel's pose. On other tabs the last value stays.
void DashBar::measure_tilt(const panel::Mat34& bar) {
  vr::VROverlayHandle_t h = vr::k_ulOverlayHandleInvalid;
  if (overlay_->FindOverlay(kSteamPanelKey, &h) != vr::VROverlayError_None || !overlay_->IsOverlayVisible(h)) return;
  panel::Mat34 dashboard;
  float scale = 1.0f;
  if (!centre_pose(overlay_, h, dashboard, scale)) return;
  const float tilt = dash::bar_tilt(bar, dashboard);
  if (tilt >= kMinTilt && tilt <= kMaxTilt) tilt_ = tilt;
}

void DashBar::show(bool on) {
  if (on == shown_ || !overlay_) return;
  if (on) {
    overlay_->ShowOverlay(handle_);
  } else {
    overlay_->HideOverlay(handle_);
    set_pointer(-1, -1);
  }
  shown_ = on;
}

void DashBar::release_overlay() {
  if (!overlay_ || handle_ == vr::k_ulOverlayHandleInvalid) return;
  overlay_->HideOverlay(handle_);
  if (uploaded_) {
    overlay_->ClearOverlayTexture(handle_);
    std::this_thread::sleep_for(kCompositorDrainDelay);
  }
  overlay_->DestroyOverlay(handle_);
  handle_ = vr::k_ulOverlayHandleInvalid;
  overlay_ = nullptr;
  shown_ = false;
}

}  // namespace vrm
