"""Manual probe of the model-gateway worker against the real Hermes install."""
import json
import os
import subprocess
import sys
import time

from dotenv import load_dotenv

load_dotenv()
py = os.environ["HERMES_PYTHON"]
root = os.environ["HERMES_DIR"]
env = dict(os.environ)
for k in ("HERMES_SESSION_ID", "HERMES_AGENT", "MSYSTEM", "MSYS", "SHELL"):
    env.pop(k, None)
env["HERMES_HOME"] = r"D:\hermes\profiles\friday"
env["PYTHONUNBUFFERED"] = "1"
worker = os.path.abspath("friday/hermes_model_gateway_worker.py")
t0 = time.time()
p = subprocess.Popen([py, worker], cwd=root, env=env, stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                     encoding="utf-8", bufsize=1)


def call(obj):
    p.stdin.write(json.dumps(obj) + "\n")
    p.stdin.flush()
    line = p.stdout.readline()
    return json.loads(line)


print("hello", call({"id": 1, "method": "hello"}), round(time.time() - t0, 2))
t = time.time()
r = call({"id": 2, "method": "providers"})
res = r.get("result", {})
print("providers", round(time.time() - t, 2),
      [(x["id"], x["route_kind"]) for x in res.get("providers", []) if x.get("authenticated")],
      res.get("main"))
t = time.time()
r = call({"id": 3, "method": "infer", "params": {
    "messages": [{"role": "user", "content": "Reply with exactly the word PONG"}],
    "max_output_tokens": 8, "temperature": 0, "timeout_s": 40}})
print("infer-default", round(time.time() - t, 2), json.dumps(r)[:600])
t = time.time()
r = call({"id": 4, "method": "infer", "params": {
    "provider": "anthropic", "model": "claude-haiku-4-5",
    "messages": [{"role": "user", "content": "Reply with exactly the word PONG"}],
    "max_output_tokens": 8, "temperature": 0, "timeout_s": 40}})
print("infer-haiku", round(time.time() - t, 2), json.dumps(r)[:600])
t = time.time()
r = call({"id": 5, "method": "infer", "params": {
    "provider": "opencode-free", "model": "deepseek-v4-flash-free",
    "messages": [{"role": "user", "content": "Reply with exactly the word PONG"}],
    "max_output_tokens": 8, "temperature": 0, "timeout_s": 40}})
print("infer-free", round(time.time() - t, 2), json.dumps(r)[:400])
call({"id": 6, "method": "shutdown"})
p.wait(timeout=10)
print("stderr tail:", p.stderr.read()[-500:])
