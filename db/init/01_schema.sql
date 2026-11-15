-- text2sql-guard demo schema (PostgreSQL 16). All data is synthetic.
-- Loaded by the postgres container on first start. Seed data comes from db/seed.py.

-- Tables must be owned by the non-superuser t2s_owner (created in 00_roles.sql).
SET ROLE t2s_owner;

CREATE TABLE users (
    id            integer PRIMARY KEY,
    full_name     text NOT NULL,
    email         text NOT NULL,
    phone         text NOT NULL,           -- format ###-###-####
    ssn           text NOT NULL,           -- format ###-##-####
    date_of_birth date NOT NULL,
    address       text NOT NULL,
    city          text NOT NULL,
    state         text NOT NULL,
    region        text NOT NULL CHECK (region IN ('north', 'south', 'east', 'west')),
    password_hash text NOT NULL,
    created_at    timestamp NOT NULL
);

CREATE TABLE employees (
    id          integer PRIMARY KEY,
    full_name   text NOT NULL,
    email       text NOT NULL,
    ssn         text NOT NULL,
    department  text NOT NULL,
    title       text NOT NULL,
    salary      numeric(10, 2) NOT NULL,
    manager_id  integer REFERENCES employees (id),
    hire_date   date NOT NULL,
    region      text NOT NULL CHECK (region IN ('north', 'south', 'east', 'west'))
);

CREATE TABLE products (
    id          integer PRIMARY KEY,
    name        text NOT NULL,
    category    text NOT NULL,
    price       numeric(10, 2) NOT NULL,
    description text NOT NULL
);

CREATE TABLE orders (
    id           integer PRIMARY KEY,
    user_id      integer NOT NULL REFERENCES users (id),
    order_date   date NOT NULL,
    status       text NOT NULL CHECK (status IN ('pending', 'shipped', 'delivered', 'cancelled', 'refunded')),
    total_amount numeric(10, 2) NOT NULL,
    region       text NOT NULL CHECK (region IN ('north', 'south', 'east', 'west')),
    sales_rep_id integer REFERENCES employees (id)
);

CREATE TABLE order_items (
    id         integer PRIMARY KEY,
    order_id   integer NOT NULL REFERENCES orders (id),
    product_id integer NOT NULL REFERENCES products (id),
    quantity   integer NOT NULL,
    unit_price numeric(10, 2) NOT NULL
);

CREATE TABLE payments (
    id          integer PRIMARY KEY,
    order_id    integer NOT NULL REFERENCES orders (id),
    method      text NOT NULL CHECK (method IN ('card', 'paypal', 'bank_transfer', 'gift_card')),
    card_number text,                      -- 16 digits, NULL unless method = 'card'
    card_last4  text,
    amount      numeric(10, 2) NOT NULL,
    paid_at     timestamp NOT NULL,
    status      text NOT NULL CHECK (status IN ('captured', 'failed', 'refunded'))
);

CREATE TABLE support_tickets (
    id         integer PRIMARY KEY,
    user_id    integer NOT NULL REFERENCES users (id),
    subject    text NOT NULL,
    body       text NOT NULL,              -- free text: may contain PII and data-borne prompt injections
    status     text NOT NULL CHECK (status IN ('open', 'pending', 'closed')),
    created_at timestamp NOT NULL
);

-- Internal table that NO application role may read (default-deny demo).
CREATE TABLE api_keys (
    id         integer PRIMARY KEY,
    service    text NOT NULL,
    secret     text NOT NULL,
    created_at timestamp NOT NULL
);

CREATE INDEX idx_orders_user ON orders (user_id);
CREATE INDEX idx_orders_date ON orders (order_date);
CREATE INDEX idx_orders_region ON orders (region);
CREATE INDEX idx_items_order ON order_items (order_id);
CREATE INDEX idx_items_product ON order_items (product_id);
CREATE INDEX idx_payments_order ON payments (order_id);
CREATE INDEX idx_tickets_user ON support_tickets (user_id);
