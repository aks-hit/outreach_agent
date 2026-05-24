import os
import stat
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def secure_file(filepath: str):
    """Sets file permissions to 600 (read/write by owner only)."""
    if not os.path.exists(filepath):
        log.warning(f"File not found, skipping: {filepath}")
        return

    try:
        # chmod 600: user can read/write. Group and others have no access.
        os.chmod(filepath, stat.S_IRUSR | stat.S_IWUSR)
        log.info(f"Secured {filepath} (chmod 600)")
    except Exception as e:
        log.error(f"Failed to secure {filepath}: {e}")


if __name__ == "__main__":
    log.info("Starting security hardening for PythonAnywhere...")

    base_dir = os.path.dirname(os.path.abspath(__file__))

    # Files to lock down
    files_to_secure = [
        os.path.join(base_dir, ".env"),
        os.path.join(base_dir, ".creds", "credentials.json"),
        os.path.join(base_dir, ".creds", "token.pickle"),
    ]

    for f in files_to_secure:
        secure_file(f)

    log.info("Security hardening complete.")
