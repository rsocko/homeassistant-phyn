"""Verify a HACS development dependency through the real HA installer.

Run only in a disposable official HA container with no account configuration.
This installs packages into that container; it never sets up a Phyn account.
"""

import asyncio
from hashlib import sha256
from importlib import import_module
from importlib.metadata import distribution
from io import BytesIO, StringIO
import json
import logging
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit, urlunsplit
from urllib.request import urlopen
from zipfile import ZipFile

from packaging.requirements import Requirement
from packaging.utils import parse_wheel_filename

from homeassistant.core import HomeAssistant
from homeassistant.requirements import RequirementsNotFound, async_process_requirements


ROOT = Path(__file__).resolve().parents[1]


async def main() -> None:
    manifest = json.loads(
        (ROOT / "custom_components" / "phyn" / "manifest.json").read_text()
    )
    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert distribution("homeassistant").version == "2026.9.3"
    assert hacs["homeassistant"] == "2026.9.3"
    assert "homeassistant" not in manifest
    assert len(manifest["requirements"]) == 1
    requirement = Requirement(manifest["requirements"][0])
    assert requirement.name == "aiophyn" and requirement.url
    url = urlsplit(requirement.url)
    assert url.scheme == "https" and url.netloc == "github.com"
    assert url.path.startswith("/rsocko/aiophyn/releases/download/")
    assert not url.query and not url.username and not url.password
    hashes = parse_qs(url.fragment)
    assert set(hashes) == {"sha256"} and len(hashes["sha256"]) == 1
    digest = hashes["sha256"][0]
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    name, version, _, _ = parse_wheel_filename(Path(url.path).name)
    assert name == "aiophyn" and version.is_prerelease
    expected_url = urlunsplit(url._replace(fragment=""))

    with TemporaryDirectory(prefix="phyn-install-") as config:
        hass = HomeAssistant(config)
        assert not hass.config.skip_pip and not hass.config.skip_pip_packages
        wrong_hash = manifest["requirements"][0].replace(digest, "0" * 64)
        failure_log = StringIO()
        handler = logging.StreamHandler(failure_log)
        installer_logger = logging.getLogger("homeassistant.util.package")
        installer_logger.addHandler(handler)
        try:
            await async_process_requirements(
                hass, "phyn_checksum_negative_probe", [wrong_hash], is_built_in=False
            )
        except RequirementsNotFound:
            assert "hash mismatch" in failure_log.getvalue().lower(), failure_log.getvalue()
            print("Negative probe: actual HA installer rejected the incorrect checksum.")
        else:
            raise AssertionError("HA installer accepted an incorrect artifact checksum")
        finally:
            installer_logger.removeHandler(handler)
        await async_process_requirements(
            hass, "phyn", manifest["requirements"], is_built_in=False
        )
        await hass.async_block_till_done()

    package = distribution("aiophyn")
    source = json.loads(package.read_text("direct_url.json") or "{}")
    assert package.version == str(version), package.version
    assert source.get("url") == expected_url, source
    assert "dir_info" not in source, "Editable source is not a HACS artifact"
    # HA's uv may omit hashes from PEP 610 metadata. Verify the actual installed
    # wheel contents as well as exercising checksum rejection in the installer.
    def verify_installed_bytes() -> None:
        with urlopen(expected_url, timeout=60) as response:
            artifact = response.read()
        assert sha256(artifact).hexdigest() == digest
        with ZipFile(BytesIO(artifact)) as wheel:
            for member in wheel.namelist():
                path = PurePosixPath(member)
                assert not path.is_absolute() and ".." not in path.parts
                if member.endswith("/") or member.endswith(".dist-info/RECORD"):
                    continue
                installed = Path(package.locate_file(member))
                assert installed.read_bytes() == wheel.read(member), member
        print("Installed package files match the independently checksum-verified public wheel.")

    await asyncio.to_thread(verify_installed_bytes)
    aiophyn = import_module("aiophyn")
    assert aiophyn.__version__ == package.version
    assert Path(aiophyn.__file__).resolve() == Path(
        package.locate_file("aiophyn/__init__.py")
    ).resolve()
    sys.path.insert(0, str(ROOT))
    integration = import_module("custom_components.phyn")
    assert Path(integration.__file__).resolve() == (
        ROOT / "custom_components" / "phyn" / "__init__.py"
    )
    print(json.dumps({
        "homeassistant": distribution("homeassistant").version,
        "integration_version": manifest["version"],
        "aiophyn_version": package.version,
        "aiophyn_import": str(Path(aiophyn.__file__).resolve()),
        "direct_url": source,
    }, indent=2))
    subprocess.run([sys.executable, "-m", "uv", "pip", "check"], check=True)
    print("Actual HA dependency installer and integration import passed; no Phyn setup or live calls.")


if __name__ == "__main__":
    asyncio.run(main())
