"""A byte-order mark in a hand-edited config file must not wipe it out.

`json.loads` rejects a leading BOM outright ("Unexpected UTF-8 BOM"), so a file
read as plain utf-8 fails to parse and is treated as absent or corrupt. For
`auth.json` that means every stored credential silently disappears *and* new
ones cannot be saved; for settings and the model catalog it means the file is
discarded wholesale.

Config files are hand-edited — model overrides are a documented workaround —
and Windows editors plus PowerShell's `>` redirect add a BOM by default, so
this is ordinary user behaviour rather than a corrupt file.

Reads use utf-8-sig, which strips a BOM when present and is a no-op otherwise.
Writes stay plain utf-8 so Tau never introduces one itself.
"""

from __future__ import annotations

import json

import pytest

BOM = "\ufeff"


def _write(path, text: str, *, bom: bool) -> None:
    path.write_text((BOM if bom else "") + text, encoding="utf-8")


class TestTheUnderlyingHazard:
    def test_plain_utf8_read_of_a_bom_file_breaks_json(self, tmp_path):
        """Pins why utf-8-sig is required rather than cosmetic."""
        path = tmp_path / "conf.json"
        _write(path, '{"a": 1}', bom=True)
        with pytest.raises(json.JSONDecodeError, match="BOM"):
            json.loads(path.read_text(encoding="utf-8"))

    def test_utf8_sig_read_handles_both(self, tmp_path):
        path = tmp_path / "conf.json"
        _write(path, '{"a": 1}', bom=True)
        assert json.loads(path.read_text(encoding="utf-8-sig")) == {"a": 1}
        _write(path, '{"a": 1}', bom=False)
        assert json.loads(path.read_text(encoding="utf-8-sig")) == {"a": 1}


class TestAuthStorage:
    def _storage(self, tmp_path):
        from tau.auth.storage import FileAuthStorage

        return FileAuthStorage(tmp_path / "auth.json")

    def _credentials(self, raw: str | None):
        from tau.auth.manager import AuthManager

        return AuthManager._parse_storage_data(None, raw)  # type: ignore[arg-type]

    @pytest.mark.parametrize("bom", [True, False])
    def test_credentials_survive(self, tmp_path, bom):
        from tau.auth.storage import LockResult

        storage = self._storage(tmp_path)
        _write(storage.store_path, '{"anthropic": {"type": "api_key", "key": "sk-x"}}', bom=bom)

        seen: list[str | None] = []
        storage.with_lock(lambda current: LockResult(result=seen.append(current)))

        assert self._credentials(seen[0]), "stored credentials were lost"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bom", [True, False])
    async def test_async_lock_path_too(self, tmp_path, bom):
        from tau.auth.storage import LockResult

        storage = self._storage(tmp_path)
        _write(storage.store_path, '{"anthropic": {"type": "api_key", "key": "sk-x"}}', bom=bom)

        seen: list[str | None] = []

        async def read(current):
            seen.append(current)
            return LockResult(result=None)

        await storage.with_lock_async(read)
        assert self._credentials(seen[0]), "stored credentials were lost on the async path"

    def test_new_writes_never_introduce_a_bom(self, tmp_path):
        """The read is forgiving; the write must not become so."""
        from tau.auth.storage import LockResult

        storage = self._storage(tmp_path)
        saved = '{"anthropic": {"type": "api_key", "key": "k"}}'
        storage.with_lock(lambda _c: LockResult(result=None, next=saved))
        assert not storage.store_path.read_bytes().startswith(b"\xef\xbb\xbf")


class TestSettingsStorage:
    @pytest.mark.parametrize("bom", [True, False])
    def test_settings_survive(self, tmp_path, bom):
        from tau.settings.storage import SCOPE, FileSettingsStorage, LockResult

        storage = FileSettingsStorage(cwd=tmp_path, config_dir=tmp_path)
        _write(storage.global_settings_path, '{"theme": "dark"}', bom=bom)

        seen: list[str | None] = []
        storage.with_lock(SCOPE.GLOBAL, lambda current: LockResult(result=seen.append(current)))
        assert json.loads(seen[0]) == {"theme": "dark"}


class TestModelCatalog:
    @pytest.mark.parametrize("bom", [True, False])
    def test_catalog_is_not_discarded(self, tmp_path, bom):
        """Catalog.load() swallows JSONDecodeError and reports "no cache", so a
        BOM made a perfectly good catalog look absent rather than broken.
        """
        from tau.inference.model.catalog import Catalog

        path = tmp_path / "models.json"
        payload = json.dumps({"data": {"anthropic": {"models": {}}}, "fetched_at": 1.0})
        _write(path, payload, bom=bom)

        catalog = Catalog(path=path)
        assert catalog.load() is True, "a valid catalog was discarded"
        assert catalog.data == {"anthropic": {"models": {}}}
        assert catalog.fetched_at == 1.0
