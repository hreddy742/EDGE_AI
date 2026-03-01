import os

import streamlit as st
import streamlit.components.v1 as components


def main() -> None:
    st.set_page_config(page_title="EdgeGuard Live Monitor", layout="wide")
    st.title("Live Monitor")
    st.caption("Push-based live monitoring: events and customer state update over SSE.")

    api_base = os.getenv("BROWSER_API_BASE_URL", os.getenv("API_BASE_URL", "http://127.0.0.1:8000")).rstrip("/")
    api_key = os.getenv("API_KEY", "").strip()
    if api_key:
        st.warning(
            "Live Monitor SSE is disabled when API auth is enabled because browser EventSource "
            "cannot send `X-API-Key` headers in this page."
        )
        st.info("Use the main dashboard page for authenticated API access.")
        return

    html = f"""
    <style>
      body {{ font-family: 'Segoe UI', sans-serif; margin: 0; }}
      .wrap {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
      .panel {{ border: 1px solid #d8dee4; border-radius: 10px; padding: 10px; background: #fafbfc; }}
      .title {{ font-weight: 600; margin-bottom: 8px; }}
      .item {{ border: 1px solid #e5e7eb; border-radius: 8px; padding: 8px; margin: 6px 0; background: #fff; }}
      .meta {{ color: #374151; font-size: 12px; }}
      .red {{ color: #b91c1c; font-weight: 600; }}
      .yellow {{ color: #a16207; font-weight: 600; }}
      .green {{ color: #166534; font-weight: 600; }}
      .mono {{ font-family: Consolas, monospace; font-size: 12px; }}
    </style>
    <div class="wrap">
      <div class="panel">
        <div class="title">Live Events</div>
        <div id="events"></div>
      </div>
      <div class="panel">
        <div class="title">Live Customers</div>
        <div id="customers"></div>
      </div>
    </div>
    <script>
      const eventsEl = document.getElementById("events");
      const customersEl = document.getElementById("customers");

      function riskBand(score) {{
        if (score >= 12) return "red";
        if (score >= 8) return "yellow";
        return "green";
      }}

      function prepend(el, html) {{
        const node = document.createElement("div");
        node.innerHTML = html;
        el.prepend(node.firstElementChild);
        while (el.children.length > 30) {{
          el.removeChild(el.lastElementChild);
        }}
      }}

      const eventsSse = new EventSource("{api_base}/live/events/stream");
      eventsSse.addEventListener("theft_event", (e) => {{
        const payload = JSON.parse(e.data);
        const score = Number(payload.risk_score_at_trigger || 0);
        const band = riskBand(score);
        prepend(eventsEl, `
          <div class="item">
            <div><span class="${{band}}">${{payload.event_type}}</span> <span class="mono">track:${{payload.track_id}}</span></div>
            <div class="meta">camera=${{payload.camera_id}} track=${{payload.track_id}} risk=${{score.toFixed(2)}}</div>
            <div class="meta">${{payload.ts_trigger}}</div>
          </div>
        `);
      }});

      const customersSse = new EventSource("{api_base}/retail/customers/stream?limit=100");
      customersSse.addEventListener("customers", (e) => {{
        const payload = JSON.parse(e.data);
        customersEl.innerHTML = "";
        for (const c of payload.slice(0, 20)) {{
          const basket = c.basket_state || {{}};
          const hand = Number(basket.hand_count || 0);
          const hidden = Number(basket.concealed_count || 0);
          const counter = Number(basket.counter_count || 0);
          const score = Number(c.risk_score_current || 0);
          const band = riskBand(score);
          prepend(customersEl, `
            <div class="item">
              <div><span class="${{band}}">${{c.global_customer_id}}</span></div>
              <div class="meta">risk=${{score.toFixed(2)}} hand=${{hand}} hidden=${{hidden}} counter=${{counter}}</div>
              <div class="meta">camera=${{c.current_camera_id || "n/a"}} zone=${{c.current_zone || "n/a"}}</div>
            </div>
          `);
        }}
      }});
    </script>
    """
    components.html(html, height=760, scrolling=True)


if __name__ == "__main__":
    main()
