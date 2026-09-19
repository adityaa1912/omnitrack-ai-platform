import time
import threading
from unittest.mock import MagicMock
import pytest
from prometheus_client import REGISTRY

from backend.scheduler import InferenceScheduler
from inference.types import Frame

@pytest.fixture
def mock_db_factory():
    def factory():
        mock = MagicMock()
        return mock
    factory.remove = MagicMock()
    return factory


@pytest.mark.perf
def test_fairness_multiple_streams(mock_db_factory):
    scheduler = InferenceScheduler(
        session_factory=mock_db_factory,
        num_workers=2,
        stream_queue_capacity=5
    )
    
    # Track which stream each frame was processed for
    processed = []
    def create_process_fn(stream_id):
        def process_fn(frame, db):
            processed.append(stream_id)
            time.sleep(0.01) # Simulate some work
        return process_fn
        
    scheduler.register("s1", create_process_fn("s1"))
    scheduler.register("s2", create_process_fn("s2"))
    scheduler.register("s3", create_process_fn("s3"))
    
    # Submit bursts to all streams slowly to avoid dropping
    for i in range(5):
        scheduler.submit("s1", Frame(frame_id=i, data=None, timestamp=time.time()))
        scheduler.submit("s2", Frame(frame_id=i, data=None, timestamp=time.time()))
        scheduler.submit("s3", Frame(frame_id=i, data=None, timestamp=time.time()))
        time.sleep(0.02)
        
    # Wait for processing
    time.sleep(0.5)
    
    # We should have processed at least 10 frames total out of 15
    assert len(processed) >= 10
    
    # Check that all streams were processed relatively fairly
    s1_count = processed.count("s1")
    s2_count = processed.count("s2")
    s3_count = processed.count("s3")
    
    assert s1_count >= 3
    assert s2_count >= 3
    assert s3_count >= 3
    
    scheduler.stop()


@pytest.mark.perf
def test_queue_full_blocking(mock_db_factory):
    scheduler = InferenceScheduler(
        session_factory=mock_db_factory,
        num_workers=1,
        stream_queue_capacity=2
    )
    
    # A process function that hangs until we let it go
    event = threading.Event()
    def process_fn(frame, db):
        event.wait()
        
    scheduler.register("s1", process_fn)
    
    # Submit frames to fill the queue and in_flight slot
    # worker will pick up frame 1 and block
    scheduler.submit("s1", Frame(frame_id=1, data=None, timestamp=time.time()))
    time.sleep(0.05)
    
    # These two will sit in the queue (capacity=2)
    scheduler.submit("s1", Frame(frame_id=2, data=None, timestamp=time.time()))
    scheduler.submit("s1", Frame(frame_id=3, data=None, timestamp=time.time()))
    
    # The fourth submit should block
    submit_blocked = []
    def blocking_submit():
        scheduler.submit("s1", Frame(frame_id=4, data=None, timestamp=time.time()))
        submit_blocked.append(True)
        
    t = threading.Thread(target=blocking_submit)
    t.start()
    
    time.sleep(0.1)
    # Thread should be blocked
    assert len(submit_blocked) == 0
    
    # Now release the worker
    event.set()
    
    # The blocked submitter should eventually finish
    t.join(timeout=2.0)
    assert len(submit_blocked) == 1
    
    scheduler.stop()


@pytest.mark.perf
def test_shutdown_deadlock_prevention(mock_db_factory):
    scheduler = InferenceScheduler(
        session_factory=mock_db_factory,
        num_workers=1,
        stream_queue_capacity=1
    )
    
    event = threading.Event()
    def process_fn(frame, db):
        event.wait()
        
    scheduler.register("s1", process_fn)
    
    # Worker grabs frame 1
    scheduler.submit("s1", Frame(frame_id=1, data=None, timestamp=time.time()))
    time.sleep(0.05)
    
    # Queue is full with frame 2
    scheduler.submit("s1", Frame(frame_id=2, data=None, timestamp=time.time()))
    
    # Submitter blocks on frame 3
    submit_completed = []
    def blocking_submit():
        scheduler.submit("s1", Frame(frame_id=3, data=None, timestamp=time.time()))
        submit_completed.append(True)
        
    t = threading.Thread(target=blocking_submit)
    t.start()
    time.sleep(0.1)
    assert len(submit_completed) == 0
    
    # Now unregister the channel!
    # The worker is still in-flight, so unregister will wait up to _UNREGISTER_TIMEOUT
    # BUT we want to ensure the blocking submit thread is woken up safely!
    
    def do_unregister():
        scheduler.unregister("s1")
        
    u = threading.Thread(target=do_unregister)
    u.start()
    time.sleep(0.1)
    
    # Allow worker to finish so unregister can complete
    event.set()
    
    u.join(timeout=2.0)
    t.join(timeout=2.0)
    
    # Submitter should have completed cleanly without deadlocking
    assert len(submit_completed) == 1
    
    scheduler.stop()
