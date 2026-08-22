import os, tempfile, shutil
from datetime import datetime
from backend.settings import get_settings
from backend.recording.manager import RecordingManager
from backend.recording.storage import LocalFileStorageProvider
from backend.recording.models import Recording, Evidence
from backend.models import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Setup temp storage and DB
storage_dir = tempfile.mkdtemp(prefix="recording_storage_")
# Use SQLite in temp file
sqlite_path = os.path.join(tempfile.mkdtemp(prefix="recording_db_"), "test.db")
engine = create_engine(f"sqlite:///{sqlite_path}", connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

# Configure settings via env vars
os.environ["OMNITRACK_RECORDING_ENABLED"] = "True"
os.environ["OMNITRACK_RECORDING_STORAGE_PATH"] = storage_dir
# Ensure settings picks up new env
settings = get_settings()

# Create manager
manager = RecordingManager(Session(), LocalFileStorageProvider(storage_dir), settings)

# Start recording
stream_id = "smoke_stream"
manager.start_recording(stream_id)

# Push dummy frames (simple dicts)
for i in range(5):
    manager.push_frame(stream_id, {"frame": i})

# Trigger event to generate evidence
manager.trigger_event(stream_id, "test_event", {"info": "smoke"})
# List evidences
vids = manager.list_evidences()
print("Evidences count:", len(vids))
if vids:
    ev = vids[0]
    print("Evidence file path:", ev.file_path)
    # Check file exists
    full_path = os.path.join(storage_dir, ev.file_path)
    print("File exists:", os.path.exists(full_path))

# Stop recording
manager.stop_recording(stream_id)

# Cleanup
shutil.rmtree(storage_dir, ignore_errors=True)
shutil.rmtree(os.path.dirname(sqlite_path), ignore_errors=True)
