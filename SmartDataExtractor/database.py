import sqlite3
def create_database(database_path: str):
    try:
        # Connect to SQLite database (or create it if it doesn't exist)
        conn = sqlite3.connect(database_path)
        cursor = conn.cursor()
        # Create Models table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS Models(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT NOT NULL,
        brand TEXT NOT NULL,
        model TEXT NOT NULL,
        truncated_model TEXT NOT NULL,
        unified_model TEXT,
        UNIQUE (brand, truncated_model)
        )
        ''')
        # Create Features table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS Features(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        audit_id INTEGER NULL,
        model_id INTEGER NULL,
        codemodel_id INTEGER NULL,
        name TEXT NOT NULL,
        value TEXT NOT NULL,
        FOREIGN KEY (audit_id) REFERENCES Audits(id),
        FOREIGN KEY (model_id) REFERENCES Models(id),
        UNIQUE (model_id, name)
        )
        ''')
        # Create Audits table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS Audits(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codemodel_id INTEGER NULL,
        model_id INTEGER NULL,
        full_text TEXT NOT NULL UNIQUE,
        updated_date DATETIME NOT NULL,
        truncated_text TEXT NOT NULL,
        uncertain_brand TEXT,
        uncertain_category TEXT,
        uncertain_type TEXT,
        search_titles TEXT,
        search_descriptions TEXT,
        category TEXT,
        brand TEXT,
        model TEXT,
        state INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (model_id) REFERENCES Models(id)
        )
        ''')

        # Commit changes and close the connection
        conn.commit()
        conn.close()

        print("Database and tables created successfully.")
    except sqlite3.Error as e:
        print(f"Error: {e}")
    finally:
        conn.close()


if __name__ == '__main__':
    create_database('SmartDataExtractor/retails.db')