from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import sqlite3

from flask import Flask, jsonify, request, send_from_directory

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
DATABASE = PROJECT / "data" / "stockpulse.db"
WEB = PROJECT / "frontend"
WORKERS = ThreadPoolExecutor(max_workers=2)
app = Flask(__name__)

SEED_PRODUCTS = [
    ("PRD-001", "SKU-ELEC-001", "Wireless Earbuds Pro", "ELECTRONICS", 79.99, 45, 20, 3),
    ("PRD-002", "SKU-ELEC-002", "USB-C Hub 7-Port", "ELECTRONICS", 34.99, 120, 30, 1),
    ("PRD-003", "SKU-APP-001", "Organic Cotton T-Shirt", "APPAREL", 24.99, 8, 15, 12),
    ("PRD-004", "SKU-APP-002", "Running Shorts - Navy", "APPAREL", 39.99, 55, 20, 2),
    ("PRD-005", "SKU-HOME-001", "Ceramic Pour-Over Set", "HOME", 49.99, 22, 10, 4),
    ("PRD-006", "SKU-HOME-002", "LED Desk Lamp - Dimmable", "HOME", 59.99, 0, 15, 0),
    ("PRD-007", "SKU-ELEC-003", "Portable Charger 20K", "ELECTRONICS", 44.99, 18, 25, 8),
    ("PRD-008", "SKU-APP-003", "Hoodie - Heather Grey", "APPAREL", 54.99, 11, 12, 15),
]


@contextmanager
def connect_db():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATABASE, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def setup_database():
    with connect_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS products (
                id TEXT PRIMARY KEY, sku TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                category TEXT NOT NULL, current_price REAL NOT NULL,
                stock_level INTEGER NOT NULL, reorder_threshold INTEGER NOT NULL,
                demand_velocity REAL NOT NULL, status TEXT NOT NULL DEFAULT 'ACTIVE',
                cost_price REAL, margin_floor REAL
            );
            CREATE TABLE IF NOT EXISTS suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, product_id TEXT NOT NULL,
                kind TEXT NOT NULL, current_price REAL, recommended_price REAL,
                current_stock INTEGER, recommended_quantity INTEGER, lead_time_days INTEGER,
                confidence REAL NOT NULL, reasoning TEXT NOT NULL,
                trigger_reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING',
                FOREIGN KEY(product_id) REFERENCES products(id)
            );
        """)
        if db.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
            for row in SEED_PRODUCTS:
                product_id, sku, name, category, price, stock, threshold, velocity = row
                status = "OUT_OF_STOCK" if stock == 0 else "ACTIVE"
                db.execute("""INSERT INTO products
                    (id,sku,name,category,current_price,stock_level,reorder_threshold,
                     demand_velocity,status,cost_price,margin_floor)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (product_id, sku, name, category, price, stock, threshold,
                     velocity, status, round(price * .5, 2), .15))
            make_suggestions(db, "PRD-003", "INVENTORY_LOW")


def make_suggestions(db, product_id, trigger):
    product = db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    if product is None:
        return
    avg = db.execute("SELECT AVG(demand_velocity) FROM products WHERE category=? AND id<>?",
                     (product["category"], product_id)).fetchone()[0] or 0
    price_exists = db.execute("""SELECT 1 FROM suggestions WHERE product_id=?
        AND kind='PRICE' AND trigger_reason=? AND status='PENDING'""",
        (product_id, trigger)).fetchone()
    if not price_exists:
        if product["stock_level"] < product["reorder_threshold"]:
            new_price = round(product["current_price"] * 1.10, 2)
            reason = "Stock is below its reorder point. A modest increase can protect remaining inventory."
            confidence = .72
        elif avg and product["demand_velocity"] > avg * 2:
            new_price = round(product["current_price"] * 1.05, 2)
            reason = "Demand is running above the category average. A small increase may capture some value."
            confidence = .68
        else:
            new_price = product["current_price"]
            reason = "There is no strong inventory or demand signal to change the current price."
            confidence = .65
        direction = "INCREASE" if new_price > product["current_price"] else "HOLD"
        db.execute("""INSERT INTO suggestions
            (product_id,kind,current_price,recommended_price,confidence,reasoning,trigger_reason)
            VALUES (?,'PRICE',?,?,?,?,?)""",
            (product_id, product["current_price"], new_price, confidence,
             f"{direction}: {reason}", trigger))
    reorder_exists = db.execute("""SELECT 1 FROM suggestions WHERE product_id=?
        AND kind='REORDER' AND trigger_reason=? AND status='PENDING'""",
        (product_id, trigger)).fetchone()
    if not reorder_exists:
        quantity = max(1, product["reorder_threshold"] * 3 - product["stock_level"])
        db.execute("""INSERT INTO suggestions
            (product_id,kind,current_stock,recommended_quantity,lead_time_days,
             confidence,reasoning,trigger_reason)
            VALUES (?,'REORDER',?,?,7,.70,?,?)""",
            (product_id, product["stock_level"], quantity,
             "Rule: replenish to three times the reorder threshold.", trigger))
    if product["stock_level"] > 0:
        db.execute("UPDATE products SET status='PRICE_REVIEW_PENDING' WHERE id=?", (product_id,))


