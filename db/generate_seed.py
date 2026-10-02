"""Generate db/02_seed.sql with deterministic, realistic e-commerce data.

Run:  python db/generate_seed.py
The output is committed so `docker compose up` works without Python.
Fixed RNG seed => identical data on every run, which keeps eval results reproducible.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from pathlib import Path

RNG = random.Random(42)
OUT = Path(__file__).with_name("02_seed.sql")

N_CUSTOMERS = 500
N_ORDERS = 5000
START = date(2024, 1, 1)
END = date(2025, 12, 31)

FIRST = ["James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael", "Linda", "David", "Elizabeth",
         "William", "Barbara", "Richard", "Susan", "Joseph", "Jessica", "Thomas", "Sarah", "Priya", "Arjun",
         "Wei", "Mei", "Carlos", "Sofia", "Lucas", "Emma", "Noah", "Olivia", "Liam", "Ava", "Hiro", "Yuki",
         "Ahmed", "Fatima", "Pierre", "Chloe", "Hans", "Greta", "Raj", "Ananya"]
LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez",
        "Patel", "Sharma", "Chen", "Wang", "Kim", "Tanaka", "Muller", "Schmidt", "Dubois", "Martin", "Silva",
        "Santos", "Khan", "Ali", "Nguyen", "Lee", "Taylor", "Anderson", "Thomas", "Moore"]

# country -> (weight, cities, states or None)
COUNTRIES = {
    "United States": (40, ["New York", "San Francisco", "Austin", "Chicago", "Seattle", "Boston"],
                      ["NY", "CA", "TX", "IL", "WA", "MA"]),
    "India": (15, ["Mumbai", "Bengaluru", "Delhi", "Pune"], ["MH", "KA", "DL", "MH"]),
    "United Kingdom": (12, ["London", "Manchester", "Edinburgh"], None),
    "Germany": (10, ["Berlin", "Munich", "Hamburg"], None),
    "Canada": (8, ["Toronto", "Vancouver", "Montreal"], ["ON", "BC", "QC"]),
    "France": (7, ["Paris", "Lyon"], None),
    "Japan": (5, ["Tokyo", "Osaka"], None),
    "Brazil": (3, ["Sao Paulo", "Rio de Janeiro"], None),
}

# top-level category -> {subcategory: [(product name, price range)]}
CATALOG = {
    "Electronics": {
        "Laptops": ["UltraBook 13", "ProBook 15", "Gaming Laptop X", "Chromebook Lite", "Workstation 17"],
        "Smartphones": ["Phone A1", "Phone A1 Pro", "Phone Z Fold", "Budget Phone S", "Phone Mini"],
        "Audio": ["Wireless Earbuds", "Noise-Cancel Headphones", "Bluetooth Speaker", "Soundbar 300", "Studio Monitors"],
        "Accessories": ["USB-C Charger", "Laptop Sleeve", "Wireless Mouse", "Mechanical Keyboard", "Webcam HD",
                        "Phone Case", "Screen Protector", "Power Bank"],
    },
    "Home & Kitchen": {
        "Cookware": ["Nonstick Pan", "Cast Iron Skillet", "Dutch Oven", "Stockpot", "Knife Set"],
        "Appliances": ["Blender Pro", "Air Fryer", "Espresso Machine", "Toaster Oven", "Stand Mixer"],
        "Furniture": ["Office Chair", "Standing Desk", "Bookshelf", "Coffee Table", "Bar Stool"],
        "Decor": ["Table Lamp", "Wall Clock", "Throw Pillow", "Area Rug", "Picture Frame Set"],
    },
    "Sports & Outdoors": {
        "Fitness": ["Yoga Mat", "Adjustable Dumbbells", "Resistance Bands", "Kettlebell", "Foam Roller"],
        "Camping": ["2-Person Tent", "Sleeping Bag", "Camping Stove", "Headlamp", "Hiking Backpack"],
        "Cycling": ["Road Helmet", "Bike Lights", "Cycling Gloves", "Bike Lock", "Water Bottle Cage"],
    },
    "Books": {
        "Fiction": ["The Silent Harbor", "Echoes of Mars", "A Winter Tale", "The Last Library", "Neon Streets"],
        "Non-Fiction": ["Data Science Handbook", "SQL in Practice", "The Habit Loop", "World History Atlas",
                        "Startup Playbook"],
        "Children": ["Picture Book: Moon", "Dino Adventures", "Learn to Count", "Bedtime Stories"],
    },
    "Clothing": {
        "Men": ["Men's Running Shoes", "Men's Jacket", "Men's Jeans", "Men's T-Shirt", "Men's Hoodie"],
        "Women": ["Women's Running Shoes", "Women's Jacket", "Women's Dress", "Women's Leggings", "Women's Sweater"],
        "Kids": ["Kids' Sneakers", "Kids' Raincoat", "Kids' Pajamas"],
    },
}
PRICE_RANGE = {
    "Laptops": (600, 2200), "Smartphones": (250, 1500), "Audio": (40, 500), "Accessories": (10, 120),
    "Cookware": (25, 250), "Appliances": (60, 600), "Furniture": (80, 700), "Decor": (15, 150),
    "Fitness": (15, 300), "Camping": (20, 350), "Cycling": (10, 150),
    "Fiction": (8, 25), "Non-Fiction": (12, 45), "Children": (6, 20),
    "Men": (20, 180), "Women": (20, 200), "Kids": (12, 70),
}
# Monthly seasonality multiplier (Nov/Dec holiday peak, summer bump).
SEASON = {1: 0.8, 2: 0.75, 3: 0.9, 4: 0.9, 5: 1.0, 6: 1.1, 7: 1.15, 8: 1.0, 9: 0.95, 10: 1.05, 11: 1.6, 12: 1.9}
CARRIERS = ["UPS", "FedEx", "DHL", "USPS"]
CARRIER_DAYS = {"UPS": (2, 6), "FedEx": (1, 5), "DHL": (3, 9), "USPS": (3, 8)}
COMMENTS = {5: ["Excellent!", "Love it", "Exceeded expectations"], 4: ["Very good", "Solid product"],
            3: ["It's okay", "Average"], 2: ["Disappointing", "Not as described"], 1: ["Terrible", "Broke quickly"]}


def q(v) -> str:
    """Render a Python value as a SQL literal."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (date, datetime)):
        return f"'{v.isoformat(sep=' ') if isinstance(v, datetime) else v.isoformat()}'"
    return "'" + str(v).replace("'", "''") + "'"


