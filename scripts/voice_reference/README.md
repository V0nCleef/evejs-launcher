# LYRA voice reference

`lyra_balanced_lift_approved.mp3` is the user-approved option 2 review master.
`lyra_cori_high_raw/` is the immutable Piper/Cori source catalog from which
the review was made. Both are build-time inputs only and are not packaged with
the launcher.

Run `scripts/process_lyra_voice_catalog.py` to render the release catalog in
`assets/audio/voice/lyra/`. The script verifies both inputs, extracts the four
phrases heard in the approved master, applies the documented Balanced Lift
matching profile to the remaining fixed lines, and records final hashes and
provenance in the release manifest.
