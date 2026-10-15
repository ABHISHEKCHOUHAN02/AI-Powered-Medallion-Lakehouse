import duckdb
import pandas as pd
import os

def run_pipeline():
    conn = duckdb.connect("warehouse.duckdb")
    
    # -------------------------------------------------------------
    # 1. BRONZE LAYER (Ingest Raw Data)
    # -------------------------------------------------------------
    print("Ingesting into Bronze Layer...")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bronze_orders AS 
        SELECT * FROM read_csv_auto('data/bronze/olist_orders_dataset.csv');
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bronze_customers AS 
        SELECT * FROM read_csv_auto('data/bronze/olist_customers_dataset.csv');
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bronze_payments AS
        SELECT * FROM read_csv_auto('data/bronze/olist_order_payments_dataset.csv');
    """)
   # -------------------------------------------------------------
    # 2. SILVER LAYER (Data Cleaning & Quality Enforcement)
    # -------------------------------------------------------------
    print("Cleaning & Processing into Silver Layer...")
    
    conn.execute("""
        CREATE OR REPLACE TABLE silver_orders AS
        SELECT 
            CAST(order_id AS VARCHAR) AS order_id,
            CAST(customer_id AS VARCHAR) AS customer_id,
            CAST(order_purchase_timestamp AS DATE) AS order_date,
            LOWER(TRIM(order_status)) AS status
        FROM bronze_orders
        WHERE order_id IS NOT NULL;
    """)

    conn.execute("""
        CREATE OR REPLACE TABLE silver_customers AS
        SELECT 
            CAST(customer_id AS VARCHAR) AS customer_id,
            'Customer_' || SUBSTR(CAST(customer_id AS VARCHAR), 1, 5) AS customer_name,
            LOWER(TRIM(customer_city)) AS country,
            UPPER(TRIM(customer_state)) AS state,
            CURRENT_DATE AS signup_date
        FROM bronze_customers
        WHERE customer_id IS NOT NULL;
    """)

    # An order can have multiple payment rows (for example, installments).
    # Keep the payment detail at order grain before joining it to orders.
    conn.execute("""
        CREATE OR REPLACE TABLE silver_payments AS
        SELECT
            CAST(order_id AS VARCHAR) AS order_id,
            SUM(TRY_CAST(payment_value AS DOUBLE)) AS payment_total
        FROM bronze_payments
        WHERE order_id IS NOT NULL
        GROUP BY CAST(order_id AS VARCHAR);
    """)

    # Export Silver to Parquet files
    os.makedirs("data/silver", exist_ok=True)
    conn.execute("COPY silver_orders TO 'data/silver/orders.parquet' (FORMAT PARQUET);")
    conn.execute("COPY silver_customers TO 'data/silver/customers.parquet' (FORMAT PARQUET);")
    conn.execute("COPY silver_payments TO 'data/silver/payments.parquet' (FORMAT PARQUET);")

    # -------------------------------------------------------------
    # 3. GOLD LAYER (Dimensional Data Warehouse - Star Schema)
    # -------------------------------------------------------------
    print("Building Gold Dimensional Warehouse...")
    
    conn.execute("""
        CREATE OR REPLACE TABLE gold_dim_customer AS
        SELECT DISTINCT
            customer_id,
            customer_name,
            country,
            state,
            signup_date
        FROM silver_customers;
    """)

    conn.execute("""
        CREATE OR REPLACE TABLE gold_fact_sales AS
        SELECT 
            o.order_id,
            o.customer_id,
            o.order_date,
            p.payment_total AS amount,
            o.status
        FROM silver_orders o
        INNER JOIN gold_dim_customer c ON o.customer_id = c.customer_id
        LEFT JOIN silver_payments p ON o.order_id = p.order_id;
    """)
    
    
    print("Pipeline Execution Complete!")
    conn.close()

if __name__ == "__main__":
    run_pipeline()
