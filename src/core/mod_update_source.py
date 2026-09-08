"""Opt-in GitHub release discovery. No package code runs during a check."""
from __future__ import annotations

from dataclasses import dataclass, replace
from functools import total_ordering
import hashlib
import json
from pathlib import Path
import re
import urllib.request
import logging
from urllib.parse import urlsplit, unquote


class ModUpdateError(ValueError):
    pass


@total_ordering
@dataclass(frozen=True)
class Version:
    numbers: tuple[int, int, int]
    prerelease: tuple[str, ...] = ()

    @classmethod
    def parse(cls, text):
        if not isinstance(text, str) or len(text) > 100:
            raise ModUpdateError("Unsupported mod release version.")
        match = re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?", text)
        if not match:
            raise ModUpdateError("Mod updates require a semantic version such as 1.2.3.")
        parts = tuple(match[4].split('.')) if match[4] else ()
        if any(not part or (part.isdigit() and len(part) > 1 and part[0] == '0') for part in parts):
            raise ModUpdateError("Invalid prerelease version.")
        return cls(tuple(int(match[i]) for i in (1, 2, 3)), parts)

    def __lt__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        if self.numbers != other.numbers:
            return self.numbers < other.numbers
        if not self.prerelease or not other.prerelease:
            return bool(self.prerelease) and not other.prerelease
        for left, right in zip(self.prerelease, other.prerelease):
            if left == right:
                continue
            if left.isdigit() and right.isdigit():
                return int(left) < int(right)
            if left.isdigit() != right.isdigit():
                return left.isdigit()
            return left < right
        return len(self.prerelease) < len(other.prerelease)


@dataclass(frozen=True)
class UpdateSource:
    repository: str
    asset: str
    channel: str = "stable"
    tag_prefix: str = "v"
    preserve_files: tuple[str, ...] = ()


def parse_update_source(value) -> UpdateSource:
    from .mod_api_manifest import _relative_path
    if not isinstance(value, dict) or not {"provider", "repository", "asset"} <= value.keys() or value.keys() - {"provider", "repository", "asset", "channel", "tagPrefix", "preserveFiles"}:
        raise ModUpdateError("updates has missing or unsupported fields.")
    repo, asset = value["repository"], value["asset"]
    if value["provider"] != "github" or not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", repo) or repo.split('/')[1] in {'.', '..'}:
        raise ModUpdateError("updates.repository must name a public GitHub owner/repository.")
    if not isinstance(asset, str) or len(asset) > 180 or asset.count("{version}") != 1 or not re.fullmatch(r"[A-Za-z0-9_.-]*\{version\}[A-Za-z0-9_.-]*\.zip", asset):
        raise ModUpdateError("updates.asset must be a ZIP filename containing {version} once.")
    channel, prefix = value.get("channel", "stable"), value.get("tagPrefix", "v")
    if channel not in ("stable", "prerelease") or not isinstance(prefix, str) or len(prefix) > 32 or not re.fullmatch(r"[A-Za-z0-9_.-]*", prefix):
        raise ModUpdateError("Unsupported mod update channel or tag prefix.")
    files = value.get("preserveFiles", [])
    if not isinstance(files, list) or len(files) > 64:
        raise ModUpdateError("preserveFiles must list at most 64 relative configuration files.")
    preserved = []
    for filename in files:
        path = _relative_path(filename, "preserveFiles")
        if path.suffix.lower() not in {".ini", ".json", ".yaml", ".yml", ".toml", ".cfg", ".txt"} or path.name.lower().startswith("evejs-launcher."):
            raise ModUpdateError("preserveFiles may contain configuration files, not executable files or manifests.")
        preserved.append(path.as_posix())
    if len(set(x.casefold() for x in preserved)) != len(preserved):
        raise ModUpdateError("preserveFiles contains duplicate paths.")
    return UpdateSource(repo, asset, channel, prefix, tuple(preserved))


@dataclass(frozen=True)
class ModRelease:
    source: UpdateSource
    version: str
    tag: str
    notes: str
    page_url: str
    download_url: str
    size: int
    digest: str = ""
    mod_id: str = ""
    evejs_versions: tuple[str, ...] | None = None
    metadata_verified: bool = False


def _release_url(url, source, *, asset=False):
    if not isinstance(url, str):
        raise ModUpdateError("Invalid GitHub release URL.")
    parsed = urlsplit(url)
    prefix = f"/{source.repository}/releases/" + ("download/" if asset else "tag/")
    if (parsed.scheme != "https" or parsed.netloc.lower() != "github.com" or not unquote(parsed.path).casefold().startswith(prefix.casefold()) or parsed.query or parsed.fragment):
        raise ModUpdateError("Release URL does not belong to the declared GitHub repository.")
    return url


