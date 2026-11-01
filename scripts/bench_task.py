"""Runs inside the official Helpdesk image (toolbox service)."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

SITE = os.environ["SITE_NAME"]
if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]+", SITE):
    raise SystemExit("Invalid SITE_NAME")


def bench(*args):
    subprocess.run(["bench", "--site", SITE, *args], check=True)


def secret(name):
    return Path("/run/secrets", name).read_text().strip()


def site_task(action):
    # Preserve the venv path: resolving its Python symlink would lose pyvenv.cfg.
    python = Path.cwd() / "env/bin/python"
    if not python.is_file():
        raise SystemExit("Ejecute toolbox desde /home/frappe/frappe-bench; falta env/bin/python")
    script = Path(__file__).with_name("site_task.py")
    subprocess.run([str(python), str(script), action], check=True)


def create():
    config = Path("sites", SITE, "site_config.json")
    if not config.exists():
        # Never echo this command: passwords are supplied only from Docker secrets.
        subprocess.run(["bench", "new-site", SITE, "--db-root-username", "root", "--db-root-password", secret("db_root_password"),
              "--admin-password", secret("administrator_password"), "--mariadb-user-host-login-scope", "%"], check=True)
    # install-app is idempotent; installs dependencies included in the official image.
    bench("install-app", "telephony")
    bench("install-app", "helpdesk")
    bench("set-config", "host_name", os.environ["PUBLIC_URL"])
    bench("set-config", "mute_emails", "1", "--parse")
    bench("set-config", "disable_telemetry", "1", "--parse")
    bench("enable-scheduler")
    bench("set-maintenance-mode", "off")


def restore(folder):
    root = Path(folder).resolve()
    if not root.is_relative_to(Path("/backup")):
        raise SystemExit("Restore source must be under /backup")
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest["site"] != SITE:
        raise SystemExit("SITE_NAME must match the backup site for this restore procedure")
    import hashlib
    for filename, digest in manifest["sha256"].items():
        path = (root / filename).resolve()
        if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise SystemExit("Backup checksum mismatch")
    files = manifest["files"]
    args = ["restore", str(root / files["database"]), "--force",
            "--db-root-username", "root", "--db-root-password", secret("db_root_password"),
            "--with-public-files", str(root / files["public"]),
            "--with-private-files", str(root / files["private"])]
    bench(*args)
    # Retain the target site's DB credentials; restore only the encryption key.
    source_config = json.loads((root / "site_config.json").read_text())
    if source_config.get("encryption_key"):
        bench("set-config", "encryption_key", source_config["encryption_key"])
    bench("set-config", "host_name", os.environ["PUBLIC_URL"])
    bench("migrate")
    bench("clear-cache")
    print("Restauración terminada; activar servicios y ejecutar validate antes de habilitar acceso.")


if __name__ == "__main__":
    action = sys.argv[1]
    try:
        if action == "create":
            create()
        elif action in ["configure", "demo", "validate"]:
            site_task(action)
        elif action == "restore":
            restore(sys.argv[2])
        else:
            raise SystemExit("Unknown toolbox task")
    except subprocess.CalledProcessError as exc:
        # CalledProcessError includes the command (and credentials); do not print it.
        raise SystemExit(f"La tarea de instalación falló (código {exc.returncode}); revise el mensaje anterior.") from None
