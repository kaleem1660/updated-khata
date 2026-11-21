"""Admin tool: unlock a shop after 3 wrong PINs.   python scripts/unlock.py "Demo Kirana" """
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from voicekhata import db  # noqa: E402

if len(sys.argv) != 2:
    sys.exit('usage: python scripts/unlock.py "Shop Name"')
conn = db.connect(os.getenv("VK_DB", "voice_khata.db"))
db.unlock_shop(conn, sys.argv[1])
print("unlocked:", sys.argv[1])
