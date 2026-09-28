"""
NEURAL-002 (PRD §24): no production weights change because of one interaction.

FRIDAY has no training code today. The one local model (`friday/reflex.py`,
Needle) is loaded for inference and nothing else, so the rule holds by
absence - which is exactly how a rule stops holding without anyone noticing.
A well-meant "learn from this correction" that calls `optimizer.step()`
inside a voice turn is one import and three lines.

So this is an architecture test over the product source (`friday/` and the
entry points): no weight-update primitive - an optimiser step, a gradient,
a fit, a trainer, a weight save, an in-place edit of a tensor's `.data` -
outside the candidate path. PRD §24.11's
lifecycle (verified trajectory -> dataset -> candidate -> holdout eval ->
promotion) is the only way weights may change, and a candidate trained
there reaches production only through promotion. No module lives there
yet, so today nothing at all is exempt.

The scanner reads the AST, not the text: `reflex.py` discusses fine-tuning
in prose, and prose is not a weight update. It is a guard against ordinary
code, not an adversarial sandbox - `getattr(opt, "st" + "ep")()` would pass
it. A delegated worker writing into a model cache is the execution
envelope's concern (SEC-005), not this scan's.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: The shipped behaviour. `scripts/`, `tests/` and `third_party/` are not.
PRODUCT = ("friday", "server.py", "agent_friday.py", "main.py", "start.py",
           "friday_exe.py")

#: Where a candidate may be trained (PRD §24.11). Empty today.
CANDIDATE_PATHS = ("friday/neural/candidates/",)

#: Importing these is itself a training capability.
TRAINING_MODULES = ("torch.optim", "torch.autograd", "optax", "peft", "trl",
                    "tensorflow.keras.optimizers", "keras.optimizers",
                    "transformers.trainer", "accelerate")

#: Imported or called by name, these train (`from transformers import Trainer`).
TRAINING_NAMES = frozenset({"Trainer", "Seq2SeqTrainer", "SFTTrainer",
                            "DPOTrainer", "get_peft_model", "LoraConfig"})

#: Method calls that change or persist weights, whatever the receiver.
UPDATE_METHODS = frozenset({"backward", "zero_grad", "apply_gradients", "fit",
                            "partial_fit", "train_on_batch", "save_pretrained",
                            "save_weights", "push_to_hub"})

#: `step()` is only an update on an optimiser or a schedule.
STEPPERS = re.compile(r"optim|^opt$|sched", re.IGNORECASE)

#: Touching these is gradient machinery, read or written.
GRADIENT_ATTRS = frozenset({"grad", "requires_grad", "requires_grad_"})


def _dotted(node: ast.AST) -> str:
    """`self.optimizer` -> "self.optimizer"; anything else -> ""."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = _dotted(node.value)
        return f"{head}.{node.attr}" if head else node.attr
    return ""


def _is_training_module(name: str) -> bool:
    return any(name == m or name.startswith(m + ".") for m in TRAINING_MODULES)


