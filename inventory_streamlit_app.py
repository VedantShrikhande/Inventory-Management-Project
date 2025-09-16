# inventory_app.py
import streamlit as st
import pandas as pd
import mysql.connector

# ----------------------------
# 1. UPDATE THESE CREDENTIALS
# ----------------------------
DB_CONFIG = {
    "host": "localhost",        # 🔹 Change if DB is on another server
    "user": "root",             # 🔹 Your MySQL username
    "password": "your_password",# 🔹 Your MySQL password
    "database": "ims_db"        # 🔹 Ensure DB is created with your schema
}

# ----------------------------
# 2. HELPER FUNCTION
# ----------------------------
@st.cache_data
def run_query(query):
    """Run a SQL query and return results as DataFrame."""
    conn = mysql.connector.connect(**DB_CONFIG)
    df = pd.read_sql(query, conn)
    conn.close()
    return df

# ----------------------------
# 3. STREAMLIT UI
# ----------------------------
st.set_page_config(page_title="Inventory Management Dashboard", layout="wide")

st.title("📦 Inventory Management Dashboard")
st.sidebar.header("Navigation")

menu = st.sidebar.radio("Select Report", [
    "Current Stock",
    "Low Stock Alerts",
    "Movement History (USB Example)",
    "Top 5 Products by Stock Value",
    "Sales Order Fulfillment Summary",
    "Inventory Valuation by Warehouse",
    "Top 5 Suppliers (6 months)",
    "Top 5 Customers (6 months)",
    "Monthly Sales Trend (12 months)",
    "Stock Turnover Ratio",
    "Aging of Stock (90+ days)",
    "Fill Rate per SO",
    "Profitability by Product"
])

# ----------------------------
# 4. REPORT QUERIES
# ----------------------------
queries = {
    "Current Stock": """
        SELECT * FROM v_current_stock ORDER BY sku, warehouse_code;
    """,
    "Low Stock Alerts": """
        SELECT * FROM v_low_stock;
    """,
    "Movement History (USB Example)": """
        SELECT m.*
        FROM inventory_movements m
        JOIN products p ON p.product_id = m.product_id
        WHERE p.sku = 'SKU-USB-16'
        ORDER BY m.acted_at DESC
        LIMIT 10;
    """,
    "Top 5 Products by Stock Value": """
        SELECT sku, product_name,
               SUM(on_hand * avg_cost) AS value
        FROM v_current_stock
        GROUP BY sku, product_name
        ORDER BY value DESC
        LIMIT 5;
    """,
    "Sales Order Fulfillment Summary": """
        SELECT so.so_id, c.customer_name, so.status,
               SUM(soi.ordered_qty) AS ordered, SUM(soi.shipped_qty) AS shipped
        FROM sales_orders so
        JOIN customers c ON c.customer_id = so.customer_id
        JOIN sales_order_items soi ON soi.so_id = so.so_id
        GROUP BY so.so_id, c.customer_name, so.status
        ORDER BY so.so_id DESC;
    """,
    "Inventory Valuation by Warehouse": """
        SELECT * FROM v_inventory_valuation;
    """,
    "Top 5 Suppliers (6 months)": """
        SELECT s.supplier_name,
               SUM(poi.received_qty * poi.unit_cost) AS total_purchase_value
        FROM purchase_order_items poi
        JOIN purchase_orders po ON po.po_id = poi.po_id
        JOIN suppliers s ON s.supplier_id = po.supplier_id
        WHERE po.created_at >= DATE_SUB(CURDATE(), INTERVAL 6 MONTH)
        GROUP BY s.supplier_name
        ORDER BY total_purchase_value DESC
        LIMIT 5;
    """,
    "Top 5 Customers (6 months)": """
        SELECT c.customer_name,
               SUM(soi.shipped_qty * soi.unit_price) AS total_sales_value
        FROM sales_order_items soi
        JOIN sales_orders so ON so.so_id = soi.so_id
        JOIN customers c ON c.customer_id = so.customer_id
        WHERE so.created_at >= DATE_SUB(CURDATE(), INTERVAL 6 MONTH)
        GROUP BY c.customer_name
        ORDER BY total_sales_value DESC
        LIMIT 5;
    """,
    "Monthly Sales Trend (12 months)": """
        SELECT DATE_FORMAT(so.created_at, '%Y-%m') AS sales_month,
               SUM(soi.shipped_qty * soi.unit_price) AS monthly_sales
        FROM sales_order_items soi
        JOIN sales_orders so ON so.so_id = soi.so_id
        WHERE so.created_at >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH)
        GROUP BY sales_month
        ORDER BY sales_month;
    """,
    "Stock Turnover Ratio": """
        SELECT p.sku, p.product_name,
               SUM(m.qty) AS total_outflow,
               AVG(s.on_hand) AS avg_stock,
               CASE WHEN AVG(s.on_hand) > 0
                    THEN ROUND(SUM(m.qty)/AVG(s.on_hand),2)
                    ELSE 0 END AS turnover_ratio
        FROM inventory_movements m
        JOIN products p ON p.product_id = m.product_id
        JOIN stock_levels s ON s.product_id = p.product_id
        WHERE m.movement_type='SALES_SHIPMENT'
          AND m.acted_at >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH)
        GROUP BY p.sku, p.product_name
        ORDER BY turnover_ratio DESC;
    """,
    "Aging of Stock (90+ days)": """
        SELECT p.sku, p.product_name, w.warehouse_code,
               s.on_hand,
               MAX(m.acted_at) AS last_movement_date,
               DATEDIFF(CURDATE(), MAX(m.acted_at)) AS days_since_last_movement
        FROM stock_levels s
        JOIN products p ON p.product_id = s.product_id
        JOIN warehouses w ON w.warehouse_id = s.warehouse_id
        LEFT JOIN inventory_movements m ON m.product_id = p.product_id AND m.warehouse_id = w.warehouse_id
        GROUP BY p.sku, p.product_name, w.warehouse_code, s.on_hand
        HAVING days_since_last_movement >= 90 OR last_movement_date IS NULL
        ORDER BY days_since_last_movement DESC;
    """,
    "Fill Rate per SO": """
        SELECT so.so_id, c.customer_name,
               SUM(soi.shipped_qty)/SUM(soi.ordered_qty)*100 AS fill_rate_percent
        FROM sales_orders so
        JOIN sales_order_items soi ON so.so_id = soi.so_id
        JOIN customers c ON c.customer_id = so.customer_id
        GROUP BY so.so_id, c.customer_name
        ORDER BY fill_rate_percent DESC;
    """,
    "Profitability by Product": """
        SELECT p.sku, p.product_name,
               SUM(soi.shipped_qty * soi.unit_price) AS revenue,
               SUM(soi.shipped_qty * s.avg_cost) AS cost,
               SUM((soi.shipped_qty * soi.unit_price) - (soi.shipped_qty * s.avg_cost)) AS profit
        FROM sales_order_items soi
        JOIN sales_orders so ON so.so_id = soi.so_id
        JOIN products p ON p.product_id = soi.product_id
        JOIN stock_levels s ON s.product_id = p.product_id AND s.warehouse_id = so.warehouse_id
        GROUP BY p.sku, p.product_name
        ORDER BY profit DESC;
    """
}

# ----------------------------
# 5. DISPLAY RESULTS
# ----------------------------
query = queries[menu]
st.subheader(f"📊 {menu}")
df = run_query(query)
st.dataframe(df, use_container_width=True)

# Extra: Chart for Monthly Sales Trend
if menu == "Monthly Sales Trend (12 months)":
    st.line_chart(df.set_index("sales_month"))
