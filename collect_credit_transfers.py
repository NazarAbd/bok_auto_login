import re
import sys
import time
import xml.etree.ElementTree as ET

from app.adb import AdbClient, AdbError, find_adb
from app.config import Config


def _to_text(value):
    """
    Convert ADB output to text safely.
    """
    if isinstance(value, bytes):
        return value.decode("utf-8", "ignore")
    return str(value or "")


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


def _build_parent_map(xml_root):
    """
    Build a map of child node -> parent node for the UI hierarchy.
    """
    parent_map = {}
    for node in xml_root.iter("node"):
        for child in node:
            parent_map[child] = node
    return parent_map


def extract_transaction_rows(xml_root):
    """
    Extract each transaction row from the statement list.

    Each row is identified by its miniBal node (amount with + or - sign).
    Returns a list of dicts: {"bal", "date", "ref", "y"} where "y" is the
    vertical center of the miniBal node, used to track how far a row has
    moved up the screen during scrolling.
    """
    rows = []
    if xml_root is None:
        return rows

    parent_map = _build_parent_map(xml_root)

    for node in xml_root.iter("node"):
        rid = node.get("resource-id", "")
        if not rid.endswith("miniBal"):
            continue

        bal = node.get("text", "").strip()

        _, y = get_center_from_bounds(node.get("bounds", ""))
        if y is None:
            y = 0

        # Climb up until we find an ancestor that also contains miniDate and miniDecr.
        ancestor = node
        while ancestor is not None:
            desc_ids = {n.get("resource-id", "") for n in ancestor.iter("node")}
            has_date = any(x.endswith("miniDate") for x in desc_ids)
            has_decr = any(x.endswith("miniDecr") for x in desc_ids)
            if has_date and has_decr:
                break
            ancestor = parent_map.get(ancestor)

        date_text = ""
        ref_text = ""
        if ancestor is not None:
            for n in ancestor.iter("node"):
                nid = n.get("resource-id", "")
                if nid.endswith("miniDate") and not date_text:
                    date_text = n.get("text", "").strip()
                elif nid.endswith("miniDecr") and not ref_text:
                    ref_text = n.get("text", "").strip()

        rows.append({"bal": bal, "date": date_text, "ref": ref_text, "y": y})

    return rows


def swipe_statement_up_by(adb, distance_px):
    """
    Swipe the statement list up by the given number of pixels.

    The content scrolls up by approximately distance_px so that the
    bottom-most row (anchor) reaches the top of the screen in one motion.
    A controlled swipe (not a fling) avoids skipping rows.
    """
    width, height = get_screen_size(adb)

    if not height:
        print("WARNING: Could not get screen size for swipe. Using fallback coordinates.")
        width, height = 1080, 2400

    center_x = width // 2
    start_y = int(height * 0.85)
    end_y = start_y - int(distance_px)

    if end_y < int(height * 0.05):
        end_y = int(height * 0.05)

    duration_ms = 800

    adb.run(
        [
            "shell",
            "input",
            "swipe",
            str(center_x),
            str(start_y),
            str(center_x),
            str(end_y),
            str(duration_ms),
        ],
        timeout=5,
    )


def _capture_rows(rows, seen_rows, seen_credits, credits, max_credits):
    """
    Add any previously unseen rows to the tracking sets and log new credits.

    Returns (new_row_appeared, done):
        new_row_appeared: True if at least one brand-new row was seen.
        done: True if max_credits was reached.
    """
    new_row_appeared = False
    for row in rows:
        signature = (row["date"], row["bal"], row["ref"])

        if signature in seen_rows:
            continue

        seen_rows.add(signature)
        new_row_appeared = True

        if row["bal"].startswith("+"):
            if signature not in seen_credits:
                seen_credits.add(signature)
                credits.append(row)
                print(f"Credit transfer: {row['bal']} | {row['date']} | {row['ref']}")

            if len(credits) >= max_credits:
                return True, True

    return new_row_appeared, False


def collect_credit_transfers(adb, max_credits=15):
    """
    Scroll through the statement list and collect credit transfer details.

    A credit is a row whose miniBal text starts with '+'.

    Strategy (anchor-based scrolling):
    - Grab the bottom-most transfer row on screen as the anchor (internal only).
    - Swipe the content up in one motion until that anchor row reaches the top
      of the screen.
    - Capture the new screen and log the credit rows.
    - Grab a new anchor and repeat until the bottom of the list is reached or
      max_credits are collected.
    """
    print("Collecting credit transfers...")

    credits = []
    seen_rows = set()
    seen_credits = set()
    scrolls = 0
    max_scrolls = 80
    settle_seconds = 0.1

    # Wait briefly for the statement list to load so we don't scroll past rows.
    start = time.time()
    while time.time() - start < 15.0:
        xml_text = dump_ui(adb)
        xml_root = parse_xml(xml_text)
        if xml_root is not None and any(
            n.get("resource-id", "").endswith("miniBal") for n in xml_root.iter("node")
        ):
            break
        time.sleep(1.0)

    _, screen_h = get_screen_size(adb)
    if not screen_h:
        screen_h = 2400
    top_threshold = int(screen_h * 0.10)

    while len(credits) < max_credits and scrolls < max_scrolls:
        xml_text = dump_ui(adb)
        xml_root = parse_xml(xml_text)
        rows = extract_transaction_rows(xml_root)

        new_row_appeared, done = _capture_rows(rows, seen_rows, seen_credits, credits, max_credits)
        if done:
            break

        # A swipe that revealed nothing new means we hit the bottom of the list.
        if scrolls > 0 and not new_row_appeared:
            break

        # Nothing on screen at all.
        if not rows:
            break

        # Grab the last (bottom-most) transfer node as the anchor (internal only).
        anchor = max(rows, key=lambda r: r["y"])
        anchor_y = anchor["y"]

        # Swipe the content up by a portion of the distance to the top so
        # each swipe is smaller and no rows are skipped.
        swipe_distance = (anchor_y - top_threshold) // 2
        if swipe_distance < int(screen_h * 0.10):
            swipe_distance = int(screen_h * 0.10)

        try:
            swipe_statement_up_by(adb, swipe_distance)
        except AdbError as exc:
            print(f"WARNING: Swipe failed while scrolling statement: {exc}")
            break

        time.sleep(settle_seconds)
        scrolls += 1

    if len(credits) < max_credits:
        print(f"Found {len(credits)} credit transfers in the last 7 days.")
    else:
        print(f"Collected {len(credits)} credit transfers (limit reached).")

    return credits


# ============================================================================
# MAIN EXECUTION
# ============================================================================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("CREDIT TRANSFERS COLLECTOR")
    print("=" * 60)

    config = Config("config.json")
    adb_path = find_adb(config.get("adb_path", ""))

    adb = AdbClient(
        adb_path,
        default_timeout=float(config.get("timeouts.adb", 10)),
    )

    devices = adb.devices()
    authorized = [serial for serial, state in devices.items() if state == "device"]

    if not authorized:
        print("\nNo authorized Android device found.")
        print("Please connect your Redmi Note 13 Pro and enable USB debugging")
        sys.exit(1)

    adb.serial = authorized[0]
    print(f"Using device: {adb.serial}")

    collect_credit_transfers(adb)

    print("\n" + "=" * 60)
    print("CREDIT TRANSFERS COLLECTION COMPLETED")
    print("=" * 60)