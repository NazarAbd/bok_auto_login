import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


class AdbError(Exception):
    pass


class NoDeviceError(AdbError):
    pass


class DeviceUnauthorizedError(AdbError):
    pass


class DeviceOfflineError(AdbError):
    pass


class MultipleDevicesError(AdbError):
    def __init__(self, devices: Dict[str, str]):
        self.devices = devices
        super().__init__("Multiple Android devices detected. Select one explicitly.")


@dataclass
class AdbResult:
    returncode: int
    stdout: object
    stderr: str


def decode_output(data: bytes) -> str:
    if not data:
        return ""
    return data.decode("utf-8", errors="replace").strip()


def _is_plausible_path(path: str) -> bool:
    return bool(path) and os.path.isfile(path)


def find_adb(config_path: str = "") -> str:
    """
    Locate adb.exe.

    Search order:
    1. Explicit config path
    2. PATH
    3. Common Windows platform-tools locations
    """
    candidates: List[str] = []

    if config_path:
        candidates.append(os.path.expandvars(os.path.expanduser(config_path)))

    for name in ("adb", "adb.exe"):
        found = shutil.which(name)
        if found:
            candidates.append(found)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)

    localappdata = os.environ.get("LOCALAPPDATA", "")
    programfiles = os.environ.get("PROGRAMFILES", "C:\\Program Files")
    programfiles_x86 = os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)")
    systemdrive = os.environ.get("SYSTEMDRIVE", "C:")

    candidates.extend(
        [
            os.path.join(project_root, "platform-tools", "adb.exe"),
            os.path.join(os.getcwd(), "platform-tools", "adb.exe"),
            os.path.join(localappdata, "Android", "Sdk", "platform-tools", "adb.exe") if localappdata else "",
            os.path.join(programfiles, "platform-tools", "adb.exe"),
            os.path.join(programfiles_x86, "platform-tools", "adb.exe"),
            os.path.join(systemdrive + os.sep, "platform-tools", "adb.exe"),
        ]
    )

    for candidate in candidates:
        if candidate and _is_plausible_path(candidate):
            return candidate

    raise AdbError(
        "ADB executable not found.\n\n"
        "Install Android platform-tools, or set adb_path in config.json.\n"
        "Example: C:\\platform-tools\\adb.exe"
    )


