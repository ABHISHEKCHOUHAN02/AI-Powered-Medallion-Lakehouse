"""Choose and build a simple chart from the shape of a query result."""

import pandas as pd
import plotly.express as px


def _categorical_columns(df: pd.DataFrame):
    return [
        column for column in df.columns
        if pd.api.types.is_object_dtype(df[column])
        or pd.api.types.is_categorical_dtype(df[column])
        or pd.api.types.is_bool_dtype(df[column])
    ]


def _preferred_category(columns):
    if not columns:
        return None
    preferred_words = ("name", "category", "city", "state", "status", "country", "type")
    return sorted(
        columns,
        key=lambda column: (
            0 if any(word in str(column).lower() for word in preferred_words) else 1,
            1 if str(column).lower().endswith("id") or "_id" in str(column).lower() else 0,
        ),
    )[0]


def recommend_chart(df: pd.DataFrame) -> str:
    if df.empty:
        return "Table"

    numeric = list(df.select_dtypes(include="number").columns)
    if len(df) == 1 and numeric:
        return "Metric"
    if not numeric:
        return "Table"

    temporal = [
        column for column in df.columns
        if pd.api.types.is_datetime64_any_dtype(df[column])
        or any(token in str(column).lower() for token in ("date", "month", "week", "year", "period"))
    ]
    if temporal:
        return "Line"

    categorical = _categorical_columns(df)
    if categorical:
        return "Bar"
    if len(numeric) >= 2:
        return "Scatter"
    return "Table"


def build_chart(df: pd.DataFrame, chart_type: str):
    """Return a Plotly figure for a user-selected or recommended chart type."""
    if df.empty:
        return None

    numeric = list(df.select_dtypes(include="number").columns)
    if chart_type == "Line":
        if not numeric:
            return None
        temporal = [
            column for column in df.columns
            if pd.api.types.is_datetime64_any_dtype(df[column])
            or any(token in str(column).lower() for token in ("date", "month", "week", "year", "period"))
        ]
        x_column = temporal[0] if temporal else next(
            (column for column in df.columns if column not in numeric), None
        )
        if x_column is None:
            return None
        ordered = df.sort_values(x_column)
        return px.line(ordered, x=x_column, y=numeric, markers=True)

    if chart_type == "Bar":
        categorical = _categorical_columns(df)
        x_column = _preferred_category(categorical) if categorical else next(
            (column for column in df.columns if column not in numeric), None
        )
        if x_column is None or not numeric:
            return None
        plot_df = df.sort_values(numeric[0], ascending=False).head(20)
        return px.bar(plot_df, x=x_column, y=numeric, barmode="group")

    if chart_type == "Scatter" and len(numeric) >= 2:
        return px.scatter(df, x=numeric[0], y=numeric[1])

    return None
