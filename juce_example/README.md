# JUCE Client Example

This is a **JUCE-based** (C++) example that calls the EDMG API over HTTP.

Why HTTP?
- Keeps heavy Python/ML off real-time audio threads
- Works for apps and plugins
- Portable across Windows/macOS/Linux

## Build

Requires:
- CMake 3.20+
- A C++ toolchain (MSVC/Xcode/clang)
- Internet access (FetchContent downloads JUCE)

```bash
cmake -S . -B build
cmake --build build -j
```

## Run

Start EDMG API:
```bash
PYTHONPATH=./src python -m uvicorn enhanced_deforum_music_generator.api.main:app --host 127.0.0.1 --port 8000
```

Then run the example:
```bash
./build/edmg_juce_client http://127.0.0.1:8000
```
