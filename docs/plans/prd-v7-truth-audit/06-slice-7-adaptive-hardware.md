# Slice 7: adaptive hardware (RES-002, RES-003, RES-004, BG-001)

Design note, 2026-09-28. Owner direction: Amendment A4 (`docs/PRD_V7.md` §98, Appendix F). Two reference machines,
both first-class: REFERENCE-A (this host: 15.4 GB RAM, integrated GPU, ~0.5 GB dedicated VRAM, idles near 76% RAM)
and REFERENCE-B (16 GB RAM, 4 GB dedicated VRAM). Nothing is tuned by hand to either.

## Why first: RES-003

`friday/governor.py` judges pressure by fixed percentages (RAM 75/88/95). On REFERENCE-A an idle system reads
ELEVATED, so optional work is shed at rest - measured 2026-09-27 (RES-001). Raising 75 to 80 would hide that, not fix
it (Appendix F, "The 76% RAM issue").

## Design

1. **Machine profile (`friday/hardware_profile.py`, RES-002).** Measured, never assumed: RAM total, an idle
   baseline (the median RAM-in-use over a calibration window taken while FRIDAY is idle), logical and physical
   CPUs, GPU name and dedicated memory where the platform reports it (unknown otherwise - never guessed), disk free,
   power source and battery. Persisted with a hardware fingerprint (RAM total, CPU count, GPU names). Capabilities
   are derived from measurements (max parallel workers and browsers from headroom over the baseline and the measured
   per-browser cost - 184 MB on REFERENCE-A, BROW-001), not from tier names.
2. **Pressure judged against the machine (RES-003).** `extra = used% - baseline%` and the rate available memory
   is falling (a short window of samples) set the state; static floors on available GB stay as guardrails. States
   GREEN, YELLOW, ORANGE, RED map onto the governor's existing NORMAL, ELEVATED, HIGH, CRITICAL, so admission
   semantics (optional shed, concurrency one, nothing new) are unchanged. Entering a worse state is immediate;
   leaving it needs the better condition to hold for a dwell time (hysteresis), so no flapping at a boundary.
3. **Reprofiling (RES-004).** When the fingerprint changes (RAM, CPU count, GPU) or the power source flips, the
   affected measurements are redone and the cause recorded.
4. **Background work (BG-001).** Optional work also waits for the person to be idle and, on battery, for power.

## RES-003 classifier, decided 2026-09-29

Owner's rule (Amendment A5): static percentage thresholds are emergency guardrails, not the primary pressure
classifier. Measured on REFERENCE-A under the test suite's load: physical RAM 83.5% used (2.54 GB available) while
the commit charge stood at 85.5% of its 30.7 GB limit, so commit is a separate signal from physical use; psutil's
page-in/page-out counters read 0 on Windows, so paging activity is not measurable through psutil and is left out
rather than guessed.

- **Headroom against the machine's own baseline (primary).** For RAM (available GB), the commit charge (GB left
  below the limit, where the platform reports it) and CPU (idle capacity), the share of the idle headroom now
  consumed: `(baseline_free - free_now) / baseline_free`. RAM and commit: YELLOW from 40%, ORANGE from 60%, RED from
  80%. CPU: YELLOW from 60%, ORANGE from 80%, RED from 95%. Memory is judged more conservatively than CPU because
  running out of memory pages the machine and recovers slowly; CPU saturation slows work and recovers at once.
- **Trend.** The least-squares slope of available RAM over the last 60 s of readings (at least three spanning 10 s).
  At the rate it is falling, if it would reach the emergency floor within 60 s the level is ORANGE, within 300 s
  YELLOW. This is what makes a machine at 68% losing memory fast read pressured while one idling at 76% reads GREEN.
- **Guardrails (emergency only).** RAM used 95% or more, available RAM below max(0.5 GB, 3% of RAM), commit headroom
  below max(1 GB, 3% of the limit), or CPU 95% or more: RED. Disk free at or below 1 GB: RED; at or below 5 GB:
  ORANGE. Nothing else is judged by a fixed percentage.
- **Baseline.** The machine profile's idle baseline (RES-002). Until a qualification has run, the first reading
  stands in as a provisional baseline, reported as provisional, and readings taken while FRIDAY holds no leased
  work refine it (their median), so a host is profiled by running on it, not by its name.
- **Hysteresis.** A worse level is entered at once: the governor errs toward protecting the machine. A better level
  is entered one step at a time, only after the reading has stayed below the boundary by a margin (5 points of
  headroom share) for 30 s of sample time. A reading oscillating around a boundary therefore makes one transition,
  not many.
- **Compatibility.** GREEN, YELLOW, ORANGE and RED are the governor's NORMAL, ELEVATED, HIGH and CRITICAL, so the
  admission policy (optional work shed, one worker at a time, nothing new) is unchanged; only what decides the
  level changes. The unit tests that pinned the old static bands are replaced by tests of the new rule, and the
  guardrail cases are kept.

## Proof

- Unit: an idle machine at 76% with a 76% baseline is GREEN; the same machine at 80% with free memory falling fast is
  YELLOW or worse before any fixed percentage; a reading oscillating around a boundary does not flap.
- Real: `scripts/stress_low_ram.py` extended to use the profile's baseline instead of margins over the start, on
  REFERENCE-A; the same run on REFERENCE-B when the owner runs it there.

## Not in this slice

Model residency and offload benchmarking (MODEL-005, MODEL-009, MODEL-010) wait for FRIDAY to manage a local model
runtime at all; they need the profile from this slice first.
