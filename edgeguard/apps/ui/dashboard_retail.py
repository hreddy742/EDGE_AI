import streamlit as st


def render_customer_summary(
    customer_id: str,
    hand_count: int,
    concealed_count: int,
    paid_count: int,
    unpaid_count: int,
    risk_score: float,
) -> None:
    color = "green"
    if risk_score >= 12:
        color = "red"
    elif risk_score >= 8:
        color = "orange"

    st.markdown(f"### Customer `{customer_id}`")
    st.markdown(
        f"- In hand: **{hand_count}**  \n"
        f"- Concealed: **{concealed_count}**  \n"
        f"- Paid: **{paid_count}**  \n"
        f"- Unpaid suspected: **{unpaid_count}**  \n"
        f"- Risk: :{color}[**{risk_score:.1f}**]"
    )
