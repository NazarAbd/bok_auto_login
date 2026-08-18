import subprocess
import time
import sys


def check_airplane_mode():
    """
    Check if airplane mode is currently enabled on the Android device via ADB.

    Returns:
        bool: True if airplane mode is ON, False if it is OFF
    """
    print("\n" + "=" * 50)
    print("CHECKING AIRPLANE MODE STATUS")
    print("=" * 50)

    # Try the modern cmd connectivity command first (API 30+)
    try:
        result = subprocess.run(
            ["adb", "shell", "cmd", "connectivity", "airplane-mode"],
            capture_output=True,
            text=True,
            timeout=10
        )
        output = result.stdout.strip().lower()
        print(f"    cmd connectivity output: {result.stdout.strip()}")

        if "enabled" in output:
            print("    Airplane mode is ON")
            return True
        elif "disabled" in output:
            print("    Airplane mode is OFF")
            return False
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print("    cmd connectivity not available, falling back...")

    # Fallback: check the global setting value
    try:
        result = subprocess.run(
            ["adb", "shell", "settings", "get", "global", "airplane_mode_on"],
            capture_output=True,
            text=True,
            timeout=10
        )
        status = result.stdout.strip()
        print(f"    airplane_mode_on setting: {status}")

        if status == "1":
            print("    Airplane mode is ON")
            return True
        else:
            print("    Airplane mode is OFF")
            return False

    except subprocess.TimeoutExpired:
        print("    ADB command timed out")
        return False
    except Exception as e:
        print(f"    Error checking airplane mode: {e}")
        return False


def disable_airplane_mode():
    """
    Disable airplane mode on the Android device via ADB.
    Uses modern cmd connectivity command first, falls back to settings+broadcast.

    Returns:
        bool: True if airplane mode is OFF (or successfully disabled),
              False if unable to disable
    """
    print("\n" + "=" * 50)
    print("DISABLING AIRPLANE MODE")
    print("=" * 50)

    # Try the modern cmd connectivity command first (API 30+)
    try:
        result = subprocess.run(
            ["adb", "shell", "cmd", "connectivity", "airplane-mode", "disable"],
            capture_output=True,
            text=True,
            timeout=10
        )
        print(f"    cmd connectivity disable output: {result.stdout.strip()}")
        if result.returncode == 0:
            print("    Waiting 3 seconds for airplane mode to turn off...")
            time.sleep(3)

            # Verify it is off
            verify = subprocess.run(
                ["adb", "shell", "cmd", "connectivity", "airplane-mode"],
                capture_output=True,
                text=True,
                timeout=10
            )
            output = verify.stdout.strip().lower()
            if "disabled" in output or "airplane mode: 0" in output:
                print("    Airplane mode is now OFF")
                return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print("    cmd connectivity not available, falling back...")

    # Fallback: use settings put + broadcast
    try:
        # Set airplane mode setting to 0
        subprocess.run(
            ["adb", "shell", "settings", "put", "global", "airplane_mode_on", "0"],
            capture_output=True, text=True, timeout=10
        )

        # Broadcast the change so the system applies it
        subprocess.run(
            ["adb", "shell", "am", "broadcast",
             "-a", "android.intent.action.AIRPLANE_MODE",
             "--ez", "state", "false"],
            capture_output=True, text=True, timeout=10
        )

        print("    Waiting 3 seconds for airplane mode to turn off...")
        time.sleep(3)

        # Verify it is off
        result = subprocess.run(
            ["adb", "shell", "settings", "get", "global", "airplane_mode_on"],
            capture_output=True, text=True, timeout=10
        )
        status = result.stdout.strip()
        print(f"    Verification - airplane_mode_on: {status}")

        if status == "0":
            print("    Airplane mode is now OFF")
            return True
        else:
            print("    Failed to disable airplane mode")
            return False

    except subprocess.TimeoutExpired:
        print("    ADB command timed out")
        return False
    except Exception as e:
        print(f"    Error disabling airplane mode: {e}")
        return False


def check_and_disable_airplane_mode():
    """
    Check if airplane mode is ON and disable it if needed.
    This is the main function to call at the beginning of your script.

    Returns:
        bool: True if airplane mode is OFF (or successfully disabled),
              False if unable to disable
    """
    if not check_airplane_mode():
        print("\nAirplane mode is already OFF - no action needed")
        return True

    print("\nAirplane mode is ON - attempting to disable...")

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        print(f"\n  Attempt {attempt}/{max_attempts}")
        if disable_airplane_mode():
            return True
        print(f"    Retrying in 3 seconds...")
        time.sleep(3)

    print("\n" + "=" * 50)
    print("FAILED TO DISABLE AIRPLANE MODE")
    print("=" * 50)
    return False


# ============================================================================
# MAIN EXECUTION
# ============================================================================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("AIRPLANE MODE CHECKER")
    print("=" * 60)

    result = check_and_disable_airplane_mode()
    if result:
        print("\nAirplane mode is OFF - ready to proceed")
    else:
        print("\nCould not disable airplane mode")
        sys.exit(1)
