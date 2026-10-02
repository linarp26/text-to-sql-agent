-- E-commerce analytics schema.
-- COMMENT ON statements are read by app/schema.py and injected into the LLM prompt,
-- so keep them accurate: they are the agent's documentation of the data.

CREATE TABLE customers (
    customer_id   SERIAL PRIMARY KEY,
    first_name    TEXT NOT NULL,
    last_name     TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    signup_date   DATE NOT NULL,
    country       TEXT NOT NULL,
    segment       TEXT NOT NULL CHECK (segment IN ('consumer', 'small_business', 'enterprise'))
);
COMMENT ON TABLE customers IS 'One row per registered customer.';
COMMENT ON COLUMN customers.signup_date IS 'Date the customer created their account.';
COMMENT ON COLUMN customers.country IS 'Customer home country (full English name, e.g. ''United States'').';
COMMENT ON COLUMN customers.segment IS 'Customer segment: consumer, small_business or enterprise.';

CREATE TABLE addresses (
    address_id    SERIAL PRIMARY KEY,
    customer_id   INT NOT NULL REFERENCES customers(customer_id),
    city          TEXT NOT NULL,
    state         TEXT,
    country       TEXT NOT NULL,
    postal_code   TEXT NOT NULL,
    is_default    BOOLEAN NOT NULL DEFAULT FALSE
);
COMMENT ON TABLE addresses IS 'Shipping addresses. A customer can have several; exactly one has is_default = true.';

CREATE TABLE categories (
    category_id        SERIAL PRIMARY KEY,
    name               TEXT NOT NULL UNIQUE,
    parent_category_id INT REFERENCES categories(category_id)
);
COMMENT ON TABLE categories IS 'Product category tree. Top-level categories have parent_category_id NULL; subcategories point to their parent.';

CREATE TABLE products (
    product_id    SERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    category_id   INT NOT NULL REFERENCES categories(category_id),
    unit_price    NUMERIC(10,2) NOT NULL,
    unit_cost     NUMERIC(10,2) NOT NULL,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    DATE NOT NULL
);
COMMENT ON TABLE products IS 'Product catalog. Products always belong to a subcategory (a category with a parent).';
COMMENT ON COLUMN products.unit_price IS 'Current list price in USD.';
COMMENT ON COLUMN products.unit_cost IS 'Cost of goods per unit in USD. Margin = unit_price - unit_cost.';
COMMENT ON COLUMN products.is_active IS 'False if the product has been discontinued.';

CREATE TABLE orders (
    order_id      SERIAL PRIMARY KEY,
    customer_id   INT NOT NULL REFERENCES customers(customer_id),
    order_date    TIMESTAMP NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('pending', 'shipped', 'delivered', 'cancelled', 'returned')),
    channel       TEXT NOT NULL CHECK (channel IN ('web', 'mobile', 'marketplace'))
);
COMMENT ON TABLE orders IS 'One row per order. Revenue questions should normally exclude cancelled orders.';
COMMENT ON COLUMN orders.status IS 'Order status: pending, shipped, delivered, cancelled or returned.';
COMMENT ON COLUMN orders.channel IS 'Sales channel: web, mobile or marketplace.';

CREATE TABLE order_items (
    order_item_id SERIAL PRIMARY KEY,
    order_id      INT NOT NULL REFERENCES orders(order_id),
    product_id    INT NOT NULL REFERENCES products(product_id),
    quantity      INT NOT NULL CHECK (quantity > 0),
    unit_price    NUMERIC(10,2) NOT NULL,
    discount      NUMERIC(4,2) NOT NULL DEFAULT 0
);
COMMENT ON TABLE order_items IS 'Line items of an order. Line revenue = quantity * unit_price * (1 - discount).';
COMMENT ON COLUMN order_items.unit_price IS 'Price per unit at the time of sale (may differ from products.unit_price).';
COMMENT ON COLUMN order_items.discount IS 'Fractional discount applied to the line, e.g. 0.10 = 10% off.';

CREATE TABLE payments (
    payment_id    SERIAL PRIMARY KEY,
    order_id      INT NOT NULL REFERENCES orders(order_id),
    method        TEXT NOT NULL CHECK (method IN ('credit_card', 'paypal', 'bank_transfer', 'gift_card')),
    amount        NUMERIC(12,2) NOT NULL,
    paid_at       TIMESTAMP NOT NULL
);
COMMENT ON TABLE payments IS 'Payments received for orders. Cancelled orders have no payment.';

CREATE TABLE shipments (
    shipment_id   SERIAL PRIMARY KEY,
    order_id      INT NOT NULL REFERENCES orders(order_id),
    carrier       TEXT NOT NULL,
    shipped_at    TIMESTAMP NOT NULL,
    delivered_at  TIMESTAMP
);
COMMENT ON TABLE shipments IS 'Shipment per order (shipped, delivered and returned orders). delivered_at is NULL while in transit.';

CREATE TABLE reviews (
    review_id     SERIAL PRIMARY KEY,
    product_id    INT NOT NULL REFERENCES products(product_id),
    customer_id   INT NOT NULL REFERENCES customers(customer_id),
    rating        INT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    review_date   DATE NOT NULL,
    comment       TEXT
);
COMMENT ON TABLE reviews IS 'Product reviews with a 1-5 star rating.';

CREATE INDEX ON orders (customer_id);
CREATE INDEX ON orders (order_date);
CREATE INDEX ON order_items (order_id);
CREATE INDEX ON order_items (product_id);
CREATE INDEX ON reviews (product_id);
