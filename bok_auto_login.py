import os
import re
import sys
import time
import xml.etree.ElementTree as ET


from app.adb import AdbClient, AdbError, find_adb
from app.config import Config

# --- CONFIGURATION ---
PASSWORD_FILE = "password.txt"
APP_PACKAGE = "com.mode.bok.ui"

# IMPORTANT: Do not try to locate the password field before this time.
APP_OPEN_WAIT_SECONDS = 5.0

# Set to False if the language switcher taps the wrong thing.
# Note: ADB input text usually types English correctly even if the visual keyboard is another language.
FORCE_SWITCH_KEYBOARD_LANGUAGE = True

# check of device internet connection
from mobile_data_checker import check_and_enable_mobile_data, check_device_connection
from airplane_mode_checker import check_and_disable_airplane_mode

# At the beginning of your script

if check_device_connection():
    if not check_and_disable_airplane_mode():
        print("Failed to disable airplane mode")
        exit(1)
    if check_and_enable_mobile_data():
        print("Mobile data is ready!")
        # Your main script code here
    else:
        print("Failed to establish mobile data connection")
        exit(1)




def escape_adb_text(text: str) -> str:
    """
    Escapes special characters for:
        adb shell input text

    Space becomes %s.
    Characters like & < > | ; ( ) * ~ " ' $ \ need backslashes.
    """
    escaped = []

    for char in text:
        if char == " ":
            escaped.append("%s")
        elif char in "&<>|;()*~\"'$\\":
            escaped.append("\\" + char)
        else:
            escaped.append(char)

    return "".join(escaped)


def parse_xml(xml_text: str):
    """
    Safely parse UIAutomator XML.
    """
    if not xml_text:
        return None

    xml_text = xml_text.lstrip("\ufeff").strip()
    xml_text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", xml_text)

    try:
        return ET.fromstring(xml_text)
    except ET.ParseError:
        return None


def dump_ui(adb: AdbClient) -> str:
    """
    Dump current UI hierarchy and return XML text.
    """
    remote_path = "/sdcard/window_dump.xml"

    adb.run(["shell", "uiautomator", "dump", remote_path], timeout=15)
    res = adb.run(["shell", "cat", remote_path], timeout=15)

    return res.stdout


def find_password_field(xml_text: str):
    """
    Find the password EditText field.

    Preferred detection:
      1. password="true"
      2. EditText with resource-id containing password/pass
    """
    root = parse_xml(xml_text)
    if root is None:
        return None, None

    best_score = 0
    best_node = None

    for node in root.iter("node"):
        attrs = node.attrib

        is_password_attr = attrs.get("password", "false").lower() == "true"
        resource_id = attrs.get("resource-id", "").lower()
        class_name = attrs.get("class", "").lower()

        is_edit_text = class_name.endswith("edittext")
        has_password_in_id = "password" in resource_id or "pass" in resource_id

        score = 0

        if is_password_attr:
            score += 100

        if is_edit_text and has_password_in_id:
            score += 80

        if is_edit_text:
            score += 20

        if score > best_score:
            best_score = score
            best_node = node

    return best_node, root


def get_center_from_bounds(bounds: str):
    """
    Convert Android bounds string:
        [x1,y1][x2,y2]
    into center coordinates.
    """
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
    if not m:
        return None, None

    x1, y1, x2, y2 = map(int, m.groups())
    center_x = (x1 + x2) // 2
    center_y = (y1 + y2) // 2

    return center_x, center_y