def build_suggestions(product_id, trigger):
    with connect_db() as db:
        make_suggestions(db, product_id, trigger)


def product_list():
    status = request.args.get("status")
    category = request.args.get("category")
    query = "SELECT * FROM products WHERE 1=1"
    values = []
    if status:
        query += " AND status=?"
        values.append(status.upper())
    if category and category.upper() != "ALL":
        query += " AND category=?"
        values.append(category.upper())
    with connect_db() as db:
        return jsonify([dict(row) for row in db.execute(query + " ORDER BY id", values)])


@app.get("/")
def home():
    return send_from_directory(WEB, "index.html")


@app.get("/<path:filename>")
def frontend_file(filename):
    return send_from_directory(WEB, filename)


@app.get("/api/health")
def health():
    return jsonify(status="ok", app="StockPulse", database="SQLite")


@app.get("/products")
def list_products():
    return product_list()


@app.post("/products")
def add_product():
    data = request.get_json(silent=True) or {}
    try:
        name = str(data["name"]).strip()
        sku = str(data["sku"]).strip()
        category = str(data["category"]).upper()
        price = float(data["currentPrice"])
        stock = int(data["stockLevel"])
        threshold = int(data["reorderThreshold"])
        if not name or not sku or category not in {"ELECTRONICS", "APPAREL", "HOME"}:
            raise ValueError("Enter a name, SKU, and valid category.")
        if price <= 0 or stock < 0 or threshold < 1:
            raise ValueError("Price must be positive, stock cannot be negative, and threshold must be at least one.")
        with connect_db() as db:
            product_id = "PRD-" + sku[-8:].upper()
            db.execute("""INSERT INTO products
                (id,sku,name,category,current_price,stock_level,reorder_threshold,
                 demand_velocity,status,cost_price,margin_floor)
                VALUES (?,?,?,?,?,?,?,0,?,?,?)""",
                (product_id, sku, name, category, price, stock, threshold,
                 "OUT_OF_STOCK" if stock == 0 else "ACTIVE", price * .5, .15))
            return jsonify(dict(db.execute("SELECT * FROM products WHERE id=?",
                                           (product_id,)).fetchone())), 201
    except (KeyError, TypeError, ValueError, sqlite3.IntegrityError) as error:
        return jsonify(error=str(error) or "Invalid product data."), 400


@app.post("/products/<product_id>/orders")
def place_order(product_id):
    data = request.get_json(silent=True) or {}
    try:
        quantity = int(data.get("quantity", 1))
        if quantity < 1:
            raise ValueError("Sale quantity must be at least one.")
        with connect_db() as db:
            product = db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
            if product is None:
                return jsonify(error="Product not found."), 404
            if quantity > product["stock_level"]:
                return jsonify(error="Sale quantity is greater than available stock."), 400
            stock = product["stock_level"] - quantity
            velocity = product["demand_velocity"] + quantity
            status = "OUT_OF_STOCK" if stock == 0 else product["status"]
            db.execute("UPDATE products SET stock_level=?,demand_velocity=?,status=? WHERE id=?",
                       (stock, velocity, status, product_id))
            average = db.execute("SELECT AVG(demand_velocity) FROM products WHERE category=? AND id<>?",
                                 (product["category"], product_id)).fetchone()[0] or 0
            triggers = []
            if stock < product["reorder_threshold"]:
                triggers.append("INVENTORY_LOW")
            if velocity > max(3, average * 3):
                triggers.append("DEMAND_SPIKE")
        for trigger in triggers:
            WORKERS.submit(build_suggestions, product_id, trigger)
        return jsonify(id=product_id, stockLevel=stock, demandVelocity=velocity,
                       triggerReason=", ".join(triggers) or None,
                       message="Sale recorded. Suggestions are processing.")
    except (TypeError, ValueError) as error:
        return jsonify(error=str(error)), 400


