# v0.3 pose concepts

These four ImageGen outputs define the intended small-window crouch, wide
title-bar prone/sprawl, and tall side-lean silhouettes. They are **concept references,
not runtime assets**: the current exports contain a baked checkerboard and no
alpha channel. Do not add them to `ThemeManifest.assets` until a true-alpha
cutout has passed the theme transparency test.

`lilith-pose-window-sprawl-concept-v3.png` was generated in built-in ImageGen
reference-edit mode from `lilith-desktop-pet-chibi-v4.png`. The prompt preserved
Lilith's white bob, closed eyes, ceramic cracks, white summer dress and red collar
cord while changing the silhouette to a full-body prone pose with folded arms and
raised ankles for a wide window edge. A second ImageGen cleanup pass still exported
RGB rather than RGBA, so the concept remains deliberately disconnected from QML.

`lilith-pose-micro-corner-grip-concept-v1.png` through `-v4.png`
explore a compact two-handed corner-grip pose for very small host windows. All
four exports are RGB with a baked checkerboard (`transparentPixels: 0`), even
after three explicit transparency cleanup/reference-edit attempts. They remain
concept references only. The runtime `micro-window-edge` profile must continue
to use a verified true-alpha fallback until a production cutout passes the same
alpha inspection as the existing pose assets.

`lilith-pose-micro-corner-grip-concept-v5.png` is a fourth built-in ImageGen
background-extraction attempt made from `-v4`.  It again exported `1254x1254`
RGB pixels with the checkerboard baked into the image and no alpha band.  The
production gate rejected it immediately; it is retained only as evidence that
visual checkerboards must never be treated as transparency.

`lilith-pose-micro-corner-grip-draft-v6-baked-checkerboard.png` is the latest
rejected draft. It is intentionally named for the defect and stored only in
`art-reference`; no theme asset key, QML source, PyInstaller data rule or
runtime pose bundle may point to it.

`lilith-pose-micro-corner-grip-draft-v7.png` through `-v9.png` repeat the
two-handed curled corner-grip composition with progressively stricter
background-extraction prompts. `-v10-portrait.png` also tests the generator's
native `1024x1536` portrait size because older outputs at that size sometimes
contained an alpha channel. All four files still decode as `Format24bppRgb`;
their corner alpha is therefore effectively `255`, and the visible checker is
painted content rather than transparency. They remain composition references
only. The v9 silhouette is the preferred square composition reference, while
v10 is the preferred portrait framing reference.

Production admission is now enforced by `scripts/verify_pose_assets.py`; the
full contract and thresholds are documented in `docs/pose-asset-gate.md`. A
concept export must pass that gate before its asset key, pose bundle mapping or
QML aspect ratio may be connected to the runtime.

`lilith-outfit-summer-dress-concept-v2.png` explores a visually distinct,
lightweight ivory cotton sundress while keeping Lilith's front-facing prayer
pose and established identity. Its silhouette is accepted as an art direction
reference, but the export is `941x1672` 24-bit RGB with opaque checkerboard
corners. It must remain disconnected from the wardrobe runtime until a genuine
RGBA export passes `scripts/verify_outfit_assets.py`.

`lilith-pose-window-dangle-concept-v1-rgba.png` is a from-scratch dangling-pose
exploration.  It does contain an alpha band, but the generated alpha never
reaches 255, the transparent RGB plane retains substantial residue, a broad
external glow remains, and the flower accessories drift from Lilith's canonical
identity.  A follow-up glow-removal edit regressed to an RGB checkerboard.
Neither output is production artwork; the RGBA concept remains disconnected
from the manifest as a composition reference only.
