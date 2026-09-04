"""
Tool registry — imports and registers all tool modules with the MCP server.
Add new tool modules here as you build them.
"""

from friday import ownership
from friday.tools import (
    audio_control, automation_control, brightness_control, document_control,
    browser_policy_control, brain_control, connector_control,
    executor_control, fabric_control, hardware_control, hermes_control,
    window_control, product_control, file_control, identity_control,
    memory_control, music_control, power_control, process_control,
    profile_control, objective_control, reminder_control, screen_control, system,
    system_control, utils, vision_control, web, web_control,
    workbench_control, vnext_control, youtube_control,
)


def register_all_tools(mcp):
    """
    Register all tool groups onto the MCP server instance.

    Everything registered here goes through the ownership guard first. When a
    durable objective has just been admitted for this request, the capabilities
    it has queued are claimed for a short window, and a conversational call to
    one of them is deferred instead of executed - otherwise "open Paint" gets
    done twice, once by the task graph and once by the model reading the same
    sentence.

    The objective executor does not pass through here; it calls
    `CapabilityRuntime` directly. So the guard cannot stop an objective doing
    its own work, which is the one thing it must never do.
    """
    mcp = ownership.guard(mcp)
    web.register(mcp)
    web_control.register(mcp)  # Phase 1B: real search, fetch, browser
    system.register(mcp)
    system_control.register(mcp)  # Phase 1A: the user's actual machine
    file_control.register(mcp)  # Phase 1C: jailed filesystem access
    memory_control.register(mcp)  # Phase 1D: durable memory with provenance
    reminder_control.register(mcp)  # Phase 1G: OS-scheduled reminders
    objective_control.register(mcp)  # Phase 3: the multi-step run book
    automation_control.register(mcp)  # §14: a trigger, a step graph, a record
    product_control.register(mcp)  # §13: catalogues in, evidence out
    document_control.register(mcp)  # the files files_read cannot open
    hardware_control.register(mcp)  # batch 2A: what this machine is
    window_control.register(mcp)  # batch 2B: individual windows
    audio_control.register(mcp)  # batch 2C: audio sessions
    brightness_control.register(mcp)  # batch 2C: screen brightness
    process_control.register(mcp)  # batch 2D: asking a program to close, and ending one
    power_control.register(mcp)  # batch 2D: lock, sleep, shutdown, restart
    vision_control.register(mcp)  # Phase 1E: camera and screen, on demand
    screen_control.register(mcp)  # point at the screen, and drive it behind CONFIRM
    music_control.register(mcp)  # play any song by name, no account needed
    profile_control.register(mcp)  # Phase 1H: the user model, learned daily
    executor_control.register(mcp)  # the question channel a Claude run calls back on
    hermes_control.register(mcp)
    browser_policy_control.register(mcp)
    vnext_control.register(mcp)
    identity_control.register(mcp)  # open things in the browser he is signed into
    youtube_control.register(mcp)  # YouTube as data, not as a page to look at
    workbench_control.register(mcp)  # build something, then show it in his browser
    connector_control.register(mcp)
    brain_control.register(mcp)
    fabric_control.register(mcp)
    utils.register(mcp)
