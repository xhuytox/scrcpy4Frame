// SPDX-License-Identifier: MIT
//
// Copyright (c) 2026 PhoneCast VR contributors
//
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files (the "Software"), to deal
// in the Software without restriction, including without limitation the rights
// to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
// copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions:
//
// The above copyright notice and this permission notice shall be included in all
// copies or substantial portions of the Software.
//
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
// IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
// FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
// AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
// LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
// OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
// SOFTWARE.
//
// Changes for scrcpy4Frame vr-mirror (the upload steps are unchanged):
//   - logging goes to vr-mirror's stderr logger (log.h);
//   - namespace and file names changed;
//   - Vulkan application/engine names;
//   - one device serves several overlays, each with its own images, staging buffer, command
//     buffer and fence (Update picks them by overlay handle).

#pragma once

#include <openvr.h>

#include <cstdint>
#include <memory>
#include <string>

namespace vrm {

// Uploads RGBA frames to reusable Vulkan overlay textures, one set per overlay on a single
// device. Use one instance per process: OpenVR's client can't take overlay textures from two
// VkDevices (it throws std::bad_alloc on the next upload). Vulkan handles stay behind this class.
class SteamFrameVulkanTexture {
public:
    SteamFrameVulkanTexture();
    ~SteamFrameVulkanTexture();

    SteamFrameVulkanTexture(const SteamFrameVulkanTexture&) = delete;
    SteamFrameVulkanTexture& operator=(const SteamFrameVulkanTexture&) = delete;

    bool Initialize(::vr::IVRSystem* system, ::vr::IVRCompositor* compositor,
                    std::string& error);
    bool Update(::vr::IVROverlay* overlayApi, ::vr::VROverlayHandle_t overlay,
                const std::uint8_t* rgba, std::uint32_t width,
                std::uint32_t height, std::string& error);

    // Call only after the overlay texture is cleared, the overlay destroyed, and
    // VR_Shutdown has returned.
    void Shutdown() noexcept;

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace vrm