def select_release(source: UpdateSource, installed: str, releases: list) -> ModRelease | None:
    current = Version.parse(installed)
    candidates = []
    if not isinstance(releases, list):
        raise ModUpdateError("GitHub returned an invalid release list.")
    for release in releases:
        if not isinstance(release, dict) or release.get("draft") is not False:
            continue
        tag = release.get("tag_name", "")
        if not isinstance(tag, str) or not tag.startswith(source.tag_prefix):
            continue
        version = tag[len(source.tag_prefix):]
        try:
            parsed = Version.parse(version)
        except ModUpdateError:
            continue
        if parsed <= current or (source.channel == "stable" and (release.get("prerelease") is not False or parsed.prerelease)):
            continue
        candidates.append((parsed, version, release))
    if not candidates:
        return None
    _, version, release = max(candidates, key=lambda item: item[0])
    filename = source.asset.replace("{version}", version)
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        raise ModUpdateError("GitHub returned invalid release assets.")
    matches = [a for a in assets if isinstance(a, dict) and a.get("name") == filename and a.get("state") == "uploaded"]
    if len(matches) != 1:
        raise ModUpdateError("The newest release must contain exactly one matching mod ZIP.")
    asset = matches[0]
    size = asset.get("size")
    if type(size) is not int or not 0 < size <= 512 * 1024 * 1024:
        raise ModUpdateError("The mod download exceeds the supported size limit.")
    digest = asset.get("digest") or ""
    if not isinstance(digest, str) or (digest and not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest)):
        raise ModUpdateError("Unsupported release digest.")
    return ModRelease(source, version, release["tag_name"], str(release.get("body") or "")[:100000],
                      _release_url(release.get("html_url"), source),
                      _release_url(asset.get("browser_download_url"), source, asset=True), size, digest.lower())


class _GitHubRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if parsed.scheme != "https" or parsed.netloc.lower() not in {"api.github.com", "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}:
            raise ModUpdateError("GitHub redirected outside its release download hosts.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def github_open(url):
    request = urllib.request.Request(url, headers={"User-Agent": "EveJS-Launcher-ModUpdates/1", "Accept": "application/vnd.github+json"})
    return urllib.request.build_opener(_GitHubRedirects()).open(request, timeout=15)


def validate_release_metadata(release, payload, *, expected_mod_id=None):
    from .mod_evejs_compatibility import parse_evejs_versions
    if (not isinstance(payload, dict) or not {'schemaVersion', 'id', 'version', 'asset'} <= payload.keys()
            or payload.keys() - {'schemaVersion', 'id', 'version', 'asset', 'evejsVersions'}
            or type(payload['schemaVersion']) is not int or payload['schemaVersion'] != 1):
        raise ModUpdateError('Invalid mod release metadata.')
    if (not isinstance(payload['id'], str) or not payload['id'] or len(payload['id']) > 128
            or (expected_mod_id is not None and payload['id'] != expected_mod_id)
            or payload['version'] != release.version
            or payload['asset'] != release.source.asset.replace('{version}', release.version)):
        raise ModUpdateError('Release metadata does not match its mod, version or ZIP asset.')
    versions = parse_evejs_versions(payload.get('evejsVersions'))
    return replace(release, mod_id=payload['id'], evejs_versions=versions, metadata_verified=True)


def check_update(source, installed, *, evejs_version=None, expected_mod_id=None, opener=github_open):
    # Bounded discovery: 100 most recent releases, no recursive repository crawl.
    with opener(f"https://api.github.com/repos/{source.repository}/releases?per_page=100") as response:
        data = response.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise ModUpdateError("The GitHub release list is too large.")
    from .mod_evejs_compatibility import supports_evejs
    releases = json.loads(data)
    if not isinstance(releases, list):
        raise ModUpdateError('GitHub returned an invalid release list.')
    candidates = []
    for entry in releases:
        try:
            candidate = select_release(source, installed, [entry])
            if candidate:
                candidates.append((candidate, entry))
        except (ValueError, TypeError):
            continue
    for candidate, entry in sorted(candidates, key=lambda item: Version.parse(item[0].version), reverse=True):
        filename = source.asset.replace('{version}', candidate.version)[:-4] + '.update.json'
        matches = [asset for asset in entry['assets'] if isinstance(asset, dict) and asset.get('name') == filename and asset.get('state') == 'uploaded']
        if len(matches) != 1:
            continue
        asset = matches[0]
        size = asset.get('size')
        if type(size) is not int or not 0 < size <= 65536:
            continue
        try:
            url = _release_url(asset.get('browser_download_url'), source, asset=True)
            with opener(url) as response:
                raw = response.read(65537)
            digest = asset.get('digest')
            if len(raw) != size or (digest and digest != 'sha256:' + hashlib.sha256(raw).hexdigest()):
                raise ModUpdateError('The release metadata failed its size or digest check.')
            candidate = validate_release_metadata(candidate, json.loads(raw), expected_mod_id=expected_mod_id)
            if supports_evejs(candidate.evejs_versions, evejs_version):
                return candidate
        except (ValueError, TypeError) as exc:
            logging.getLogger(__name__).warning('Rejected mod update metadata for %s: %s', source.repository, exc)
    return None


def download_release(release, destination: Path, *, opener=github_open, progress=None):
    _release_url(release.download_url, release.source, asset=True)
    size = 0
    digest = hashlib.sha256()
    created = False
    try:
        with opener(release.download_url) as response, destination.open('xb') as output:
            created = True
            if progress:
                progress(0, release.size)
            last_percent = -1
            while block := response.read(128 * 1024):
                size += len(block)
                if size > release.size:
                    raise ModUpdateError("The downloaded package exceeds its declared size.")
                digest.update(block)
                output.write(block)
                percent = size * 100 // max(1, release.size)
                if progress and percent != last_percent:
                    progress(size, release.size)
                    last_percent = percent
        if size != release.size or (release.digest and release.digest != 'sha256:' + digest.hexdigest()):
            raise ModUpdateError("The downloaded package failed its size or digest check.")
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise
    return destination
