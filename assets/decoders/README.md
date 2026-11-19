Optional game sound decoder
===========================

The launcher can discover `vgmstream-cli` on PATH. A package can instead stage
the reviewed upstream r2168 decoder using
`python scripts/prepare_game_audio_decoder.py --platform windows` (or `linux`).
Builds validate the staged file manifest before including it. Payload folders
are generated inputs, excluded from Git. Upstream license and dependency notices
travel beside the unchanged executable and DLLs; source locations and exact
archive/file hashes are recorded in `decoder-manifest.json`.

Upstream: https://github.com/vgmstream/vgmstream/tree/r2168

The decoder runs as a separate process. Its components can be replaced by a
compatible build. No EVE game audio or extracted/decoded media is included.
The launcher reads only verified resources in the selected installed client
cache, decodes to memory and removes its temporary text pointers afterwards.
