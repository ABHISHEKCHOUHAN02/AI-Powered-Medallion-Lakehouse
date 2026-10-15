import os
import threading

import duckdb
import sqlglot
from dotenv import load_dotenv
from google import genai
from sqlglot import exp
from sqlglot.errors import ParseError

load_dotenv()

DATABASE_PATH = "warehouse.duckdb"
MAX_ROWS = 500
QUERY_TIMEOUT_SECONDS = 10
MAX_SQL_REPAIR_ATTEMPTS = 2
ALLOWED_TABLES = {"gold_dim_customer", "gold_fact_sales"}

SCHEMA_PROMPT = """
You are a text-to-SQL assistant for a DuckDB database. Generate exactly one valid SQL SELECT query.
Return only SQL: no markdown, code fences, or explanation.
Use only the tables and columns listed below. Limit results to at most 500 rows.

Schema:
1. gold_dim_customer
   - customer_id VARCHAR
   - customer_name VARCHAR
   - country VARCHAR (currently contains normalized customer_city values, not countries)
   - state VARCHAR (normalized customer_state abbreviation)
   - signup_date DATE
2. gold_fact_sales
   - order_id VARCHAR
   - customer_id VARCHAR
   - order_date DATE
   - amount DOUBLE (sum of payment_value rows for the order; may be NULL when no payment matched)
   - status VARCHAR (lowercase order status)

Metric definitions:
- Delivered revenue: SUM(amount) for rows where status = 'delivered'. Exclude NULL amounts; do not count other statuses as revenue.
- Delivered order count: COUNT(DISTINCT order_id) where status = 'delivered'.
- Average order value: delivered revenue divided by the distinct delivered order count with a non-NULL amount.
- Customer spend: SUM(amount) for delivered orders belonging to that customer.
- Order count by status: COUNT(DISTINCT order_id) grouped by status, including all statuses unless the question filters them.
- Monthly revenue: delivered revenue grouped by DATE_TRUNC('month', order_date).
- For year, quarter, month, or week buckets, use DATE_TRUNC so the result is a consistent date/time bucket. Use EXTRACT only when the user explicitly asks for a numeric date part.
- The `country` column is actually city data. For city questions, use it but alias the output as customer_city. Do not present it as country-level data.
- For state breakdowns, join gold_dim_customer.state to gold_fact_sales using customer_id.
- Each row in gold_fact_sales represents one order. Use COUNT(DISTINCT order_id) for counts and do not join payment details (already aggregated into amount).
- For Top-N results, order by the requested metric first and add a stable tie-breaker (customer_id ASC for customer lists). Ties at the cutoff may produce multiple equally valid answers.
- Before returning SQL, check that every selected non-aggregate field is grouped and every referenced field exists in the schema.

Examples:
Question: What is total delivered revenue?
SQL: SELECT SUM(amount) AS delivered_revenue FROM gold_fact_sales WHERE status = 'delivered' AND amount IS NOT NULL LIMIT 500;

Question: Show monthly delivered revenue.
SQL: SELECT DATE_TRUNC('month', order_date) AS order_month, SUM(amount) AS delivered_revenue FROM gold_fact_sales WHERE status = 'delivered' AND amount IS NOT NULL GROUP BY 1 ORDER BY 1 LIMIT 500;

Question: Who are the 10 highest-spending customers?
SQL: SELECT c.customer_id, c.customer_name, SUM(f.amount) AS delivered_revenue FROM gold_dim_customer AS c JOIN gold_fact_sales AS f ON c.customer_id = f.customer_id WHERE f.status = 'delivered' AND f.amount IS NOT NULL GROUP BY c.customer_id, c.customer_name ORDER BY delivered_revenue DESC, c.customer_id ASC LIMIT 10;

Question: Show delivered order counts by year.
SQL: SELECT DATE_TRUNC('year', order_date) AS order_year, COUNT(DISTINCT order_id) AS delivered_order_count FROM gold_fact_sales WHERE status = 'delivered' GROUP BY 1 ORDER BY 1 LIMIT 500;

Question: Break delivered revenue down by state.
SQL: SELECT c.state, SUM(f.amount) AS delivered_revenue FROM gold_dim_customer AS c JOIN gold_fact_sales AS f ON c.customer_id = f.customer_id WHERE f.status = 'delivered' AND f.amount IS NOT NULL GROUP BY c.state ORDER BY delivered_revenue DESC LIMIT 500;

Question: How many orders are in each status?
SQL: SELECT status, COUNT(DISTINCT order_id) AS order_count FROM gold_fact_sales GROUP BY status ORDER BY order_count DESC LIMIT 500;

Question: What is the average order value for delivered orders?
SQL: SELECT SUM(amount) / NULLIF(COUNT(DISTINCT CASE WHEN amount IS NOT NULL THEN order_id END), 0) AS average_order_value FROM gold_fact_sales WHERE status = 'delivered' LIMIT 500;
"""


