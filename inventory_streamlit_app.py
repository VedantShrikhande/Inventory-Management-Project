"""
Inventory Management — Streamlit Interactive UI (Fixed)

This file is a **fixed**, more robust version of the Streamlit app.
It addresses the error `ModuleNotFoundError: No module named 'streamlit'` by:
  - Importing `streamlit` lazily (try/except) so the script fails gracefully when Streamlit is not installed.
  - Providing a **CLI fallback** mode so you can still run queries and smoke-tests without Streamlit installed.
  - Emitting clear installation instructions when dependencies are missing.
  - Adding a small set of smoke tests (non-destructive) to validate DB connectivity.

Usage:
  - Recommended (interactive): install dependencies and run with Streamlit
      pip install streamlit sqlalchemy pymysql pandas
      export DB_URI='mysql+pymysql://root:password@127.0.0.1:3306/ims_db'
      streamlit run inventory_streamlit_app.py

  - Fallback (CLI): run without Streamlit
      python inventory_streamlit_app.py --cli

Note: this file does **not** attempt to install packages automatically. Run the pip commands above inside your environment.
"""

import os
import sys
import argparse
import warnings
from datetime import date, timedelta

# try lazy import of streamlit so this module can still run (in a limited way) without it
try:
    import streamlit as st
    HAS_STREAMLIT = True
except ModuleNotFoundError:
    HAS_STREAMLIT = False

# core libs (used in both modes)
try:
    import pandas as pd
    import sqlalchemy
    from sqlalchemy import create_engine, text
except Exception as e:
    print('Required packages missing: please install pandas and sqlalchemy.\nRun: pip install pandas sqlalchemy pymysql')
    raise

# ---------- CONFIG ----------
DB_URI = os.getenv('DB_URI', 'mysql+pymysql://root:root@localhost:3306/ims_db')
engine = create_engine(DB_URI, pool_recycle=3600)

# ---------- CORE FUNCTIONS (no Streamlit dependency) ----------

def run_query_core(sql, params=None):
    """Run a SELECT-style query and return a pandas DataFrame.
    Raises exception if the query fails (caller can catch/show message).
    """
    with engine.connect() as conn:
        df = pd.read_sql_query(text(sql), conn, params=params)
    return df


def run_nonselect_core(sql, params=None):
    """Run INSERT/UPDATE/DELETE or DDL statements; returns (True,msg) or (False,error)."""
    try:
        with engine.begin() as conn:
            conn.execute(text(sql), params or {})
        return True, 'Executed successfully'
    except Exception as e:
        return False, str(e)


def call_procedure_core(proc_name, args=()):
    """Call a stored procedure and attempt to return any resultsets as list of DataFrames.
    Supports MySQL drivers that implement cursor.nextset() (PyMySQL, mysql-connector).
    Returns (True, [df, ...]) on success or (False, 'error message') on failure.
    """
    try:
        raw_conn = engine.raw_connection()
        cur = raw_conn.cursor()
        cur.callproc(proc_name, args)

        resultsets = []
        # attempt to collect resultsets from the cursor
        try:
            # fetch initial resultset (if any)
            rows = cur.fetchall()
            if cur.description:
                cols = [d[0] for d in cur.description]
                resultsets.append(pd.DataFrame(rows, columns=cols))
        except Exception:
            # no rows/description for first resultset -- move on
            pass

        # iterate nextset() while supported
        try:
            while cur.nextset():
                try:
                    rows = cur.fetchall()
                    if cur.description:
                        cols = [d[0] for d in cur.description]
                        resultsets.append(pd.DataFrame(rows, columns=cols))
                except Exception:
                    pass
        except Exception:
            # some cursors don't support nextset(); ignore
            pass

        raw_conn.commit()
        cur.close()
        raw_conn.close()
        return True, resultsets
    except Exception as e:
        try:
            cur.close()
        except Exception:
            pass
        try:
            raw_conn.close()
        except Exception:
            pass
        return False, str(e)


# ---------- QUERIES ----------
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


# ---------- HELPERS & TESTS ----------

def print_install_instructions():
    print('\nMissing Streamlit or other packages?')
    print('Install with:')
    print('  pip install streamlit sqlalchemy pymysql pandas')
    print("Set DB_URI like: export DB_URI='mysql+pymysql://user:pass@host:3306/ims_db'")
    print('Then run: streamlit run inventory_streamlit_app.py\n')


