#!/usr/bin/env python3
"""Local operations, Python standard library only. Run from any directory."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys

from backup_manifest import select_files

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)


def run(args, capture=False):
    return subprocess.run(args, check=True, text=True, capture_output=capture)


def env():
    path = ROOT / ".env"
    if not path.exists():
        raise SystemExit("Ejecute primero: python3 scripts/sanitas.py init")
    values = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def compose(*args, capture=False):
    files = ["-f", "compose.yaml"]
    if os.environ.get("SANITAS_HTTPS") == "1":
        files += ["-f", "deploy/compose.https.yaml"]
    return run(["docker", "compose", "--env-file", ".env", *files, *args], capture)


def init():
    if not (ROOT / ".env").exists():
        shutil.copyfile(ROOT / ".env.example", ROOT / ".env")
        (ROOT / ".env").chmod(0o600)
    directory = ROOT / ".secrets"
    directory.mkdir(mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    for name in ["db_root_password", "administrator_password", "demo_password"]:
        path = directory / name
        if not path.exists():
            # Directory is private; individual files must be readable by container UID 1000.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            with os.fdopen(fd, "w") as handle:
                handle.write(secrets.token_urlsafe(32) + "\n")
    (ROOT / "backups").mkdir(mode=0o700, exist_ok=True)
    print(".env y secretos locales preparados; las contraseñas existentes se conservaron.")


def preflight():
    values = env()
    if not shutil.which("docker"):
        raise SystemExit("Docker no está instalado")
    image = values.get("HELPDESK_IMAGE", "")
    if not re.fullmatch(r"ghcr\.io/frappe/helpdesk:(v\d+\.\d+\.\d+)|ghcr\.io/frappe/helpdesk@sha256:[a-f0-9]{64}", image):
        raise SystemExit("HELPDESK_IMAGE debe ser un release fijo o digest de la imagen oficial")
    compose("config", "--quiet")
    result = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], text=True, capture_output=True)
    if result.returncode:
        raise SystemExit("Motor Docker inaccesible. Inicie Docker Desktop o Docker Engine y repita. No se instaló ninguna instancia.")
    print(f"Docker Engine {result.stdout.strip()}; Compose válido.")


def task(action):
    compose("run", "--rm", "-T", "toolbox", action)


def backup():
    values = env()
    site = values["SITE_NAME"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = ROOT / "backups" / stamp
    target.mkdir(mode=0o700)
    compose("exec", "-T", "backend", "bench", "--site", site, "set-maintenance-mode", "on")
    compose("exec", "-T", "backend", "bench", "--site", site, "disable-scheduler")
    compose("stop", "scheduler", "queue-short", "queue-long")
    try:
        compose("exec", "-T", "backend", "bench", "--site", site, "backup", "--with-files", "--compress")
        source = f"/home/frappe/frappe-bench/sites/{site}"
        compose("cp", f"backend:{source}/private/backups/.", str(target))
        compose("cp", f"backend:{source}/site_config.json", str(target / "site_config.json"))
        files = select_files(target)
        names = list(files.values()) + ["site_config.json"]
        hashes = {name: hashlib.sha256((target / name).read_bytes()).hexdigest() for name in names}
        (target / "manifest.json").write_text(json.dumps(dict(site=site, utc=stamp, image=values["HELPDESK_IMAGE"], files=files, sha256=hashes), indent=2) + "\n")
        for path in target.iterdir():
            path.chmod(0o600)
        print(f"Respaldo: {target}")
    finally:
        compose("exec", "-T", "backend", "bench", "--site", site, "enable-scheduler")
        compose("up", "-d", "scheduler", "queue-short", "queue-long")
        compose("exec", "-T", "backend", "bench", "--site", site, "set-maintenance-mode", "off")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "preflight", "install", "start", "stop", "status", "configure", "demo", "validate", "backup", "restore", "credentials", "images"])
    parser.add_argument("backup_directory", nargs="?")
    parser.add_argument("--confirm-restore", action="store_true")
    args = parser.parse_args()
    if args.action == "init":
        init()
        return
    if args.action == "credentials":
        for label, filename in [("Administrator", "administrator_password"), ("Usuarios demo", "demo_password")]:
            print(f"{label}: {(ROOT / '.secrets' / filename).read_text().strip()}")
        return
    preflight()
    if args.action == "install":
        compose("pull")
        compose("up", "-d", "db", "redis-cache", "redis-queue", "configurator")
        task("create")
        task("configure")
        task("demo")
        compose("up", "-d", "--wait", "--wait-timeout", "300")
        task("validate")
        print("Instalación terminada. Pendiente: lista de verificación por navegador en docs/VALIDACION.md")
    elif args.action == "start":
        compose("up", "-d", "--wait", "--wait-timeout", "300")
    elif args.action == "stop":
        compose("stop")
    elif args.action == "status":
        compose("ps", "-a")
    elif args.action in ["configure", "demo", "validate"]:
        task(args.action)
    elif args.action == "backup":
        backup()
    elif args.action == "images":
        compose("images")
        print("Para fijar digest: docker image inspect IMAGEN --format '{{json .RepoDigests}}'")
    elif args.action == "restore":
        if not args.confirm_restore or not args.backup_directory:
            raise SystemExit("Uso: restore backups/FECHA --confirm-restore (reemplaza todos los datos del sitio destino)")
        folder = Path(args.backup_directory).resolve()
        if not folder.is_relative_to(ROOT / "backups") or not (folder / "manifest.json").is_file():
            raise SystemExit("Se requiere respaldo completo con manifiesto bajo backups/")
        manifest = json.loads((folder / "manifest.json").read_text())
        if manifest["site"] != env()["SITE_NAME"]:
            raise SystemExit("SITE_NAME no coincide con el respaldo; no se alteró el sitio")
        if manifest["image"] != env()["HELPDESK_IMAGE"]:
            raise SystemExit("Configure la misma HELPDESK_IMAGE del manifiesto antes de restaurar")
        for name in [*manifest["files"].values(), "site_config.json"]:
            if Path(name).name != name or name not in manifest["sha256"]:
                raise SystemExit("Manifiesto de respaldo inválido")
            if hashlib.sha256((folder / name).read_bytes()).hexdigest() != manifest["sha256"][name]:
                raise SystemExit("Checksum de respaldo incorrecto; no se alteró el sitio")
        site = env()["SITE_NAME"]
        compose("exec", "-T", "backend", "bench", "--site", site, "set-maintenance-mode", "on")
        compose("stop", "scheduler", "queue-short", "queue-long", "frontend", "websocket")
        compose("run", "--rm", "-T", "toolbox", "restore", "/backup/" + str(folder.relative_to(ROOT / "backups")))
        compose("up", "-d")
        task("validate")
        compose("exec", "-T", "backend", "bench", "--site", site, "enable-scheduler")
        compose("exec", "-T", "backend", "bench", "--site", site, "set-maintenance-mode", "off")
    # preflight itself needs no further action.


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"Operación interrumpida (código {exc.returncode}).") from None