def violations_in(source: str, rel: str) -> list[str]:
    """Every weight-update primitive in one module, as "path:line what"."""
    found = []

    def hit(node: ast.AST, what: str) -> None:
        found.append(f"{rel}:{node.lineno} {what}")

    for node in ast.walk(ast.parse(source, filename=rel)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_training_module(alias.name):
                    hit(node, f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            if _is_training_module(node.module):
                hit(node, f"from {node.module} import ...")
            for alias in node.names:
                if (alias.name in TRAINING_NAMES
                        or (_is_training_module(f"{node.module}.{alias.name}"))
                        or (node.module.startswith("safetensors")
                            and alias.name.startswith("save"))):
                    hit(node, f"from {node.module} import {alias.name}")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in TRAINING_NAMES:
                hit(node, f"{func.id}(...)")
            elif ((isinstance(func, ast.Name) and func.id == "__import__")
                  or (isinstance(func, ast.Attribute)
                      and func.attr == "import_module")):
                first = node.args[0] if node.args else None
                if (isinstance(first, ast.Constant)
                        and isinstance(first.value, str)
                        and _is_training_module(first.value)):
                    hit(node, f"dynamic import of {first.value}")
            elif isinstance(func, ast.Attribute):
                receiver = _dotted(func.value)
                last = receiver.rsplit(".", 1)[-1]
                if func.attr in UPDATE_METHODS:
                    hit(node, f"{receiver or '<expr>'}.{func.attr}()")
                elif func.attr == "step" and STEPPERS.search(last):
                    hit(node, f"{receiver}.step()")
                elif (last == "data" and func.attr.endswith("_")
                      and not func.attr.startswith("_")):
                    # PyTorch's in-place convention: `p.data.add_(g)` edits a
                    # weight directly, no optimiser needed.
                    hit(node, f"{receiver}.{func.attr}()")
                elif func.attr == "train" and "trainer" in last.lower():
                    hit(node, f"{receiver}.train()")
                elif receiver == "torch" and func.attr == "save":
                    hit(node, "torch.save()")
                elif func.attr in TRAINING_NAMES:
                    hit(node, f"{receiver}.{func.attr}(...)")
        elif isinstance(node, ast.Attribute) and node.attr in GRADIENT_ATTRS:
            hit(node, f"{_dotted(node.value) or '<expr>'}.{node.attr}")
    return found


def _product_files() -> list[Path]:
    files = []
    for entry in PRODUCT:
        path = ROOT / entry
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(sorted(path.rglob("*.py")))
    return files


def _exempt(rel: str) -> bool:
    return rel.startswith(CANDIDATE_PATHS)


def test_no_production_module_updates_weights():
    found = []
    for path in _product_files():
        rel = path.relative_to(ROOT).as_posix()
        if not _exempt(rel):
            found += violations_in(path.read_text(encoding="utf-8"), rel)
    assert found == [], (
        "weight-update primitives outside the candidate path "
        f"{CANDIDATE_PATHS} - train a candidate there and promote it:\n"
        + "\n".join(found))


def test_the_scan_reads_the_product_it_claims_to():
    """A scanner pointed at the wrong directory passes by reading nothing."""
    names = {p.relative_to(ROOT).as_posix() for p in _product_files()}
    assert len(names) > 100
    assert {"friday/reflex.py", "friday/voice_brain.py", "server.py"} <= names


PLANTED = [
    "def learn(optimizer, loss):\n    loss.backward()\n",
    "def learn(self):\n    self.optimizer.step()\n",
    "def learn(opt):\n    opt.step()\n",
    "def learn(scheduler):\n    scheduler.step()\n",
    "def learn(model, x, y):\n    model.fit(x, y)\n",
    "def learn(model, x, y):\n    model.partial_fit(x, y)\n",
    "import torch\ndef keep(model):\n    torch.save(model.state_dict(), 'prod.pt')\n",
    "def keep(model):\n    model.save_pretrained('models/reflex')\n",
    "import torch.optim as optim\n",
    "from torch import optim\n",
    "from peft import get_peft_model\n",
    "from transformers import Trainer\n",
    "from safetensors.torch import save_file\n",
    "import importlib\noptim = importlib.import_module('torch.optim')\n",
    "peft = __import__('peft')\n",
    "def tune(trainer):\n    trainer.train()\n",
    "def nudge(p, g):\n    p.data.add_(g)\n",
    "def decay(layer):\n    layer.weight.data.mul_(0.9)\n",
    "def learn(p):\n    return p.grad\n",
    "def learn(w):\n    w.requires_grad = True\n",
    "def learn(w):\n    w.requires_grad_()\n",
]


@pytest.mark.parametrize("source", PLANTED)
def test_a_planted_weight_update_is_caught(source):
    """The negative control: each primitive, planted, is found."""
    assert violations_in(source, "friday/voice_brain.py")


@pytest.mark.parametrize("source", [
    '"""We could fine-tune with LoRA and call optimizer.step()."""\n',
    "# model.fit(x, y) would retrain the reflex\n",
    "def run(plan):\n    plan.step()\n",
    "def run(model):\n    model.train_mode = False\n",
    "import torch\ndef infer(model, x):\n    return model(x)\n",
    "import torch\ndef infer(m, x):\n    with torch.no_grad():\n        return m(x)\n",
    "def read(self):\n    return self.data.get('x')\n",
])
def test_inference_and_prose_are_not_updates(source):
    assert violations_in(source, "friday/voice_brain.py") == []


def test_only_the_candidate_path_is_exempt():
    assert _exempt("friday/neural/candidates/reflex_lora.py")
    assert not _exempt("friday/neural/serve.py")
    assert not _exempt("friday/voice_brain.py")
