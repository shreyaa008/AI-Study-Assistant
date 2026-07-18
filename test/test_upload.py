"""
test_upload.py
--------------
Quick manual test for the /upload endpoint.
Run with: python test_upload.py

Before running:
- Replace API_URL below with your real Invoke URL from API Gateway
- Make sure you have the 'requests' library: pip install requests
"""

import base64
import requests

API_URL = "https://<your-invoke-url>/dev/upload"  # <-- replace this

# Take some sample text and encode it to Base64, exactly like the
# frontend will do with a real uploaded file.
sample_text = "This is a test note for my AWS project."
encoded_content = base64.b64encode(sample_text.encode("utf-8")).decode("utf-8")

payload = {
    "student_id": "shreya01",
    "filename": "test-note.txt",
    "file_content_base64": encoded_content,
}

response = requests.post(API_URL, json=payload)

print("Status code:", response.status_code)
print("Response body:", response.json())