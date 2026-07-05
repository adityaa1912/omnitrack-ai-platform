import re

def callback(message):
    return re.sub(
        rb"\nCo-Authored-By: Claude.*?<noreply@anthropic\.com>\n?",
        b"",
        message,
        flags=re.IGNORECASE,
    )