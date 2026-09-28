"""The friday-operate skill's own test: the launcher answers with its build."""
from friday.launcher import version

text = version()
assert text.startswith("FRIDAY"), text
print(text)
