import os

from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_USERS = [int(user_id) for user_id in os.getenv("ADMIN_USERS").split(",")]
REDIS_URL = os.getenv("REDIS_URL")
LIMIT_TIME = int(os.getenv("LIMIT_TIME"))
LIMIT_MESSAGES = int(os.getenv("LIMIT_MESSAGES"))
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "10"))
