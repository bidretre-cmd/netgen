import sqlite3
import os

db_path = os.path.join('e:\\123w1q2s\\project antibosan\\netflix\\webapp', 'instance', 'app.db')

if not os.path.exists(db_path):
    print(f"Database not found at {db_path}")
    exit(1)

try:
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    # Check if column exists
    c.execute("PRAGMA table_info(users)")
    columns = [info[1] for info in c.fetchall()]
    
    if 'ads_percentage' not in columns:
        c.execute("ALTER TABLE users ADD COLUMN ads_percentage INTEGER DEFAULT 0")
        conn.commit()
        print("Column 'ads_percentage' added successfully.")
    else:
        print("Column 'ads_percentage' already exists.")
        
    conn.close()
except Exception as e:
    print(f"Error: {e}")
