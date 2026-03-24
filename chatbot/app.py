import os
import re
import sqlite3
import sys
from flask import Flask, jsonify, render_template, request
import anthropic

# ─── Config ────────────────────────────────────────────────────────────────

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
DB_PATH     = os.path.join(BASE_DIR, "flour_company.db")
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")
MODEL       = "claude-haiku-4-5"

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    sys.exit("ERROR: ANTHROPIC_API_KEY environment variable is not set.")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ─── DB Schema (embedded for Claude prompt) ────────────────────────────────

DB_SCHEMA = """
CREATE TABLE products (
    product_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL,   -- 'Wheat', 'Specialty', 'Gluten-Free'
    weight_kg   REAL NOT NULL,   -- package weight in kg
    price_usd   REAL NOT NULL,   -- retail price per package
    sku         TEXT UNIQUE NOT NULL
);

CREATE TABLE suppliers (
    supplier_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    contact_name    TEXT,
    email           TEXT,
    phone           TEXT,
    country         TEXT NOT NULL,
    lead_time_days  INTEGER NOT NULL
);

CREATE TABLE orders (
    order_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_name   TEXT NOT NULL,
    customer_email  TEXT,
    product_id      INTEGER NOT NULL REFERENCES products(product_id),
    quantity        INTEGER NOT NULL,
    unit_price      REAL NOT NULL,   -- price paid at order time
    order_date      TEXT NOT NULL,   -- YYYY-MM-DD
    status          TEXT NOT NULL    -- 'pending', 'shipped', 'delivered', 'cancelled'
);

CREATE TABLE inventory (
    inventory_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      INTEGER NOT NULL REFERENCES products(product_id),
    supplier_id     INTEGER NOT NULL REFERENCES suppliers(supplier_id),
    quantity_kg     REAL NOT NULL,
    warehouse       TEXT NOT NULL,
    last_updated    TEXT NOT NULL    -- YYYY-MM-DD
);
"""

SQL_SYSTEM_PROMPT = f"""You are an expert SQL assistant for a flour company database.

## Database Schema
{DB_SCHEMA}

## Column Notes
- products.category values: 'Wheat', 'Specialty', 'Gluten-Free'
- orders.status values: 'pending', 'shipped', 'delivered', 'cancelled'
- orders.unit_price is the price at order time; may differ from products.price_usd
- inventory.quantity_kg is total weight in kilograms currently on hand
- Dates are stored as ISO strings: YYYY-MM-DD

## Your Task
Given a user question, respond with ONLY a valid SQLite SELECT statement. No explanation,
no markdown fences, no commentary — raw SQL only.

If the question cannot be answered from this database (e.g. general baking advice),
respond with exactly: NO_SQL

## Rules
- Only use SELECT. Never use INSERT, UPDATE, DELETE, DROP, ALTER, or CREATE.
- Use LOWER() for case-insensitive text comparisons.
- "Low stock" means inventory.quantity_kg < 100.
- "Recent orders" means ORDER BY order_date DESC LIMIT 10.
- Join tables when the question spans multiple entities.
- Keep queries simple and correct.
"""

FORMAT_SYSTEM_PROMPT = """You are a helpful assistant for a flour company. You have queried
the company database to answer a user question.

Given the original question, the SQL that was run, and the result rows, write a clear,
friendly, concise answer in natural language.

Guidelines:
- Format prices as $X.XX and weights with units (e.g. 4,800 kg).
- If the result is empty, say so helpfully (e.g. "No matching records were found.").
- Do not mention SQL, column names, or table names in your answer.
- Write in full sentences. Be brief but complete.
- If results were truncated to 100 rows, mention that only the first 100 are shown.
"""

SQL_BLOCKLIST = {"insert", "update", "delete", "drop", "alter", "create", "attach"}

# ─── Database ───────────────────────────────────────────────────────────────

def init_db():
    if not os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        with open(SCHEMA_PATH) as f:
            conn.executescript(f.read())
        conn.commit()
        conn.close()

init_db()

# ─── Flask App ──────────────────────────────────────────────────────────────

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "Message is required."}), 400

    # Step 1: NL → SQL
    sql_response = client.messages.create(
        model=MODEL,
        max_tokens=300,
        system=SQL_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": message}],
    )
    raw = sql_response.content[0].text.strip()

    # Strip markdown fences if present
    sql = re.sub(r"```(?:sql)?\s*", "", raw, flags=re.IGNORECASE).replace("```", "").strip()

    rows = []
    columns = []
    truncated = False
    sql_used = sql

    if sql.upper() == "NO_SQL":
        sql_used = ""
    else:
        # Step 2: Safety check
        sql_lower = sql.lower()
        for blocked in SQL_BLOCKLIST:
            if blocked in sql_lower:
                return jsonify({"error": "That query is not permitted."}), 400

        # Step 3: Execute
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cur = conn.execute(sql)
            raw_rows = cur.fetchmany(101)
            conn.close()

            if len(raw_rows) > 100:
                raw_rows = raw_rows[:100]
                truncated = True

            if raw_rows:
                columns = list(raw_rows[0].keys())
                rows = [list(r) for r in raw_rows]
        except Exception as e:
            rows = []
            columns = []
            sql_used = f"[SQL error: {e}]"

    # Step 4: Format response
    result_text = (
        "No rows returned."
        if not rows
        else "\n".join(
            [", ".join(str(v) for v in row) for row in [columns] + rows]
        )
    )
    if truncated:
        result_text += "\n(Results limited to 100 rows.)"

    format_prompt = (
        f"User question: {message}\n\n"
        f"SQL executed: {sql_used or 'None (no database query needed)'}\n\n"
        f"Query results:\n{result_text}"
    )

    format_response = client.messages.create(
        model=MODEL,
        max_tokens=500,
        system=FORMAT_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": format_prompt}],
    )
    answer = format_response.content[0].text.strip()

    return jsonify({"answer": answer, "sql": sql_used, "row_count": len(rows)})


if __name__ == "__main__":
    app.run(debug=True)
