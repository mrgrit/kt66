"""A pruned image must not erase an otherwise running lab from the portal."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import docker
import main


class PrunedImageContainer:
    name = "kt66-web"
    status = "running"
    attrs = {
        "Config": {"Image": "kt66-web:training"},
        "Image": "sha256:pruned",
        "NetworkSettings": {"Networks": {"kt66-dmz": {"IPAddress": "10.20.32.80"}}},
        "State": {"StartedAt": "2026-09-09T00:00:00Z"},
    }

    @property
    def image(self):
        raise docker.errors.ImageNotFound("image was pruned")


class InventoryTests(unittest.TestCase):
    def test_pruned_image_keeps_container_visible(self):
        client = Mock()
        client.containers.list.return_value = [PrunedImageContainer()]
        with patch.object(main, "docker_client", return_value=client):
            inventory = main.list_containers()
        self.assertEqual(len(inventory), 1)
        self.assertEqual(inventory[0]["name"], "kt66-web")
        self.assertEqual(inventory[0]["status"], "running")
        self.assertEqual(inventory[0]["image"], "kt66-web:training")
        self.assertEqual(inventory[0]["ip"], "10.20.32.80")
        client.close.assert_called_once()

    def test_unrelated_containers_are_excluded(self):
        other = Mock()
        other.name = "unrelated-service"
        client = Mock()
        client.containers.list.return_value = [other, PrunedImageContainer()]
        with patch.object(main, "docker_client", return_value=client):
            self.assertEqual([r["name"] for r in main.list_containers()], ["kt66-web"])

    def test_ledger_changes_are_reflected_without_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "assets.yaml"
            with patch.object(main, "ASSETS_PATH", ledger):
                ledger.write_text("it_assets:\n  - {id: fw, container: kt66-fw, ip: 10.20.30.1, name: Firewall}\n  - {id: dgx, remote: 10.20.50.10}\n")
                self.assertEqual(main.expected_containers(), [("kt66-fw", "10.20.30.1", "Firewall")])
                ledger.write_text("it_assets: []\n")
                self.assertEqual(main.expected_containers(), [])


if __name__ == "__main__":
    unittest.main()
