import os
from pathlib import Path

import requests
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="EdgeGuard Theft Risk Dashboard", layout="wide")
api_base = os.getenv("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
browser_api_base = os.getenv("BROWSER_API_BASE_URL", api_base).rstrip("/")
api_key = os.getenv("API_KEY", "").strip()
api_headers = {"X-API-Key": api_key} if api_key else {}


def _api_get(url: str, **kwargs):
    headers = dict(kwargs.pop("headers", {}) or {})
    headers.update(api_headers)
    return requests.get(url, headers=headers, **kwargs)


def _render_webrtc_player(camera: str, height: int = 320) -> None:
    safe_camera = camera.replace("'", "\\'")
    api_key_js = api_key.replace("\\", "\\\\").replace("'", "\\'")
    html = f"""
    <div style="background:#0b1220;border-radius:8px;overflow:hidden;border:1px solid #1f2937;">
      <video id="v-{safe_camera}" autoplay playsinline controls muted style="width:100%;height:auto;display:block;"></video>
      <div id="s-{safe_camera}" style="padding:6px 10px;font-size:12px;color:#93c5fd;">Connecting WebRTC...</div>
    </div>
    <script>
      (async function() {{
        const video = document.getElementById("v-{safe_camera}");
        const status = document.getElementById("s-{safe_camera}");
        const pc = new RTCPeerConnection();
        pc.ontrack = (ev) => {{
          video.srcObject = ev.streams[0];
          status.textContent = "Live via WebRTC";
        }};
        pc.onconnectionstatechange = () => {{
          if (pc.connectionState === "failed" || pc.connectionState === "disconnected") {{
            status.textContent = "WebRTC disconnected";
          }}
        }};
        try {{
          const offer = await pc.createOffer({{ offerToReceiveVideo: true }});
          await pc.setLocalDescription(offer);
          const headers = {{ "Content-Type": "application/json" }};
          const apiKey = '{api_key_js}';
          if (apiKey) {{
            headers["X-API-Key"] = apiKey;
          }}
          const res = await fetch("{browser_api_base}/webrtc/offer", {{
            method: "POST",
            headers,
            body: JSON.stringify({{
              sdp: offer.sdp,
              type: offer.type,
              camera_id: "{safe_camera}"
            }})
          }});
          if (!res.ok) {{
            status.textContent = "WebRTC offer failed: " + res.status;
            return;
          }}
          const answer = await res.json();
          await pc.setRemoteDescription(answer);
        }} catch (err) {{
          status.textContent = "WebRTC error: " + String(err);
        }}
      }})();
    </script>
    """
    components.html(html, height=height, scrolling=False)


st.title("EdgeGuard Theft Risk Dashboard")
known_cameras = ["cam-entry", "cam-shelf-1", "cam-shelf-2", "cam-shelf-3", "cam-shelf-4", "cam-counter"]
source_type = "unknown"
try:
    health_resp = _api_get(f"{api_base}/health", timeout=5)
    if health_resp.ok:
        payload = health_resp.json()
        known_cameras = payload.get("cameras", known_cameras) or known_cameras
        source_type = str(payload.get("source_type", "unknown"))
except requests.RequestException:
    pass

if source_type == "file":
    st.warning("Pipeline source_type is 'file'. Switch to RTSP for true live camera monitoring.")

with st.sidebar:
    st.header("Filters")
    if api_key:
        st.caption("API auth enabled via `API_KEY`.")
    if os.getenv("CROSS_CAMERA_REID_ENABLED", "false").lower() != "true":
        st.caption("Cross-camera ReID is experimental and currently disabled.")
    view_mode = st.selectbox("View Mode", ["single", "all_cameras"], index=1)
    camera_id = st.selectbox("Camera ID", known_cameras)
    event_type = st.selectbox(
        "Event Type",
        ["all", "POSSIBLE_CONCEALMENT", "HIGH_RISK_EXIT", "SELF_CHECKOUT_NONSCAN"],
    )
    since = st.text_input("Since (ISO)", "")
    until = st.text_input("Until (ISO)", "")
    st.caption("Use the Live Monitor page for push-based realtime updates.")
    if st.button("Refresh now", use_container_width=True):
        st.rerun()

params: dict[str, str | int] = {"limit": 300}
if camera_id:
    params["camera_id"] = camera_id
if event_type != "all":
    params["event_type"] = event_type
if since:
    params["since"] = since
if until:
    params["until"] = until

left, right = st.columns([1.3, 1.0])

with left:
    if view_mode == "single":
        st.subheader("Live Stream")
        _render_webrtc_player(camera_id, height=420)
    else:
        st.subheader("All Camera Streams")
        cam_cols = st.columns(2)
        for idx, cam in enumerate(known_cameras):
            with cam_cols[idx % 2]:
                st.caption(cam)
                _render_webrtc_player(cam, height=260)

with right:
    st.subheader("Events")
    events: list[dict] = []
    try:
        response = _api_get(f"{api_base}/events", params=params, timeout=10)
        response.raise_for_status()
        events = response.json()
    except requests.RequestException as exc:
        st.error(f"Failed to fetch events: {exc}")

    if events:
        rows = [
            {
                "track_id": e["track_id"],
                "event_type": e["event_type"],
                "risk": e["risk_score_at_trigger"],
                "ts_trigger": e["ts_trigger"],
            }
            for e in events
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)

        selected_idx = st.selectbox(
            "Select Track Event",
            options=list(range(len(events))),
            format_func=lambda i: f"track {events[i].get('track_id')} | {events[i].get('event_type')} | {events[i].get('ts_trigger')}",
        )
        selected = events[selected_idx]
        st.caption(selected["short_explanation"])
        st.json(selected.get("details", {}))
        selected_customer_id = selected.get("details", {}).get("customer_id")

        snapshot_path = selected.get("snapshot_path")
        if snapshot_path:
            snapshot = Path(snapshot_path)
            if snapshot.exists():
                st.image(str(snapshot), caption="Event snapshot", use_container_width=True)

        clip_path = selected.get("details", {}).get("clip_path")
        if clip_path:
            clip = Path(clip_path)
            if clip.exists():
                st.video(str(clip))

        track_id = selected.get("track_id")
        if track_id is not None:
            try:
                timeline_resp = _api_get(
                    f"{api_base}/tracks/{track_id}/timeline",
                    params={"camera_id": camera_id} if camera_id else None,
                    timeout=10,
                )
                timeline_resp.raise_for_status()
                timeline = timeline_resp.json()
                points = timeline.get("points", [])
                if points:
                    st.subheader(f"Track {track_id} Risk Timeline")
                    chart_rows = [{"ts": p["ts"], "risk_score": p["risk_score"]} for p in points]
                    st.line_chart(chart_rows, x="ts", y="risk_score", height=220)
                signals = timeline.get("signals", [])
                if signals:
                    st.write("Recent signals")
                    st.dataframe(signals[-20:], use_container_width=True, hide_index=True)
            except requests.RequestException as exc:
                st.warning(f"Timeline fetch failed: {exc}")

        if selected_customer_id:
            st.subheader(f"Customer {selected_customer_id}")
            try:
                cust_resp = _api_get(f"{api_base}/retail/customers/{selected_customer_id}", timeout=10)
                cust_resp.raise_for_status()
                customer = cust_resp.json()
                basket = customer.get("basket_state", {})
                hand = basket.get("hand_count", 0)
                hidden = basket.get("concealed_count", 0)
                counter = basket.get("counter_count", 0)
                seen_now = hand + counter
                expected = seen_now + hidden
                missing = max(0, expected - seen_now)
                st.write(
                    {
                        "risk_score": customer.get("risk_score_current", 0.0),
                        "hand_count": hand,
                        "hidden_count": hidden,
                        "counter_count": counter,
                        "seen_now": seen_now,
                        "expected": expected,
                        "missing": missing,
                    }
                )
                clips_resp = _api_get(
                    f"{api_base}/retail/customers/{selected_customer_id}/clips",
                    params={"limit": 10},
                    timeout=10,
                )
                clips_resp.raise_for_status()
                clips = clips_resp.json().get("items", [])
                if clips:
                    st.write("Recent customer clips")
                    st.dataframe(clips, use_container_width=True, hide_index=True)
            except requests.RequestException as exc:
                st.warning(f"Customer details fetch failed: {exc}")
    else:
        st.info("No events found for current filters.")
