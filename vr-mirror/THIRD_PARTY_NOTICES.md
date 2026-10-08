# Third-party notices

## Vulkan texture upload

`src/vulkan_texture.h` and `src/vulkan_texture.cpp` are adapted from MIT-licensed code. The
upload steps are unchanged. The changes are:

- Logging goes to vr-mirror's stderr logger.
- The namespace and file names are changed.
- The Vulkan application and engine names are changed.
- One device serves several overlays, each with its own images, staging buffer, command buffer
  and fence.

No other code from that project is included.

```
MIT License

Copyright (c) 2026 PhoneCast VR contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Libraries linked into the binary

- **FFmpeg** (libavcodec, libavutil, libswscale), LGPL 2.1 or later: <https://ffmpeg.org>.
  The Flatpak links a static build that has only the H.264 decoder
  (`flatpak/modules/vr-mirror.yml`).
- **OpenVR SDK**, BSD-3-Clause, Valve Corporation: <https://github.com/ValveSoftware/openvr>.
  It is linked statically.

The scrcpy 5.0 wire format is implemented from scrcpy's documentation and source. scrcpy is
Apache-2.0, Genymobile and Romain Vimont. No scrcpy code is copied. The unit tests reuse
scrcpy's published test vectors as expected byte values.