def smoke_tests():
    """Non-destructive checks to validate DB connectivity and basic tables."
    out = []
    try:
        with engine.connect() as conn:
            r = conn.execute(text('SELECT 1 AS ok'))
            v = r.fetchone()[0]
            out.append(('SELECT 1', v))
    except Exception as e:
        out.append(('SELECT 1', f'ERROR: {e}'))

    # try to query products (if table exists) - don't fail if it doesn't
    try:
        df = run_query_core("SELECT sku, product_name FROM products LIMIT 5")
        out.append(('products_sample_rows', df.shape[0]))
    except Exception as e:
        out.append(('products_sample_rows', f'ERROR or table missing: {e}'))

    return out


# ---------- STREAMLIT UI (if available) ----------
if HAS_STREAMLIT:
    # wrap the core query function with Streamlit caching for speed
    run_query = st.cache_data(ttl=300)(run_query_core)
    run_nonselect = run_nonselect_core
    call_procedure = call_procedure_core

    st.set_page_config(page_title='Inventory Management UI', layout='wide')
    st.title('Inventory Management — Interactive Dashboard')

    st.sidebar.header('Controls')
    selected = st.sidebar.selectbox('Select report / query', list(QUERIES.keys()))

    sku_input = st.sidebar.text_input('Filter by SKU (optional)', '')
    start_date = st.sidebar.date_input('Start date', date.today() - timedelta(days=365))
    end_date   = st.sidebar.date_input('End date', date.today())

    run = st.sidebar.button('Run Query')

    st.subheader(selected)
    sql = QUERIES[selected]
    params = {}
    if ':sku' in sql and sku_input:
        params['sku'] = sku_input

    if run:
        try:
            df = run_query(sql, params)
        except Exception as e:
            st.error(f'Query failed: {e}')
            df = pd.DataFrame()

        if df is None or df.empty:
            st.info('No rows returned.')
        else:
            st.dataframe(df)
            if selected == 'Monthly Sales Trend (12 months)':
                df['sales_month'] = pd.to_datetime(df['sales_month'] + '-01')
                df = df.set_index('sales_month').sort_index()
                st.line_chart(df['monthly_sales'])
            if selected in ('Top 5 Suppliers (6 months)','Top 5 Customers (6 months)'):
                st.bar_chart(df.set_index(df.columns[0]))
            if selected == 'Current Stock (all warehouses)':
                if 'stock_value' in df.columns:
                    top = df.groupby(['sku','product_name'])['stock_value'].sum().reset_index().sort_values('stock_value', ascending=False).head(10)
                    st.write('Top 10 products by stock value')
                    st.bar_chart(top.set_index('sku')['stock_value'])

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

    st.markdown('---')
    st.caption('Tip: set DB_URI environment variable and run this file with Streamlit. Use the schema SQL first to create objects.')


# ---------- CLI fallback (when Streamlit not installed) ----------
else:
    def cli_menu():
        print('\nStreamlit is not available in this environment.')
        print_install_instructions()

        parser = argparse.ArgumentParser(description='Inventory DB CLI fallback')
        parser.add_argument('--test', action='store_true', help='Run smoke tests and exit')
        parser.add_argument('--query', type=str, help='Run a named query from the built-in list')
        parser.add_argument('--sku', type=str, help='SKU parameter for queries that accept :sku')
        args = parser.parse_args()

        if args.test:
            print('\nRunning smoke tests...')
            results = smoke_tests()
            for t, r in results:
                print(f'{t}: {r}')
            return

        if args.query:
            qname = args.query
            if qname not in QUERIES:
                print(f'Query "{qname}" not found. Available queries:')
                for k in QUERIES.keys():
                    print(' -', k)
                return
            sql = QUERIES[qname]
            params = {}
            if ':sku' in sql and args.sku:
                params['sku'] = args.sku
            try:
                df = run_query_core(sql, params)
                if df.empty:
                    print('No rows returned.')
                else:
                    print(df.to_string(index=False))
            except Exception as e:
                print('Query failed:', e)
            return

        # interactive selection
        print('\nInteractive CLI mode: select a report to run (type number)')
        keys = list(QUERIES.keys())
        for i, k in enumerate(keys, 1):
            print(f'{i:2d}. {k}')
        print(' 0. Run smoke tests')
        try:
            choice = int(input('\nEnter choice: ').strip())
        except Exception:
            print('Invalid input')
            return

        if choice == 0:
            results = smoke_tests()
            for t, r in results:
                print(f'{t}: {r}')
            return

        if 1 <= choice <= len(keys):
            qname = keys[choice-1]
            sql = QUERIES[qname]
            params = {}
            if ':sku' in sql:
                sku = input('Enter SKU (or leave blank): ').strip()
                if sku:
                    params['sku'] = sku
            try:
                df = run_query_core(sql, params)
                if df.empty:
                    print('No rows returned.')
                else:
                    print(df.to_string(index=False))
            except Exception as e:
                print('Query failed:', e)
        else:
            print('Choice out of range.')

    # run CLI when invoked as script
    if __name__ == '__main__':
        cli_menu()

# if streamlit is present we still want to allow the file to be executed directly for debugging
if __name__ == '__main__' and HAS_STREAMLIT:
    print('Streamlit is available. To run the interactive UI, execute:')
    print('  streamlit run inventory_streamlit_app.py')
    print('\nRunning smoke tests for quick verification...')
    for t, r in smoke_tests():
        print(f'{t}: {r}')
    print('\nExiting (Streamlit UI requires streamlit run).')
