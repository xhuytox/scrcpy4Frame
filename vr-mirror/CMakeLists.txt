cmake_minimum_required(VERSION 3.16)
project(vr-mirror VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
  set(_vr_mirror_openvr_default ON)
else()
  set(_vr_mirror_openvr_default OFF)  # macOS dev builds: --no-vr only
endif()
option(VR_MIRROR_WITH_OPENVR "Build the OpenVR/Vulkan panel (OFF: --no-vr only binary)"
       ${_vr_mirror_openvr_default})
option(VR_MIRROR_FFMPEG_STATIC
       "Link libavcodec/libswscale/libavutil statically (from the prefix pkg-config finds)" OFF)
option(VR_MIRROR_STATIC_RUNTIME
       "Link libstdc++ and libgcc statically on Linux so the binary also runs on the host" ON)
option(VR_MIRROR_BUILD_TESTS "Build the protocol unit tests" ON)
set(OPENVR_ROOT "$ENV{OPENVR_ROOT}" CACHE PATH "OpenVR SDK checkout or install prefix")

find_package(Threads REQUIRED)

# pkg-config also searches CMAKE_PREFIX_PATH, such as /app/vrmirror-ffmpeg.
find_package(PkgConfig REQUIRED)
pkg_check_modules(FFMPEG REQUIRED IMPORTED_TARGET libavcodec libavutil libswscale)

add_library(vrmirror_ffmpeg INTERFACE)
if(VR_MIRROR_FFMPEG_STATIC)
  # Explicit .a paths so the shared FFmpeg in the Flatpak's /app/lib (for scrcpy) is never picked.
  # Single-pass static linking needs dependents first: avcodec, swscale, avutil.
  set(_ffmpeg_static_libs)
  foreach(_lib avcodec swscale avutil)
    find_library(VR_MIRROR_FFMPEG_${_lib}_A NAMES lib${_lib}.a
                 HINTS ${FFMPEG_STATIC_LIBRARY_DIRS} NO_DEFAULT_PATH)
    if(NOT VR_MIRROR_FFMPEG_${_lib}_A)
      message(FATAL_ERROR "VR_MIRROR_FFMPEG_STATIC: lib${_lib}.a not found in '${FFMPEG_STATIC_LIBRARY_DIRS}'")
    endif()
    list(APPEND _ffmpeg_static_libs ${VR_MIRROR_FFMPEG_${_lib}_A})
  endforeach()
  # Private dependencies (-lm, ...) in pkg-config's order.
  set(_ffmpeg_system_libs ${FFMPEG_STATIC_LIBRARIES})
  list(REMOVE_ITEM _ffmpeg_system_libs avcodec swscale avutil)
  list(REMOVE_DUPLICATES _ffmpeg_system_libs)
  # Remaining flags. macOS "-framework X" pairs must stay together.
  set(_ffmpeg_link_options)
  set(_pending_framework OFF)
  foreach(_flag IN LISTS FFMPEG_STATIC_LDFLAGS_OTHER)
    if(_pending_framework)
      list(APPEND _ffmpeg_link_options "SHELL:-framework ${_flag}")
      set(_pending_framework OFF)
    elseif(_flag STREQUAL "-framework")
      set(_pending_framework ON)
    else()
      list(APPEND _ffmpeg_link_options ${_flag})
    endif()
  endforeach()
  list(REMOVE_DUPLICATES _ffmpeg_link_options)
  message(STATUS "FFmpeg (static): ${_ffmpeg_static_libs} ${_ffmpeg_system_libs} ${_ffmpeg_link_options}")
  target_include_directories(vrmirror_ffmpeg INTERFACE ${FFMPEG_STATIC_INCLUDE_DIRS})
  target_compile_options(vrmirror_ffmpeg INTERFACE ${FFMPEG_STATIC_CFLAGS_OTHER})
  target_link_libraries(vrmirror_ffmpeg INTERFACE ${_ffmpeg_static_libs} ${_ffmpeg_system_libs}
                        Threads::Threads)
  target_link_options(vrmirror_ffmpeg INTERFACE ${_ffmpeg_link_options})
else()
  message(STATUS "FFmpeg (shared): ${FFMPEG_LINK_LIBRARIES}")
  target_link_libraries(vrmirror_ffmpeg INTERFACE PkgConfig::FFMPEG)
endif()

if(VR_MIRROR_WITH_OPENVR)
  if(APPLE)
    set(_openvr_platform osx64)
  elseif(CMAKE_SYSTEM_PROCESSOR MATCHES "^(aarch64|arm64|ARM64)$")
    set(_openvr_platform linuxarm64)
  else()
    set(_openvr_platform linux64)
  endif()
  find_path(OPENVR_INCLUDE_DIR openvr.h
    HINTS ${OPENVR_ROOT}
    PATH_SUFFIXES include/openvr headers include)
  # OpenVR's own CMake writes the static library to <src>/bin/linux64 even on aarch64.
  find_library(OPENVR_STATIC_LIBRARY
    NAMES libopenvr_api.a
    HINTS ${OPENVR_ROOT}
    PATH_SUFFIXES lib lib64 bin/${_openvr_platform} bin/linux64 lib/${_openvr_platform})
  if(NOT OPENVR_INCLUDE_DIR OR NOT OPENVR_STATIC_LIBRARY)
    message(FATAL_ERROR
      "OpenVR not found (static libopenvr_api.a + openvr.h). Pass -DOPENVR_ROOT=<checkout or "
      "install prefix>, or -DVR_MIRROR_WITH_OPENVR=OFF for a --no-vr build.")
  endif()
  # libvulkan.so.1 is dlopen()ed at run time, so only the headers are needed.
  find_path(VULKAN_INCLUDE_DIR vulkan/vulkan.h HINTS ENV VULKAN_SDK PATH_SUFFIXES include)
  if(NOT VULKAN_INCLUDE_DIR)
    message(FATAL_ERROR "Vulkan headers (vulkan/vulkan.h) not found; set VULKAN_INCLUDE_DIR")
  endif()
  message(STATUS "OpenVR: ${OPENVR_INCLUDE_DIR} ${OPENVR_STATIC_LIBRARY}")
  message(STATUS "Vulkan headers: ${VULKAN_INCLUDE_DIR}")
endif()

set(_warnings)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  set(_warnings -Wall -Wextra)
endif()

# No sockets, FFmpeg or OpenVR, so the unit tests can link it.
add_library(vrmirror_core STATIC
  src/log.cpp
  src/scrcpy_protocol.cpp
  src/panel_math.cpp
  src/panel_chrome.cpp
  src/ui_draw.cpp
  src/dash_math.cpp)
target_include_directories(vrmirror_core PUBLIC src)
target_compile_options(vrmirror_core PRIVATE ${_warnings})

add_executable(vr-mirror
  src/main.cpp
  src/scrcpy_client.cpp
  src/decoder.cpp)
target_link_libraries(vr-mirror PRIVATE vrmirror_core vrmirror_ffmpeg Threads::Threads)
target_compile_options(vr-mirror PRIVATE ${_warnings})
if(VR_MIRROR_WITH_OPENVR)
  target_sources(vr-mirror PRIVATE src/vr_panel.cpp src/dash_bar.cpp src/countdown.cpp src/vulkan_texture.cpp)
  target_compile_definitions(vr-mirror PRIVATE VR_MIRROR_WITH_OPENVR=1)
  target_include_directories(vr-mirror PRIVATE ${OPENVR_INCLUDE_DIR} ${VULKAN_INCLUDE_DIR})
  target_link_libraries(vr-mirror PRIVATE ${OPENVR_STATIC_LIBRARY} ${CMAKE_DL_LIBS})
  if(APPLE)
    target_link_libraries(vr-mirror PRIVATE "-framework Foundation")  # OpenVR's path helpers
  endif()
else()
  target_compile_definitions(vr-mirror PRIVATE VR_MIRROR_WITH_OPENVR=0)
endif()
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
  target_link_libraries(vr-mirror PRIVATE ${CMAKE_DL_LIBS})
  if(VR_MIRROR_STATIC_RUNTIME)
    # Runs on the SteamOS host via `flatpak-spawn --host`, outside the Flatpak runtime.
    target_link_options(vr-mirror PRIVATE -static-libstdc++ -static-libgcc)
  endif()
endif()

# The helper registers these manifests from its own directory, so copy them next to the dev
# binary too.
set(_vr_mirror_data
  manifest/vr-mirror.vrmanifest
  manifest/vr-mirror-actions.json
  manifest/vr-mirror-bindings-frame-controller.json)
foreach(_file IN LISTS _vr_mirror_data)
  get_filename_component(_name ${_file} NAME)
  configure_file(${_file} ${CMAKE_CURRENT_BINARY_DIR}/${_name} COPYONLY)
endforeach()

install(TARGETS vr-mirror RUNTIME DESTINATION bin)
install(FILES ${_vr_mirror_data} DESTINATION share/scrcpy4frame)

if(VR_MIRROR_BUILD_TESTS)
  enable_testing()
  add_executable(protocol_test tests/protocol_test.cpp)
  target_link_libraries(protocol_test PRIVATE vrmirror_core)
  target_compile_options(protocol_test PRIVATE ${_warnings})
  add_test(NAME protocol_test COMMAND protocol_test)
endif()
