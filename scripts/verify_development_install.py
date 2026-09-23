"""Verify a HACS development dependency through the real HA installer.

Run only in a disposable official HA container with no account configuration.
This installs packages into that container; it never sets up a Phyn account.
"""

import asyncio
from importlib import import_module
from importlib.metadata import distribution
import json
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit, urlunsplit

from packaging.requirements import Requirement
from packaging.utils import parse_wheel_filename

from homeassistant.core import HomeAssistant
from homeassistant.requirements import async_process_requirements


ROOT = Path(__file__).resolve().parents[1]


async def main() -> None:
    manifest = json.loads(
        (ROOT / "custom_components" / "phyn" / "manifest.json").read_text()
    )
    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert distribution("homeassistant").version == "2026.9.3"
    assert manifest["homeassistant"] == hacs["homeassistant"] == "2026.9.3"
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
        await async_process_requirements(
            hass, "phyn", manifest["requirements"], is_built_in=False
        )
        await hass.async_block_till_done()

    package = distribution("aiophyn")
    source = json.loads(package.read_text("direct_url.json") or "{}")
    assert package.version == str(version), package.version
    assert source.get("url") == expected_url, source
    assert source.get("archive_info", {}).get("hashes", {}).get("sha256") == digest, source
    assert "dir_info" not in source, "Editable source is not a HACS artifact"
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