def check_and_switch_keyboard_language(adb: AdbClient, xml_root):
    """
    Try to detect whether the on-screen keyboard is English.
    If not, try to tap the keyboard language switch button.

    WARNING:
    Keyboard UI differs between Gboard, SwiftKey, Facemoji, Xiaomi keyboard, etc.
    This is best-effort.

    Also note:
    'adb shell input text' injects characters at OS level, so it usually types
    the exact password correctly even if the visual keyboard language is not English.
    """
    if xml_root is None:
        print("WARNING: Could not read keyboard UI. Skipping language check.")
        return

    if not FORCE_SWITCH_KEYBOARD_LANGUAGE:
        print("Keyboard language switching is disabled in script settings.")
        return

    print("Checking keyboard language...")

    is_english = False
    spacebar_text = ""
    switcher_node = None

    keyboard_package_keywords = (
        "inputmethod",
        "keyboard",
        "latin",
        "gboard",
        "swiftkey",
        "facemoji",
        "miui.inputmethod",
    )

    for node in xml_root.iter("node"):
        attrs = node.attrib

        text = attrs.get("text", "").lower()
        desc = attrs.get("content-desc", "").lower()
        package = attrs.get("package", "").lower()

        # Detect English indicator, usually on spacebar.
        if "english" in text or text == "en" or "english" in desc:
            is_english = True
            spacebar_text = attrs.get("text", "")

        # Only trust language-switch buttons that appear to belong to the keyboard package.
        is_keyboard_package = any(keyword in package for keyword in keyboard_package_keywords)

        if is_keyboard_package:
            if (
                "switch language" in desc
                or "change language" in desc
                or "language switch" in desc
                or "language" in desc
                or "globe" in desc
            ):
                class_name = attrs.get("class", "").lower()
                if class_name.endswith("imageview") or "button" in class_name:
                    switcher_node = node

    if is_english:
        print(f"Keyboard is already English. Indicator: '{spacebar_text}'")
        return

    if switcher_node is not None:
        bounds = switcher_node.attrib.get("bounds", "")
        x, y = get_center_from_bounds(bounds)

        if x is not None and y is not None:
            print("Keyboard does not appear to be English. Tapping language switcher...")
            adb.run(["shell", "input", "tap", str(x), str(y)], timeout=5)
            time.sleep(1.5)
            return

    print("WARNING: Could not confidently find keyboard language switch button.")
    print("Continuing anyway. ADB input text usually types English correctly regardless of visual keyboard language.")


def find_login_button(xml_root):
    """
    Find the Login button with high confidence.

    If no strong match is found, the script must abort.
    Do NOT guess, because wrong taps or double taps may ban the account.
    """
    if xml_root is None:
        return None

    # English and Arabic login keywords, in case the app is localized.
    keywords = [
        "login",
        "log in",
        "sign in",
        "signin",
        "submit",
        "continue",
        "دخول",
        "تسجيل الدخول",
        "تسجيل",
    ]

    best_score = 0
    best_node = None

    for node in xml_root.iter("node"):
        attrs = node.attrib

        text = attrs.get("text", "").lower()
        resource_id = attrs.get("resource-id", "").lower()
        content_desc = attrs.get("content-desc", "").lower()
        class_name = attrs.get("class", "").lower()

        score = 0

        if any(keyword in text for keyword in keywords):
            score += 100

        if any(keyword in content_desc for keyword in keywords):
            score += 90

        if any(keyword in resource_id for keyword in keywords):
            score += 80

        if "button" in class_name and score > 0:
            score += 20

        if score > best_score:
            best_score = score
            best_node = node

    # Require high confidence before tapping.
    if best_score >= 80:
        return best_node

    return None


