import streamlit as st
import sqlite3
import pandas as pd
import requests
import re

# --- DATABASE SETUP ---
DB_NAME = "lobbying.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    conn.execute('''CREATE TABLE IF NOT EXISTS filings 
                    (uuid TEXT PRIMARY KEY, reg_name TEXT, client_name TEXT, 
                     amount REAL, year INTEGER, period TEXT, issues TEXT, 
                     bills TEXT, is_current BOOLEAN)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS lobbyists 
                    (uuid TEXT, name TEXT, position TEXT, is_revolving BOOLEAN, tier TEXT)''')
    conn.commit()
    return conn

def classify_revolving_door(text):
    if not text: return False, "None"
    high_tier = r'(Senator|Member|Representative|Congress|Secretary|Director|General Counsel)'
    mid_tier = r'(Chief of Staff|Counsel|Advisor|Legislative Director|LD|COS)'
    if re.search(high_tier, text, re.I): return True, "High-Level"
    if re.search(mid_tier, text, re.I): return True, "Mid-Level"
    return True, "Staff"

def sync_data(limit=100):
    conn = sqlite3.connect(DB_NAME)
    # Using the official LDA.gov API
    API_URL = "https://lda.gov/api/v1/filings/"
    try:
        res = requests.get(API_URL, params={'ordering': '-dt_posted', 'page_size': limit}).json()
        for f in res.get('results', []):
            uuid = f['filing_uuid']
            # Deduplication logic: Hide old versions of the same filing
            conn.execute("UPDATE filings SET is_current=0 WHERE client_name=? AND year=? AND period=?", 
                         (f['client']['name'], f['filing_year'], f['filing_period']))
            
            all_issues = " ".join([a.get('specific_lobbying_issues', '') or '' for a in f.get('lobbying_activities', [])])
            bills = ", ".join(list(set(re.findall(r'(?:S\.|H\.R\.)\s*\d+', all_issues))))
            
            conn.execute("INSERT OR REPLACE INTO filings VALUES (?,?,?,?,?,?,?,?,?)",
                         (uuid, f['registrant']['name'], f['client']['name'], f['amount'], 
                          f['filing_year'], f['filing_period'], all_issues, bills, 1))
            
            for act in f.get('lobbying_activities', []):
                for lob in act.get('lobbyists', []):
                    full_name = f"{lob['first_name']} {lob['last_name']}"
                    is_rev, tier = classify_revolving_door(lob['covered_position'])
                    conn.execute("INSERT INTO lobbyists VALUES (?,?,?,?,?)", 
                                 (uuid, full_name, lob['covered_position'], is_rev, tier))
        conn.commit()
    except Exception as e:
        st.error(f"Sync failed: {e}")
    finally:
        conn.close()

# --- UI CODE ---
st.set_page_config(page_title="Lobbying Research Tool", layout="wide")
st.title("🏛️ Public Citizen Lobbying Research Tool")

# Ensure DB is ready
init_db()

with st.sidebar:
    st.header("Controls")
    sync_amount = st.slider("Records to fetch", 10, 500, 100)
    if st.button("🔄 Sync with Senate Data"):
        with st.spinner("Updating database..."):
            sync_data(limit=sync_amount)
            st.success("Sync complete!")

# Filters
c1, c2, c3 = st.columns(3)
with c1: q_client = st.text_input("Client Name (e.g. Pfizer)")
with c2: q_bill = st.text_input("Bill Number (e.g. S.1234)")
with c3: rev_only = st.checkbox("Revolving Door Only")

# Query
conn = sqlite3.connect(DB_NAME)
query = """
    SELECT f.client_name, f.reg_name as Firm, f.amount, f.year, f.period, f.bills, 
           group_concat(l.name || ' (' || l.tier || ')') as Lobbyists
    FROM filings f
    LEFT JOIN lobbyists l ON f.uuid = l.uuid
    WHERE f.is_current = 1
"""
if q_client: query += f" AND f.client_name LIKE '%{q_client}%'"
if q_bill: query += f" AND f.bills LIKE '%{q_bill}%'"
if rev_only: query += " AND l.is_revolving = 1"
query += " GROUP BY f.uuid ORDER BY f.amount DESC"

df = pd.read_sql(query, conn)
st.dataframe(df, use_container_width=True)

# Export
csv = df.to_csv(index=False).encode('utf-8')
st.download_button("📥 Download Analysis CSV", data=csv, file_name="lobbying_data.csv")
