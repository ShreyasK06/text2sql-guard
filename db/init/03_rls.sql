-- Row-level security. Runs after the tables exist.
-- RLS is not FORCEd: the table owner (t2s_owner) and superusers bypass it, which is the point
-- of the "sandbox off" evaluation configuration.
-- The manager sees only rows of the region in app.current_region (set by the executor with
-- SET LOCAL). Unset or empty -> nullif(...) is NULL -> comparison is NULL -> no rows.
-- products has no RLS.

ALTER TABLE users           ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders          ENABLE ROW LEVEL SECURITY;
ALTER TABLE employees       ENABLE ROW LEVEL SECURITY;
ALTER TABLE payments        ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_items     ENABLE ROW LEVEL SECURITY;
ALTER TABLE support_tickets ENABLE ROW LEVEL SECURITY;

-- Analyst and admin: all rows.
CREATE POLICY all_rows ON users           FOR SELECT TO t2s_analyst, t2s_admin USING (true);
CREATE POLICY all_rows ON orders          FOR SELECT TO t2s_analyst, t2s_admin USING (true);
CREATE POLICY all_rows ON employees       FOR SELECT TO t2s_analyst, t2s_admin USING (true);
CREATE POLICY all_rows ON payments        FOR SELECT TO t2s_analyst, t2s_admin USING (true);
CREATE POLICY all_rows ON order_items     FOR SELECT TO t2s_analyst, t2s_admin USING (true);
CREATE POLICY all_rows ON support_tickets FOR SELECT TO t2s_analyst, t2s_admin USING (true);

-- Manager: own region only.
CREATE POLICY manager_region ON users FOR SELECT TO t2s_manager
    USING (region = nullif(current_setting('app.current_region', true), ''));
CREATE POLICY manager_region ON orders FOR SELECT TO t2s_manager
    USING (region = nullif(current_setting('app.current_region', true), ''));
CREATE POLICY manager_region ON employees FOR SELECT TO t2s_manager
    USING (region = nullif(current_setting('app.current_region', true), ''));
CREATE POLICY manager_region ON payments FOR SELECT TO t2s_manager
    USING (EXISTS (SELECT 1 FROM orders o
                   WHERE o.id = payments.order_id
                     AND o.region = nullif(current_setting('app.current_region', true), '')));
CREATE POLICY manager_region ON order_items FOR SELECT TO t2s_manager
    USING (EXISTS (SELECT 1 FROM orders o
                   WHERE o.id = order_items.order_id
                     AND o.region = nullif(current_setting('app.current_region', true), '')));
CREATE POLICY manager_region ON support_tickets FOR SELECT TO t2s_manager
    USING (EXISTS (SELECT 1 FROM users u
                   WHERE u.id = support_tickets.user_id
                     AND u.region = nullif(current_setting('app.current_region', true), '')));