class QueryValidationError(ValueError):
    """Raised when generated SQL is outside the assistant's read-only policy."""


def validate_and_limit_sql(sql: str, max_rows: int = MAX_ROWS) -> str:
    """Parse generated SQL, enforce its read-only scope, and cap returned rows."""
    if max_rows < 1:
        raise ValueError("max_rows must be at least 1.")
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except ParseError as exc:
        raise QueryValidationError(f"Could not parse generated SQL: {exc}") from exc

    if len(statements) != 1 or statements[0] is None:
        raise QueryValidationError("Only one SQL statement is permitted.")

    query = statements[0]
    if not isinstance(query, exp.Select):
        raise QueryValidationError("Only a SELECT query is permitted.")

    cte_names = {cte.alias_or_name.lower() for cte in query.find_all(exp.CTE)}
    for table in query.find_all(exp.Table):
        table_name = table.name.lower()
        if table.db or table.catalog:
            raise QueryValidationError("Qualified tables and attached databases are not permitted.")
        if table_name not in ALLOWED_TABLES and table_name not in cte_names:
            raise QueryValidationError(f"Table '{table.name}' is not allowed.")

    blocked_function_names = {
        "ATTACH", "COPY", "GLOB", "PARQUET_SCAN", "QUERY", "QUERY_TABLE",
        "READ_BLOB", "READ_CSV", "READ_CSV_AUTO", "READ_JSON", "READ_PARQUET",
        "READ_TEXT", "READ_FILE", "READ_BINARY",
    }
    for node in query.walk():
        if not isinstance(node, exp.Func):
            continue
        name = node.name if isinstance(node, exp.Anonymous) else node.sql_name()
        normalized_name = str(name).upper()
        if normalized_name in blocked_function_names or normalized_name.startswith("READ_"):
            raise QueryValidationError(f"Function '{name}' is not permitted.")

    limit = query.args.get("limit")
    if limit is not None:
        limit_value = limit.expression
        if not isinstance(limit_value, exp.Literal) or limit_value.is_string:
            raise QueryValidationError("Use a numeric LIMIT value.")
        try:
            requested_rows = int(limit_value.this)
        except (TypeError, ValueError) as exc:
            raise QueryValidationError("Use a numeric LIMIT value.") from exc
        if requested_rows < 0:
            raise QueryValidationError("LIMIT cannot be negative.")
        if requested_rows > max_rows:
            limit.set("expression", exp.Literal.number(max_rows))
    else:
        query.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))

    return query.sql(dialect="duckdb")


