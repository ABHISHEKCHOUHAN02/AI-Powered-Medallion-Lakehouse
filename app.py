import streamlit as st
import plotly.express as px
from src.sql_analytics import get_gold_metrics
from src.ai_agent import generate_and_execute_sql

st.set_page_config(page_title="Lakehouse Analytics & AI Assistant", layout="wide")

st.title("📊 E-Commerce Lakehouse & Conversational BI Engine")

tab1, tab2 = st.tabs(["📈 Executive Dashboard", "🤖 Conversational AI Assistant"])

with tab1:
    st.header("Gold Layer Business Analytics")
    df_running, df_ranking = get_gold_metrics()
    
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Monthly Growth & Cumulative Sales")
        fig = px.line(df_running, x='sales_month', y=['monthly_revenue', 'cumulative_revenue'], 
                      title="Revenue Progression (Window Function View)")
        st.plotly_chart(fig, use_container_width=True)
        
    with col2:
        st.subheader("Top Customers by Spend (Ranked)")
        st.dataframe(df_ranking)

with tab2:
    st.header("Ask Questions in Plain English (Text-to-SQL)")
    user_query = st.text_input("e.g., 'What is the total revenue by country?'")
    
    if st.button("Generate & Run Query") and user_query:
        sql, result = generate_and_execute_sql(user_query)
        
        st.subheader("Generated SQL Query")
        st.code(sql, language="sql")
        
        st.subheader("Query Results")
        if isinstance(result, str):
            st.error(result)
        else:
            st.dataframe(result)