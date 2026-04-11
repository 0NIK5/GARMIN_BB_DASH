#!/usr/bin/env python
"""Reset the database by removing the old file and letting the app recreate it."""
import os
import sys
import time

db_path = "data/body_battery.db"

# Kill any lingering Python processes (if running via this script)
if os.name == 'nt':  # Windows
    os.system("taskkill /F /IM python.exe 2>nul")
    time.sleep(1)

# Remove the old database
if os.path.exists(db_path):
    try:
        os.remove(db_path)
        print(f"✅ Deleted {db_path}")
    except PermissionError:
        print(f"❌ Cannot delete {db_path} - it's still in use")
        print("Try closing all Python processes and VSCode, then run this script again")
        sys.exit(1)
else:
    print(f"Database not found at {db_path}")

print("✅ Database reset complete. Start backend and worker to recreate with new schema.")
