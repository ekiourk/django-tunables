"""Generate the keys module with no Django settings configured."""

import sys

from tunables import Catalogue, Float, Group, Tunable
from tunables.export import keys_module

catalogue = Catalogue([Group("pricing", [Tunable("vat_rate", Float(min=0.0, max=1.0), 0.24)])])
sys.stdout.write(keys_module(catalogue))