def detect_and_close_welcome_message(adb: AdbClient):
    """
    Wait until the login activity changes to a different activity,
    then detect if there's a popup message and press the confirmation button.
    """
    print("Waiting for activity to change after login...")
    
    # We wait until the password field and login button disappear, 
    # which reliably indicates the activity has transitioned to the dashboard/home screen.
    max_wait_seconds = 15
    start_time = time.time()
    activity_changed = False
    
    while time.time() - start_time < max_wait_seconds:
        try:
            xml_text = dump_ui(adb)
            xml_root = parse_xml(xml_text)
            
            password_node, _ = find_password_field(xml_text)
            login_node = find_login_button(xml_root)
            
            if password_node is None and login_node is None:
                print("Login screen elements are gone. Activity has likely changed.")
                activity_changed = True
                break
                
        except AdbError as exc:
            print(f"WARNING: UI dump failed while waiting for activity change: {exc}")
            
        time.sleep(1.0)
        
    if not activity_changed:
        print("WARNING: Timeout waiting for activity to change. Proceeding to check for popup anyway.")
        
    # Give a brief moment for any popup to render after activity change
    time.sleep(2.0)
    
    print("Checking for welcome popup message...")
    try:
        xml_text = dump_ui(adb)
        xml_root = parse_xml(xml_text)
    except AdbError as exc:
        print(f"WARNING: Could not dump UI to check for welcome popup: {exc}")
        return
        
    if xml_root is None:
        print("WARNING: Could not parse UI XML for welcome popup.")
        return
        
    # Keywords to look for in the popup button
    keywords = ["ok", "موافق", "حسنا", "تم"]
    
    best_node = None
    best_score = 0
    
    for node in xml_root.iter("node"):
        attrs = node.attrib
        
        # Normalize text and content-desc (strip whitespace and lowercase English)
        text = attrs.get("text", "").strip().lower()
        content_desc = attrs.get("content-desc", "").strip().lower()
        class_name = attrs.get("class", "").lower()
        
        score = 0
        
        # Exact match for the keywords is safer to avoid clicking random things
        if text in keywords or content_desc in keywords:
            score += 100
            
        # Bonus if it's explicitly a button class
        if "button" in class_name and score > 0:
            score += 20
            
        if score > best_score:
            best_score = score
            best_node = node
            
    if best_node is not None and best_score >= 100:
        bounds = best_node.attrib.get("bounds", "")
        x, y = get_center_from_bounds(bounds)
        
        if x is not None and y is not None:
            original_text = best_node.attrib.get("text", "") or best_node.attrib.get("content-desc", "")
            print(f"Welcome popup detected! Found button with text '{original_text}'. Tapping at {x},{y}...")
            try:
                adb.run(["shell", "input", "tap", str(x), str(y)], timeout=5)
                print("Welcome popup closed successfully.")
            except AdbError as exc:
                print(f"WARNING: Failed to tap welcome button: {exc}")
        else:
            print("WARNING: Found welcome button but could not calculate its center coordinates.")
    else:
        print("No welcome popup detected.")


def read_password() -> str:
    if not os.path.exists(PASSWORD_FILE):
        with open(PASSWORD_FILE, "w", encoding="utf-8") as f:
            f.write("ChangeThisToYourPassword")

        print(f"ERROR: {PASSWORD_FILE} was missing.")
        print("I created it. Put your real password inside it, then run again.")
        sys.exit(1)

    with open(PASSWORD_FILE, "r", encoding="utf-8") as f:
        password = f.read().strip()

    if not password:
        print(f"ERROR: {PASSWORD_FILE} is empty.")
        sys.exit(1)

    if password == "ChangeThisToYourPassword":
        print(f"ERROR: Please put your real password into {PASSWORD_FILE}.")
        sys.exit(1)

    return password
def _to_text(value):
    """
    Convert ADB output to text safely.
    """
    if isinstance(value, bytes):
        return value.decode("utf-8", "ignore")
    return str(value or "")


def get_current_activity(adb):
    """
    Get current foreground activity using adb dumpsys.

    Returns something like:
        com.mode.bok.ui/.MainActivity

    Returns None if it cannot detect it.
    """
    commands_and_patterns = [
        (
            ["shell", "dumpsys", "activity", "activities"],
            r"(?:mResumedActivity|topResumedActivity|ResumedActivity|mFocusedActivity)[^\r\n]*?([A-Za-z0-9_.$]+/[A-Za-z0-9_.$]+)",
        ),
        (
            ["shell", "dumpsys", "window", "windows"],
            r"mCurrentFocus=[^\r\n]*?([A-Za-z0-9_.$]+/[A-Za-z0-9_.$]+)",
        ),
    ]

    for cmd, pattern in commands_and_patterns:
        try:
            res = adb.run(cmd, timeout=15)
            out = _to_text(res.stdout)

            match = re.search(pattern, out, re.IGNORECASE)
            if match:
                return match.group(1)

        except AdbError:
            continue

    return None


