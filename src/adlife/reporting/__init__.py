"""The offline, self-contained HTML report renderer.

:mod:`adlife.reporting.html` turns a stored run or a paired comparison into one
self-contained HTML file - CSS and the Plotly runtime inlined, chart payloads
serialized so they cannot break out of their script element - and nothing here ever
touches a network.
"""

from __future__ import annotations
