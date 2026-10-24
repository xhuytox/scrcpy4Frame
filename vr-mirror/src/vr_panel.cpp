#include "vr_panel.h"

#include "countdown.h"
#include "log.h"

#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <thread>

namespace vrm {

namespace {

using Clock = std::chrono::steady_clock;

// Fall back to a default standing pose if the HMD pose is still invalid this long after the
// first frame.
constexpr auto kPlacementFallbackDelay = std::chrono::seconds(2);

// The compositor copies overlay textures asynchronously. Exiting with a copy pending makes it
// read freed memory and crash with SIGBUS, so wait between releasing the texture and VR_Shutdown.
constexpr auto kCompositorDrainDelay = std::chrono::milliseconds(300);

// Turns the panel's left side toward the user after squaring it to them. On the Steam Frame
// (2026-10-11) it measured square to the point between the eyes within half a degree, as does
// SteamVR's dashboard bar, yet it looked turned left side away, to either eye and at any
// distance, while the dashboard looked square. So this corrects what the user sees, not the
// maths. The user chose a fixed turn for everyone; tune by feel.
constexpr float kSideTurnRadians = 2.0f * 3.14159265f / 180.0f;

// How long SteamVR Input gets to report the trigger after the laser is switched off.
constexpr auto kLaserFreeConfirm = std::chrono::milliseconds(300);

// Zoom mode. Pressing or releasing the stick nudges it, so its scroll is ignored this long after a
// click. The fingers go down once the stick has moved this many units of smooth scroll, and lift
// once it has sent nothing for kPinchIdle (let go: the stream stops).
constexpr auto kClickScrollQuiet = std::chrono::milliseconds(150);
constexpr float kPinchStartUnits = 0.2f;
constexpr auto kPinchIdle = std::chrono::milliseconds(150);
// Turning: the laser's angle about the anchor is ignored this close to it (a share of the image's
// short side), where it jumps about.
constexpr float kTurnDeadShare = 0.04f;

bool file_exists(const std::string& path) {
  struct stat st {};
  return ::stat(path.c_str(), &st) == 0 && S_ISREG(st.st_mode);
}

// Inside a Flatpak sandbox OpenVR looks for openvrpaths.vrpath under the sandbox's
// XDG_CONFIG_HOME. Point it at the host's copy. Harmless on the host.
void point_openvr_at_host_registry() {
  if (std::getenv("VR_PATHREG_OVERRIDE")) return;
  const char* xdg = std::getenv("XDG_CONFIG_HOME");
  const char* home = std::getenv("HOME");
  if (!xdg || !*xdg || !home || !*home) return;
  if (file_exists(std::string(xdg) + "/openvr/openvrpaths.vrpath")) return;
  const std::string host = std::string(home) + "/.config/openvr/openvrpaths.vrpath";
  if (file_exists(host)) ::setenv("VR_PATHREG_OVERRIDE", host.c_str(), 1);
}

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

const char* overlay_error_name(vr::IVROverlay* ov, vr::EVROverlayError err) {
  const char* name = ov ? ov->GetOverlayErrorNameFromEnum(err) : nullptr;
  return name ? name : "VROverlayError_Unknown";
}

bool is_quit(uint32_t type) {
  return type == vr::VREvent_Quit || type == vr::VREvent_DriverRequestedQuit;
}

}  // namespace

VrPanel::VrPanel() = default;

VrPanel::~VrPanel() { shutdown(); }

VrPanel::InitResult VrPanel::init(const std::string& manifest_path, std::string& message) {
  point_openvr_at_host_registry();

  // Background init fails instead of launching SteamVR, so use it as a probe.
  vr::EVRInitError err = vr::VRInitError_None;
  vr::VR_Init(&err, vr::VRApplication_Background);
  if (err != vr::VRInitError_None) {
    message = vr::VR_GetVRInitErrorAsSymbol(err);
    return InitResult::Unavailable;
  }
  vr::VR_Shutdown();

  err = vr::VRInitError_None;
  vr::VR_Init(&err, vr::VRApplication_Overlay);
  if (err != vr::VRInitError_None) {
    message = vr::VR_GetVRInitErrorAsSymbol(err);
    return InitResult::Unavailable;
  }
  vr_initialized_ = true;
  overlay_ = vr::VROverlay();
  if (!vr::VRSystem() || !overlay_) {
    shutdown();
    message = vr::VR_GetVRInitErrorAsSymbol(vr::VRInitError_Init_InterfaceNotFound);
    return InitResult::Unavailable;
  }

  // The middle of the view, which is where the panel goes, from both eyes' lenses.
  float tan[4] = {0.0f, 0.0f, 0.0f, 0.0f};
  for (const vr::EVREye eye : {vr::Eye_Left, vr::Eye_Right}) {
    float l = 0.0f, r = 0.0f, t = 0.0f, b = 0.0f;
    vr::VRSystem()->GetProjectionRaw(eye, &l, &r, &t, &b);
    tan[0] += l / 2.0f;
    tan[1] += r / 2.0f;
    tan[2] += t / 2.0f;
    tan[3] += b / 2.0f;
  }
  view_ = panel::view_center(tan[0], tan[1], tan[2], tan[3]);
  char buf[160];
  std::snprintf(buf, sizeof buf, "middle of the view: %.1f deg below, %.1f deg right of the HMD's forward axis",
                std::asin(-view_.y) * 57.29578, std::asin(view_.x) * 57.29578);
  log_info("panel", buf);

  // Without a registered manifest (is_dashboard_overlay + binary_path_linux_arm), SteamVR asks
  // overlay apps to quit when a game starts.
  register_manifest(manifest_path);
  const size_t slash = manifest_path.rfind('/');
  init_input((slash == std::string::npos ? std::string(".") : manifest_path.substr(0, slash)) +
             "/vr-mirror-actions.json");

  // Not SetOverlayRaw: at video rate it flickers and leaks, and a raw upload in flight at exit
  // crashes the compositor.
  texture_ = std::make_unique<SteamFrameVulkanTexture>();
  std::string vk_error;
  if (!texture_->Initialize(vr::VRSystem(), vr::VRCompositor(), vk_error)) {
    message = "Vulkan initialisation failed: " + vk_error;
    shutdown();
    return InitResult::Fatal;
  }

  // pid in the keys so a lingering previous instance can't collide.
  const std::string key = std::string("io.github.xhuytox.scrcpy4frame.mirror.") +
                          std::to_string(static_cast<long>(::getpid())) + ".";
  const vr::EVROverlayError oe = overlay_->CreateOverlay((key + "panel").c_str(), "scrcpy4Frame device", &panel_);
  if (oe != vr::VROverlayError_None) {
    message = overlay_error_name(overlay_, oe);
    shutdown();
    return InitResult::Unavailable;
  }
  overlay_->SetOverlayInputMethod(panel_, vr::VROverlayInputMethod_Mouse);
  // Smooth scroll only: it scrolls the phone, or sets depth while carrying. Discrete scroll
  // moves in steps and ticks the controller's haptics on every one.
  overlay_->SetOverlayFlag(panel_, vr::VROverlayFlags_SendVRDiscreteScrollEvents, false);
  overlay_->SetOverlayFlag(panel_, vr::VROverlayFlags_SendVRSmoothScrollEvents, true);
  overlay_->SetOverlayFlag(panel_, vr::VROverlayFlags_MakeOverlaysInteractiveIfVisible,
                           kMakeInteractiveIfVisible);
  overlay_->SetOverlayWidthInMeters(panel_, long_side_m_);
  overlay_->HideOverlay(panel_);

  // Optional: without it, recenter happens at once.
  auto countdown = std::make_unique<Countdown>();
  std::string countdown_error;
  if (countdown->init(texture_.get(), key + "countdown", view_, countdown_error)) {
    countdown_ = std::move(countdown);
  } else {
    log_warn("panel", "no recenter countdown: " + countdown_error);
  }
  return InitResult::Ok;
}

void VrPanel::register_manifest(const std::string& manifest_path) {
  vr::IVRApplications* apps = vr::VRApplications();
  if (!apps) {
    log_warn("openvr", "IVRApplications unavailable; manifest not registered");
    return;
  }
  if (!file_exists(manifest_path)) {
    log_warn("openvr", "manifest not found: " + manifest_path +
                           " (SteamVR may ask the panel to quit when a game starts)");
  } else {
    const vr::EVRApplicationError e = apps->AddApplicationManifest(manifest_path.c_str(), false);
    if (e != vr::VRApplicationError_None) {
      const char* name = apps->GetApplicationsErrorNameFromEnum(e);
      log_warn("openvr", std::string("AddApplicationManifest failed: ") + (name ? name : "?"));
    }
  }
  const vr::EVRApplicationError e = apps->IdentifyApplication(static_cast<uint32_t>(::getpid()), kAppKey);
  if (e != vr::VRApplicationError_None) {
    const char* name = apps->GetApplicationsErrorNameFromEnum(e);
    log_warn("openvr", std::string("IdentifyApplication failed: ") + (name ? name : "?"));
  } else {
    log_info("openvr", std::string("identified as ") + kAppKey);
  }
}

// Optional. Without these actions the laser just stays on while carrying or resizing.
void VrPanel::init_input(const std::string& actions_path) {
  vr::IVRInput* input = vr::VRInput();
  if (!input) {
    log_warn("openvr-input", "IVRInput unavailable; the laser stays on while carrying");
    return;
  }
  if (!file_exists(actions_path)) {
    log_warn("openvr-input", "action manifest not found: " + actions_path + " (the laser stays on while carrying)");
    return;
  }
  const vr::EVRInputError e = input->SetActionManifestPath(actions_path.c_str());
  if (e != vr::VRInputError_None) {
    log_warn("openvr-input", "SetActionManifestPath failed (" + std::to_string(static_cast<int>(e)) + ")");
    return;
  }
  input_ready_ = input->GetActionSetHandle("/actions/panel", &action_set_) == vr::VRInputError_None &&
                 input->GetActionHandle("/actions/panel/in/hold", &hold_action_) == vr::VRInputError_None &&
                 input->GetActionHandle("/actions/panel/in/depth", &depth_action_) == vr::VRInputError_None &&
                 input->GetActionHandle("/actions/panel/in/aim", &aim_action_) == vr::VRInputError_None &&
                 input->GetInputSourceHandle("/user/hand/left", &hands_[0]) == vr::VRInputError_None &&
                 input->GetInputSourceHandle("/user/hand/right", &hands_[1]) == vr::VRInputError_None;
  if (input_ready_) {
    log_info("openvr-input", "actions registered");
  } else {
    log_warn("openvr-input", "action handles unavailable; the laser stays on while carrying");
  }
}

void VrPanel::shutdown() {
  if (countdown_) countdown_->release_overlay();
  if (overlay_ && panel_ != vr::k_ulOverlayHandleInvalid) {
    overlay_->HideOverlay(panel_);
    overlay_->ClearOverlayTexture(panel_);
    std::this_thread::sleep_for(kCompositorDrainDelay);
    overlay_->DestroyOverlay(panel_);
    panel_ = vr::k_ulOverlayHandleInvalid;
    std::this_thread::sleep_for(kCompositorDrainDelay);
  }
  overlay_ = nullptr;
  if (vr_initialized_) {
    vr::VR_Shutdown();
    vr_initialized_ = false;
  }
  // Must come after DestroyOverlay and VR_Shutdown (see vulkan_texture.h).
  if (texture_) texture_->Shutdown();
  texture_.reset();
}

void VrPanel::set_title(const std::string& title) {
  if (!overlay_ || panel_ == vr::k_ulOverlayHandleInvalid || title.empty()) return;
  overlay_->SetOverlayName(panel_, ("scrcpy4Frame: " + title).c_str());
}

void VrPanel::set_long_side(float meters) {
  long_side_m_ = std::clamp(meters, panel::kMinLongSideMeters, max_long_side());
  if (has_frame_) apply_geometry();
}

void VrPanel::set_theater_long_side(float meters) {
  theater_long_m_ = std::clamp(meters, panel::kMinLongSideMeters, panel::kMaxTheaterLongSideMeters);
}

float VrPanel::max_long_side() const {
  return theater_ ? panel::kMaxTheaterLongSideMeters : panel::kMaxLongSideMeters;
}

void VrPanel::set_audio(bool on, bool enabled) {
  audio_on_ = on;
  audio_enabled_ = enabled;
  update_hover();
}

void VrPanel::set_scroll_speed(float screens_per_click, float screen_long_dp) {
  scroll_screens_per_click_ = screens_per_click;
  screen_long_dp_ = screen_long_dp;
}

void VrPanel::set_saved_pose(const panel::Mat34& pose) {
  saved_pose_ = pose;
  has_saved_pose_ = true;
}


scrcpy::Position VrPanel::phone_point(float mouse_x, float mouse_y) const {
  // Mouse Y is bottom-up and the phone image sits above the control strip.
  return scrcpy::map_overlay_point(mouse_x, mouse_y - static_cast<float>(chrome_.strip_h()), shown_w_, shown_h_);
}

float VrPanel::meters_per_pixel() const {
  return shown_w_ ? panel::panel_width_m(shown_w_, shown_h_, long_side_m_) / static_cast<float>(shown_w_) : 0.0f;
}

// The whole overlay: the phone image and the controls under it.
float VrPanel::height_m() const { return meters_per_pixel() * static_cast<float>(chrome_.height()); }

// How far the phone screen's bottom edge is below the overlay's centre. A theater screen stands on
// it, like the Steam Frame's own theater screen, with the controls under it below the floor.
float VrPanel::foot_m() const {
  return meters_per_pixel() * (0.5f * static_cast<float>(chrome_.height()) - static_cast<float>(chrome_.strip_h()));
}

// Where the phone screen's centre is from the overlay's, in metres along the panel's axes (X
// right, Y up): up past the controls under it and left of the bracket's margin. The panel is
// turned square to the user's line to this point, since that's what they look at.
panel::Vec3 VrPanel::screen_offset() const {
  const float mpp = meters_per_pixel();
  return {-0.5f * static_cast<float>(chrome_.margin_r) * mpp, 0.5f * static_cast<float>(chrome_.strip_h()) * mpp, 0.0f};
}

bool VrPanel::show_frame(const RgbaFrame& frame, std::string& error) {
  if (!overlay_ || !texture_) {
    error = "VR panel not initialised";
    return false;
  }
  bool reshaped = false;
  const float old_foot = foot_m();
  if (frame.width != shown_w_ || frame.height != shown_h_) {
    // A gesture can't survive a reshape. The server drops it on rotation anyway. Nor can the
    // zoom anchor: the image under it has moved.
    release_pointer(pending_);
    if (zoom_) end_zoom(pending_, false);
    if (resizing_) end_resize();
    shown_w_ = frame.width;
    shown_h_ = frame.height;
    chrome_ = chrome::layout(shown_w_, shown_h_);
    composite_.assign(static_cast<size_t>(chrome_.width()) * chrome_.height() * 4, 0);
    chrome_dirty_ = true;
    reshaped = true;
    log_info("panel", "showing " + std::to_string(shown_w_) + "x" + std::to_string(shown_h_));
  }
  // Copy into the top-left, leaving the chrome intact.
  const size_t src_row = static_cast<size_t>(shown_w_) * 4, dst_row = static_cast<size_t>(chrome_.width()) * 4;
  for (uint32_t y = 0; y < shown_h_; ++y) {
    std::memcpy(composite_.data() + y * dst_row, frame.pixels.data() + y * src_row, src_row);
  }
  if (zoom_) mark_anchor();
  if (!hidden_ && !upload_composite(error)) return false;  // while hidden, only keep the frame
  if (reshaped) {
    apply_geometry();
    // Reshaped around its centre, a theater screen on the floor would sink into it or lift off.
    // Its foot, the phone screen's bottom edge, stays put instead.
    if (theater_ && placed_) {
      set_pose(panel::offset_along_up(panel_pose_, foot_m() - old_foot));
      moved_ = true;
    }
  }
  if (!has_frame_) {
    has_frame_ = true;
    first_frame_at_ = Clock::now();
  }
  if (!placed_) try_place();
  if (placed_) show_panel();
  return true;
}

bool VrPanel::upload_composite(std::string& error) {
  if (chrome_dirty_) {
    chrome::draw(composite_.data(), chrome_, chrome_state_);
    chrome_dirty_ = false;
  }
  return texture_->Update(overlay_, panel_, composite_.data(), chrome_.width(), chrome_.height(), error);
}

void VrPanel::set_chrome(const chrome::State& state) {
  if (state == chrome_state_) return;
  chrome_state_ = state;
  chrome_dirty_ = true;
  reupload();
}

// A static phone screen sends no new frames, so re-upload now.
void VrPanel::reupload() {
  if (!has_frame_ || hidden_) return;
  std::string error;
  if (!upload_composite(error)) log_warn("panel", "refresh failed: " + error);
}

// While a control is in use or a finger is down, the other controls don't highlight.
void VrPanel::update_hover() {
  const bool grabbing = grab_device_ != vr::k_unTrackedDeviceIndexInvalid;
  const bool busy = grabbing || resizing_ || pointer_down_ || turning_;
  const chrome::Zone z = has_mouse_ ? chrome::zone(chrome_, mouse_x_, mouse_y_, menu_open_) : chrome::Zone::Empty;
  const bool free = !busy && pressed_ == chrome::Zone::Empty;
  chrome::State s;
  s.bar = grabbing || (free && z == chrome::Zone::Bar);
  s.resize = resizing_ || (free && z == chrome::Zone::Resize);
  const auto button = [&](chrome::Zone b) { return !busy && z == b && (free || pressed_ == b); };
  s.back = button(chrome::Zone::Back);
  s.home = button(chrome::Zone::Home);
  s.recents = button(chrome::Zone::Recents);
  s.more = button(chrome::Zone::More);
  s.hide = button(chrome::Zone::Hide);
  s.menu_open = menu_open_;
  s.theater = button(chrome::Zone::Theater);
  s.theater_on = theater_;
  s.recenter = button(chrome::Zone::Recenter);
  s.audio = button(chrome::Zone::Audio);
  s.audio_on = audio_on_;
  s.audio_enabled = audio_enabled_;
  set_chrome(s);
}

void VrPanel::show_panel() {
  if (!visible_ && !hidden_) {
    overlay_->ShowOverlay(panel_);
    visible_ = true;
  }
}

void VrPanel::set_hidden(bool hidden) {
  if (!overlay_ || hidden == hidden_) return;
  if (hidden) {
    if (countdown_) countdown_->cancel();  // a pending recenter would show it again
    release_pointer(pending_);  // never leave a finger down on the phone
    if (zoom_) end_zoom(pending_, false);
    if (grab_device_ != vr::k_unTrackedDeviceIndexInvalid) end_grab();
    if (resizing_) end_resize();
    pressed_ = chrome::Zone::Empty;
    has_mouse_ = false;
    menu_open_ = false;
    update_hover();
    hidden_ = true;
    overlay_->HideOverlay(panel_);
    visible_ = false;
    log_info("panel", "hidden (the phone keeps streaming)");
  } else {
    hidden_ = false;
    log_info("panel", "shown");
    if (has_frame_) {
      std::string error;
      if (!upload_composite(error)) log_warn("panel", "upload on show failed: " + error);
      if (!placed_) try_place();
      if (placed_) show_panel();
    }
  }
  visibility_changed_ = true;
}

bool VrPanel::take_visibility(bool& visible) {
  if (!visibility_changed_) return false;
  visibility_changed_ = false;
  visible = !hidden_;
  return true;
}

// World-locked. Uses the saved pose if any, otherwise on the HMD's line of sight (farther in
// theater).
bool VrPanel::try_place() {
  if (has_saved_pose_) {
    panel_pose_ = saved_pose_;
    has_saved_pose_ = false;
  } else {
    panel::Mat34 head;
    if (device_pose(vr::k_unTrackedDeviceIndex_Hmd, head)) {
      // ok
    } else if (has_frame_ && Clock::now() - first_frame_at_ >= kPlacementFallbackDelay) {
      log_warn("panel", "HMD pose unavailable; using a default standing position");
      head = panel::identity();
      head.m[1][3] = 1.6f;
    } else {
      return false;
    }
    const float distance = theater_ ? panel::kTheaterDistanceMeters : panel::kDistanceMeters;
    panel_pose_ = panel::turned(panel::place_on_gaze(head, distance, view_, screen_offset()), kSideTurnRadians);
    moved_ = true;
  }
  tilt_.reset(std::fabs(panel_pose_.m[1][2]) > 1e-3f);  // at rest as placed: square if tilted at all
  const vr::HmdMatrix34_t m = to_vr(panel_pose_);
  overlay_->SetOverlayTransformAbsolute(panel_, vr::TrackingUniverseStanding, &m);
  placed_ = true;
  return true;
}

void VrPanel::apply_geometry() {
  if (!overlay_ || shown_w_ == 0 || shown_h_ == 0) return;
  // OpenVR derives the height from the texture's aspect ratio.
  overlay_->SetOverlayWidthInMeters(panel_, meters_per_pixel() * static_cast<float>(chrome_.width()));
  const vr::HmdVector2_t scale{{static_cast<float>(chrome_.width()), static_cast<float>(chrome_.height())}};
  overlay_->SetOverlayMouseScale(panel_, &scale);
}

void VrPanel::set_pose(const panel::Mat34& pose) {
  panel_pose_ = pose;
  const vr::HmdMatrix34_t m = to_vr(panel_pose_);
  overlay_->SetOverlayTransformAbsolute(panel_, vr::TrackingUniverseStanding, &m);
}

void VrPanel::recenter() {
  if (!overlay_) return;
  if (!countdown_) {
    recenter_now();
    return;
  }
  if (!countdown_->running()) log_info("panel", "recenter in 3 s");
  countdown_->start();
}

void VrPanel::recenter_now() {
  if (!overlay_) return;
  if (countdown_) countdown_->cancel();
  if (grab_device_ != vr::k_unTrackedDeviceIndexInvalid) end_grab();
  if (resizing_) end_resize();
  menu_open_ = false;
  update_hover();
  has_saved_pose_ = false;
  placed_ = false;
  if (hidden_) {
    set_hidden(false);  // places it in front of the user
    return;
  }
  if (has_frame_ && try_place()) show_panel();
}

// Showing a hidden panel brings it in front of the user at once, except in theater, where it comes
// back where it was hidden.
void VrPanel::reveal() {
  if (theater_ && hidden_) {
    set_hidden(false);
  } else {
    recenter_now();
  }
}

// Theater: the theater size, 3.8 m straight ahead standing on the floor (the phone screen's bottom
// edge on it), facing the user, every time it starts. Pressed again it brings back the size from
// before, in front of the user at once.
void VrPanel::toggle_theater() {
  if (theater_) {
    theater_ = false;
    theater_changed_ = true;
    set_long_side(pre_theater_long_m_);
    log_info("panel", "theater off");
    recenter_now();
    return;
  }
  panel::Mat34 head;
  if (!overlay_ || !placed_ || !device_pose(vr::k_unTrackedDeviceIndex_Hmd, head)) return;
  if (countdown_) countdown_->cancel();  // this is where the user wants it now
  pre_theater_long_m_ = long_side_m_;
  theater_ = true;
  theater_changed_ = true;
  set_long_side(theater_long_m_);
  set_pose(panel::turned(panel::place_theater(head, foot_m(), screen_offset()), kSideTurnRadians));
  tilt_.reset(false);
  moved_ = true;
  update_hover();  // the Theater button shows that it's on
  log_info("panel", "theater on");
}

void VrPanel::set_theater(bool on) {
  if (!overlay_) return;
  if (on != theater_) toggle_theater();  // ending it already shows the panel in front of the user
  if (on && theater_ && hidden_) set_hidden(false);
}

bool VrPanel::take_theater(bool& on) {
  if (!theater_changed_) return false;
  theater_changed_ = false;
  on = theater_;
  return true;
}

void VrPanel::set_menu_open(bool open) {
  if (open == menu_open_) return;
  menu_open_ = open;
  // Closing the dashboard closes the menu, so note whether it's open now.
  if (open) dashboard_was_visible_ = overlay_->IsDashboardVisible();
  update_hover();
}

bool VrPanel::take_moved(panel::Mat34& pose, bool& in_front, float& long_side) {
  if (!moved_) return false;
  moved_ = false;
  pose = panel_pose_;
  in_front = theater_;
  long_side = theater_ ? pre_theater_long_m_ : long_side_m_;
  return true;
}

bool VrPanel::device_pose(vr::TrackedDeviceIndex_t device, panel::Mat34& pose) const {
  if (device >= vr::k_unMaxTrackedDeviceCount) return false;
  vr::TrackedDevicePose_t poses[vr::k_unMaxTrackedDeviceCount] = {};
  vr::VRSystem()->GetDeviceToAbsoluteTrackingPose(vr::TrackingUniverseStanding, 0.0f, poses,
                                                  vr::k_unMaxTrackedDeviceCount);
  if (!poses[device].bPoseIsValid) return false;
  pose = from_vr(poses[device].mDeviceToAbsoluteTracking);
  return true;
}

// Overlay mouse events may carry the HMD or an invalid index. Fall back to the dashboard's
// primary device, then to a controller with the trigger held.
vr::TrackedDeviceIndex_t VrPanel::resolve_pointer_device(vr::TrackedDeviceIndex_t device) const {
  vr::IVRSystem* system = vr::VRSystem();
  const auto valid = [system](vr::TrackedDeviceIndex_t d) {
    return d < vr::k_unMaxTrackedDeviceCount &&
           system->GetTrackedDeviceClass(d) == vr::TrackedDeviceClass_Controller;
  };
  if (valid(device)) return device;
  device = overlay_->GetPrimaryDashboardDevice();
  if (valid(device)) return device;
  for (const auto role : {vr::TrackedControllerRole_LeftHand, vr::TrackedControllerRole_RightHand}) {
    const vr::TrackedDeviceIndex_t candidate = system->GetTrackedDeviceIndexForControllerRole(role);
    vr::VRControllerState_t state{};
    if (valid(candidate) && system->GetControllerState(candidate, &state, sizeof state) &&
        (state.ulButtonPressed & vr::ButtonMaskFromId(vr::k_EButton_SteamVR_Trigger))) {
      return candidate;
    }
  }
  return vr::k_unTrackedDeviceIndexInvalid;
}

// A tracked-device-relative transform lets SteamVR move the panel with the controller, with no
// per-frame updates from us.
void VrPanel::begin_grab(vr::TrackedDeviceIndex_t device) {
  device = resolve_pointer_device(device);
  panel::Mat34 controller;
  if (!placed_ || !device_pose(device, controller)) return;
  grab_relative_ = panel::multiply(panel::inverse_rigid(controller), panel_pose_);
  const vr::HmdMatrix34_t rel = to_vr(grab_relative_);
  const vr::EVROverlayError e = overlay_->SetOverlayTransformTrackedDeviceRelative(panel_, device, &rel);
  if (e != vr::VROverlayError_None) {
    log_warn("panel", std::string("grab failed: ") + overlay_error_name(overlay_, e));
    return;
  }
  if (countdown_) countdown_->cancel();  // the user is placing it
  grab_device_ = device;
  glide_.reset();
  grab_started_ = Clock::now();
  set_carrying(true);
  begin_laser_free(device);
}

// Hides the laser's dot on the panel while carrying or resizing.
void VrPanel::set_carrying(bool carrying) {
  overlay_->SetOverlayFlag(panel_, vr::VROverlayFlags_HideLaserIntersection, carrying);
}

void VrPanel::finish_action() {
  if (grab_device_ != vr::k_unTrackedDeviceIndexInvalid) {
    end_grab();
  } else if (resizing_) {
    end_resize();
  }
}

// Back to an absolute transform where the controller left the panel.
void VrPanel::end_grab() {
  if (grab_device_ == vr::k_unTrackedDeviceIndexInvalid) return;
  const bool laser_was_off = laser_free_;
  end_laser_free();
  panel::Mat34 controller;
  if (device_pose(grab_device_, controller)) {
    panel_pose_ = panel::multiply(controller, grab_relative_);
  }
  const vr::HmdMatrix34_t m = to_vr(panel_pose_);
  overlay_->SetOverlayTransformAbsolute(panel_, vr::TrackingUniverseStanding, &m);
  grab_device_ = vr::k_unTrackedDeviceIndexInvalid;
  moved_ = true;
  set_carrying(false);
  char buf[240];
  std::snprintf(buf, sizeof buf, "carried %.1f s, laser %s; depth %+.3f m (%u scroll events, max |delta| %.3f)",
                std::chrono::duration<double>(Clock::now() - grab_started_).count(),
                laser_was_off ? "off (SteamVR Input)" : "on", static_cast<double>(glide_.travelled_m),
                glide_.events, static_cast<double>(glide_.max_abs_delta));
  log_info("panel", buf);
  glide_.reset();
  update_hover();
}

// Every tick while carrying, `seconds` since the last: turns the panel to face the head again,
// upright, or square once it's well above or below it (aimed), after moving it `push_m` along the
// eye->panel line (positive = farther). In between, SteamVR moves it with the controller.
void VrPanel::update_grab(float push_m, float seconds) {
  panel::Mat34 controller, head;
  if (!device_pose(grab_device_, controller) || !device_pose(vr::k_unTrackedDeviceIndex_Hmd, head)) return;
  const panel::Vec3 eye = panel::translation(head);
  panel::Mat34 now = panel::multiply(controller, grab_relative_);
  if (push_m != 0.0f) {
    now = panel::push_away(now, eye, push_m, theater_ ? panel::kMaxTheaterDistanceMeters : panel::kMaxDistanceMeters);
  }
  aim_eye_ = eye;
  aim_right_ = {head.m[0][0], head.m[1][0], head.m[2][0]};
  grab_relative_ = panel::multiply(panel::inverse_rigid(controller), aimed(panel::translation(now), seconds));
  const vr::HmdMatrix34_t rel = to_vr(grab_relative_);
  overlay_->SetOverlayTransformTrackedDeviceRelative(panel_, grab_device_, &rel);
}

// The panel at `at` facing aim_eye_, as far through turning between upright and square as tilt_
// is `seconds` on: crossing kUprightWithinRadians starts it turning (panel::TiltAnimation).
panel::Mat34 VrPanel::aimed(const panel::Vec3& at, float seconds) {
  const panel::Vec3 content = screen_offset();
  const float share = tilt_.update(panel::past_upright(at, aim_eye_, aim_right_, content), seconds);
  return panel::turned(panel::facing_tilted(at, aim_eye_, share, aim_right_, content), kSideTurnRadians);
}

// Resizing keeps the centre where it was when the bracket was grabbed, like the Steam Frame's
// windows, in theater too. With the laser on, the pointer comes from overlay mouse events so it
// matches the visible beam.
void VrPanel::begin_resize(vr::TrackedDeviceIndex_t device) {
  if (!placed_ || shown_w_ == 0) return;
  resize_long0_ = long_side_m_;
  resize_pointer(resize_grab_x_, resize_grab_y_);
  resize_anchor_ = panel::translation(panel_pose_);
  resizing_ = true;
  aim_started_ = false;
  if (countdown_) countdown_->cancel();  // the user is placing it
  set_carrying(true);
  begin_laser_free(resolve_pointer_device(device));
}

// The laser's point in metres from the centre, x right, y down. Mouse Y is bottom-up.
void VrPanel::resize_pointer(float& x, float& y) const {
  const float mpp = meters_per_pixel();
  x = (mouse_x_ - 0.5f * static_cast<float>(chrome_.width())) * mpp;
  y = (0.5f * static_cast<float>(chrome_.height()) - mouse_y_) * mpp;
}

void VrPanel::update_resize() {
  float x = 0.0f, y = 0.0f;
  resize_pointer(x, y);
  update_resize_to(x, y);
}

// `x`, `y` are the pointer in metres from the centre.
void VrPanel::update_resize_to(float x, float y) {
  const float scale = panel::resize_scale(resize_grab_x_, resize_grab_y_, x, y);
  const float long_side = std::clamp(resize_long0_ * scale, panel::kMinLongSideMeters, max_long_side());
  if (std::fabs(long_side - long_side_m_) < 1e-4f) return;
  long_side_m_ = long_side;
  apply_geometry();  // around the centre, which doesn't move
}

void VrPanel::end_resize() {
  if (!resizing_) return;
  end_laser_free();
  set_carrying(false);
  resizing_ = false;
  if (std::fabs(long_side_m_ - resize_long0_) > 1e-4f) {
    // The phone screen's centre has moved, so the panel faces the head again and may start turning
    // between upright and square (aimed; poll carries the turn on). It turns about its centre:
    // tilted about its foot, a tall theater screen close by would lean over the user.
    panel::Mat34 head;
    if (device_pose(vr::k_unTrackedDeviceIndex_Hmd, head)) {
      aim_eye_ = panel::translation(head);
      aim_right_ = {head.m[0][0], head.m[1][0], head.m[2][0]};
      set_pose(aimed(panel::translation(panel_pose_), 0.0f));
    }
    moved_ = true;
    char buf[80];
    std::snprintf(buf, sizeof buf, "resized: long side %.3f m", static_cast<double>(long_side_m_));
    log_info("panel", buf);
  }
  update_hover();
}

// OpenVR can hide the laser dot on an overlay but not the beam. Outside the dashboard the laser
// exists only because of MakeOverlaysInteractiveIfVisible, so clearing that flag while carrying
// or resizing removes it. Trigger, stick and aim then come from SteamVR Input. If that never
// reports the trigger, the flag is restored and overlay events take over again.
void VrPanel::begin_laser_free(vr::TrackedDeviceIndex_t device) {
  if (!input_ready_ || !kMakeInteractiveIfVisible || overlay_->IsDashboardVisible()) return;
  const vr::ETrackedControllerRole role = vr::VRSystem()->GetControllerRoleForTrackedDeviceIndex(device);
  if (role != vr::TrackedControllerRole_LeftHand && role != vr::TrackedControllerRole_RightHand) return;
  laser_free_hand_ = hands_[role == vr::TrackedControllerRole_LeftHand ? 0 : 1];
  overlay_->SetOverlayFlag(panel_, vr::VROverlayFlags_MakeOverlaysInteractiveIfVisible, false);
  laser_free_ = true;
  laser_free_held_ = false;
  laser_free_since_ = Clock::now();
}

void VrPanel::end_laser_free() {
  if (!laser_free_) return;
  laser_free_ = false;
  overlay_->SetOverlayFlag(panel_, vr::VROverlayFlags_MakeOverlaysInteractiveIfVisible, kMakeInteractiveIfVisible);
}

// Returns how far the stick moves the carried panel this tick.
float VrPanel::poll_laser_free(float seconds) {
  vr::IVRInput* input = vr::VRInput();
  vr::VRActiveActionSet_t set{};
  set.ulActionSet = action_set_;
  set.ulRestrictedToDevice = laser_free_hand_;
  // Outranks the game's action sets so the trigger and stick don't reach the game.
  set.nPriority = vr::k_nActionSetOverlayGlobalPriorityMin;
  input->UpdateActionState(&set, sizeof set, 1);

  vr::InputDigitalActionData_t hold{};
  const bool active = input->GetDigitalActionData(hold_action_, &hold, sizeof hold, laser_free_hand_) ==
                          vr::VRInputError_None && hold.bActive;
  const bool waited = Clock::now() - laser_free_since_ >= kLaserFreeConfirm;
  if (active && hold.bState) {
    laser_free_held_ = true;
    laser_free_misses_ = 0;
  } else if (active && (laser_free_held_ || waited)) {
    // Released, or a click too short to see. Two misses in a row means the binding doesn't
    // report the trigger, so keep the laser on from now on.
    if (!laser_free_held_ && ++laser_free_misses_ >= 2) {
      input_ready_ = false;
      log_warn("openvr-input", "the trigger never showed up as held; the laser stays on while carrying");
    }
    finish_action();
    return 0.0f;
  } else if (!active && (laser_free_held_ || waited)) {
    log_warn("openvr-input", "no trigger state from SteamVR Input; showing the laser again");
    if (!laser_free_held_) input_ready_ = false;
    end_laser_free();  // the action goes on with overlay events
    return 0.0f;
  }
  if (!laser_free_held_) return 0.0f;

  if (grab_device_ != vr::k_unTrackedDeviceIndexInvalid) {
    vr::InputAnalogActionData_t stick{};
    if (input->GetAnalogActionData(depth_action_, &stick, sizeof stick, laser_free_hand_) == vr::VRInputError_None &&
        stick.bActive) {
      const float meters = panel::stick_depth(stick.y, seconds);  // stick forward = farther
      glide_.travelled_m += meters;
      return meters;
    }
  } else if (resizing_) {
    // Move the corner by how far the aim point on the panel plane has moved since the resize began.
    vr::InputPoseActionData_t aim{};
    if (input->GetPoseActionDataRelativeToNow(aim_action_, vr::TrackingUniverseStanding, 0.0f, &aim, sizeof aim,
                                              laser_free_hand_) != vr::VRInputError_None ||
        !aim.bActive || !aim.pose.bPoseIsValid) {
      return 0.0f;
    }
    const panel::Mat34 m = from_vr(aim.pose.mDeviceToAbsoluteTracking);
    const panel::Vec3 forward{-m.m[0][2], -m.m[1][2], -m.m[2][2]};
    float x = 0.0f, y = 0.0f;
    if (!panel::ray_on_panel(panel_pose_, resize_anchor_, panel::translation(m), forward, x, y)) return 0.0f;
    if (!aim_started_) {
      aim_started_ = true;
      aim_x0_ = x;
      aim_y0_ = y;
      return 0.0f;
    }
    update_resize_to(resize_grab_x_ + (x - aim_x0_), resize_grab_y_ + (y - aim_y0_));
  }
  return 0.0f;
}

void VrPanel::begin_zoom() {
  const bool grabbing = grab_device_ != vr::k_unTrackedDeviceIndexInvalid;
  if (!has_frame_ || hidden_ || !has_mouse_ || grabbing || resizing_ || pointer_down_ ||
      pressed_ != chrome::Zone::Empty || chrome::zone(chrome_, mouse_x_, mouse_y_, menu_open_) != chrome::Zone::Phone) {
    return;
  }
  zoom_ = true;
  zoom_anchor_ = phone_point(mouse_x_, mouse_y_);
  mark_anchor();
  reupload();
  log_info("panel", "zoom mode on at " + std::to_string(zoom_anchor_.x) + "," + std::to_string(zoom_anchor_.y));
}

// Lifts the fingers and takes the anchor away, re-uploading the image if `upload`.
void VrPanel::end_zoom(std::vector<PanelAction>& out, bool upload) {
  if (!zoom_) return;
  end_pinch(out);
  end_turn(out);
  zoom_ = false;
  unmark_anchor();
  if (upload) reupload();
  log_info("panel", "zoom mode off");
}

// A smooth scroll event in zoom mode, `delta` units with positive for stick forward: spreads the
// fingers (zoom in) or closes them, putting them down first if they aren't. Held past the end of
// a stroke, the fingers lift and come down again for another, so zoom goes on.
void VrPanel::zoom_scroll(float delta, std::vector<PanelAction>& out) {
  const Clock::time_point now = Clock::now();
  if (now < scroll_quiet_until_ || !std::isfinite(delta)) return;
  last_pinch_at_ = now;
  if (!pinch_down_) {
    // A nudge doesn't put two fingers down, which apps would take for a two-finger tap.
    pinch_start_units_ += delta;
    if (std::fabs(pinch_start_units_) < kPinchStartUnits) return;
    pinch_.start(static_cast<float>(zoom_anchor_.x), static_cast<float>(zoom_anchor_.y), shown_w_, shown_h_,
                 pinch_start_units_ > 0.0f);
    pinch_down_ = true;
    pinch_started_ = now;
    pinch_events_ = 0;
    pinch_strokes_ = 1;
    send_pinch(PanelAction::Type::TouchDown, out);
    delta = pinch_start_units_;
    pinch_start_units_ = 0.0f;
  } else if (pinch_.spent(delta)) {
    send_pinch(PanelAction::Type::TouchUp, out);
    pinch_.restart(delta > 0.0f);
    send_pinch(PanelAction::Type::TouchDown, out);
    ++pinch_strokes_;
  }
  ++pinch_events_;
  pinch_.add(delta);
  send_pinch(PanelAction::Type::TouchMove, out);
}

// Both fingers, as scrcpy's own pinch does: the laser's finger and a virtual one.
void VrPanel::send_pinch(PanelAction::Type type, std::vector<PanelAction>& out) {
  for (int i = 0; i < 2; ++i) {
    PanelAction a;
    a.type = type;
    a.pointer_id = i == 0 ? scrcpy::kPointerIdGenericFinger : scrcpy::kPointerIdVirtualFinger;
    float x = 0.0f, y = 0.0f;
    pinch_.finger(i, x, y);
    a.pos = scrcpy::make_position(std::lround(x), std::lround(y), shown_w_, shown_h_);
    out.push_back(a);
  }
}

// Lifts both fingers, logging the pinch for tuning kPinchPerUnit.
void VrPanel::end_pinch(std::vector<PanelAction>& out) {
  pinch_start_units_ = 0.0f;
  if (!pinch_down_) return;
  pinch_down_ = false;
  send_pinch(PanelAction::Type::TouchUp, out);
  char buf[160];
  std::snprintf(buf, sizeof buf, "pinch %.2f s, %u scroll events, %u stroke(s), ending at %.0f px apart",
                std::chrono::duration<double>(Clock::now() - pinch_started_).count(), pinch_events_,
                pinch_strokes_, static_cast<double>(pinch_.span()));
  log_info("panel", buf);
}

// The laser's angle about the anchor, in radians from the image's x axis (y down). False while
// it's too close to the anchor to tell.
bool VrPanel::turn_angle(float& radians) const {
  const scrcpy::Position p = phone_point(mouse_x_, mouse_y_);
  const float dx = static_cast<float>(p.x - zoom_anchor_.x), dy = static_cast<float>(p.y - zoom_anchor_.y);
  const float dead = kTurnDeadShare * static_cast<float>(std::min(shown_w_, shown_h_));
  if (dx * dx + dy * dy < dead * dead) return false;
  radians = std::atan2(dy, dx);
  return true;
}

// The trigger on the image in zoom mode: two fingers on a circle about the anchor, lined up with
// the laser, that turn as it goes round.
void VrPanel::begin_turn(std::vector<PanelAction>& out) {
  float radians = 0.0f;
  turn_angle(radians);  // level if the laser is on the anchor
  pinch_.start_turn(static_cast<float>(zoom_anchor_.x), static_cast<float>(zoom_anchor_.y), shown_w_, shown_h_,
                    radians);
  turning_ = true;
  turn_last_ = radians;
  turn_total_ = 0.0f;
  pinch_started_ = Clock::now();
  send_pinch(PanelAction::Type::TouchDown, out);
}

void VrPanel::update_turn(std::vector<PanelAction>& out) {
  float radians = 0.0f;
  if (!turn_angle(radians)) return;  // hold still near the anchor
  float d = radians - turn_last_;
  if (d > 3.14159265f) d -= 6.28318531f;
  if (d < -3.14159265f) d += 6.28318531f;
  turn_total_ += d;
  turn_last_ = radians;
  pinch_.aim(radians);
  send_pinch(PanelAction::Type::TouchMove, out);
}

void VrPanel::end_turn(std::vector<PanelAction>& out) {
  if (!turning_) return;
  turning_ = false;
  send_pinch(PanelAction::Type::TouchUp, out);
  char buf[120];
  std::snprintf(buf, sizeof buf, "turn %.2f s, %+.0f deg, %.0f px across",
                std::chrono::duration<double>(Clock::now() - pinch_started_).count(),
                static_cast<double>(turn_total_) * 57.29578, static_cast<double>(pinch_.span()));
  log_info("panel", buf);
  update_hover();
}

// Draws the anchor over the image in composite_, keeping the pixels it covers for unmark_anchor.
// A new frame overwrites both, so it's called again after each.
void VrPanel::mark_anchor() {
  const float cx = static_cast<float>(zoom_anchor_.x) + 0.5f, cy = static_cast<float>(zoom_anchor_.y) + 0.5f;
  const float reach = chrome::anchor_reach(chrome_);
  const auto lo = [](float v) { return static_cast<uint32_t>(std::max(0.0f, std::floor(v))); };
  const uint32_t x0 = lo(cx - reach), y0 = lo(cy - reach);
  const uint32_t x1 = std::min(shown_w_, lo(cx + reach) + 1), y1 = std::min(shown_h_, lo(cy + reach) + 1);
  if (x0 >= x1 || y0 >= y1) return;
  anchor_box_[0] = x0;
  anchor_box_[1] = y0;
  anchor_box_[2] = x1 - x0;
  anchor_box_[3] = y1 - y0;
  const size_t row = static_cast<size_t>(chrome_.width()) * 4, run = static_cast<size_t>(anchor_box_[2]) * 4;
  anchor_under_.resize(run * anchor_box_[3]);
  for (uint32_t y = 0; y < anchor_box_[3]; ++y) {
    std::memcpy(anchor_under_.data() + y * run, composite_.data() + (y0 + y) * row + x0 * 4, run);
  }
  chrome::draw_anchor(composite_.data(), chrome_, cx, cy);
}

void VrPanel::unmark_anchor() {
  if (anchor_under_.empty()) return;
  const size_t row = static_cast<size_t>(chrome_.width()) * 4, run = static_cast<size_t>(anchor_box_[2]) * 4;
  for (uint32_t y = 0; y < anchor_box_[3]; ++y) {
    std::memcpy(composite_.data() + (anchor_box_[1] + y) * row + anchor_box_[0] * 4, anchor_under_.data() + y * run,
                run);
  }
  anchor_under_.clear();
}

void VrPanel::release_pointer(std::vector<PanelAction>& out) {
  if (!pointer_down_) return;
  pointer_down_ = false;
  PanelAction a;
  a.type = PanelAction::Type::TouchUp;
  a.pos = phone_point(mouse_x_, mouse_y_);
  out.push_back(a);
}

void VrPanel::handle_event(const vr::VREvent_t& ev, std::vector<PanelAction>& out) {
  if (shown_w_ == 0 || shown_h_ == 0) return;
  // Switching the laser off can still produce mouse and focus events. Ignore them.
  if (laser_free_ && ev.eventType != vr::VREvent_OverlayHidden) return;
  const bool grabbing = grab_device_ != vr::k_unTrackedDeviceIndexInvalid;
  PanelAction a;
  switch (ev.eventType) {
    case vr::VREvent_MouseMove:
      mouse_x_ = ev.data.mouse.x;
      mouse_y_ = ev.data.mouse.y;
      has_mouse_ = true;
      if (turning_) {
        update_turn(out);
      } else if (pointer_down_) {
        a.type = PanelAction::Type::TouchMove;
        a.pos = phone_point(mouse_x_, mouse_y_);
        out.push_back(a);
      } else if (resizing_) {
        update_resize();
      }
      update_hover();
      break;
    case vr::VREvent_MouseButtonDown: {
      mouse_x_ = ev.data.mouse.x;
      mouse_y_ = ev.data.mouse.y;
      has_mouse_ = true;
      const chrome::Zone z = chrome::zone(chrome_, mouse_x_, mouse_y_, menu_open_);
      // A click anywhere but the menu closes it, and still does what it does there.
      if (z != chrome::Zone::More && !chrome::is_menu_item(z)) set_menu_open(false);
      if (ev.data.mouse.button & vr::VRMouseButton_Left) {
        if (grabbing || resizing_ || pointer_down_ || turning_ || pressed_ != chrome::Zone::Empty) break;
        end_pinch(out);  // the trigger takes over from the stick's fingers
        switch (z) {
          case chrome::Zone::Phone:
            if (zoom_) {
              begin_turn(out);
              break;
            }
            pointer_down_ = true;
            a.type = PanelAction::Type::TouchDown;
            a.pos = phone_point(mouse_x_, mouse_y_);
            out.push_back(a);
            break;
          case chrome::Zone::Bar: begin_grab(ev.trackedDeviceIndex); break;
          case chrome::Zone::Resize: begin_resize(ev.trackedDeviceIndex); break;
          case chrome::Zone::Back:
          case chrome::Zone::Home:
          case chrome::Zone::Recents:
          case chrome::Zone::More:
          case chrome::Zone::Hide:
          case chrome::Zone::Theater:
          case chrome::Zone::Recenter: pressed_ = z; break;  // acts on release
          case chrome::Zone::Audio:
            if (audio_enabled_) pressed_ = z;
            break;
          case chrome::Zone::Empty: break;
        }
        update_hover();
      } else if (ev.data.mouse.button & vr::VRMouseButton_Right) {
        // The stick's click on the Frame: zoom mode on at the laser, or off. Pressing the stick
        // nudges it.
        scroll_quiet_until_ = Clock::now() + kClickScrollQuiet;
        if (zoom_) {
          end_zoom(out, true);
        } else {
          begin_zoom();
        }
      } else if (ev.data.mouse.button & vr::VRMouseButton_Middle) {
        a.type = PanelAction::Type::Home;
        out.push_back(a);
      }
      break;
    }
    case vr::VREvent_MouseButtonUp:
      if (ev.data.mouse.button & vr::VRMouseButton_Right) scroll_quiet_until_ = Clock::now() + kClickScrollQuiet;
      if (ev.data.mouse.button & vr::VRMouseButton_Left) {
        mouse_x_ = ev.data.mouse.x;
        mouse_y_ = ev.data.mouse.y;
        if (grabbing) {
          end_grab();
        } else if (turning_) {
          end_turn(out);
        } else if (resizing_) {
          update_resize();
          end_resize();
        } else if (pressed_ != chrome::Zone::Empty) {
          // A button acts on release, and only if the laser is still on it.
          const chrome::Zone pressed = pressed_;
          pressed_ = chrome::Zone::Empty;
          if (chrome::zone(chrome_, mouse_x_, mouse_y_, menu_open_) == pressed) {
            switch (pressed) {
              case chrome::Zone::Back: a.type = PanelAction::Type::Back; out.push_back(a); break;
              case chrome::Zone::Home: a.type = PanelAction::Type::Home; out.push_back(a); break;
              case chrome::Zone::Recents: a.type = PanelAction::Type::AppSwitch; out.push_back(a); break;
              case chrome::Zone::More: set_menu_open(!menu_open_); break;
              case chrome::Zone::Hide: set_hidden(true); break;
              case chrome::Zone::Theater:
                menu_open_ = false;
                toggle_theater();
                break;
              case chrome::Zone::Recenter:
                menu_open_ = false;
                recenter();
                break;
              case chrome::Zone::Audio:  // the menu stays open to show the change
                a.type = PanelAction::Type::AudioCast;
                a.on = !audio_on_;
                out.push_back(a);
                break;
              default: break;  // only buttons are pressed
            }
          }
        } else if (pointer_down_) {
          release_pointer(out);
        }
        update_hover();
      }
      break;
    case vr::VREvent_ScrollSmooth:
      if (grabbing) {
        glide_.add(ev.data.scroll.ydelta);
      } else if (zoom_) {
        // Anywhere on the panel: the pinch is at the anchor, not the laser.
        if (!resizing_ && !pointer_down_ && !turning_) zoom_scroll(ev.data.scroll.ydelta, out);
      } else if (!resizing_ && has_mouse_ &&
                 chrome::zone(chrome_, mouse_x_, mouse_y_, menu_open_) == chrome::Zone::Phone) {
        // A stream of small deltas, one wheel click per unit, as the app's own pages get them
        // through gamescope. Scaled so the phone scrolls as fast as those pages.
        const float k = panel::scroll_clicks_per_unit(scroll_screens_per_click_, screen_long_dp_, shown_w_, shown_h_);
        a.type = PanelAction::Type::Scroll;
        a.pos = phone_point(mouse_x_, mouse_y_);
        a.hscroll = ev.data.scroll.xdelta * k;
        a.vscroll = ev.data.scroll.ydelta * k;
        out.push_back(a);
      }
      break;
    case vr::VREvent_FocusLeave:
    case vr::VREvent_OverlayHidden:
      release_pointer(out);  // never leave a finger down on the phone
      end_pinch(out);
      end_turn(out);
      // Zoom mode outlasts the laser leaving the panel, but not the panel going away.
      if (ev.eventType == vr::VREvent_OverlayHidden && zoom_) end_zoom(out, false);
      if (grabbing) end_grab();
      if (resizing_) end_resize();
      pressed_ = chrome::Zone::Empty;
      has_mouse_ = false;
      update_hover();
      break;
    default:
      break;
  }
}

bool VrPanel::poll(std::vector<PanelAction>& out) {
  if (!overlay_) return true;
  vr::VREvent_t ev{};
  while (vr::VRSystem()->PollNextEvent(&ev, sizeof ev)) {
    if (ev.eventType == vr::VREvent_Quit) {
      vr::VRSystem()->AcknowledgeQuit_Exiting();
      return false;
    }
    if (ev.eventType == vr::VREvent_DriverRequestedQuit) return false;
  }
  while (overlay_->PollNextOverlayEvent(panel_, &ev, sizeof ev)) {
    if (is_quit(ev.eventType)) {
      if (ev.eventType == vr::VREvent_Quit) vr::VRSystem()->AcknowledgeQuit_Exiting();
      return false;
    }
    handle_event(ev, out);
  }
  // Carrying updates every tick (at least every 5 ms), not only on events, so motion is smooth
  // and the panel keeps facing the user.
  const Clock::time_point now = Clock::now();
  if ((pinch_down_ || pinch_start_units_ != 0.0f) && now - last_pinch_at_ >= kPinchIdle) end_pinch(out);
  const float seconds = std::chrono::duration<float>(now - last_tick_).count();
  last_tick_ = now;
  float push = 0.0f;
  if (laser_free_) {
    push = poll_laser_free(seconds);
  } else if (grab_device_ != vr::k_unTrackedDeviceIndexInvalid) {
    push = glide_.take(seconds);
  }
  if (grab_device_ != vr::k_unTrackedDeviceIndexInvalid) {
    update_grab(push, seconds);
  } else if (tilt_.busy()) {
    // A turn between upright and square goes on after the panel is let go, facing the head as it
    // was then, since the panel stays put otherwise.
    set_pose(aimed(panel::translation(panel_pose_), seconds));
    if (!tilt_.busy()) moved_ = true;  // the next session opens as it now rests
  }
  if (countdown_ && countdown_->tick()) recenter_now();
  if (menu_open_) {
    const bool dashboard = overlay_->IsDashboardVisible();
    if (dashboard_was_visible_ && !dashboard) set_menu_open(false);
    dashboard_was_visible_ = dashboard;
  }
  if (!pending_.empty()) {
    out.insert(out.begin(), pending_.begin(), pending_.end());
    pending_.clear();
  }
  if (has_frame_ && !placed_ && !hidden_ && try_place()) show_panel();
  return true;
}

}  // namespace vrm
