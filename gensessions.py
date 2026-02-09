import os
import sys

# Add the current directory to sys.path to allow importing from pyUltroid
sys.path.append(os.getcwd())

from pyUltroid.configs import Var
from pyUltroid.startup.session_gen import Session

def gen_sessions():
    """
    Manually generate/download bot.session and asst.session 
    using the remote API configuration.
    """
    print("🚀 Starting Ultroid Session Downloader...")

    # 1. Main Bot Session (bot.session)
    print("\n📦 Processing: bot.session")
    if not (Var.SESSION and (len(Var.SESSION) > 50 or Var.SESSION.startswith("1"))):
        print("   -> Downloading main session from remote API...")
        Session("bot").call(
            api_id=Var.API_ID,
            api_hash=Var.API_HASH,
            bot_token=Var.BOT_TOKEN,
            lib="telethon"
        ).download()
    else:
        print("   -> Skipping: Valid string session already exists in environment.")

    # 2. Assistant Bot Session (asst.session)
    print("\n📦 Processing: asst.session")
    if Var.BOT_TOKEN:
        print("   -> Downloading assistant session from remote API...")
        Session("asst").call(
            api_id=Var.API_ID,
            api_hash=Var.API_HASH,
            bot_token=Var.BOT_TOKEN,
            lib="telethon"
        ).download()
    else:
        print("   -> Error: BOT_TOKEN is missing. Cannot download assistant session.")

    print("\n✅ Process finished. If successful, you should see 'bot.session' and 'asst.session' in this directory.")

if __name__ == "__main__":
    gen_sessions()
