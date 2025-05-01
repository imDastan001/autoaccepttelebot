from pymongo import MongoClient
import os
from dotenv import load_dotenv
load_dotenv()
MONGO_URI = os.environ.get("MONGO_URI")
DB_NAME = os.environ.get("DB_NAME")

# 🔹 Connect to MongoDB
client = MongoClient(MONGO_URI)

# 🔹 Use a single database
db = client[DB_NAME]

# 🔹 Create two collections inside the database
User_collection = db["users"]

# ✅ Test Connection
def test_connection():
    try:
        client.admin.command("ping")
        print("✅ Connected to MongoDB!")
    except Exception as e:
        print(f"❌ Connection failed: {e}")

test_connection()