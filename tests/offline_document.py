"""Build the version-zero document with no Django settings configured."""

import json
import sys

from tunables import Catalogue, Float, Group, Tunable
from tunables.document import defaults_document
from tunables.schema import document_schema

catalogue = Catalogue([Group("pricing", [Tunable("vat_rate", Float(min=0.0, max=1.0), 0.24)])])
document = defaults_document(catalogue, environment="offline")
schema = document_schema(catalogue)
json.dump({"environment": document["environment"], "version": document["version"], "id": schema["$id"]}, sys.stdout)
