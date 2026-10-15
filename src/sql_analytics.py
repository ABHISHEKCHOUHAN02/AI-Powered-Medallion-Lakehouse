import duckdb

def get_gold_metrics():
    conn = duckdb.connect("warehouse.duckdb")
    
    # Query 1: Monthly Running Sales (Window Function)
    running_sales_query = """
        SELECT 
            DATE_TRUNC('month', order_date) AS sales_month,
            SUM(amount) AS monthly_revenue,
            SUM(SUM(amount)) OVER (ORDER BY DATE_TRUNC('month', order_date)) AS cumulative_revenue
        FROM gold_fact_sales
        WHERE status = 'delivered'
        GROUP BY 1
        ORDER BY 1;
    """
    
    # Query 2: Customer RFM Ranking (CTEs & NTILE Window Function)
    customer_ranking_query = """
        WITH CustomerStats AS (
            SELECT 
                c.customer_id,
                c.customer_name,
                COUNT(f.order_id) AS total_orders,
                SUM(f.amount) AS total_spent,
                MAX(f.order_date) AS last_order_date
            FROM gold_dim_customer c
            JOIN gold_fact_sales f ON c.customer_id = f.customer_id
            WHERE f.status = 'delivered'
            GROUP BY c.customer_id, c.customer_name
        )
        SELECT 
            customer_name,
            total_orders,
            total_spent,
            RANK() OVER (ORDER BY total_spent DESC) AS spend_rank,
            NTILE(4) OVER (ORDER BY total_spent DESC) AS customer_tier
        FROM CustomerStats
        ORDER BY spend_rank
        LIMIT 10;
    """
    
    df_running = conn.execute(running_sales_query).df()
    df_ranking = conn.execute(customer_ranking_query).df()
    
    conn.close()
    return df_running, df_ranking