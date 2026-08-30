"""
Executors: other agents that ADA hands development work to.

An executor is not a model provider. ADA does not think with it - ADA gives it
a task bundle, watches it work, answers its questions from project memory,
enforces policy on what it may touch, and verifies the result before anything
is called done.
"""

from friday.executors.claude_code import ClaudeCodeExecutor, TaskBundle

__all__ = ["ClaudeCodeExecutor", "TaskBundle"]
