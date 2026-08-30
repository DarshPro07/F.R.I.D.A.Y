# F.R.I.D.A.Y

A local-first AI assistant built to use a computer, work with tools, remember context, and handle longer tasks without needing every step spelled out.

Friday started as an experiment: how close can I get to an assistant that feels less like a chatbot and more like something I can actually work with?

## What Friday can do

Friday can:

* understand a task and decide how to approach it
* use tools and browser automation
* work with Hermes for coding and longer execution tasks
* remember useful context between interactions
* connect to different AI models and services
* use voice through LiveKit, or through its own control room
* recognise my face before it will do anything at all
* look through the camera or at my screen when I ask it to
* inspect its available capabilities instead of pretending something exists
* recover from failed operations and continue where possible
* ask for permission before sensitive actions
* work with the computer instead of only replying with text

## How it works

Friday is the assistant and orchestration layer.

Hermes handles execution-heavy work such as coding, commands, repository changes and longer agent tasks.

The rest of the system provides memory, tools, connectors, permissions, browser control, voice and capability discovery.

```text
User
  |
  v
Friday
  |
  +-- Memory
  +-- Tools
  +-- Browser
  +-- Voice / LiveKit
  +-- Connectors
  +-- Capability System
  |
  v
Hermes
  |
  v
Execution
```

## Why I built it

I don't want to give an assistant ten prompts just to complete one task.

The goal is to be able to say what I want done and let Friday handle the technical steps, while coming back to me when it genuinely needs a decision, permission or missing information.

That includes tasks such as researching something, working on a codebase, operating a website, using a connected tool or managing a longer project.

## Current state

Friday is under active development.

The project currently includes work around:

* local computer interaction
* Hermes integration
* persistent memory
* model and tool connectors
* LiveKit voice interaction
* browser automation
* permission boundaries
* capability discovery
* failure recovery
* testing against real user interactions

Some integrations require your own API keys or local setup.

## Setup

Clone the repository:

```bash
git clone https://github.com/DarshPro07/F.R.I.D.A.Y.git
cd F.R.I.D.A.Y
```

Create your local environment file from the example:

```bash
copy .env.example .env
```

Add your own credentials to `.env`.

Never commit `.env` or API keys to the repository.

### The control room

The control room is the screen I actually use. It runs as its own process, separate from the MCP server, so it can't destabilise the live agent.

```bash
.venv\Scripts\python.exe scripts\run_ui.py
```

That serves on `http://127.0.0.1:8770/` and opens it full-screen. It will ask for the camera.

Nothing works until it recognises my face. To teach it who I am, point it at a folder of photos of yourself:

```bash
.venv\Scripts\python.exe scripts\enrol_face.py path\to\photos --max 40
```

It runs the same recognition model the live gate uses, checks every photo (one face, in focus, and consistent with the others) and tells you which ones it rejected and why. You can also enrol from the lock screen with the camera.

If something else is using the camera — a meeting, a stream — it won't take it. It asks for a PIN instead, and you set that PIN the first time it needs one. If you want the camera left alone entirely:

```bash
.venv\Scripts\python.exe scripts\run_ui.py --password
```

Other flags: `--no-browser` to start the server without opening a window.

### The rest

The MCP server (`server.py`) and the LiveKit voice agent (`agent_friday.py`) run separately and are unchanged by any of the above. The control room reads from them; it does not replace them.

## Security

Friday is being built with explicit permission boundaries for actions that can affect the computer or external accounts.

API keys and credentials belong in local environment files and should never be committed to Git.

Generated runtime data, private screenshots, camera captures and local databases are also excluded from the public repository.

## AI usage

I used AI extensively while building Friday, including Claude and Hermes for coding, debugging, research and testing.

I designed the project direction, architecture, behaviour, permission rules, integrations and evaluation process. A large part of the work has been testing generated code against the real system, finding where it fails, changing the implementation and testing it again.

AI is both a development tool used to build Friday and part of the product itself.

## Project status

Friday is experimental software. Expect things to change as I keep testing and rebuilding parts of the system.

## Author

Built by [@DarshPro07](https://github.com/DarshPro07)
