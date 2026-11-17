"""Prepare Canonical's verified desktop disk for the project-owned Hyper-V VM.

No host features, network switches or VM registrations are changed here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import time
from urllib.request import urlopen
import zipfile

URL = ('https://partner-images.canonical.com/hyper-v/desktop/noble/20261110/'
       'ubuntu-noble-hyperv-amd64-ubuntu-desktop-hyperv.vhdx.zip')
SHA256 = '584fe77dc6c3049c050e33724eaa6047ce8aeecce6669208f4f2a41d67e9fe38'


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as source:
        for data in iter(lambda: source.read(4 * 1024 * 1024), b''):
            value.update(data)
    return value.hexdigest()


def ordinary(path):
    for current in (path, *path.parents):
        if current.exists() and (current.is_symlink() or getattr(current.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('VM storage must not use a junction or linked path: ' + str(current))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('D:/EveJS-Linux-Test'))
    args = parser.parse_args()
    root = args.root.absolute()
    ordinary(root)
    marker = root / 'project-vm.json'
    if root.exists() and any(root.iterdir()) and not marker.is_file():
        raise ValueError('The target folder is not an identified EveJS test VM folder.')
    root.mkdir(exist_ok=True)
    data = {'purpose': 'evejs-launcher-linux-qa', 'source': URL, 'sourceSha256': SHA256,
            'vmName': 'EveJS-Linux-QA', 'status': 'preparing'}
    if marker.exists():
        previous = json.loads(marker.read_text())
        if previous.get('purpose') != data['purpose'] or previous.get('sourceSha256') != SHA256:
            raise ValueError('The VM folder belongs to another image or task.')
        disk = root / 'Ubuntu-24.04-EveJS.vhdx'
        if previous.get('status') == 'disk-ready' and disk.is_file():
            print('Verified image was already prepared: ' + str(disk), flush=True)
            return
    marker.write_text(json.dumps(data, indent=2) + '\n')
    archive = root / 'ubuntu-desktop.vhdx.zip'
    if not archive.exists():
        if shutil.disk_usage(root).free < 12 * 1024**3:
            raise ValueError('The VM drive needs at least 12 GiB free for the image download.')
        partial = root / 'ubuntu-desktop.vhdx.zip.partial'
        if partial.exists():
            raise ValueError('An incomplete download exists; retain it for inspection or restart it explicitly.')
        with urlopen(URL, timeout=60) as source, partial.open('xb') as output:
            total = 0
            last_report = time.monotonic()
            value = hashlib.sha256()
            while chunk := source.read(4 * 1024 * 1024):
                total += len(chunk)
                if total > 7 * 1024**3:
                    raise ValueError('The Ubuntu image exceeds its download size limit.')
                output.write(chunk)
                value.update(chunk)
                if time.monotonic() - last_report >= 15:
                    print(f'Downloaded {total / 1024**3:.2f} GiB', flush=True)
                    last_report = time.monotonic()
        if value.hexdigest() != SHA256:
            raise ValueError('The Ubuntu download checksum does not match Canonical’s published image.')
        os.rename(partial, archive)
    elif digest(archive) != SHA256:
        raise ValueError('The existing Ubuntu archive checksum is incorrect.')
    disk = root / 'Ubuntu-24.04-EveJS.vhdx'
    if disk.exists():
        raise ValueError('An unrecorded VM disk already exists. It was preserved.')
    with zipfile.ZipFile(archive) as source:
        entries = [item for item in source.infolist() if not item.is_dir()]
        if len(entries) != 1 or not entries[0].filename.endswith('.vhdx'):
            raise ValueError('Unexpected Canonical desktop image layout.')
        entry = entries[0]
        if stat.S_ISLNK(entry.external_attr >> 16) or entry.file_size > 64 * 1024**3:
            raise ValueError('Unexpected VM disk type or size.')
        if shutil.disk_usage(root).free < entry.file_size + 24 * 1024**3:
            raise ValueError('Insufficient disk space for expansion and the working VM.')
        print(f'Extracting {entry.file_size / 1024**3:.2f} GiB desktop disk', flush=True)
        with source.open(entry) as content, disk.open('xb') as output:
            shutil.copyfileobj(content, output, length=4 * 1024 * 1024)
    data.update(status='disk-ready', disk=str(disk), diskSha256=digest(disk))
    marker.write_text(json.dumps(data, indent=2) + '\n')
    print('Desktop disk verified and ready: ' + str(disk), flush=True)


if __name__ == '__main__':
    main()
