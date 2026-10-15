# AI-Powered Medallion Lakehouse

## Text-to-SQL evaluation

The benchmark in `eval/golden_questions.json` contains 30 natural-language questions with trusted SQL queries. `src/evaluate_agent.py` asks the configured Gemini model each question, sends the response through the production SQL validator and DuckDB execution path, then compares returned values with the trusted query results. Requests are spaced five seconds apart by default to respect the Gemini free-tier request limit.

Run it from the project root:

```powershell
uv run python -m src.evaluate_agent --output eval/reports/baseline.json
```

The report records validation pass rate, execution accuracy, result-match rate, result-match rate among executed queries, request failures, and mean latency. Result comparison ignores row order and column aliases and rounds floating-point values to six decimal places. Scores use all 30 questions as the denominator; the conditional result-match score uses successfully executed queries.

### Baseline

The baseline report is `eval/reports/baseline.json`. It was measured with the schema prompt, metric definitions, few-shot examples, SQLGlot validator, and DuckDB execution limits before the prompt refinements and SQL repair loop.

| Measure | Baseline |
| --- | ---: |
| Questions | 30 |
| Validation pass rate | 100% |
| Execution accuracy | 100% |
| Exact result-match rate | 90% (27/30) |
| Result match among executed queries | 90% |
| Mean latency | 2.3 s |

The updated report is `eval/reports/after_prompt_repair.json`.

| Measure | Baseline | Updated | Change |
| --- | ---: | ---: | ---: |
| Validation pass rate | 100% | 100% | no change |
| Execution accuracy | 100% | 100% | no change |
| Exact result-match rate | 90% (27/30) | 93.3% (28/30) | +3.3 percentage points |
| Mean latency | 2.3 s | 5.4 s | +3.1 s |
| SQL repair attempts | not recorded | 0 | no repair triggered |

The refined prompt fixed the year-bucket output mismatch by preferring `DATE_TRUNC` for time buckets. Two exact-result mismatches remain: top-customer queries can return different but equally ranked customers when counts tie at the cutoff. The benchmark had no DuckDB execution failures, so it measured no repair-loop recovery; it confirms the existing 100% execution rate remained intact. Latency varies with model response time.

## Conversational results

The assistant keeps the last three exchanges of questions and executed SQL in Streamlit session state so follow-ups can reuse prior context. Query results receive an automatic chart suggestion based on their columns (time buckets → line, category plus measure → bar, single-value result → metric). The chart selector lets users switch between Auto, Line, Bar, Scatter, and Table; the underlying result table remains available for inspection.
