#Inventory Management — Streamlit Interactive UI
#File: inventory_streamlit_app.py
#Requires: streamlit, sqlalchemy, pymysql, pandas
#Run: pip install streamlit sqlalchemy pymysql pandas
     #export DB_URI='mysql+pymysql://user:pass@host:3306/ims_db'
     #streamlit run inventory_streamlit_app.py

#This app runs the SQL schema & example queries provided earlier.


import os
import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from datetime import date, timedelta

# ---------- CONFIG ----------
DB_URI = os.getenv('DB_URI', 'mysql+pymysql://root:root@localhost:3306/ims_db')
engine = create_engine(DB_URI, pool_recycle=3600)

st.set_page_config(page_title='Inventory Management UI', layout='wide')
st.title('Inventory Management — Interactive Dashboard')

# ---------- UTILITIES ----------
@st.cache_data(ttl=300)
def run_query(sql, params=None):
    """Run a SELECT-style query and return a DataFrame"""
    try:
        with engine.connect() as conn:
            df = pd.read_sql_query(text(sql), conn, params=params)
        return df
    except Exception as e:
        st.error(f'Query failed: {e}')
        return pd.DataFrame()


def run_nonselect(sql, params=None):
    """Run INSERT/UPDATE/DELETE or CALL statements; returns affected rows or messages."""
    try:
        with engine.begin() as conn:  # transaction
            res = conn.execute(text(sql), params or {})
            # For CALL statements, SQLAlchemy may not return rowcount; return success message
        return True, 'Executed successfully'
    except Exception as e:
        return False, str(e)


def call_procedure(proc_name, args=()):
    """Call stored procedure using raw connection (to support MySQL procedures)."""
    try:
        raw_conn = engine.raw_connection()
        cur = raw_conn.cursor()
        cur.callproc(proc_name, args)
        # fetch any resultsets
        results = []
        for result in cur.stored_results():
            try:
                cols = [d[0] for d in result.description] if result.description else []
                rows = result.fetchall()
                results.append(pd.DataFrame(rows, columns=cols))
            except Exception:
                pass
        raw_conn.commit()
        cur.close()
        raw_conn.close()
        return True, results
    except Exception as e:
        return False, str(e)

# ---------- PREDEFINED QUERIES (from your script) ----------
QUERIES = {
    'Current Stock (all warehouses)':
    """
    SELECT * FROM v_current_stock ORDER BY sku, warehouse_code
    """,

    'Low Stock Alerts':
    """
    SELECT * FROM v_low_stock
    """,

    'Movement History (product)':
    """
    SELECT m.*
    FROM inventory_movements m
    JOIN products p ON p.product_id = m.product_id
    WHERE p.sku = :sku
    ORDER BY m.acted_at DESC
    LIMIT 200
    """,

    'Top 5 Suppliers (6 months)':
    """
    SELECT s.supplier_name,
           SUM(poi.received_qty * poi.unit_cost) AS total_purchase_value
    FROM purchase_order_items poi
    JOIN purchase_orders po ON po.po_id = poi.po_id
    JOIN suppliers s ON s.supplier_id = po.supplier_id
    WHERE po.created_at >= DATE_SUB(CURDATE(), INTERVAL 6 MONTH)
    GROUP BY s.supplier_name
    ORDER BY total_purchase_value DESC
    LIMIT 5
    """,

    'Top 5 Customers (6 months)':
    """
    SELECT c.customer_name,
           SUM(soi.shipped_qty * soi.unit_price) AS total_sales_value
    FROM sales_order_items soi
    JOIN sales_orders so ON so.so_id = soi.so_id
    JOIN customers c ON c.customer_id = so.customer_id
    WHERE so.created_at >= DATE_SUB(CURDATE(), INTERVAL 6 MONTH)
    GROUP BY c.customer_name
    ORDER BY total_sales_value DESC
    LIMIT 5
    """,

    'Monthly Sales Trend (12 months)':
    """
    SELECT DATE_FORMAT(so.created_at, '%Y-%m') AS sales_month,
           SUM(soi.shipped_qty * soi.unit_price) AS monthly_sales
    FROM sales_order_items soi
    JOIN sales_orders so ON so.so_id = soi.so_id
    WHERE so.created_at >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH)
    GROUP BY sales_month
    ORDER BY sales_month
    """,

    'Stock Turnover (12 months)':
    """
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
    ORDER BY turnover_ratio DESC
    """,

    'Aging of Stock (>=90 days)':
    """
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
    ORDER BY days_since_last_movement DESC
    """,

    'Fill Rate per SO':
    """
    SELECT so.so_id, c.customer_name,
           SUM(soi.shipped_qty)/SUM(soi.ordered_qty)*100 AS fill_rate_percent
    FROM sales_orders so
    JOIN sales_order_items soi ON so.so_id = soi.so_id
    JOIN customers c ON c.customer_id = so.customer_id
    GROUP BY so.so_id, c.customer_name
    ORDER BY fill_rate_percent DESC
    """,

    'Profitability by Product':
    """
    SELECT p.sku, p.product_name,
           SUM(soi.shipped_qty * soi.unit_price) AS revenue,
           SUM(soi.shipped_qty * s.avg_cost) AS cost,
           SUM((soi.shipped_qty * soi.unit_price) - (soi.shipped_qty * s.avg_cost)) AS profit
    FROM sales_order_items soi
    JOIN sales_orders so ON so.so_id = soi.so_id
    JOIN products p ON p.product_id = soi.product_id
    JOIN stock_levels s ON s.product_id = p.product_id AND s.warehouse_id = so.warehouse_id
    GROUP BY p.sku, p.product_name
    ORDER BY profit DESC
    """,
}

