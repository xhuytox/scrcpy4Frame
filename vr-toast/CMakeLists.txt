cmake_minimum_required(VERSION 3.16)
project(vr-toast VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

# OPENVR_ROOT: an OpenVR SDK checkout with an in-tree static build, or an install prefix like /app.
set(OPENVR_ROOT "$ENV{OPENVR_ROOT}" CACHE PATH "OpenVR SDK checkout or install prefix")
option(VR_TOAST_STATIC_RUNTIME
       "Link libstdc++ and libgcc statically on Linux so the binary also runs on the host" ON)
option(VR_TOAST_ALLOW_SHARED_OPENVR
       "Fall back to a shared libopenvr_api if no static one is found (dev builds only)" OFF)

if(APPLE)
  set(_openvr_platform osx64)  # dev builds only
elseif(CMAKE_SYSTEM_PROCESSOR MATCHES "^(aarch64|arm64|ARM64)$")
  set(_openvr_platform linuxarm64)
else()
  set(_openvr_platform linux64)
endif()

find_path(OPENVR_INCLUDE_DIR openvr.h
  HINTS ${OPENVR_ROOT}
  PATH_SUFFIXES include/openvr headers include)

# OpenVR's CMake writes the static library to bin/linux64 even on aarch64.
set(_openvr_lib_suffixes lib lib64 bin/${_openvr_platform} bin/linux64 lib/${_openvr_platform})
find_library(OPENVR_STATIC_LIBRARY
  NAMES libopenvr_api.a
  HINTS ${OPENVR_ROOT}
  PATH_SUFFIXES ${_openvr_lib_suffixes})

if(OPENVR_STATIC_LIBRARY)
  set(OPENVR_LIBRARY ${OPENVR_STATIC_LIBRARY})
elseif(VR_TOAST_ALLOW_SHARED_OPENVR)
  find_library(OPENVR_SHARED_LIBRARY
    NAMES openvr_api
    HINTS ${OPENVR_ROOT}
    PATH_SUFFIXES ${_openvr_lib_suffixes})
  set(OPENVR_LIBRARY ${OPENVR_SHARED_LIBRARY})
  if(OPENVR_LIBRARY)
    message(WARNING "Linking a shared libopenvr_api (${OPENVR_LIBRARY}); the binary will need it at run time.")
  endif()
endif()

if(NOT OPENVR_INCLUDE_DIR OR NOT OPENVR_LIBRARY)
  message(FATAL_ERROR
    "OpenVR not found. Build it as a static library (cmake -DBUILD_SHARED=OFF in an OpenVR "
    "checkout) and pass -DOPENVR_ROOT=<checkout or install prefix>.\n"
    "  include dir: ${OPENVR_INCLUDE_DIR}\n  static lib:  ${OPENVR_STATIC_LIBRARY}")
endif()
message(STATUS "OpenVR headers: ${OPENVR_INCLUDE_DIR}")
message(STATUS "OpenVR library: ${OPENVR_LIBRARY}")

find_package(Threads REQUIRED)

add_executable(vr-toast main.cpp)
target_include_directories(vr-toast PRIVATE ${OPENVR_INCLUDE_DIR})
target_link_libraries(vr-toast PRIVATE ${OPENVR_LIBRARY} ${CMAKE_DL_LIBS} Threads::Threads)
if(APPLE)
  target_link_libraries(vr-toast PRIVATE "-framework Foundation")  # OpenVR's path helpers
endif()
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(vr-toast PRIVATE -Wall -Wextra)
endif()
if(CMAKE_SYSTEM_NAME STREQUAL "Linux" AND VR_TOAST_STATIC_RUNTIME)
  # Runs on the host via `flatpak-spawn --host`, so it can't depend on the runtime's libstdc++.
  target_link_options(vr-toast PRIVATE -static-libstdc++ -static-libgcc)
endif()

install(TARGETS vr-toast RUNTIME DESTINATION bin)