@app.patch("/products/<product_id>/stock")
def update_stock(product_id):
    data = request.get_json(silent=True) or {}
    try:
        stock = int(data["stockLevel"])
        if stock < 0:
            raise ValueError("Stock cannot be negative.")
        with connect_db() as db:
            product = db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
            if product is None:
                return jsonify(error="Product not found."), 404
            status = "OUT_OF_STOCK" if stock == 0 else product["status"]
            db.execute("UPDATE products SET stock_level=?,status=? WHERE id=?",
                       (stock, status, product_id))
            low = stock < product["reorder_threshold"]
        if low:
            WORKERS.submit(build_suggestions, product_id, "INVENTORY_LOW")
        return jsonify(id=product_id, stockLevel=stock)
    except (KeyError, TypeError, ValueError) as error:
        return jsonify(error=str(error)), 400


@app.get("/suggestions")
def list_suggestions():
    status = request.args.get("status", "PENDING").upper()
    with connect_db() as db:
        rows = db.execute("""SELECT s.*,p.name AS product_name,p.sku
            FROM suggestions s JOIN products p ON p.id=s.product_id
            WHERE s.status=? ORDER BY s.id DESC""", (status,)).fetchall()
        return jsonify([dict(row) for row in rows])


@app.patch("/suggestions/<int:suggestion_id>")
def decide_suggestion(suggestion_id):
    data = request.get_json(silent=True) or {}
    action = str(data.get("action", "")).upper()
    if action not in {"ACCEPT", "REJECT"}:
        return jsonify(error="Action must be accept or reject."), 400
    with connect_db() as db:
        suggestion = db.execute("SELECT * FROM suggestions WHERE id=?",
                                (suggestion_id,)).fetchone()
        if suggestion is None:
            return jsonify(error="Suggestion not found."), 404
        if suggestion["status"] != "PENDING":
            return jsonify(error="This suggestion was already reviewed."), 400
        db.execute("UPDATE suggestions SET status=? WHERE id=?", (action + "ED", suggestion_id))
        if action == "ACCEPT" and suggestion["kind"] == "PRICE":
            db.execute("UPDATE products SET current_price=? WHERE id=?",
                       (suggestion["recommended_price"], suggestion["product_id"]))
        elif action == "ACCEPT":
            db.execute("UPDATE products SET stock_level=stock_level+? WHERE id=?",
                       (suggestion["recommended_quantity"], suggestion["product_id"]))
        still_open = db.execute("SELECT 1 FROM suggestions WHERE product_id=? AND status='PENDING'",
                                (suggestion["product_id"],)).fetchone()
        if not still_open:
            db.execute("""UPDATE products SET status=CASE WHEN stock_level=0
                THEN 'OUT_OF_STOCK' ELSE 'ACTIVE' END WHERE id=?""",
                (suggestion["product_id"],))
        return jsonify(dict(db.execute("SELECT * FROM suggestions WHERE id=?",
                                       (suggestion_id,)).fetchone()))


@app.post("/products/<product_id>/suggest-pricing")
@app.post("/products/<product_id>/suggest-reorder")
def manual_suggestion(product_id):
    trigger = request.args.get("triggerReason", "MANUAL").upper()
    if trigger not in {"INITIAL", "INVENTORY_LOW", "DEMAND_SPIKE", "MANUAL"}:
        return jsonify(error="Invalid trigger reason."), 400
    with connect_db() as db:
        if not db.execute("SELECT 1 FROM products WHERE id=?", (product_id,)).fetchone():
            return jsonify(error="Product not found."), 404
        make_suggestions(db, product_id, trigger)
    return jsonify(message="Rule suggestions queued.", triggerReason=trigger), 202


@app.errorhandler(Exception)
def server_error(error):
    app.logger.exception("Unexpected request error", exc_info=error)
    return jsonify(error="Something went wrong. Please try again."), 500


setup_database()

if __name__ == "__main__":
    print(f"StockPulse is ready. SQLite data: {DATABASE}")
    app.run(host="127.0.0.1", port=5000, debug=False)
