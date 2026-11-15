-- GENERATED FILE. DO NOT EDIT BY HAND.
-- Source: policies/policy.yaml   Generator: python -m db.gen_grants
-- Column-level SELECT grants per database role. Masked columns are granted too: the DB grant
-- says what a role may read at all, the output guard applies the mask afterwards.
-- Tables and columns not listed here are not granted to anyone (default deny).

-- Start from a clean slate (the owner keeps its own privileges).
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC, t2s_analyst, t2s_manager, t2s_admin;

-- role: analyst (t2s_analyst)
GRANT SELECT (id, name, category, price, description) ON products TO t2s_analyst;
GRANT SELECT (id, user_id, order_date, status, total_amount, region, sales_rep_id) ON orders TO t2s_analyst;
GRANT SELECT (id, order_id, product_id, quantity, unit_price) ON order_items TO t2s_analyst;
GRANT SELECT (id, order_id, method, amount, paid_at, status) ON payments TO t2s_analyst;
GRANT SELECT (id, city, state, region, created_at) ON users TO t2s_analyst;
GRANT SELECT (id, department, title, hire_date, region) ON employees TO t2s_analyst;
GRANT SELECT (id, user_id, status, created_at) ON support_tickets TO t2s_analyst;

-- role: manager (t2s_manager)
GRANT SELECT (id, name, category, price, description) ON products TO t2s_manager;
GRANT SELECT (id, user_id, order_date, status, total_amount, region, sales_rep_id) ON orders TO t2s_manager;
GRANT SELECT (id, order_id, product_id, quantity, unit_price) ON order_items TO t2s_manager;
GRANT SELECT (id, order_id, method, card_last4, amount, paid_at, status) ON payments TO t2s_manager;
GRANT SELECT (id, full_name, email, phone, city, state, region, created_at) ON users TO t2s_manager;
GRANT SELECT (id, full_name, department, title, manager_id, hire_date, region) ON employees TO t2s_manager;
GRANT SELECT (id, user_id, subject, body, status, created_at) ON support_tickets TO t2s_manager;

-- role: admin (t2s_admin)
GRANT SELECT (id, name, category, price, description) ON products TO t2s_admin;
GRANT SELECT (id, user_id, order_date, status, total_amount, region, sales_rep_id) ON orders TO t2s_admin;
GRANT SELECT (id, order_id, product_id, quantity, unit_price) ON order_items TO t2s_admin;
GRANT SELECT (id, order_id, method, card_number, card_last4, amount, paid_at, status) ON payments TO t2s_admin;
GRANT SELECT (id, full_name, email, phone, ssn, date_of_birth, address, city, state, region, created_at) ON users TO t2s_admin;
GRANT SELECT (id, full_name, email, ssn, department, title, salary, manager_id, hire_date, region) ON employees TO t2s_admin;
GRANT SELECT (id, user_id, subject, body, status, created_at) ON support_tickets TO t2s_admin;
