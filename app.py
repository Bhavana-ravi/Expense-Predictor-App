from __future__ import annotations

import os
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from functools import wraps
from typing import Any

from flask import Flask, jsonify, request, session, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash

ARIMA: Any | None = None
try:
    from statsmodels.tsa.arima.model import ARIMA
    HAS_ARIMA = True
except Exception:
    HAS_ARIMA = False

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("EXPENSE_DB_PATH", os.path.join(BASE, "expense_predictor.db"))
app = Flask(__name__, static_folder="static")
app.config.update(SECRET_KEY=os.environ.get("EXPENSE_SECRET", "change-this-local-secret"), SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

def connection():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    return db

def init_db():
    with connection() as db:
        db.executescript("""
          CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, full_name TEXT, email TEXT UNIQUE, high_spend_category TEXT, use_reason TEXT);
          CREATE TABLE IF NOT EXISTS expenses (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, amount REAL NOT NULL CHECK(amount > 0), category TEXT NOT NULL, expense_date TEXT NOT NULL, note TEXT, FOREIGN KEY(user_id) REFERENCES users(id));
          CREATE TABLE IF NOT EXISTS goals (id INTEGER PRIMARY KEY, user_id INTEGER UNIQUE NOT NULL, title TEXT NOT NULL, target_amount REAL NOT NULL CHECK(target_amount > 0), saved_amount REAL NOT NULL DEFAULT 0 CHECK(saved_amount >= 0), target_date TEXT, FOREIGN KEY(user_id) REFERENCES users(id));
          CREATE TABLE IF NOT EXISTS user_goals (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, title TEXT NOT NULL, target_amount REAL NOT NULL CHECK(target_amount > 0), saved_amount REAL NOT NULL DEFAULT 0 CHECK(saved_amount >= 0), target_date TEXT, FOREIGN KEY(user_id) REFERENCES users(id));
        """)
        # Existing-user-only demo account so the app is immediately usable.
        # Allow upgrades when an earlier local database already exists.
        for column in ("full_name TEXT", "email TEXT", "high_spend_category TEXT", "use_reason TEXT"):
            try: db.execute(f"ALTER TABLE users ADD COLUMN {column}")
            except sqlite3.OperationalError: pass
        db.execute("INSERT OR IGNORE INTO users(username,password_hash,full_name,email,high_spend_category,use_reason) VALUES(?,?,?,?,?,?)", ("demo", generate_password_hash("Expense@123"), "Demo User", "demo@example.local", "Groceries", "Plan a monthly budget"))
        # Preserve an older single goal if this local app is being upgraded.
        if db.execute("SELECT COUNT(*) FROM user_goals").fetchone()[0] == 0:
            for old_goal in db.execute("SELECT user_id,title,target_amount,saved_amount,target_date FROM goals").fetchall():
                db.execute("INSERT INTO user_goals(user_id,title,target_amount,saved_amount,target_date) VALUES(?,?,?,?,?)", tuple(old_goal))

def logged_in(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("uid"):
            return jsonify(error="Please sign in to continue."), 401
        return fn(*args, **kwargs)
    return wrapped

@app.post("/api/login")
def login():
    body = request.get_json(silent=True) or {}
    username, password = str(body.get("username", "")).strip(), str(body.get("password", ""))
    if not username or not password:
        return jsonify(error="Enter both your username and password."), 400
    with connection() as db:
        user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify(error="This account is not registered, or the password is incorrect."), 401
    session.clear(); session["uid"] = user["id"]; session["username"] = user["username"]
    return jsonify(username=user["username"])

@app.post("/api/signup")
def signup():
    body = request.get_json(silent=True) or {}
    full_name = str(body.get("full_name", "")).strip()[:80]
    email = str(body.get("email", "")).strip().lower()[:120]
    username = str(body.get("username", "")).strip()[:50]
    password = str(body.get("password", ""))
    category = str(body.get("high_spend_category", "")).strip()[:50]
    reason = str(body.get("use_reason", "")).strip()[:200]
    if not full_name or "@" not in email or not username or len(password) < 8 or not category or not reason:
        return jsonify(error="Complete every field. Use a valid email and a password of at least 8 characters."), 400
    if not username.replace("_", "").replace("-", "").isalnum():
        return jsonify(error="Username may only use letters, numbers, hyphens, and underscores."), 400
    try:
        with connection() as db:
            cur = db.execute("INSERT INTO users(username,password_hash,full_name,email,high_spend_category,use_reason) VALUES(?,?,?,?,?,?)", (username, generate_password_hash(password), full_name, email, category, reason))
            uid = cur.lastrowid
    except sqlite3.IntegrityError:
        return jsonify(error="That username or email is already registered."), 409
    session.clear(); session["uid"] = uid; session["username"] = username
    return jsonify(username=username), 201

@app.post("/api/logout")
def logout():
    session.clear(); return jsonify(ok=True)

@app.get("/api/me")
def me():
    return jsonify(authenticated=bool(session.get("uid")), username=session.get("username"))

@app.get("/api/expenses")
@logged_in
def expenses():
    with connection() as db:
        rows = db.execute("SELECT id, amount, category, expense_date, note FROM expenses WHERE user_id=? ORDER BY expense_date DESC, id DESC", (session["uid"],)).fetchall()
    return jsonify([dict(r) for r in rows])

@app.get("/api/goals")
@logged_in
def get_goals_data():
    with connection() as db:
        rows = db.execute("SELECT id,title,target_amount,saved_amount,target_date FROM user_goals WHERE user_id=? ORDER BY id DESC", (session["uid"],)).fetchall()
    return jsonify([dict(row) for row in rows])

@app.post("/api/goals")
@logged_in
def create_goal():
    body = request.get_json(silent=True) or {}
    title = str(body.get("title", "")).strip()[:80]
    try:
        target = round(float(str(body.get("target_amount", "")).replace(",", "")), 2)
        saved = round(float(str(body.get("saved_amount", 0)).replace(",", "")), 2)
        target_date = datetime.strptime(str(body.get("target_date")), "%Y-%m-%d").date().isoformat() if body.get("target_date") else None
    except (TypeError, ValueError):
        return jsonify(error="Use valid goal amounts and an optional target date."), 400
    if not title or target <= 0 or saved < 0 or saved > target:
        return jsonify(error="Enter a goal name, a positive target, and a saved amount no greater than the target."), 400
    with connection() as db:
        db.execute("INSERT INTO user_goals(user_id,title,target_amount,saved_amount,target_date) VALUES(?,?,?,?,?)", (session["uid"],title,target,saved,target_date))
    return jsonify(ok=True)

@app.post("/api/goals/<int:goal_id>/save")
@logged_in
def add_goal_savings(goal_id):
    body = request.get_json(silent=True) or {}
    try: amount = round(float(str(body.get("amount", "")).replace(",", "")), 2)
    except (TypeError, ValueError): return jsonify(error="Enter a valid savings amount."), 400
    if amount <= 0: return jsonify(error="Savings amount must be greater than zero."), 400
    with connection() as db:
        result = db.execute("UPDATE user_goals SET saved_amount=saved_amount+? WHERE id=? AND user_id=? AND saved_amount+?<=target_amount", (amount, goal_id, session["uid"], amount))
    if not result.rowcount: return jsonify(error="That amount would exceed the goal target, or the goal was not found."), 400
    return jsonify(ok=True)

@app.post("/api/expenses")
@logged_in
def add_expense():
    body = request.get_json(silent=True) or {}
    try:
        amount = round(float(str(body.get("amount", "")).replace(",", "")), 2)
        expense_date = datetime.strptime(str(body.get("expense_date")), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return jsonify(error="Use a valid amount and date."), 400
    category = str(body.get("category", "")).strip()[:40]
    if not 0 < amount <= 10000000 or not category or expense_date > date.today():
        return jsonify(error="Enter a positive amount, a category, and a date that is not in the future."), 400
    with connection() as db:
        db.execute("INSERT INTO expenses(user_id,amount,category,expense_date,note) VALUES(?,?,?,?,?)", (session["uid"], amount, category, expense_date.isoformat(), str(body.get("note", "")).strip()[:160]))
    return jsonify(ok=True), 201

@app.delete("/api/expenses/<int:expense_id>")
@logged_in
def delete_expense(expense_id):
    with connection() as db:
        db.execute("DELETE FROM expenses WHERE id=? AND user_id=?", (expense_id, session["uid"]))
    return jsonify(ok=True)

@app.post("/api/sample-week")
@logged_in
def sample_week():
    samples = [("Groceries", 680, "Weekly market"), ("Transport", 140, "Metro and auto"), ("Coffee & dining", 245, "Lunch with friends"), ("Utilities", 510, "Mobile recharge"), ("Groceries", 390, "Fresh produce"), ("Entertainment", 180, "Movie night"), ("Health", 325, "Pharmacy")]
    with connection() as db:
        existing = db.execute("SELECT COUNT(*) FROM expenses WHERE user_id=?", (session["uid"],)).fetchone()[0]
        if existing: return jsonify(error="Sample data can only be added to an empty expense list."), 409
        for index, (category, amount, note) in enumerate(samples):
            d = date.today() - timedelta(days=6-index)
            db.execute("INSERT INTO expenses(user_id,amount,category,expense_date,note) VALUES(?,?,?,?,?)", (session["uid"], amount, category, d.isoformat(), note))
    return jsonify(ok=True)

def forecast(rows):
    if not rows: return {"has_data": False}
    daily = defaultdict(float)
    for r in rows: daily[r["expense_date"]] += r["amount"]
    end = date.today(); start = end - timedelta(days=27)
    current = [round(daily[(start + timedelta(days=i)).isoformat()], 2) for i in range(28)]
    recent = current[-7:]
    # Custom weekly heuristic: weighted recent spend adjusted by observed weekly direction.
    first, last = sum(current[-14:-7]), sum(recent)
    trend = (last - first) / 7 if first else 0
    weekly = [max(0, round((recent[i % 7] * .65 + sum(recent)/7 * .35) + trend * (i+1) * .25, 2)) for i in range(7)]
    monthly = []
    if len([x for x in current if x]) >= 5 and HAS_ARIMA and ARIMA is not None:
        try:
            model = ARIMA(current, order=(1, 0, 1)).fit()
            monthly = [max(0, round(float(x), 2)) for x in model.forecast(30)]
        except Exception: monthly = []
    if not monthly:
        avg = sum(current) / 28
        monthly = [round(max(0, avg + trend * (i+1) * .2), 2) for i in range(30)]
    weekday_totals = [0.0] * 7
    weekday_counts = [0] * 7
    for index, amount in enumerate(current):
        weekday = (start + timedelta(days=index)).weekday()
        weekday_totals[weekday] += amount
        weekday_counts[weekday] += 1
    for index, amount in enumerate(monthly):
        weekday = (end + timedelta(days=index + 1)).weekday()
        weekday_average = weekday_totals[weekday] / weekday_counts[weekday]
        recent_same_weekday = recent[index % len(recent)]
        weekday_pattern = (recent_same_weekday + weekday_average) / 2
        monthly[index] = round(max(0, amount * .25 + weekday_pattern * .75), 2)
    categories = defaultdict(float)
    for r in rows: categories[r["category"]] += r["amount"]
    active_days = sum(1 for x in current if x > 0)
    confidence = "High" if active_days >= 20 else "Medium" if active_days >= 10 else "Building"
    week_start = end + timedelta(days=1)
    month_start = week_start
    category_list = [{"name": k, "amount": round(v,2)} for k,v in sorted(categories.items(), key=lambda x:x[1], reverse=True)]
    discretionary = sum(v for k,v in categories.items() if k in {"Dining", "Coffee & dining", "Entertainment", "Shopping", "Other"})
    weekly_reduce = round(min(sum(weekly) * .12, discretionary * .25), 2)
    monthly_reduce = round(min(sum(monthly) * .12, discretionary), 2)
    return {"has_data": True, "current": current, "weekly": weekly, "monthly": monthly, "current_total": round(sum(current),2), "weekly_total": round(sum(weekly),2), "monthly_total": round(sum(monthly),2), "categories": category_list, "arima": HAS_ARIMA, "confidence": confidence, "active_days": active_days, "week_dates": [(week_start + timedelta(days=i)).isoformat() for i in range(7)], "month_dates": [(month_start + timedelta(days=i)).isoformat() for i in range(30)], "weekly_reduce": weekly_reduce, "monthly_reduce": monthly_reduce}

@app.get("/api/predictions")
@logged_in
def predictions():
    with connection() as db:
        rows = db.execute("SELECT amount,category,expense_date FROM expenses WHERE user_id=?", (session["uid"],)).fetchall()
    return jsonify(forecast(rows))

@app.get("/")
def index(): return send_from_directory(app.static_folder, "index.html")

init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True, use_reloader=False)
