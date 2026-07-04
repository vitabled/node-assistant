"""Test bootstrap: point DATA_DIR at a fresh temp dir BEFORE any app module
imports (accounts/storage/infra_billing_store read it at module load)."""
import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="ni_test_")
