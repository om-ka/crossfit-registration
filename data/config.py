import os
from dotenv import load_dotenv

# Load environment variables only if a local .env exists (avoid find_dotenv/__main__ issues in Azure)
if os.path.exists(".env"):
    load_dotenv(".env", override=False)

class Config:
    def __init__(self):
        self.user_creds = {
            'email': os.getenv('ARBOX_USER_EMAIL') or '',
            'password': os.getenv('ARBOX_USER_PASSWORD') or ''
        }
        self.alertzy_account_key = os.getenv('ALERTZY_ACCOUNT_KEY') or ''
        self.timezone = os.getenv('TZ') or "Asia/Jerusalem"

# Create global config instance
config = Config()
