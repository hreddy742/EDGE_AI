import streamlit as st


def main() -> None:
    st.title("Review Queue")
    st.caption("Unresolved alert sessions and evidence review workflow.")
    st.write(
        [
            {
                "customer_id": "cam01:12",
                "risk_band": "RED",
                "missing_count": 1,
                "clip_id": "example-clip-001",
                "status": "KEEP",
            }
        ]
    )
    st.info("Reviewer actions (keep/resolve/override retention) to be wired to API.")


if __name__ == "__main__":
    main()
