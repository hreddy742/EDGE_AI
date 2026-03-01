"""Camera Zone Setup page.

Lets an operator:
  1. Select a camera and fetch a live frame from the running pipeline.
  2. Draw polygon zones on the frame using an interactive canvas.
  3. Review all defined zones overlaid on the image.
  4. Save zones — written to a JSON file on disk AND posted to the running API.

Requires: pip install streamlit-drawable-canvas
"""
from __future__ import annotations

import json
import os
from io import BytesIO
from pathlib import Path

import requests
import streamlit as st
from PIL import Image, ImageDraw

try:
    from streamlit_drawable_canvas import st_canvas  # type: ignore[import]
    CANVAS_AVAILABLE = True
except ImportError:
    CANVAS_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
API_BASE = os.getenv("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
API_KEY = os.getenv("API_KEY", "").strip()
API_HEADERS = {"X-API-Key": API_KEY} if API_KEY else {}

ZONE_TYPES = [
    "shelf_zone",
    "exit_zone",
    "bagging_zone",
    "scanner_zone",
    "counter_zone",
]

ZONE_COLORS: dict[str, str] = {
    "shelf_zone":    "#FF6B6B",
    "exit_zone":     "#4ECDC4",
    "bagging_zone":  "#45B7D1",
    "scanner_zone":  "#96CEB4",
    "counter_zone":  "#FFEAA7",
}

ZONE_DESCRIPTIONS: dict[str, str] = {
    "shelf_zone":    "Product shelf area — hands/wrists here trigger shelf-interaction signals.",
    "exit_zone":     "Store exit — crossing after concealment triggers high-risk alert.",
    "bagging_zone":  "Bagging area at self-checkout — hand-to-bag concealment detected here.",
    "scanner_zone":  "Scanner area — wrist here marks a scan event.",
    "counter_zone":  "Counter / cashier area — items placed here are considered paid.",
}

CANVAS_W = 800
CANVAS_H = 450


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _fetch_cameras() -> list[str]:
    try:
        resp = requests.get(f"{API_BASE}/health", headers=API_HEADERS, timeout=3)
        if resp.ok:
            return resp.json().get("cameras", ["cam-entry", "cam-shelf-1", "cam-shelf-2", "cam-shelf-3", "cam-shelf-4", "cam-counter"]) or ["cam-entry", "cam-shelf-1", "cam-shelf-2", "cam-shelf-3", "cam-shelf-4", "cam-counter"]
    except requests.RequestException:
        pass
    return ["cam-entry", "cam-shelf-1", "cam-shelf-2", "cam-shelf-3", "cam-shelf-4", "cam-counter"]


def _fetch_frame(camera_id: str) -> Image.Image | None:
    try:
        resp = requests.get(
            f"{API_BASE}/latest_frame",
            params={"camera_id": camera_id},
            headers=API_HEADERS,
            timeout=5,
        )
        if resp.ok:
            img = Image.open(BytesIO(resp.content))
            return img.resize((CANVAS_W, CANVAS_H))
    except requests.RequestException:
        pass
    return None


def _fetch_existing_zones(camera_id: str) -> dict[str, list]:
    try:
        resp = requests.get(f"{API_BASE}/config/cameras", headers=API_HEADERS, timeout=3)
        if resp.ok:
            for cam in resp.json().get("cameras", []):
                if cam["camera_id"] == camera_id:
                    return cam.get("zones") or {}
    except requests.RequestException:
        pass
    return {}


def _save_zones(camera_id: str, zones: dict, file_path: str) -> None:
    zones_serializable = {
        k: [[int(p[0]), int(p[1])] for p in pts]
        for k, pts in zones.items()
        if pts
    }
    payload = {"camera_id": camera_id, "zones": zones_serializable}

    # 1. Write JSON to disk
    try:
        out = Path(file_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        st.success(f"Zones written to `{out.resolve()}`")
    except OSError as exc:
        st.warning(f"Could not write file: {exc}")

    # 2. POST to running API (non-fatal if pipeline is offline)
    try:
        resp = requests.post(
            f"{API_BASE}/config/cameras/{camera_id}/zones",
            json=zones_serializable,
            headers=API_HEADERS,
            timeout=5,
        )
        if resp.ok:
            st.success(f"Zones applied to running pipeline for **{camera_id}**.")
        elif resp.status_code == 401:
            st.error("API unauthorized. Set `API_KEY` for Streamlit to match backend `X-API-Key`.")
        else:
            st.warning(f"API returned {resp.status_code}: {resp.text[:200]}")
    except requests.RequestException as exc:
        st.info(f"Pipeline API not reachable — zones saved to file only. ({exc})")


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

def _hex_to_rgba(hex_color: str, alpha: int = 60) -> tuple[int, int, int, int]:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (r, g, b, alpha)


def _draw_zones_on_image(
    img: Image.Image,
    zones: dict[str, list],
) -> Image.Image:
    """Overlay all saved zones onto the image (RGBA compositing)."""
    overlay = img.convert("RGBA").copy()
    draw = ImageDraw.Draw(overlay)
    for zone_name, polygon in zones.items():
        if len(polygon) < 3:
            continue
        pts = [(int(p[0]), int(p[1])) for p in polygon]
        fill = _hex_to_rgba(ZONE_COLORS.get(zone_name, "#FFFFFF"), alpha=55)
        outline = _hex_to_rgba(ZONE_COLORS.get(zone_name, "#FFFFFF"), alpha=230)
        draw.polygon(pts, fill=fill, outline=outline[:3] + (230,))
        # Label at polygon centroid
        cx = sum(p[0] for p in pts) // len(pts)
        cy = sum(p[1] for p in pts) // len(pts)
        label = zone_name.replace("_zone", "").upper()
        draw.text((cx - 2, cy - 2), label, fill=(0, 0, 0, 200))
        draw.text((cx, cy), label, fill=(255, 255, 255, 240))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def _extract_polygon_from_canvas(canvas_result) -> list[list[int]] | None:
    """Parse polygon coordinates from st_canvas JSON output."""
    if canvas_result is None or canvas_result.json_data is None:
        return None
    objects = canvas_result.json_data.get("objects", [])
    if not objects:
        return None
    obj = objects[-1]  # most recently drawn object
    obj_type = obj.get("type", "")
    left = float(obj.get("left", 0))
    top = float(obj.get("top", 0))

    if obj_type == "polygon":
        path = obj.get("path", [])
        pts = []
        for cmd in path:
            if cmd[0] in ("M", "L") and len(cmd) >= 3:
                pts.append([int(float(cmd[1]) + left), int(float(cmd[2]) + top)])
        return pts if len(pts) >= 3 else None

    if obj_type == "path":
        path = obj.get("path", [])
        pts = []
        for cmd in path:
            if cmd[0] in ("M", "L") and len(cmd) >= 3:
                pts.append([int(float(cmd[1])), int(float(cmd[2]))])
        # Deduplicate consecutive identical points
        deduped = [pts[0]] if pts else []
        for p in pts[1:]:
            if p != deduped[-1]:
                deduped.append(p)
        return deduped if len(deduped) >= 3 else None

    return None


# ---------------------------------------------------------------------------
# Fallback: plain JSON editor when canvas library is absent
# ---------------------------------------------------------------------------

def _manual_entry_fallback(camera_id: str) -> None:
    st.warning(
        "**streamlit-drawable-canvas** is not installed.  "
        "Run `pip install streamlit-drawable-canvas` and restart for the visual editor.  "
        "Using manual JSON entry for now."
    )
    st.subheader("Manual Zone JSON Entry")
    default = json.dumps(
        {
            "shelf_zone":   [[0, 0], [300, 0], [300, 300], [0, 300]],
            "exit_zone":    [[350, 0], [640, 0], [640, 200], [350, 200]],
            "bagging_zone": [],
            "scanner_zone": [],
        },
        indent=2,
    )
    raw = st.text_area("Zones (edit and save)", value=default, height=280, key="manual_json")
    file_path = st.text_input("Output file", value=f"./config/zones.{camera_id}.json")
    if st.button("Save Zones", type="primary"):
        try:
            parsed = json.loads(raw)
            _save_zones(camera_id, parsed, file_path)
        except json.JSONDecodeError as exc:
            st.error(f"Invalid JSON: {exc}")


# ---------------------------------------------------------------------------
# Main page
# ---------------------------------------------------------------------------

def main() -> None:
    st.title("Camera Zone Setup")
    if API_KEY:
        st.caption("Using API authentication via `API_KEY`.")
    st.caption(
        "Draw zones directly on the camera feed.  "
        "Zones control which areas trigger shelf-interaction, concealment, and exit alerts."
    )

    # Session state
    if "sz_zones" not in st.session_state:
        st.session_state.sz_zones: dict[str, list] = {}
    if "sz_frame" not in st.session_state:
        st.session_state.sz_frame: Image.Image | None = None
    if "sz_camera_id" not in st.session_state:
        st.session_state.sz_camera_id: str = "cam01"

    cameras = _fetch_cameras()

    # Top controls
    top_left, top_right = st.columns([3, 1])
    with top_left:
        camera_id = st.selectbox("Camera", cameras, key="sz_cam_select")
        if camera_id != st.session_state.sz_camera_id:
            st.session_state.sz_camera_id = camera_id
            st.session_state.sz_zones = {}
            st.session_state.sz_frame = None

    with top_right:
        st.write("")
        if st.button("Fetch Live Frame", use_container_width=True):
            img = _fetch_frame(camera_id)
            if img:
                st.session_state.sz_frame = img
                existing = _fetch_existing_zones(camera_id)
                if existing:
                    st.session_state.sz_zones = existing
                    st.success(f"Loaded {len(existing)} existing zones from API.")
                else:
                    st.session_state.sz_zones = {}
            else:
                st.warning("Could not fetch frame — is the pipeline running?")

    if not CANVAS_AVAILABLE:
        _manual_entry_fallback(camera_id)
        return

    st.divider()

    # Background image: frame with existing zones drawn on it
    frame = st.session_state.sz_frame
    bg_img = (
        _draw_zones_on_image(frame, st.session_state.sz_zones)
        if frame is not None
        else Image.new("RGB", (CANVAS_W, CANVAS_H), color=(25, 25, 35))
    )

    # Zone type and draw mode selectors
    zt_col, dm_col, hint_col = st.columns([2, 1, 3])
    with zt_col:
        selected_zone = st.selectbox("Zone Type to Draw", ZONE_TYPES, key="sz_zone_type")
    with dm_col:
        draw_mode = st.selectbox("Draw Mode", ["polygon", "freedraw"], key="sz_draw_mode")
    with hint_col:
        st.info(ZONE_DESCRIPTIONS.get(selected_zone, ""))

    stroke_hex = ZONE_COLORS.get(selected_zone, "#FFFFFF")

    canvas_result = st_canvas(
        fill_color=stroke_hex + "30",
        stroke_width=2,
        stroke_color=stroke_hex,
        background_image=bg_img,
        update_streamlit=False,
        width=CANVAS_W,
        height=CANVAS_H,
        drawing_mode=draw_mode,
        point_display_radius=5,
        key=f"sz_canvas_{camera_id}",
    )

    # Canvas instructions
    if draw_mode == "polygon":
        st.caption("Click to add vertices. Double-click to close the polygon.")
    else:
        st.caption("Draw a closed shape freehand. The outline will be traced as a polygon.")

    # Action buttons
    btn1, btn2, btn3 = st.columns(3)
    with btn1:
        if st.button("Add as Selected Zone", type="primary", use_container_width=True):
            pts = _extract_polygon_from_canvas(canvas_result)
            if pts and len(pts) >= 3:
                st.session_state.sz_zones[selected_zone] = pts
                st.success(f"Zone **{selected_zone}** set ({len(pts)} vertices).")
                st.rerun()
            else:
                st.warning("Draw a polygon with at least 3 points, then click here.")
    with btn2:
        if st.button("Clear Selected Zone", use_container_width=True):
            if selected_zone in st.session_state.sz_zones:
                del st.session_state.sz_zones[selected_zone]
                st.rerun()
            else:
                st.info(f"{selected_zone} is not set.")
    with btn3:
        if st.button("Clear All Zones", use_container_width=True):
            st.session_state.sz_zones = {}
            st.rerun()

    st.divider()

    # Zone summary table
    st.subheader("Defined Zones")
    if st.session_state.sz_zones:
        cols = st.columns(len(ZONE_TYPES))
        for col, z in zip(cols, ZONE_TYPES):
            color = ZONE_COLORS[z]
            pts = st.session_state.sz_zones.get(z, [])
            status = f"{len(pts)} pts" if pts else "—"
            col.markdown(
                f'<div style="border-left:4px solid {color}; padding:4px 8px;">'
                f"<b>{z.replace('_zone','').upper()}</b><br/>{status}</div>",
                unsafe_allow_html=True,
            )
    else:
        st.info("No zones defined yet. Draw polygons above and click **Add as Selected Zone**.")

    st.divider()

    # Save controls
    save_left, save_right = st.columns([3, 1])
    with save_left:
        zones_file = st.text_input(
            "Zones JSON path (written to disk + sent to API)",
            value=f"./config/zones.{camera_id}.json",
            key="sz_file_path",
        )
    with save_right:
        st.write("")
        st.write("")
        if st.button("Save Zones", type="primary", use_container_width=True):
            if not st.session_state.sz_zones:
                st.error("No zones to save.")
            else:
                _save_zones(camera_id, st.session_state.sz_zones, zones_file)

    # Advanced JSON editor
    with st.expander("Edit zones as raw JSON (advanced)"):
        raw_json = st.text_area(
            "Zones JSON",
            value=json.dumps(
                {k: [[int(x), int(y)] for x, y in v] for k, v in st.session_state.sz_zones.items()},
                indent=2,
            ),
            height=220,
            key="sz_raw_json",
        )
        if st.button("Apply JSON", key="sz_apply_json"):
            try:
                parsed = json.loads(raw_json)
                st.session_state.sz_zones = {
                    k: [[int(p[0]), int(p[1])] for p in pts]
                    for k, pts in parsed.items()
                }
                st.success("Zones updated from JSON.")
                st.rerun()
            except (json.JSONDecodeError, KeyError, TypeError, IndexError) as exc:
                st.error(f"Invalid JSON: {exc}")


if __name__ == "__main__":
    main()