# ---------- SIDEBAR: Controls ----------
st.sidebar.header('Controls')
selected = st.sidebar.selectbox('Select report / query', list(QUERIES.keys()))

# optional SKU filter
sku_input = st.sidebar.text_input('Filter by SKU (optional)', '')
# date range (for queries that support it)
start_date = st.sidebar.date_input('Start date', date.today() - timedelta(days=365))
end_date   = st.sidebar.date_input('End date', date.today())

run = st.sidebar.button('Run Query')

# ---------- MAIN: Run Selected Query ----------
st.subheader(selected)
sql = QUERIES[selected]
params = {}
if ':sku' in sql and sku_input:
    params['sku'] = sku_input

if run:
    df = run_query(sql, params)
    if df is None or df.empty:
        st.info('No rows returned.')
    else:
        st.dataframe(df)
        # quick visualizations tailored to a few queries
        if selected == 'Monthly Sales Trend (12 months)':
            df['sales_month'] = pd.to_datetime(df['sales_month'] + '-01')
            df = df.set_index('sales_month').sort_index()
            st.line_chart(df['monthly_sales'])
        if selected == 'Top 5 Suppliers (6 months)' or selected == 'Top 5 Customers (6 months)':
            st.bar_chart(df.set_index(df.columns[0]))
        if selected == 'Current Stock (all warehouses)':
            # show top products by stock value
            top = df.groupby(['sku','product_name'])['stock_value'].sum().reset_index().sort_values('stock_value', ascending=False).head(10)
            st.write('Top 10 products by stock value')
            st.bar_chart(top.set_index('sku')['stock_value'])

# ---------- STORED PROCS / ACTIONS ----------
st.markdown('---')
st.header('Run Operations (Stored Procedures)')
proc_choice = st.selectbox('Select operation', ['None','Receive PO Item (sp_receive_po_item)','Allocate SO Item (sp_allocate_so_item)','Ship SO Item (sp_ship_so_item)','Apply Adjustment Item (sp_apply_adjustment_item)'])

if proc_choice == 'Receive PO Item (sp_receive_po_item)':
    po_id = st.number_input('PO ID', min_value=1, step=1)
    product_sku = st.text_input('Product SKU')
    qty = st.number_input('Receive qty', min_value=0.01, value=1.0, step=1.0)
    unit_cost = st.number_input('Unit cost', min_value=0.0, value=0.0, step=1.0)
    user_id = st.selectbox('Acted by (user_id)', [1,2,3])
    if st.button('Receive'): 
        pid = None
        if product_sku:
            p = run_query("SELECT product_id FROM products WHERE sku = :sku", {'sku': product_sku})
            if not p.empty:
                pid = int(p.iloc[0]['product_id'])
            else:
                st.error('SKU not found')
        else:
            st.error('Provide SKU')
        if pid:
            ok, res = call_procedure('sp_receive_po_item', (po_id, pid, float(qty), float(unit_cost), int(user_id)))
            if ok:
                st.success('PO receive applied')
            else:
                st.error(f'Failed: {res}')

if proc_choice == 'Allocate SO Item (sp_allocate_so_item)':
    so_id = st.number_input('SO ID', min_value=1, step=1)
    product_sku = st.text_input('Product SKU (allocate)')
    qty = st.number_input('Allocate qty', min_value=0.01, value=1.0)
    if st.button('Allocate'):
        p = run_query("SELECT product_id FROM products WHERE sku = :sku", {'sku': product_sku})
        if p.empty:
            st.error('SKU not found')
        else:
            pid = int(p.iloc[0]['product_id'])
            ok, res = call_procedure('sp_allocate_so_item', (so_id, pid, float(qty)))
            if ok:
                st.success('Allocated successfully')
            else:
                st.error(f'Failed: {res}')

if proc_choice == 'Ship SO Item (sp_ship_so_item)':
    so_id = st.number_input('SO ID (ship)', min_value=1, step=1)
    product_sku = st.text_input('Product SKU (ship)')
    qty = st.number_input('Ship qty', min_value=0.01, value=1.0)
    user_id = st.selectbox('Acted by (user_id) - ship', [1,2,3], key='ship_user')
    if st.button('Ship'):
        p = run_query("SELECT product_id FROM products WHERE sku = :sku", {'sku': product_sku})
        if p.empty:
            st.error('SKU not found')
        else:
            pid = int(p.iloc[0]['product_id'])
            ok, res = call_procedure('sp_ship_so_item', (so_id, pid, float(qty), int(user_id)))
            if ok:
                st.success('Shipped successfully')
            else:
                st.error(f'Failed: {res}')

if proc_choice == 'Apply Adjustment Item (sp_apply_adjustment_item)':
    adj_id = st.number_input('Adjustment ID', min_value=1, step=1)
    product_sku = st.text_input('Product SKU (adjust)')
    qty_change = st.number_input('Qty change (use negative for loss)', value=0.0)
    user_id = st.selectbox('Acted by (user_id) - adj', [1,2,3], key='adj_user')
    if st.button('Apply Adjustment'):
        p = run_query("SELECT product_id FROM products WHERE sku = :sku", {'sku': product_sku})
        if p.empty:
            st.error('SKU not found')
        else:
            pid = int(p.iloc[0]['product_id'])
            ok, res = call_procedure('sp_apply_adjustment_item', (adj_id, pid, float(qty_change), int(user_id)))
            if ok:
                st.success('Adjustment applied')
            else:
                st.error(f'Failed: {res}')

# ---------- FOOTER ----------
st.markdown('---')
st.caption('Tip: set DB_URI environment variable and run this file with Streamlit. Use the schema SQL first to create objects.')
