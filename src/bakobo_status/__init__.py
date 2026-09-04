"""bakobo-status — the tool that writes what the public status page says.

An incident is a file in this repository, appended to by this command, and the site is generated
from those files. The reasoning is in bakobo/infra's intent tree at @52ehw3d7; the short version is
that a record in git is diffable, attributable and reproducible, and that a playbook can call a
command where it cannot click a console.
"""

from .errors import StatusError
from .incidents import Incident, Store, Update

__all__ = ["Incident", "StatusError", "Store", "Update"]
