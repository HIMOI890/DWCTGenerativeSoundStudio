cmake_minimum_required(VERSION 3.20)
project(edmg_juce_client VERSION 0.1.0)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

include(FetchContent)

FetchContent_Declare(
  juce
  GIT_REPOSITORY https://github.com/juce-framework/JUCE.git
  GIT_TAG 8.0.0
)
FetchContent_MakeAvailable(juce)

juce_add_console_app(edmg_juce_client PRODUCT_NAME "edmg_juce_client")

target_sources(edmg_juce_client PRIVATE
  src/main.cpp
)

target_compile_definitions(edmg_juce_client PRIVATE
  JUCE_WEB_BROWSER=0
  JUCE_USE_CURL=1
)

target_link_libraries(edmg_juce_client PRIVATE
  juce::juce_core
  juce::juce_events
)
