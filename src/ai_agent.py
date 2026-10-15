import os
import duckdb
from google import genai
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

SCHEMA_PROMPT = """
You are a SQL expert for a DuckDB database. Generate ONLY a valid SQL SELECT query to answer the user request.
Do not wrap code in backticks or markdown text. Do not explain anything.

Schema:
1. gold_dim_customer (customer_id VARCHAR, customer_name VARCHAR, country VARCHAR, signup_date DATE)
2. gold_fact_sales (order_id VARCHAR, customer_id VARCHAR, order_date DATE, amount DOUBLE, status VARCHAR)
"""

def generate_and_execute_sql(user_question: str):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "Error: GEMINI_API_KEY is missing from environment variables.", None
        
    client = genai.Client(api_key=api_key)
    
    prompt = f"{SCHEMA_PROMPT}\nUser Question: {user_question}\nSQL Query:"
    response = client.models.generate_content(
        model='gemini-3.5-flash-lite',
        contents=prompt,
    )
    
    generated_sql = response.text.strip().replace("```sql", "").replace("```", "")
    
    if not generated_sql.lower().startswith("select"):
        return "Error: Only SELECT queries are permitted.", None
    
    conn = duckdb.connect("warehouse.duckdb")
    try:
        result_df = conn.execute(generated_sql).df()
        conn.close()
        return generated_sql, result_df
    except Exception as e:
        conn.close()
        return generated_sql, f"Execution Error: {str(e)}"