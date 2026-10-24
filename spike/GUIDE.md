# Garmin spike: about 45 minutes

Goal: find out whether the script can create Garmin workouts, and save real workout JSON
for the compiler to copy. Run every command from `/Users/mw/Dev/fit`.

## 1. Log in (5 min)

```sh
uv run spike/spike.py login
```

Enter your email, password and MFA code.

- `Workout list OK` → go to step 2.
- `FAILED` → **stop**. Paste the FAILED line to Claude.

## 2. Build 4 workouts in the web UI (20 min)

Open connect.garmin.com → Training & Planning → Workouts → Create → Running.
Names must start with `SPIKE`, exactly as shown.

**SPIKE A steps** (add any text as the description)
1. Warmup: 10:00, HR zone 2
2. Run: 1 km, pace 4:35–4:45 /km
3. Recover: lap button, no target
4. Rest: 1:00
5. Cooldown: 5:00

**SPIKE B targets**
1. Run: 5:00, heart rate 140–150 bpm
2. Run: 5:00, cadence 170–180 spm
3. Run: 2 km, no target

**SPIKE C repeat**
1. Warmup: 10:00
2. Repeat 3×: Run 400 m at pace 3:50–4:00 /km, then Recover 1:30
3. Cooldown: 10:00

**SPIKE D nested**
1. Repeat 2×: Run 1:00, then a Repeat 3× (Run 0:30, Recover 0:30) inside it

If the editor won't let you put a repeat inside a repeat, skip D and note it.

## 3. Save them (1 min)

```sh
uv run spike/spike.py fetch
```

Expect `Fetched 4 workouts` (or 3 without D).

## 4. Push one and check the watch (10 min)

```sh
uv run spike/spike.py push
```

Then:
1. Open the Garmin Connect app, sync your watch.
2. Check tomorrow in the app calendar: `SPIKE push test v2` is there.
3. Check the watch: Training → Workouts (or the calendar) shows it.

## 5. Clean up (1 min)

```sh
uv run spike/spike.py cleanup
```

Expect `Left in library: 0`. Check whether the calendar item for tomorrow is gone too.

## 6. Report back

Tell Claude these four answers:
1. Login worked? (yes / FAILED line)
2. Nested repeat possible in the editor? (yes / no)
3. Workout showed up on the watch? (yes / no)
4. Calendar item gone after cleanup? (yes / no)

The JSON is in `spike/fixtures/`. Claude reads it from there.
