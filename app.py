import streamlit as st
import sqlite3
import pandas as pd
import requests
import re
import time

# --- CONFIGURATION ---
DB_NAME = "lobbying_pro.db"
BASE_URL = "https://lda.gov/api/v1/"

# --- DATABASE SETUP ---
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

def fetch_bulk_data(api_key, target_year, max_pages=10):
    """
    Handles bulk fetching. Uses API Key if provided.
    The API requires a filter (like year) to allow pagination.
    """
    headers = {}
    if api_key:
        # Standard LDA.gov header format: Authorization: Token <key>
        headers['Authorization'] = f"Token {api_key}"
    
    conn = sqlite3.connect(DB_NAME)
    records_count = 0
    
    # Progress tracking in UI
    progress_bar = st.progress(0)
    status_text = st.empty()

    next_url = f"{BASE_URL}filings/?filing_year={target_year}&page_size=25"

    for p in range(max_pages):
        if not next_url:
            break
            
        status_text.text(f"Fetching page {p+1} for year {target_year}...")
        try:
            response = requests.get(next_url, headers=headers)
            if response.status_code == 429:
                st.error("Rate limit hit! Wait a minute or add an API Key for 8x faster syncing.")
                break
            
            data = response.json()
            filings = data.get('results', [])
            
            for f in filings:
                uuid = f['filing_uuid']
                # De-duplication logic
                conn.execute("UPDATE filings SET is_current=0 WHERE client_name=? AND year=? AND period=?", 
                             (f['client']['name'], f['filing_year'], f['filing_period']))
                
                issues = " ".join([a.get('specific_lobbying_issues', '') or '' for a in f.get('lobbying_activities', [])])
                bills = ", ".join(list(set(re.findall(r'(?:S\.|H\.R\.)\s*\d+', issues))))
                
                conn.execute("INSERT OR REPLACE INTO filings VALUES (?,?,?,?,?,?,?,?,?)",
                             (uuid, f['registrant']['name'], f['client']['name'], f['amount'], 
                              f['filing_year'], f['filing_period'], issues, bills, 1))
                
                for act in f.get('lobbying_activities', []):
                    for lob in act.get('lobbyists', []):
                        is_rev, tier = classify_revolving_door(lob['covered_position'])
                        conn.execute("INSERT INTO lobbyists (uuid, name, position, is_revolving, tier) VALUES (?,?,?,?,?)", 
                                     (uuid, f"{lob['first_name']} {lob['last_name']}", lob['covered_position'], is_rev, tier))
            
            records_count += len(filings)
            next_url = data.get('next')
            progress_bar.progress((p + 1) / max_pages)
            
            # Be kind to the API
            time.sleep(0.1 if api_key else 0.5)
            
        except Exception as e:
            st.error(f"Error on page {p+1}: {e}")
            break
            
    conn.commit()
    conn.close()
    return records_count

# --- UI CODE ---
st.set_page_config(page_title="Lobbying Bulk Pro", layout="wide")
st.title("🏛️ InfluenceTracker Bulk: Public Citizen Pro")

init_db()

with st.sidebar:
    st.header("🔑 Authentication")
    api_key = st.text_input("LDA.gov API Key", type="password", help="Register at lda.gov to get a key for 8x faster data pulls.")
    
    st.divider()
    st.header("📥 Bulk Data Sync")
    year_to_sync = st.selectbox("Select Year to Fetch", range(2025, 2015, -1))
    pages_to_sync = st.slider("Number of pages (25 records each)", 1, 100, 10)
    
    if st.button("🚀 Start Bulk Sync"):
        with st.spinner("Processing bulk data..."):
            count = fetch_bulk_data(api_key, year_to_sync, pages_to_sync)
            st.success(f"Successfully added {count} filings to your local database!")

# --- SEARCH AREA ---
st.header("🔍 Research Search")
with st.form("search_form"):
    col1, col2, col3 = st.columns(3)
    with col1: q_client = st.text_input("Client")
    with col2: q_bill = st.text_input("Bill Number")
    with col3: rev_only = st.checkbox("Revolving Door Only")
    submit = st.form_submit_button("Run Analysis")

# --- RESULTS ---
conn = sqlite3.connect(DB_NAME)
if submit:
    query = """
        SELECT f.client_name as Client, f.reg_name as Firm, f.amount as Spend, 
               f.year as Year, f.period as Qtr, f.bills as Bills, 
               group_concat(l.name || ' (' || l.tier || ')') as Lobbyists
        FROM filings f
        LEFT JOIN lobbyists l ON f.uuid = l.uuid
        WHERE f.is_current = 1
    """
    params = []
    if q_client:
        query += " AND f.client_name LIKE ?"
        params.append(f'%{q_client}%')
    if q_bill:
        query += " AND f.bills LIKE ?"
        params.append(f'%{q_bill}%')
    if rev_only:
        query += " AND l.is_revolving = 1"
        
    query += " GROUP BY f.uuid ORDER BY f.amount DESC"
    
    df = pd.read_sql(query, conn, params=params)
    st.dataframe(df, use_container_width=True)
    st.download_button("📥 Download CSV", df.to_csv(index=False), "lobbying_data.csv")
else:
    # Stats overview
    st.write("### Database Statistics")
    stats = pd.read_sql("SELECT year, count(*) as count, sum(amount) as total FROM filings WHERE is_current=1 GROUP BY year", conn)
    if not stats.empty:
        st.bar_chart(stats.set_index('year')['count'])
        st.table(stats)
    else:
        st.info("Your local database is empty. Use the sidebar to pull data from the government API.")

conn.close()
