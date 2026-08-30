"""
Secure secret entry — a separate PROCESS whose text field the model
structurally cannot read.

Why a subprocess and not a function: the entry window runs in its own
Python process; the typed value goes from that process's masked Tk field
directly into Windows Credential Manager and only the opaque ref crosses
back over stdout. Friday's process - and therefore every tool result,
transcript, log line, and model context - never holds the plaintext even
transiently. This replaces the interim scratch-file surface in
secret_broker.py for provider credentials (no file ever exists to shred).

The window is deliberately as simple as Notepad: one masked field, one
button, one sentence telling the user Friday cannot read the field.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

#: The whole child program. Inline so there is no separate script file to
#: drift; it imports only stdlib + friday.connectors.wincred.
_CHILD = textwrap.dedent("""
    import json, sys, tkinter as tk

    sys.path.insert(0, sys.argv[3])
    from friday.connectors import wincred

    title, name = sys.argv[1], sys.argv[2]
    out = {"status": "cancelled"}

    root = tk.Tk()
    root.title(title)
    root.attributes("-topmost", True)
    root.resizable(False, False)
    tk.Label(root, text=title, font=("Segoe UI", 12, "bold"),
             anchor="w").pack(fill="x", padx=14, pady=(12, 2))
    tk.Label(root, text="Paste your key below. Friday cannot read this "
             "field;\\nit goes straight into Windows Credential Manager.",
             justify="left", anchor="w").pack(fill="x", padx=14)
    entry = tk.Entry(root, show="\\u2022", width=52, font=("Consolas", 11))
    entry.pack(padx=14, pady=10)
    entry.focus_set()

    def save(event=None):
        global out
        value = entry.get().strip()
        if not value:
            return
        ref = wincred.write(name, value)
        out = {"status": "stored", "credential_ref": ref,
               "length": len(value)}
        root.destroy()

    tk.Button(root, text="Save & Connect", command=save,
              width=18).pack(pady=(0, 12))
    root.bind("<Return>", save)
    root.mainloop()
    print(json.dumps(out))
""")


def request_secret(*, title: str, credential_name: str,
                   timeout: float = 300.0) -> dict:
    """Open the window; block until saved/closed. Returns metadata only:
    {status, credential_ref?, length?} - never the value."""
    from pathlib import Path

    repo_root = str(Path(__file__).resolve().parents[2])
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _CHILD, title, credential_name,
             repo_root],
            capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"status": "timeout",
                "note": "the entry window was left open too long"}
    for line in reversed((proc.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                break
    return {"status": "cancelled",
            "note": (proc.stderr or "window closed")[-200:]}