def _execute_with_timeout(sql: str):
    """Run a read-only query and interrupt DuckDB if it exceeds the time budget."""
    conn = duckdb.connect(DATABASE_PATH, read_only=True)
    timed_out = threading.Event()

    def interrupt_query():
        timed_out.set()
        try:
            conn.interrupt()
        except Exception:
            # The query may have finished and the connection may already be closing.
            pass

    timer = threading.Timer(QUERY_TIMEOUT_SECONDS, interrupt_query)
    try:
        timer.start()
        result_df = conn.execute(sql).df()
        if timed_out.is_set():
            raise TimeoutError
        return result_df
    except Exception as exc:
        if timed_out.is_set():
            raise TimeoutError(
                f"Query exceeded the {QUERY_TIMEOUT_SECONDS}-second time limit."
            ) from exc
        raise
    finally:
        timer.cancel()
        conn.close()


def _clean_model_sql(raw_sql: str) -> str:
    sql = (raw_sql or "").strip()
    if sql.startswith("```"):
        lines = sql.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        sql = "\n".join(lines).strip()
    return sql


def _generate_sql(client, prompt: str) -> str:
    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=prompt,
    )
    return _clean_model_sql(response.text)


def generate_and_execute_sql(
    user_question: str,
    *,
    conversation_history: list[dict] | None = None,
    include_metadata: bool = False,
):
    metadata = {"repair_attempts": 0, "repair_succeeded": False}

    def result(sql: str, value):
        if include_metadata:
            return sql, value, metadata.copy()
        return sql, value

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return result("", "GEMINI_API_KEY is missing from environment variables.")

    try:
        client = genai.Client(api_key=api_key)
        context_lines = []
        for message in (conversation_history or [])[-6:]:
            if message.get("role") == "user":
                context_lines.append(f"Previous user question: {message.get('content', '')}")
            elif message.get("role") == "assistant" and message.get("sql"):
                context_lines.append(f"SQL used for that answer: {message['sql']}")
        context = "\n".join(context_lines)
        if context:
            prompt = (
                f"{SCHEMA_PROMPT}\nConversation context:\n{context}\n"
                "The new question may refer to the previous question or query. Resolve follow-ups using this context, "
                "preserve the prior metric and filters unless the user changes them, and generate SQL for the new request.\n"
                f"New user question: {user_question}\nSQL Query:"
            )
        else:
            prompt = f"{SCHEMA_PROMPT}\nUser Question: {user_question}\nSQL Query:"
        generated_sql = _generate_sql(client, prompt)
    except QueryValidationError as exc:
        return result("", f"SQL rejected: {exc}")
    except Exception as exc:
        return result("", f"Could not generate SQL: {exc}")

    for attempt in range(MAX_SQL_REPAIR_ATTEMPTS + 1):
        try:
            safe_sql = validate_and_limit_sql(generated_sql)
        except QueryValidationError as exc:
            return result("", f"SQL rejected: {exc}")

        try:
            query_result = _execute_with_timeout(safe_sql)
            metadata["repair_succeeded"] = attempt > 0
            return result(safe_sql, query_result)
        except TimeoutError as exc:
            # A timeout is a resource guard, not a syntax error the model can repair.
            return result(safe_sql, str(exc))
        except Exception as execution_error:
            if attempt >= MAX_SQL_REPAIR_ATTEMPTS:
                return result(
                    safe_sql,
                    f"Query execution failed after {metadata['repair_attempts']} repair attempts: {execution_error}",
                )

            repair_prompt = (
                f"{SCHEMA_PROMPT}\n"
                f"Original user question: {user_question}\n"
                f"SQL that failed in DuckDB:\n{safe_sql}\n"
                f"DuckDB error:\n{str(execution_error)[:2000]}\n\n"
                "Correct the SQL so it answers the original question. Use only the listed tables and columns. "
                "Return exactly one read-only SELECT query, with no markdown or explanation."
            )
            metadata["repair_attempts"] += 1
            try:
                generated_sql = _generate_sql(client, repair_prompt)
            except Exception as repair_error:
                return result(
                    safe_sql,
                    f"Query execution failed: {execution_error}. SQL repair request failed: {repair_error}",
                )

    return result("", "Query execution failed without a result.")