def insert(table: str, cols: list[str], rows: list[tuple], chunk: int = 500) -> str:
    out = []
    for i in range(0, len(rows), chunk):
        values = ",\n".join("(" + ", ".join(q(v) for v in r) + ")" for r in rows[i:i + chunk])
        out.append(f"INSERT INTO {table} ({', '.join(cols)}) VALUES\n{values};")
    return "\n".join(out)


def rand_date(a: date, b: date) -> date:
    return a + timedelta(days=RNG.randint(0, (b - a).days))


def seasonal_datetime() -> datetime:
    # Rejection-sample a day weighted by seasonality, with mild year-over-year growth.
    while True:
        d = rand_date(START, END)
        weight = SEASON[d.month] * (1.25 if d.year == 2025 else 1.0)
        if RNG.random() < weight / 2.4:
            return datetime(d.year, d.month, d.day, RNG.randint(0, 23), RNG.randint(0, 59), RNG.randint(0, 59))


def main() -> None:
    parts = ["-- Generated by db/generate_seed.py (seed=42). Do not edit by hand.", "BEGIN;"]

    # Customers + addresses
    countries = list(COUNTRIES)
    weights = [COUNTRIES[c][0] for c in countries]
    customers, addresses, cust_country = [], [], {}
    for cid in range(1, N_CUSTOMERS + 1):
        fn, ln = RNG.choice(FIRST), RNG.choice(LAST)
        country = RNG.choices(countries, weights)[0]
        segment = RNG.choices(["consumer", "small_business", "enterprise"], [75, 20, 5])[0]
        customers.append((fn, ln, f"{fn.lower()}.{ln.lower()}{cid}@example.com",
                          rand_date(date(2023, 1, 1), date(2025, 10, 31)), country, segment))
        cust_country[cid] = country
        _, cities, states = COUNTRIES[country]
        for k in range(RNG.choices([1, 2, 3], [70, 25, 5])[0]):
            idx = RNG.randrange(len(cities))
            addresses.append((cid, cities[idx], states[idx] if states else None, country,
                              f"{RNG.randint(10000, 99999)}", k == 0))
    parts.append(insert("customers", ["first_name", "last_name", "email", "signup_date", "country", "segment"],
                        customers))
    parts.append(insert("addresses", ["customer_id", "city", "state", "country", "postal_code", "is_default"],
                        addresses))

    # Categories + products
    categories, products, sub_ids = [], [], {}
    cat_id = 0
    for top, subs in CATALOG.items():
        cat_id += 1
        top_id = cat_id
        categories.append((top_id, top, None))
        for sub in subs:
            cat_id += 1
            categories.append((cat_id, sub, top_id))
            sub_ids[sub] = cat_id
    parts.append(insert("categories", ["category_id", "name", "parent_category_id"], categories))
    parts.append("SELECT setval('categories_category_id_seq', (SELECT MAX(category_id) FROM categories));")

    product_price = {}
    pid = 0
    for subs in CATALOG.values():
        for sub, names in subs.items():
            lo, hi = PRICE_RANGE[sub]
            for name in names:
                pid += 1
                price = round(RNG.uniform(lo, hi), 2)
                cost = round(price * RNG.uniform(0.45, 0.75), 2)
                products.append((name, sub_ids[sub], price, cost, RNG.random() > 0.08,
                                 rand_date(date(2022, 6, 1), date(2024, 6, 30))))
                product_price[pid] = price
    parts.append(insert("products", ["name", "category_id", "unit_price", "unit_cost", "is_active", "created_at"],
                        products))
    n_products = pid
    # Popularity skew: a few products sell much more than others.
    popularity = [RNG.paretovariate(1.3) for _ in range(n_products)]

    # Orders, items, payments, shipments
    # Customer activity skew: some customers are frequent buyers.
    activity = [RNG.paretovariate(1.5) for _ in range(N_CUSTOMERS)]
    order_dates = sorted(seasonal_datetime() for _ in range(N_ORDERS))
    orders, items, payments, shipments, purchased = [], [], [], [], []
    for oid, odt in enumerate(order_dates, start=1):
        cid = RNG.choices(range(1, N_CUSTOMERS + 1), activity)[0]
        status = RNG.choices(["delivered", "shipped", "pending", "cancelled", "returned"], [78, 6, 3, 8, 5])[0]
        # Recent orders are more likely still in flight.
        if odt.date() > date(2025, 12, 20) and status == "delivered":
            status = RNG.choice(["shipped", "pending"])
        channel = RNG.choices(["web", "mobile", "marketplace"], [50, 35, 15])[0]
        orders.append((cid, odt, status, channel))

        total = 0.0
        for p in set(RNG.choices(range(1, n_products + 1), popularity, k=RNG.choices([1, 2, 3, 4], [50, 30, 15, 5])[0])):
            qty = RNG.choices([1, 2, 3, 5], [70, 20, 7, 3])[0]
            price = round(product_price[p] * RNG.uniform(0.95, 1.05), 2)
            disc = RNG.choices([0, 0.05, 0.10, 0.20], [70, 15, 10, 5])[0]
            if odt.month == 11 and odt.day >= 24:  # Black Friday week
                disc = max(disc, 0.15)
            items.append((oid, p, qty, price, disc))
            total += qty * price * (1 - disc)
            if status in ("delivered", "returned"):
                purchased.append((cid, p, odt.date()))

        if status != "cancelled":
            method = RNG.choices(["credit_card", "paypal", "bank_transfer", "gift_card"], [60, 25, 10, 5])[0]
            payments.append((oid, method, round(total, 2), odt + timedelta(minutes=RNG.randint(0, 120))))
        if status in ("shipped", "delivered", "returned"):
            carrier = RNG.choice(CARRIERS)
            shipped = odt + timedelta(hours=RNG.randint(4, 72))
            lo, hi = CARRIER_DAYS[carrier]
            delivered = None if status == "shipped" else shipped + timedelta(days=RNG.randint(lo, hi),
                                                                               hours=RNG.randint(0, 12))
            shipments.append((oid, carrier, shipped, delivered))

    parts.append(insert("orders", ["customer_id", "order_date", "status", "channel"], orders))
    parts.append(insert("order_items", ["order_id", "product_id", "quantity", "unit_price", "discount"], items))
    parts.append(insert("payments", ["order_id", "method", "amount", "paid_at"], payments))
    parts.append(insert("shipments", ["order_id", "carrier", "shipped_at", "delivered_at"], shipments))

    # Reviews: ~30% of purchases get reviewed; product "quality" drives rating.
    quality = {p: RNG.uniform(2.5, 4.8) for p in range(1, n_products + 1)}
    reviews, seen = [], set()
    for cid, p, d in purchased:
        if (cid, p) in seen or RNG.random() > 0.3:
            continue
        seen.add((cid, p))
        rating = max(1, min(5, round(RNG.gauss(quality[p], 0.9))))
        reviews.append((p, cid, rating, d + timedelta(days=RNG.randint(3, 30)),
                        RNG.choice(COMMENTS[rating]) if RNG.random() < 0.7 else None))
    parts.append(insert("reviews", ["product_id", "customer_id", "rating", "review_date", "comment"], reviews))

    parts.append("COMMIT;")
    parts.append("ANALYZE;")
    OUT.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    print(f"wrote {OUT} | customers={len(customers)} products={n_products} orders={len(orders)} "
          f"items={len(items)} reviews={len(reviews)}")


if __name__ == "__main__":
    main()
