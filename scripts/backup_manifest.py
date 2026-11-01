"""Select a complete native Bench backup, including compressed .tgz archives."""
from pathlib import Path


def select_files(directory):
    directory = Path(directory)
    candidates = [p for p in directory.glob("*-database.sql.gz") if "-partial-" not in p.name]
    if not candidates:
        raise RuntimeError("No se encontró un respaldo completo sin cifrado de Bench")
    database = max(candidates, key=lambda p: p.name)
    prefix = database.name.removesuffix("-database.sql.gz")

    def archive(kind):
        found = [directory / f"{prefix}-{kind}.{suffix}" for suffix in ["tgz", "tar", "tar.gz"]]
        found = [p for p in found if p.is_file()]
        if len(found) != 1:
            raise RuntimeError(f"Respaldo incompleto o ambiguo: {kind}")
        return found[0].name

    return dict(database=database.name, public=archive("files"), private=archive("private-files"))
