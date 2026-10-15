"""Produce shareable source from an explicit allowlist, without signing secrets."""
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = ("app", "scripts", "third-party-source", ".github", "docs")
FILES = ("README.md", "README_AR.md", "THIRD_PARTY_NOTICES.md", "LICENSE", "dependency-lock.json", "package.json", ".gitignore")
FORBIDDEN = {".jks", ".keystore", ".p12", ".pfx", ".key", ".pem", ".env"}


def package():
    files = [ROOT / name for name in FILES if (ROOT / name).is_file()]
    for name in DIRECTORIES:
        files.extend(p for p in (ROOT / name).rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    for path in files:
        if path.is_symlink() or ROOT not in path.resolve().parents or path.suffix.lower() in FORBIDDEN or any("secret" in x.lower() or "password" in x.lower() or "signing" in x.lower() for x in path.relative_to(ROOT).parts):
            raise ValueError("Refusing to package potential signing material: " + str(path.relative_to(ROOT)))
    output = ROOT / "outputs/Luqta-Android-Source.zip"
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, str(path.relative_to(ROOT)))
        validation = ROOT / "outputs/validation.json"
        if validation.is_file():
            archive.write(validation, "outputs/validation.json")
    print(f"Source: {output} ({output.stat().st_size} bytes)")


if __name__ == "__main__":
    package()
