-- Flour Company Database Schema + Seed Data

-- TABLE 1: Products
CREATE TABLE IF NOT EXISTS products (
    product_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL,   -- 'Wheat', 'Specialty', 'Gluten-Free'
    weight_kg   REAL NOT NULL,   -- package weight in kg
    price_usd   REAL NOT NULL,   -- retail price per package
    sku         TEXT UNIQUE NOT NULL
);

-- TABLE 2: Suppliers
CREATE TABLE IF NOT EXISTS suppliers (
    supplier_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    contact_name    TEXT,
    email           TEXT,
    phone           TEXT,
    country         TEXT NOT NULL,
    lead_time_days  INTEGER NOT NULL  -- typical delivery lead time
);

-- TABLE 3: Orders (includes customer info)
CREATE TABLE IF NOT EXISTS orders (
    order_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_name   TEXT NOT NULL,
    customer_email  TEXT,
    product_id      INTEGER NOT NULL REFERENCES products(product_id),
    quantity        INTEGER NOT NULL,
    unit_price      REAL NOT NULL,   -- price at time of order
    order_date      TEXT NOT NULL,   -- ISO 8601: YYYY-MM-DD
    status          TEXT NOT NULL    -- 'pending', 'shipped', 'delivered', 'cancelled'
);

-- TABLE 4: Inventory
CREATE TABLE IF NOT EXISTS inventory (
    inventory_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      INTEGER NOT NULL REFERENCES products(product_id),
    supplier_id     INTEGER NOT NULL REFERENCES suppliers(supplier_id),
    quantity_kg     REAL NOT NULL,   -- total kg currently on hand
    warehouse       TEXT NOT NULL,
    last_updated    TEXT NOT NULL    -- ISO 8601: YYYY-MM-DD
);

-- ─── SEED DATA ─────────────────────────────────────────────────────────────

INSERT INTO products (name, category, weight_kg, price_usd, sku) VALUES
    ('All-Purpose Flour',  'Wheat',       2.0,  3.49, 'APF-2KG'),
    ('Bread Flour',        'Wheat',       5.0,  7.99, 'BRD-5KG'),
    ('Whole Wheat Flour',  'Wheat',       2.0,  4.29, 'WWF-2KG'),
    ('Cake Flour',         'Specialty',   1.0,  3.99, 'CAK-1KG'),
    ('Rye Flour',          'Specialty',   2.0,  5.49, 'RYE-2KG'),
    ('Almond Flour',       'Gluten-Free', 0.5,  8.99, 'ALM-500G'),
    ('Rice Flour',         'Gluten-Free', 1.0,  4.49, 'RIC-1KG'),
    ('Semolina',           'Specialty',   2.0,  4.99, 'SEM-2KG');

INSERT INTO suppliers (name, contact_name, email, phone, country, lead_time_days) VALUES
    ('GrainSource Co.',    'Jane Harlow',  'jane@grainsource.com',  '+1-800-555-0101', 'USA',     5),
    ('Nordic Mills',       'Erik Strand',  'erik@nordicmills.se',   '+46-8-555-0202',  'Sweden',  14),
    ('Southern Grain Ltd.','Maria Santos', 'maria@sgrain.com.br',   '+55-11-555-0303', 'Brazil',  21),
    ('PureMill Organics',  'Lena Fischer', 'lena@puremill.de',      '+49-30-555-0404', 'Germany', 10);

INSERT INTO orders (customer_name, customer_email, product_id, quantity, unit_price, order_date, status) VALUES
    ('Alice Johnson',  'alice@example.com',  1, 20, 3.49, '2025-10-05', 'delivered'),
    ('Bob Martinez',   'bob@example.com',    2, 10, 7.99, '2025-10-12', 'delivered'),
    ('Alice Johnson',  'alice@example.com',  3, 15, 4.29, '2025-11-01', 'shipped'),
    ('Carol Lee',      'carol@example.com',  6,  5, 8.99, '2025-11-08', 'delivered'),
    ('David Kim',      'david@example.com',  4, 30, 3.99, '2025-11-14', 'pending'),
    ('Bob Martinez',   'bob@example.com',    5, 12, 5.49, '2025-11-20', 'pending'),
    ('Carol Lee',      'carol@example.com',  1, 25, 3.49, '2025-12-02', 'shipped'),
    ('Eve Turner',     'eve@example.com',    7, 18, 4.49, '2025-12-10', 'cancelled'),
    ('David Kim',      'david@example.com',  8, 10, 4.99, '2025-12-18', 'pending'),
    ('Alice Johnson',  'alice@example.com',  2,  8, 7.99, '2026-01-05', 'pending'),
    ('Frank Nguyen',   'frank@example.com',  6,  3, 8.99, '2026-01-15', 'shipped'),
    ('Eve Turner',     'eve@example.com',    3, 20, 4.29, '2026-02-01', 'cancelled');

INSERT INTO inventory (product_id, supplier_id, quantity_kg, warehouse, last_updated) VALUES
    (1, 1, 4800.0, 'North Warehouse',  '2026-03-01'),
    (2, 1, 2200.0, 'North Warehouse',  '2026-03-01'),
    (3, 4, 1350.0, 'East Warehouse',   '2026-03-05'),
    (4, 2,  620.0, 'East Warehouse',   '2026-03-05'),
    (5, 2,   85.0, 'East Warehouse',   '2026-03-10'),  -- low stock
    (6, 4,   42.0, 'Cold Storage',     '2026-03-10'),  -- low stock
    (7, 3,  310.0, 'South Warehouse',  '2026-03-12'),
    (8, 3,  780.0, 'South Warehouse',  '2026-03-12');
