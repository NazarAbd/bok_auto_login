# Redmi Note 13 Pro Camera Automation

A Windows 10 Python application that controls a connected Xiaomi Redmi Note 13 Pro using:

- ADB for device communication
- scrcpy for screen mirroring
- UIAutomator for UI detection
- MediaStore/filesystem verification for newly captured photos
- `adb pull` for copying the verified photo to the PC

The application is designed to:

1. Detect the phone over USB.
2. Verify ADB authorization.
3. Start scrcpy.
4. Launch the Camera app using Android intents/package detection.
5. Wait until the Camera UI is actually ready.
6. Detect the shutter button through UIAutomator when possible.
7. Fall back to a configurable coordinate only if UIAutomator cannot find the shutter.
8. Press the shutter exactly once.
9. Verify that exactly one new photo appeared.
10. Pull the verified photo to `photos/`.
11. Report success only after the local file has been verified.

---

## Important device-specific notes

This app is targeted at:

- Xiaomi Redmi Note 13 Pro
- Android 16
- Windows 10

However, Xiaomi/HyperOS camera implementations can vary.

The following items are device-specific:

- Camera package name
- Camera activity name
- Shutter button `resource-id`
- Shutter button `content-desc`
- Whether UIAutomator can see the shutter control
- Whether MIUI/HyperOS requires the extra USB debugging security setting
- Photo output format: JPEG or HEIC
- Photo save location, usually `/sdcard/DCIM/Camera`

The app does not blindly assume a fixed button location.  
It first attempts UIAutomator detection.  
Only if that fails does it use the configured fallback coordinate.

---

## Installation on Windows 10

### 1. Install Python

Install Python 3.10 or newer from python.org or Microsoft Store.

Check:

```cmd
py -3 --version
```