def wait_for_activity_change(adb, old_activity, timeout=25.0, poll_interval=0.5):
    """
    Wait until current activity becomes different from old_activity.
    """
    start = time.time()
    current = old_activity

    while time.time() - start < timeout:
        current = get_current_activity(adb)

        if current and old_activity and current != old_activity:
            return current

        time.sleep(poll_interval)

    return current


def normalize_text(value):
    """
    Normalize UI text:
    - remove extra spaces
    - trim
    - lower case English

    This helps match:
        "تفاصيل  الحساب"
        "تفاصيل الحساب"
    as the same text.
    """
    value = _to_text(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value.lower()


def get_screen_size(adb):
    """
    Get device screen size.

    Returns:
        width, height

    If failed:
        None, None
    """
    try:
        res = adb.run(["shell", "wm", "size"], timeout=5)
        out = _to_text(res.stdout)

        match = re.search(r"(\d+)x(\d+)", out)
        if match:
            return int(match.group(1)), int(match.group(2))

    except AdbError:
        pass

    return None, None


def find_account_summary_button(xml_root, screen_size=None):
    """
    Find button/node with text:
        English: Account Summary
        Arabic:  تفاصيل الحساب

    It searches anywhere on screen, but gives higher score to:
    - top area
    - English button on right side
    - Arabic button on left side
    - clickable nodes
    - button-like nodes
    """
    if xml_root is None:
        return None

    english_target = normalize_text("Account Summary")
    arabic_target = normalize_text("تفاصيل الحساب")

    # Extra duplicate for the double-space version you showed.
    arabic_target_double = normalize_text("تفاصيل  الحساب")

    exact_targets = {
        english_target,
        arabic_target,
        arabic_target_double,
    }

    screen_w, screen_h = screen_size or (None, None)
    candidates = []

    for node in xml_root.iter("node"):
        attrs = node.attrib

        text = normalize_text(attrs.get("text", ""))
        desc = normalize_text(attrs.get("content-desc", ""))
        resource_id = normalize_text(attrs.get("resource-id", "")).replace(" ", "")
        class_name = attrs.get("class", "").lower()

        clickable = attrs.get("clickable", "false").lower() == "true"
        enabled = attrs.get("enabled", "false").lower() == "true"

        bounds = attrs.get("bounds", "")
        x, y = get_center_from_bounds(bounds)

        if x is None or y is None:
            continue

        score = 0
        matched = None

        # Exact match is strongest.
        if text in exact_targets:
            score = 100
            matched = text

        elif desc in exact_targets:
            score = 100
            matched = desc

        else:
            # Partial match fallback.
            for target in exact_targets:
                if target and (target in text or target in desc):
                    score = 85
                    matched = text if target in text else desc
                    break

        if score < 85:
            continue

        # Helpful resource-id hints.
        if "accountsummary" in resource_id or "account_summary" in resource_id:
            score += 30

        # Clickable/enabled/button-like nodes are better.
        if clickable:
            score += 25

        if enabled:
            score += 10

        if "button" in class_name:
            score += 15

        # Prefer top area, because you said it is usually top right/top left.
        if screen_h is not None and y <= int(screen_h * 0.25):
            score += 15

        # Prefer expected side:
        # English usually top right.
        # Arabic usually top left.
        if screen_w is not None:
            if matched == english_target and x >= int(screen_w * 0.60):
                score += 10

            if matched in (arabic_target, arabic_target_double) and x <= int(screen_w * 0.40):
                score += 10

        candidates.append((score, y, x, matched, node))

    if not candidates:
        return None

    # Highest score first.
    # If same score, choose top-most button.
    candidates.sort(key=lambda item: (-item[0], item[1]))

    return candidates[0][4]


def press_account_summary_and_wait(adb, find_timeout=25.0, activity_timeout=25.0):
    """
    Wait for Account Summary / تفاصيل الحساب button,
    tap it once,
    then wait until activity changes.
    """
    print("Looking for Account Summary button...")

    screen_size = get_screen_size(adb)
    start = time.time()
    button_node = None

    # Wait until the button appears.
    while time.time() - start < find_timeout:
        try:
            xml_text = dump_ui(adb)
            xml_root = parse_xml(xml_text)

            button_node = find_account_summary_button(xml_root, screen_size)

            if button_node is not None:
                break

        except AdbError as exc:
            print(f"WARNING: UI dump failed while looking for Account Summary: {exc}")

        time.sleep(1.0)

    if button_node is None:
        print("WARNING: Account Summary button was not found.")
        return False

    # Get current activity BEFORE tapping.
    old_activity = get_current_activity(adb)
    print(f"Current activity before Account Summary: {old_activity}")

    bounds = button_node.attrib.get("bounds", "")
    x, y = get_center_from_bounds(bounds)

    if x is None or y is None:
        print("WARNING: Found Account Summary button but could not read its bounds.")
        return False

    visible_text = button_node.attrib.get("text", "") or button_node.attrib.get("content-desc", "")
    resource_id = button_node.attrib.get("resource-id", "")

    print(f"Found Account Summary button: text='{visible_text}' id='{resource_id}' bounds='{bounds}'")
    print(f"Tapping Account Summary ONCE at {x},{y}...")

    try:
        adb.run(["shell", "input", "tap", str(x), str(y)], timeout=5)
    except AdbError as exc:
        print(f"WARNING: Failed to tap Account Summary button: {exc}")
        return False

    # If we know the old activity, wait for it to change.
    if old_activity:
        new_activity = wait_for_activity_change(
            adb,
            old_activity,
            timeout=activity_timeout,
            poll_interval=0.5,
        )

        if new_activity and new_activity != old_activity:
            print(f"Activity changed: {old_activity} -> {new_activity}")
            return True

        print(f"WARNING: Activity did not change. Current activity: {new_activity}")
        return False

    # Fallback if activity detection failed:
    # wait until the Account Summary button disappears.
    print("WARNING: Could not detect old activity. Waiting for button to disappear instead.")

    start = time.time()

    while time.time() - start < activity_timeout:
        try:
            xml_text = dump_ui(adb)
            xml_root = parse_xml(xml_text)

            if find_account_summary_button(xml_root, screen_size) is None:
                print("Account Summary button disappeared.")
                return True

        except AdbError:
            pass

        time.sleep(1.0)

    print("WARNING: Account Summary button did not disappear.")
    return False

def run_auto_login():
    print("Reading password file...")
    password = read_password()

    print("Loading configuration and finding ADB...")
    config = Config("config.json")
    adb_path = find_adb(config.get("adb_path", ""))

    adb = AdbClient(
        adb_path,
        default_timeout=float(config.get("timeouts.adb", 10)),
    )

    devices = adb.devices()
    authorized = [serial for serial, state in devices.items() if state == "device"]

    if not authorized:
        print("ERROR: No authorized Android device found.")
        sys.exit(1)

    adb.serial = authorized[0]
    print(f"Using device: {adb.serial}")

    # 1. Launch Bok app.
    print("Launching Bok app...")
    try:
        adb.run(
            [
                "shell",
                "monkey",
                "-p",
                APP_PACKAGE,
                "-c",
                "android.intent.category.LAUNCHER",
                "1",
            ],
            timeout=10,
        )
    except AdbError as exc:
        print(f"FATAL: Failed to launch Bok app: {exc}")
        sys.exit(1)

    # 2. IMPORTANT: Wait exactly 5 seconds before looking for the password field.
    print(f"Waiting exactly {APP_OPEN_WAIT_SECONDS} seconds for app to fully open...")
    time.sleep(APP_OPEN_WAIT_SECONDS)

    # 3. Now it is safe to search for the password field.
    print("Searching for password field...")

    password_node = None
    xml_root = None

    # Retry only AFTER the required 5-second wait.
    for attempt in range(3):
        try:
            xml_text = dump_ui(adb)
            password_node, xml_root = find_password_field(xml_text)

            if password_node is not None:
                break

        except AdbError as exc:
            print(f"WARNING: UI dump attempt {attempt + 1} failed: {exc}")

        if attempt < 2:
            print("Password field not found yet. Waiting 1 second and retrying...")
            time.sleep(1.0)

    if password_node is None:
        print("FATAL: Could not find the password field.")
        print("Aborting to avoid tapping the wrong place.")
        sys.exit(1)

    bounds = password_node.attrib.get("bounds", "")
    x, y = get_center_from_bounds(bounds)

    if x is None or y is None:
        print("FATAL: Found password field but could not read its bounds.")
        sys.exit(1)

    print(f"Found password field. Tapping at {x},{y}...")
    adb.run(["shell", "input", "tap", str(x), str(y)], timeout=5)

    # Wait for keyboard to appear.
    time.sleep(1.5)

    # 4. Check keyboard language.
    try:
        keyboard_xml_text = dump_ui(adb)
        keyboard_xml_root = parse_xml(keyboard_xml_text)
    except AdbError as exc:
        print(f"WARNING: Could not dump keyboard UI: {exc}")
        keyboard_xml_root = None

    check_and_switch_keyboard_language(adb, keyboard_xml_root)

    # 5. Type exact password.
    print("Typing password...")
    escaped_password = escape_adb_text(password)

    try:
        adb.run(["shell", "input", "text", escaped_password], timeout=10)
    except AdbError as exc:
        print(f"FATAL: Failed to type password: {exc}")
        sys.exit(1)

    # Small pause before pressing Login.
    time.sleep(1.0)

    # 6. Find Login button.
    print("Searching for Login button...")

    try:
        login_xml_text = dump_ui(adb)
        login_xml_root = parse_xml(login_xml_text)
    except AdbError as exc:
        print(f"FATAL: Could not dump UI before Login: {exc}")
        sys.exit(1)

    login_node = find_login_button(login_xml_root)

    if login_node is None:
        print("FATAL: Could not confidently find the Login button.")
        print("Aborting immediately to prevent wrong tap or double tap.")
        sys.exit(1)

    bounds = login_node.attrib.get("bounds", "")
    x, y = get_center_from_bounds(bounds)

    if x is None or y is None:
        print("FATAL: Found Login button but could not read its bounds.")
        sys.exit(1)

    login_text = login_node.attrib.get("text", "")
    login_id = login_node.attrib.get("resource-id", "")

    print(f"Found Login button. text='{login_text}' id='{login_id}'")
    print(f"Tapping Login EXACTLY ONCE at {x},{y}...")

    try:
        adb.run(["shell", "input", "tap", str(x), str(y)], timeout=5)
    except AdbError as exc:
        print(f"FATAL: Login tap failed: {exc}")
        sys.exit(1)

    # 7. Handle Welcome Popup instead of immediate termination.
    # The safeguard against double-tapping LOGIN is still intact because we don't tap login again.
    print("Login tap sent. Waiting for next screen and checking for welcome popup...")
    detect_and_close_welcome_message(adb)

      # Wait for Account Summary button, tap it once, then wait for activity change.
    press_account_summary_and_wait(adb)

    print("Automation finished successfully.")
    sys.exit(0)


if __name__ == "__main__":
    run_auto_login()