class AdbClient:
    def __init__(
        self,
        adb_path: str,
        serial: Optional[str] = None,
        default_timeout: float = 10.0,
        logger: Optional[logging.Logger] = None,
    ):
        self.adb_path = adb_path
        self.serial = serial
        self.default_timeout = float(default_timeout)
        self.log = logger or logging.getLogger("camera_automation.adb")

    def run(
        self,
        args: List[str],
        timeout: Optional[float] = None,
        expect_rc: bool = True,
        binary: bool = False,
        use_serial: bool = True,
    ) -> AdbResult:
        timeout = self.default_timeout if timeout is None else float(timeout)

        cmd = [self.adb_path]
        if use_serial and self.serial:
            cmd.extend(["-s", self.serial])
        cmd.extend(args)

        self.log.debug("ADB command: %s", " ".join(cmd))

        try:
            completed = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise AdbError(f"ADB executable not found: {self.adb_path}") from exc
        except subprocess.TimeoutExpired as exc:
            raise AdbError(f"ADB command timed out after {timeout}s: {' '.join(cmd)}") from exc
        except Exception as exc:
            raise AdbError(f"ADB command failed unexpectedly: {' '.join(cmd)}: {exc}") from exc

        stdout = completed.stdout if binary else decode_output(completed.stdout)
        stderr = decode_output(completed.stderr)

        if expect_rc and completed.returncode != 0:
            raise AdbError(
                f"ADB command failed with return code {completed.returncode}.\n"
                f"Command: {' '.join(cmd)}\n"
                f"stderr: {stderr or 'empty'}\n"
                f"stdout: {stdout if not binary else '<binary>'}"
            )

        return AdbResult(completed.returncode, stdout, stderr)

    def start_server(self) -> None:
        try:
            self.run(["start-server"], timeout=self.default_timeout, use_serial=False)
        except AdbError as exc:
            self.log.warning("adb start-server failed: %s", exc)

    def devices(self) -> Dict[str, str]:
        res = self.run(["devices"], timeout=self.default_timeout, use_serial=False)
        result: Dict[str, str] = {}

        for line in res.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("List of devices"):
                continue
            if "\t" not in line:
                continue

            serial, state = line.split("\t", 1)
            serial = serial.strip()
            state = state.strip()
            if serial:
                result[serial] = state

        return result

    def get_device_state(self, serial: str) -> Optional[str]:
        return self.devices().get(serial)

    def ensure_device_state(self, serial: str) -> None:
        state = self.get_device_state(serial)

        if state is None:
            raise NoDeviceError(
                "No Android device detected. Connect the Redmi Note 13 Pro with USB debugging enabled."
            )

        if state == "unauthorized":
            raise DeviceUnauthorizedError(
                "ADB authorization is not granted.\n\n"
                "Unlock the phone and accept the USB debugging authorization dialog, then retry."
            )

        if state == "offline":
            raise DeviceOfflineError(
                "The device is offline.\n\n"
                "Reconnect the USB cable, unlock the phone, and retry."
            )

        if state != "device":
            raise AdbError(f"Device {serial} is in unexpected ADB state: {state}")

    def get_prop(self, prop: str) -> str:
        res = self.run(["shell", "getprop", prop], timeout=self.default_timeout)
        return res.stdout.strip()

    def get_device_info(self) -> Dict[str, str]:
        if not self.serial:
            raise AdbError("No device serial selected.")

        self.ensure_device_state(self.serial)

        model = (
            self.get_prop("ro.product.model")
            or self.get_prop("ro.product.vendor.model")
            or self.get_prop("ro.product.device")
            or "unknown"
        )
        manufacturer = (
            self.get_prop("ro.product.manufacturer")
            or self.get_prop("ro.product.vendor.manufacturer")
            or "unknown"
        )
        android_version = self.get_prop("ro.build.version.release") or "unknown"

        return {
            "serial": self.serial,
            "model": model,
            "manufacturer": manufacturer,
            "android_version": android_version,
        }

    def get_screen_size(self) -> Tuple[int, int]:
        res = self.run(["shell", "wm", "size"], timeout=self.default_timeout)
        matches = re.findall(r"(\d+)x(\d+)", res.stdout)
        if not matches:
            raise AdbError(f"Could not determine screen size from: {res.stdout}")

        width, height = map(int, matches[-1])
        return width, height

    def get_orientation(self) -> int:
        """
        Return rotation as 0, 1, 2, or 3.
        Best effort only. Some Xiaomi builds hide or rename these fields.
        """
        outputs = []

        for args in (["shell", "dumpsys", "window", "displays"], ["shell", "dumpsys", "window"]):
            try:
                res = self.run(args, timeout=max(20.0, self.default_timeout))
                outputs.append(res.stdout)
            except AdbError:
                continue

        text = "\n".join(outputs)

        patterns = [
            r"mCurrentRotation=(\d)",
            r"SurfaceOrientation[:=]\s*(\d)",
            r"displayRotation=(\d)",
        ]

        for pattern in patterns:
            m = re.search(pattern, text)
            if m:
                try:
                    return int(m.group(1)) % 4
                except ValueError:
                    continue

        return 0

    def get_logical_screen_size(self) -> Tuple[int, int]:
        """
        Return screen size in the current coordinate space used by input tap.
        If orientation is landscape, swap physical width/height.
        """
        width, height = self.get_screen_size()
        orientation = self.get_orientation()
        if orientation in (1, 3):
            return height, width
        return width, height

    def is_screen_on(self) -> bool:
        try:
            res = self.run(["shell", "dumpsys", "power"], timeout=max(15.0, self.default_timeout))
        except AdbError:
            return True

        return bool(
            re.search(
                r"Display Power: state=ON|mWakefulness=Awake",
                res.stdout,
                re.IGNORECASE,
            )
        )

    def wake_screen(self) -> None:
        self.run(["shell", "input", "keyevent", "224"], timeout=self.default_timeout)

    def is_locked(self) -> bool:
        try:
            res = self.run(["shell", "dumpsys", "window"], timeout=max(20.0, self.default_timeout))
        except AdbError:
            return False

        return bool(
            re.search(
                r"mDreamingLockscreen=true|mShowingLockscreen=true|isStatusBarKeyguard=true",
                res.stdout,
                re.IGNORECASE,
            )
        )

    def current_focus(self) -> Tuple[Optional[str], Optional[str]]:
        """
        Return foreground package and activity, best effort.
        """
        try:
            res = self.run(["shell", "dumpsys", "window"], timeout=max(20.0, self.default_timeout))
        except AdbError:
            return None, None

        text = res.stdout

        m = re.search(r"mCurrentFocus=Window\{[^\}]*\}", text)
        if m:
            token = m.group(0)
            comp = re.search(r"([A-Za-z0-9_.]+(?:/[A-Za-z0-9_.$]+)?)\}", token)
            if comp:
                value = comp.group(1)
                if "/" in value:
                    package, activity = value.split("/", 1)
                    return package, activity
                return value, None

        m = re.search(r"mFocusedApp=.*?([A-Za-z0-9_.]+/[A-Za-z0-9_.$]+)", text)
        if m:
            package, activity = m.group(1).split("/", 1)
            return package, activity

        try:
            res = self.run(
                ["shell", "dumpsys", "activity", "activities"],
                timeout=max(20.0, self.default_timeout),
            )
            m = re.search(
                r"mResumedActivity:.*?([A-Za-z0-9_.]+)/([A-Za-z0-9_.$]+)",
                res.stdout,
            )
            if m:
                return m.group(1), m.group(2)
        except AdbError:
            pass

        return None, None

    def device_epoch(self) -> int:
        try:
            res = self.run(["shell", "date", "+%s"], timeout=self.default_timeout)
            return int(res.stdout.strip())
        except Exception:
            return 0

    def tap(self, x: int, y: int) -> None:
        self.run(["shell", "input", "tap", str(int(x)), str(int(y))], timeout=10.0)