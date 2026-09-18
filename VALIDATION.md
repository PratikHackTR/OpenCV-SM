# Local validation — 2026-09-17

Environment: Windows x64, Python 3.12, dependencies recorded in `requirements-lock.txt`.

## Swing ownership and handoff — 2026-09-18

- 88 automated tests passed. Added alternating-hand handoffs, raised owner/lowered spare with changing output order and shape jitter, both-web ownership, overlapping-palm uncertainty, label-flip versus distant-hand identity, lost-owner persistent WASD lock, explicit walking rearm and stale/focus/disable lock preservation.
- Handoff dispatch retains Shift with no repeated Space or Shift release. New hand starts at zero camera displacement. The previous acceleration curve and aim/E tests still pass.
- Raised WEB now uses wrist height rather than fingertip direction; wrist rotation no longer invalidates a recognized raised web. Release grace is 350 ms, handoff confirmation 80 ms, owner fist exit 100 ms.
- Qt/model smoke check passed; UI preview regenerated. Walking requires the explicit resume button after a swing. No webcam-derived grounded-state claim is made.
- These checks use synthetic landmarks and a fake input receiver. Live game recognition and gameplay feel were not verified in this revision. Native SendInput implementation is unchanged.

## Swing isolation and movement overlay — 2026-09-18

- 77 tests passed. New coverage includes other-hand/body interference during swing, short shape uncertainty without recasting, owner-only fist exit, exit-once/restart, head-only bobbing without dodge, pending action cleanup, Shift-held Space exit ordering and focus-loss cancellation.
- WASD debug metadata matches the actual 22% thresholds. A synthetic overlay was rendered to `artifacts/wasd-overlay.png`; no camera image was saved.
- Native input receiver attempt did not retain foreground and correctly sent zero events. This attempt does not verify native delivery of the new exit sequence. Previous native delivery results below refer to earlier controls.
- Aim/E behavior and camera gain curves are retained. Live gameplay feel remains unverified.

## Calibration-free controls — 2026-09-18

- 68 tests passed. New coverage: anywhere-in-frame aim without pose/calibration, neutral-centred accelerated camera, two-finger trigger rearming, shape-independent Hulk clap with bilateral approach, simultaneous aim/WASD, free single-finger camera, jump without wizard, no jump on body reacquisition, E double-tap timing and second-tap cancellation.
- Native Qt input receiver passed: 31 accepted keyboard/mouse events. Two E down/up pairs arrived with a measured 78 ms gap between the first release and second press. F, W, S, A, D, jump/swing, mouse buttons and relative movement were received. Swing Space hold was 109 ms with a 63 ms gap before Shift. This is Windows delivery to a test window, not in-game gesture validation.
- Qt/model smoke check passed; tracking reset opens no wizard and requires no region editor. Old profiles are not loaded into runtime gesture recognition.
- Real-person recognition accuracy and combat responsiveness remain unverified. No claim of perfect recognition or measured gameplay latency is made.

## Hand mouse, clap, seal and salute (superseded) — 2026-09-18

- 64 unit tests passed, including continuous upward-web swing, release on gesture/lowering, fresh swing centre, controlling-hand order changes, two-finger aim entry/exit, no stationary aim drift, head independence, large-jump rejection and single-hand label-flip tolerance.
- Clap E rearming, joined-fingertip F single-shot behavior, salute W hold/release and punch exclusion are covered by synthetic landmarks. E/F dispatch outside aim, once-only relative mouse dispatch, swing elapsed-time integration and focus/F8 cleanup use a fake input backend.
- Model execution and Qt startup/render/close smoke checks passed; the hand-controls UI no longer opens or requires a region editor.
- The region editor and A/D controls are retired. Five-step calibration remains compatible; old region settings are ignored.
- Live in-game recognition/feel and sustained camera performance still require user testing; unit tests do not establish recognition accuracy on a real person.

## Manual regions and head camera (superseded) — 2026-09-18

- 64 automated tests passed. Region coverage includes A/D/W dwell, immediate exit release, open-hand rejection, conflicting-hand cancellation, manual two-wrist aim, missing regions, tracking loss, head camera while aiming, persistence and invalid/overlapping box rejection.
- Offscreen Qt tests exercise actual mouse drags with letterboxing, reverse-direction redraw, save, cancel and camera-stop behavior. Region editing never enables game input.
- Native input dispatch is exercised with a fake backend for W release on focus loss, aim, stale frames and F8. Head camera integration is consistent at 50/100 Hz. Existing swing, jump, dodge, attack, gadget and calibration regressions pass.
- Model/Qt smoke checks passed. Live player recognition and the new in-game feel remain unverified.
- Prior arm-relative steering and wrist-relative camera controls have been replaced. Boxes have no automatic defaults; the user must draw and save all four before enabling controls.

## Comfort controls (superseded) — 2026-09-18

