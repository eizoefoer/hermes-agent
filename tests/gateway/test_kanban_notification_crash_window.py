"""Real HTTP/native-DB characterization: remote acceptance is not a local receipt.

This is NOT exactly-once acceptance. It pins an unresolved release blocker with
an actual notifier process exit between the two independent systems' commits.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


CHILD = r'''
import asyncio, json, os, sys, time
from types import SimpleNamespace
from urllib.request import Request, urlopen
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_notify as kbn
from gateway.kanban_watchers_notifier import _KanbanNotification

# Independent processes share only a disposable native DB and the HTTP receiver.
real_time = time.time
if sys.argv[1] == "retry":
    time.time = lambda: real_time() + 1000
with kbc.connect_closing() as conn:
    if sys.argv[1] == "crash":
        tid = kb.create_task(conn, title="isolated crash window", assignee="worker")
        kbn.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="isolated-origin", thread_id="topic-7")
        kb.complete_task(conn, tid, summary="done", force=True)
    else:
        tid = conn.execute("SELECT id FROM tasks").fetchone()[0]
    key = dict(task_id=tid, platform="telegram", chat_id="isolated-origin", thread_id="topic-7")
    old, cursor, events = kbn.claim_unseen_events_for_sub(conn, **key, kinds=["completed"])
    sub = kbn.list_notify_subs(conn, tid)[0]
    sub["_claim_expires_at"] = sub["claim_expires_at"]
    task = kb.get_task(conn, tid)
    assert len(events) == 1

class HTTPAdapter:
    async def send(self, chat_id, text, metadata=None):
        payload = json.dumps(dict(chat_id=chat_id, text=text, metadata=metadata)).encode()
        with urlopen(Request(os.environ["PROBE_URL"], data=payload, headers={"Content-Type":"application/json"}), timeout=10) as response:
            assert response.status == 200
        return SimpleNamespace(success=True)

def sub_op(board, op, subscription, **kwargs):
    if op == "record_notify_ping" and sys.argv[1] == "crash":
        os._exit(77)  # receiver accepted; local successful-ping checkpoint absent
    with kbc.connect_closing() as conn:
        return getattr(kbn, op)(conn, **key,
            expected_claim_expires_at=subscription["_claim_expires_at"], **kwargs)

async def artifacts(**kwargs):
    return None
runner = SimpleNamespace(_kanban_sub_op=sub_op, _deliver_kanban_artifacts=artifacts)
notification = _KanbanNotification(runner, dict(sub=sub, task=task, events=events, cursor=cursor, old_cursor=old), platform_cls=str, sub_fail_counts={})
notification.adapter = HTTPAdapter()
assert asyncio.run(notification._send_pings()) is True
with kbc.connect_closing() as conn:
    row = kbn.list_notify_subs(conn, tid)[0]
    assert row["last_ping_event_id"] == cursor
print(json.dumps(dict(task=tid, cursor=cursor, checkpoint=cursor)))
'''


def test_remote_acceptance_before_checkpoint_replays_after_process_loss(tmp_path):
    accepted = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            accepted.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {**os.environ, "HERMES_KANBAN_DB": str(tmp_path / "native.db"),
           "PROBE_URL": f"http://127.0.0.1:{server.server_port}/accept"}
    root = Path(__file__).resolve().parents[2]
    try:
        crashed = subprocess.run([sys.executable, "-c", CHILD, "crash"], cwd=root, env=env,
                                 capture_output=True, text=True, timeout=60, check=False)
        assert crashed.returncode == 77, crashed.stdout + crashed.stderr
        assert len(accepted) == 1
        retried = subprocess.run([sys.executable, "-c", CHILD, "retry"], cwd=root, env=env,
                                 capture_output=True, text=True, timeout=60, check=False)
        assert retried.returncode == 0, retried.stdout + retried.stderr
        assert len(accepted) == 2, "Unresolved transport ambiguity must remain visible"
        assert accepted[0] == accepted[1]
        assert accepted[0]["chat_id"] == "isolated-origin"
        assert accepted[0]["metadata"]["thread_id"] == "topic-7"
        print("CRASH-WINDOW RECEIPT: notifier exit=77; accepted HTTP deliveries=2; "
              "same origin/thread/payload; retry ping checkpoint persisted. "
              "Exactly-once remote delivery is NOT supported.")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
        assert not thread.is_alive()
