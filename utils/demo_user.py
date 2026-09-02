"""Demo / seed admin identity, resolved from env at import time.

`DEMO_ADMIN_EMAIL` and `DEMO_ADMIN_NAME` are the email and display name used
for:

  * the seeded admin account when ``SEED_DEMO_USER=true`` (see
    ``web_ui/backend/auth.py:seed_demo_user``)
  * the default project owner in CLI / DAO calls that don't carry a user
    context (``database/dao.py`` and ``orchestrator.py``)

Defaults preserve the historical ``doctor@ai4med.edu`` / ``Doctor`` pair so
existing deployments keep working without setting any new env var.
"""

import os

DEMO_ADMIN_EMAIL = os.getenv("DEMO_ADMIN_EMAIL", "doctor@ai4med.edu").strip().lower()
DEMO_ADMIN_NAME = os.getenv("DEMO_ADMIN_NAME", "Doctor").strip()
