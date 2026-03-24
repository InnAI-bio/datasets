import os
import re
import sqlite3
import sys
import uuid
from flask import Flask, jsonify, render_template, request, session
import anthropic

# ─── Config ────────────────────────────────────────────────────────────────

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
DB_PATH     = os.path.join(BASE_DIR, "flour_company.db")
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")
MODEL       = "claude-haiku-4-5"
MAX_HISTORY = 10  # conversation turns to keep per session

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
You will receive a conversation history followed by the latest user question.
Respond with ONLY one of:
  1. A valid SQLite SELECT statement — raw SQL, no markdown, no explanation.
  2. Exactly: NO_SQL  — if the question is off-topic (e.g. general baking advice).
  3. Exactly: CLARIFY: <your question>  — if the user's intent is genuinely ambiguous.

## When to Clarify
Ask CLARIFY only when the question has multiple distinct interpretations that would produce
different SQL queries. Examples:
- "sales" could mean total revenue (quantity × unit_price), units sold, or order count → CLARIFY
- "best products" could mean by revenue, by units, or by number of customers → CLARIFY
- "top products" without a metric specified → CLARIFY
- "recent" is vague but default to ORDER BY order_date DESC LIMIT 10 → no need to clarify
- "low stock" means quantity_kg < 100 → no need to clarify

Use the conversation history to resolve references like "those products", "that supplier",
"the same query", "also show me...". If a prior clarification already resolved the ambiguity,
use that answer — don't ask again.

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

Given the conversation history, the SQL that was run, and the result rows, write a clear,
friendly, concise answer in natural language.

Guidelines:
- Format prices as $X.XX and weights with units (e.g. 4,800 kg).
- If the result is empty, say so helpfully (e.g. "No matching records were found.").
- Do not mention SQL, column names, or table names in your answer.
- Write in full sentences. Be brief but complete.
- If results were truncated to 100 rows, mention that only the first 100 are shown.
- Reference prior answers naturally when relevant (e.g. "Compared to the previous result...").
"""

SQL_BLOCKLIST = {"insert", "update", "delete", "drop", "alter", "create", "attach"}

# ─── In-memory conversation store ──────────────────────────────────────────
# session_id -> list of {"user": str, "sql_response": str, "answer": str}
# sql_response is the raw Claude SQL output (SELECT..., NO_SQL, or CLARIFY: ...)
_conversations: dict = {}


def get_history(session_id: str) -> list:
    return _conversations.get(session_id, [])


def add_turn(session_id: str, user: str, sql_response: str, answer: str):
    turns = _conversations.setdefault(session_id, [])
    turns.append({"user": user, "sql_response": sql_response, "answer": answer})
    if len(turns) > MAX_HISTORY:
        _conversations[session_id] = turns[-MAX_HISTORY:]


def build_sql_messages(history: list, current_message: str) -> list:
    """Multi-turn messages for the SQL generation step.

    Each past turn contributes:
      user    → the user's original question
      assistant → the SQL Claude produced (or CLARIFY/NO_SQL)
    This lets Claude resolve references and honour prior clarifications.
    """
    messages = []
    for turn in history:
        messages.append({"role": "user", "content": turn["user"]})
        messages.append({"role": "assistant", "content": turn["sql_response"]})
    messages.append({"role": "user", "content": current_message})
    return messages


def build_format_prompt(history: list, message: str, sql_used: str, result_text: str) -> str:
    """Format prompt that includes recent Q&A history for context."""
    parts = []
    if history:
        parts.append("## Conversation so far")
        for turn in history[-5:]:
            parts.append(f"User: {turn['user']}")
            parts.append(f"Assistant: {turn['answer']}")
        parts.append("")
    parts += [
        f"User question: {message}",
        "",
        f"SQL executed: {sql_used or 'None (no database query needed)'}",
        "",
        f"Query results:\n{result_text}",
    ]
    return "\n".join(parts)


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
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24))


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "Message is required."}), 400

    # Get or create session ID
    session_id = session.get("session_id")
    if not session_id:
        session_id = str(uuid.uuid4())
        session["session_id"] = session_id

    history = get_history(session_id)

    # Step 1: NL → SQL (with conversation history)
    sql_response = client.messages.create(
        model=MODEL,
        max_tokens=400,
        system=SQL_SYSTEM_PROMPT,
        messages=build_sql_messages(history, message),
    )
    raw = sql_response.content[0].text.strip()

    # Strip markdown fences if present
    sql = re.sub(r"```(?:sql)?\s*", "", raw, flags=re.IGNORECASE).replace("```", "").strip()

    # Handle clarification request
    if sql.upper().startswith("CLARIFY:"):
        clarify_q = sql[len("CLARIFY:"):].strip()
        add_turn(session_id, message, sql, clarify_q)
        return jsonify({"answer": clarify_q, "sql": "", "row_count": 0})

    rows = []
    columns = []
    truncated = False
    sql_used = sql

    if sql.upper() == "NO_SQL":
        sql_used = ""
    else:
        # Safety check
        sql_lower = sql.lower()
        for blocked in SQL_BLOCKLIST:
            if blocked in sql_lower:
                return jsonify({"error": "That query is not permitted."}), 400

        # Execute
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

    # Step 4: Format response (with history context)
    result_text = (
        "No rows returned."
        if not rows
        else "\n".join(
            [", ".join(str(v) for v in row) for row in [columns] + rows]
        )
    )
    if truncated:
        result_text += "\n(Results limited to 100 rows.)"

    format_response = client.messages.create(
        model=MODEL,
        max_tokens=500,
        system=FORMAT_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_format_prompt(history, message, sql_used, result_text)}],
    )
    answer = format_response.content[0].text.strip()

    add_turn(session_id, message, sql_used or "NO_SQL", answer)

    return jsonify({"answer": answer, "sql": sql_used, "row_count": len(rows)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
