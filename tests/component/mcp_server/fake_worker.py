"""Stand-in for src.mcp_server.worker: behaviour is read from the job's request.json."""

import json
import sys
import time
from pathlib import Path

job_dir = Path(sys.argv[1])
behaviour = json.loads((job_dir / "request.json").read_text())["spec"]["behaviour"]
print(f"fake worker {behaviour}", flush=True)
(job_dir / "progress.json").write_text(json.dumps({"phase": "running"}))

if behaviour == "sleep":
    time.sleep(60)
elif behaviour == "ignore_term":
    import signal

    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(60)
elif behaviour == "crash":
    sys.exit(3)
elif behaviour == "fail":
    error = {"code": "data_not_found", "message": "no bars", "fix": "import"}
    (job_dir / "result.json").write_text(json.dumps({"status": "failed", "error": error}))
    sys.exit(1)
elif behaviour.startswith("ok"):
    time.sleep(0.2)
    (job_dir / "result.json").write_text(json.dumps({"status": "ok", "run_id": behaviour}))
