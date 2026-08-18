import subprocess
import time
import sys

def check_and_enable_mobile_data():
    """
    Check if mobile data is connected on the Android device via ADB.
    If mobile data is disabled/disconnected, enable it automatically.
    
    Returns:
        bool: True if mobile data is connected (or successfully enabled),
              False if unable to establish connection
    """
    
    print("=" * 50)
    print("CHECKING MOBILE DATA CONNECTION")
    print("=" * 50)
    
    # First, check if mobile data setting is enabled
    print("\n[1/4] Checking mobile data setting...")
    try:
        # Check the global mobile_data setting (1 = enabled, 0 = disabled)
        result = subprocess.run(
            ["adb", "shell", "settings", "get", "global", "mobile_data"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        mobile_data_setting = result.stdout.strip()
        print(f"    Mobile data setting: {mobile_data_setting}")
        
        # If mobile data is disabled, enable it
        if mobile_data_setting == "0":
            print("    Mobile data is DISABLED. Enabling now...")
            enable_result = subprocess.run(
                ["adb", "shell", "svc", "data", "enable"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if enable_result.returncode == 0:
                print("    ✓ Mobile data enabled successfully")
                # Wait for connection to establish
                print("    Waiting 3 seconds for connection...")
                time.sleep(3)
            else:
                print(f"    ✗ Failed to enable mobile data: {enable_result.stderr}")
                return False
        else:
            print("    ✓ Mobile data setting is enabled")
            
    except subprocess.TimeoutExpired:
        print("    ✗ ADB command timed out")
        return False
    except Exception as e:
        print(f"    ✗ Error checking mobile data setting: {e}")
        return False
    
    # Check actual data connection state
    print("\n[2/4] Checking data connection state...")
    try:
        # Check telephony registry for connection state
        # mDataConnectionState: 0=disconnected, 1=connecting, 2=connected
        result = subprocess.run(
            ["adb", "shell", "dumpsys", "telephony.registry"],
            capture_output=True,
            text=True,
            timeout=10,
            shell=False
        )
        
        # Parse the output to find data connection states
        output_lines = result.stdout.split('\n')
        connection_states = []
        
        for line in output_lines:
            if "mDataConnectionState" in line:
                state = line.strip()
                connection_states.append(state)
                print(f"    {state}")
        
        # Check if any SIM has connected data (state = 2)
        has_connection = any("mDataConnectionState=2" in state for state in connection_states)
        
        if has_connection:
            print("    ✓ Mobile data is CONNECTED")
            print("\n" + "=" * 50)
            print("MOBILE DATA CONNECTION: ACTIVE")
            print("=" * 50)
            return True
        else:
            print("    ✗ No active data connection detected")
            
    except subprocess.TimeoutExpired:
        print("    ✗ ADB command timed out")
    except Exception as e:
        print(f"    ✗ Error checking connection state: {e}")
    
    # If not connected, try to force enable data
    print("\n[3/4] Attempting to force enable data connection...")
    try:
        # Try toggling data off and on to reset connection
        print("    Resetting data connection...")
        subprocess.run(["adb", "shell", "svc", "data", "disable"], 
                      capture_output=True, timeout=10)
        time.sleep(2)
        subprocess.run(["adb", "shell", "svc", "data", "enable"], 
                      capture_output=True, timeout=10)
        
        print("    Waiting 5 seconds for connection to establish...")
        time.sleep(5)
        
        # Re-check connection state
        result = subprocess.run(
            ["adb", "shell", "dumpsys", "telephony.registry"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        output_lines = result.stdout.split('\n')
        connection_states = []
        
        for line in output_lines:
            if "mDataConnectionState" in line:
                state = line.strip()
                connection_states.append(state)
                print(f"    Re-check: {state}")
        
        has_connection = any("mDataConnectionState=2" in state for state in connection_states)
        
        if has_connection:
            print("    ✓ Mobile data is now CONNECTED")
            print("\n" + "=" * 50)
            print("MOBILE DATA CONNECTION: ACTIVE")
            print("=" * 50)
            return True
        else:
            print("    ✗ Still no data connection")
            
    except Exception as e:
        print(f"    ✗ Error during connection reset: {e}")
    
    # Final verification
    print("\n[4/4] Final verification...")
    try:
        # Check if we can ping through the device (optional)
        result = subprocess.run(
            ["adb", "shell", "ping", "-c", "1", "-W", "3", "8.8.8.8"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if "1 received" in result.stdout or "1 packets received" in result.stdout:
            print("    ✓ Internet connectivity confirmed via ping")
            print("\n" + "=" * 50)
            print("MOBILE DATA CONNECTION: ACTIVE")
            print("=" * 50)
            return True
        else:
            print("    ✗ Cannot ping external IP - no internet connection")
            
    except Exception as e:
        print(f"    ✗ Error during ping test: {e}")
    
    print("\n" + "=" * 50)
    print("MOBILE DATA CONNECTION: FAILED TO ESTABLISH")
    print("=" * 50)
    return False


def check_device_connection():
    """
    Check if ADB device is connected and accessible.
    
    Returns:
        bool: True if device is connected, False otherwise
    """
    print("\nCHECKING ADB DEVICE CONNECTION")
    print("-" * 30)
    
    try:
        result = subprocess.run(
            ["adb", "devices"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        # Parse the output
        lines = result.stdout.strip().split('\n')
        devices = []
        
        for line in lines[1:]:  # Skip the first line "List of devices attached"
            if line.strip() and "device" in line:
                devices.append(line.split('\t')[0])
        
        if devices:
            print(f"    ✓ Connected devices: {', '.join(devices)}")
            return True
        else:
            print("    ✗ No devices connected")
            print("    Please connect your device and enable USB debugging")
            return False
            
    except Exception as e:
        print(f"    ✗ Error checking ADB connection: {e}")
        return False

# ============================================================================
# MAIN EXECUTION - Call the function at the beginning of your script
# ============================================================================

if __name__ == "__main__":
    """
    This is the main entry point of the script.
    The mobile data check is called here at the beginning.
    """
    
    print("\n" + "=" * 60)
    print("ANDROID AUTOMATION SCRIPT STARTING")
    print("=" * 60)
    
    # Step 1: Check if device is connected via ADB
    if check_device_connection():
        print("\n✓ Device connected successfully")
        
        # Step 2: Check and enable mobile data (THIS IS THE MAIN FUNCTION CALL)
        # This function is called at the beginning of the script as required
        mobile_data_status = check_and_enable_mobile_data()
        
        if mobile_data_status:
            print("\n✓ Mobile data is ready - Proceeding with script execution")
            # Continue with your main script logic here
            # ...
            # ...
        else:
            print("\n✗ Mobile data is not available - Script cannot proceed")
            print("Please check your device's mobile data settings manually")
            sys.exit(1)  # Exit with error code
    else:
        print("\n✗ No device connected - Script cannot proceed")
        print("Please connect your Redmi Note 13 Pro and enable USB debugging")
        sys.exit(1)  # Exit with error code
    
    print("\n" + "=" * 60)
    print("SCRIPT EXECUTION COMPLETED")
    print("=" * 60)
    