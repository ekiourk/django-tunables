import re

IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
TAG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