- 60 tests passed: relative two-axis wrist aim, stationary-hand/noise suppression, head/body independence, hand loss/reacquisition, outlier re-anchoring, one-time delta dispatch, duplicate-frame rejection, foreground loss, swing-only arm steering with dwell/release and the five-step wizard.
- Space → Shift sequencing, gadget, punch, jump, dodge and input-release regression tests pass.
- Qt offscreen startup, motion settings, five-step wizard open/sample/cancel and model smoke checks passed. IPCam refresh/URL validation remains covered.
- New defaults: aim gain 450, wrist slop 5% of calibrated shoulder width, arm engage threshold 40% with 220 ms dwell. Both slop and gain are adjustable; arm steering can be disabled.
- Existing numeric profiles remain readable. Head calibration values no longer affect input. Head-turn steps were removed.
- These checks use synthetic landmarks and a fake input backend. The updated feel and false-trigger rate have not been measured during live gameplay.

## IPCam — 2026-09-18

- Added an always-available IPCam source and a URL field visible only for that selection. Refresh preserves IPCam selection and the edited URL, including with zero local devices.
- URL validation and the UI smoke check passed; all 50 control/calibration tests still pass.
- The supplied LAN HTTP video endpoint opened through FFmpeg and returned a 1440 × 1440 frame. No frame was saved. Network capture uses separate open/read timeouts and keeps the existing stale-input watchdog.

## Previous motion controls (historical)

- 50 tests passed. Coverage includes swing entry sequencing/cancellation, head neutral jitter and deliberate turns, the seven-step calibration, profile persistence/corruption, aim debug reasons, plus the prior gesture and dispatch tests.
- Punch sequences were exercised at 5, 10, 14, 30 and 60 inference FPS. Crouch/return sequences were exercised at 10, 14, 30 and 60 FPS. These are synthetic landmark trajectories, not accuracy measurements on people.
- The native input receiver accepted 21 events: the keyboard/mouse checks plus Space down/up followed by Shift down/up through the real input worker. Observed swing timing was 109 ms Space hold and a 63 ms gap before Shift. All expected events reached the receiver. This was a local receiver, not a game test.
- Focus loss, F8 and stale-camera tests release keys and mouse buttons and stop camera movement. Nişan mode suppresses A/D, swing and attack; background one-shot events are discarded.
- Qt startup/render/close and wizard open/sample/cancel checks passed. Game input remained disabled while the wizard was visible. Both A4 tech USB2.0 Camera and OBS Virtual Camera enumerated.
- Physical A4 tech capture succeeded. Two short eight-second checks reported approximately 4.5 FPS at the end after startup, although the driver reported 60 FPS. No person/hands were detected in those checks. Sequential and concurrent inference had the same capture-limited FPS; no numeric speedup claim is made.
- Smooth preview now reads capture frames independently; hand/pose inference runs concurrently; camera motion uses elapsed time rather than inference frame count.
- `pip check` and compilation checks passed. The user confirmed the prior elevated Shift/Space version worked in the game. The newly added controls have not yet been validated with live player gestures in-game.

## Initial startup checks (historical)

- `python -m unittest discover -s tests -v`: 11 passed. Synthetic landmarks exercise raised web/fist detection, release, calibration, jump rearming and tracking loss. A fake keyboard backend exercises foreground changes, stale frames, F8, pulse duration and disabled/background input.
- `python -m pip check`: no broken requirements.
- Both MediaPipe task models loaded and executed on a blank 640 × 480 image, returning zero hands/poses.
- Qt console rendered at 1440 × 900. Camera refresh listed the same device as DirectShow enumeration. Closing the console stopped its input worker.
- Windows foreground-process lookup succeeded; the x64 `INPUT` structure size was 40 bytes.
- An actual capture attempt on the listed **OBS Virtual Camera** failed to open. Capture/inference workers exited and input remained disabled.

## Remaining live checks

- Camera hot-plug and unplug with a physical device.
- Real hand/body recognition, sensitivity tuning and false-trigger rate.
- Sustained camera/inference FPS and end-to-end latency while the game runs.
- New aim/gadget/attack/dodge and steering behavior in Spider-Man Remastered.

Local generated evidence is under `artifacts/` and excluded from Git. The current tests establish input delivery and state-machine behavior, not a near-perfect recognition rate or end-to-end gameplay latency.

## Input diagnostic follow-up

- The running Spider-Man process (PID 26244 during inspection) had High integrity, RID 12288. The controller interpreter had Medium integrity, RID 8192. This is a Windows UIPI input boundary.
- Added integrity preflight, explicit elevated launch, accepted-event logging/counter and a foreground-scoped delayed Space test independent of camera inference.
- Expanded suite: 15 tests passed, including manual-test cancellation, target focus and integrity failure.
- A real local Qt receiver received all four scan-code events: Shift down/up and Space down/up. `SendInput` accepted four events. This verifies actual Windows event delivery to a same-integrity receiver; game acceptance still needs the elevated in-game test.
- No inference tuning was made in this follow-up; input delivery has priority.
