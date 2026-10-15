import pandas as pd
import plotly.express as px
import streamlit as st

from src.ai_agent import generate_and_execute_sql
from src.sql_analytics import get_gold_metrics
from src.visualization import build_chart, recommend_chart


def render_assistant_message(message: dict, message_index: int):
    with st.chat_message("assistant"):
        if message.get("error"):
            st.error(message["error"])
            return

        st.caption(message.get("content", "Query complete."))
        if message.get("sql"):
            with st.expander("View validated SQL"):
                st.code(message["sql"], language="sql")

        df = message.get("data")
        if not isinstance(df, pd.DataFrame):
            return

        recommendation = recommend_chart(df)
        chart_options = ["Auto", "Line", "Bar", "Scatter", "Table"]
        choice = st.selectbox(
            f"Visualization (automatic suggestion: {recommendation})",
            chart_options,
            key=f"chart_choice_{message_index}",
        )
        chart_type = recommendation if choice == "Auto" else choice

        if chart_type == "Metric" and not df.empty:
            numeric_columns = list(df.select_dtypes(include="number").columns)
            if numeric_columns:
                metric_column = numeric_columns[0]
                value = df.iloc[0][metric_column]
                st.metric(str(metric_column).replace("_", " ").title(), f"{value:,.2f}")
        elif chart_type != "Table":
            figure = build_chart(df, chart_type)
            if figure is not None:
                st.plotly_chart(figure, use_container_width=True)
            else:
                st.info(f"This result does not have the columns needed for a {chart_type.lower()} chart.")

        with st.expander(f"View result data ({len(df):,} rows)"):
            st.dataframe(df, use_container_width=True)


st.set_page_config(page_title="Lakehouse Analytics & AI Assistant", layout="wide")
st.title("📊 E-Commerce Lakehouse & Conversational BI Engine")

tab1, tab2 = st.tabs(["📈 Executive Dashboard", "🤖 Conversational AI Assistant"])

with tab1:
    st.header("Gold Layer Business Analytics")
    df_running, df_ranking = get_gold_metrics()

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Monthly Growth & Cumulative Sales")
        fig = px.line(
            df_running,
            x="sales_month",
            y=["monthly_revenue", "cumulative_revenue"],
            title="Revenue Progression (Window Function View)",
        )
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("Top Customers by Spend (Ranked)")
        st.dataframe(df_ranking, use_container_width=True)

with tab2:
    st.header("Ask questions in plain English")
    st.caption("Ask a follow-up such as “Now break that down by city.” The assistant carries recent question and SQL context forward.")

    if "conversation" not in st.session_state:
        st.session_state.conversation = []

    if st.button("Clear conversation", key="clear_conversation"):
        st.session_state.conversation = []
        st.rerun()

    for index, message in enumerate(st.session_state.conversation):
        if message["role"] == "user":
            with st.chat_message("user"):
                st.markdown(message["content"])
        else:
            render_assistant_message(message, index)

    user_question = st.chat_input("Ask a question about the sales data")
    if user_question:
        prior_context = list(st.session_state.conversation)
        st.session_state.conversation.append({"role": "user", "content": user_question})
        with st.chat_message("user"):
            st.markdown(user_question)

        with st.spinner("Generating and safely running SQL…"):
            sql, result = generate_and_execute_sql(
                user_question,
                conversation_history=prior_context,
            )

        if isinstance(result, str):
            assistant_message = {
                "role": "assistant",
                "error": result,
                "sql": None,
            }
        else:
            assistant_message = {
                "role": "assistant",
                "content": f"Query complete — {len(result):,} row(s).",
                "sql": sql,
                "data": result,
            }
        st.session_state.conversation.append(assistant_message)
        render_assistant_message(assistant_message, len(st.session_state.conversation) - 1)
