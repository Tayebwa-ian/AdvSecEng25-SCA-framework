"""
CW305 (standalone) + Rigol MSO5074 segmented capture
----------------------------------------------------

Purpose
-------
Run AES-128 encryptions on a CW305 FPGA board and capture aligned power traces
with a Rigol MSO5074 oscilloscope. No ChipWhisperer scope is used — the Rigol
does all waveform acquisition.

Triggering
----------
1) Digital trigger (recommended):
   - Export a trigger pin from your AES design (asserted during encryption).
   - Wire that pin to Rigol CHAN2 (or EXT), set TRIGGER_MODE = "digital".

2) Power trigger (fallback):
   - Trigger directly on the power spike on CHAN1.
   - Set TRIGGER_MODE = "power".

Wiring
------
- Measure power across the shunt (CW305) → Rigol CHAN1 (coax or diff probe).
- Trigger pin from CW305 → Rigol CHAN2 (or EXT) if using digital trigger.
- Common ground between CW305 and Rigol.

Requirements
------------
- Python packages: chipwhisperer, pyvisa, numpy
- Rigol VISA connection (NI-VISA or pyvisa-py + WinUSB)
- A working CW305 bitstream that exposes AES control registers used here.

Outputs
-------
Saves a compressed .npz containing:
- waves: np.float32 array (N_TRACES x POINTS_PER_SEGMENT or averaged)
- plaintexts, keys, ciphertexts: uint8 arrays for each logical trace
- rigol / cw305 / meta dicts with capture configuration
"""

import os
import time
from dataclasses import dataclass
from random import SystemRandom

import numpy as np
import pyvisa
import chipwhisperer as cw

# =========================
# User Configuration
# =========================

# Capture plan
N_TRACES = 5000            # number of logical traces saved
AVERAGE_OVER = 1           # segments per logical trace; averaged in software if >1

# Trigger mode: "digital" (use CHAN2/EXT) or "power" (trigger on CHAN1 power)
TRIGGER_MODE = "digital"   # "digital" or "power"

# Rigol channels & scales
MEAS_CH = "CHAN1"          # analog measurement channel (power)
TRIG_SRC = "CHAN2"         # "CHAN2" or "EXT" for digital; overridden to "CHAN1" if TRIGGER_MODE="power"
TRIG_LEVEL_V = 1.5         # digital trigger level — adjust to your FPGA IO level

# Timebase & vertical (tune for your setup)
POINTS_PER_SEGMENT = 500   # memory depth per segment (ACQ:MDEP)
TIME_PER_DIV = 2e-6        # sec/div; widen if missing triggers, narrow for more resolution
VERTICAL_SCALE_V = 0.05    # volts/div on MEAS_CH; tune so the power waveform fits vertically

# CW305 bitstream path (your provided path)
BITSTREAM = r"C:\Users\Admin\Desktop\Security\advseceng25-sca-framework\out\cw305.bit"

# Save path (your provided path)
SAVE_PATH = r"C:\Users\Admin\Desktop\Security\advseceng25-sca-framework\src\py\data\traces_mso5074_1.npz"

# Optional: hardcode the VISA resource for the Rigol (USB or LAN). Set to None to auto-detect.
RIGOL_VISA_FORCE = None    # e.g. "USB0::0x1AB1::0x04CE::MS5A12345678::INSTR"

# Optional: fixed AES-128 key used for all traces (hex string)
FIXED_KEY_HEX = "10A58869D74BE5A374CF867CFB473859"

# Small safety delays
SCOPE_ARM_DELAY = 0.25     # seconds to wait after arming scope before first trigger
POST_TRIGGER_DELAY = 0.001 # small pause after each encryption to help re-arm

# Readback timeout (ms). Increase if transfers are slow.
RIGOL_READ_TIMEOUT_MS = 120000  # 120 seconds

# Maximum attempts to read a segment before giving up (will return NaN-filled array on fail)
MAX_SEGMENT_READ_ATTEMPTS = 2

# =========================
# Internal Helpers
# =========================

_rng = SystemRandom()

@dataclass
class TraceMeta:
    """Holds metadata for each logical trace."""
    pt: bytes
    key: bytes
    ct: bytes

def make_plaintexts(n: int) -> list[bytes]:
    """Generate n random 16-byte plaintexts."""
    return [bytes(_rng.getrandbits(8) for _ in range(16)) for _ in range(n)]

# ---------------------------
# Rigol MSO5000 helpers
# ---------------------------

def find_rigol(scope_hint: str = "MSO5") -> str:
    """
    Auto-detect a Rigol MSO5000 via VISA by querying *IDN?.
    Tries system backend first, then the pure-Python backend (@py).
    Returns a VISA resource string.
    Raises RuntimeError if none found.
    """
    def _scan(rm) -> list[tuple[str, str]]:
        hits = []
        try:
            resources = rm.list_resources()
        except Exception:
            resources = []
        for r in resources:
            try:
                inst = rm.open_resource(r)
                inst.timeout = 2000
                idn = inst.query("*IDN?").strip()
                inst.close()
            except Exception:
                continue
            if "RIGOL" in idn.upper() and scope_hint.upper() in idn.upper():
                hits.append((r, idn))
        return hits

    # Try system backend (e.g., NI-VISA)
    try:
        rm0 = pyvisa.ResourceManager()
        hits0 = _scan(rm0)
        if hits0:
            print(f"[INFO] Found Rigol devices (system backend): {hits0}")
            return hits0[0][0]
    except Exception:
        pass

    # Try pure Python backend (@py)
    try:
        rm1 = pyvisa.ResourceManager("@py")
        hits1 = _scan(rm1)
        if hits1:
            print(f"[INFO] Found Rigol devices (pyvisa-py): {hits1}")
            return hits1[0][0]
    except Exception:
        pass

    raise RuntimeError("Rigol scope not found via VISA. Check drivers/cables or set RIGOL_VISA_FORCE.")

def setup_rigol_segmode(total_segments: int, visa_override: str | None = None):
    """
    Configure the Rigol MSO5074 for segmented acquisition.
    Returns (rm, inst) where inst is an open pyvisa instrument.
    """
    visa_str = visa_override or find_rigol("MSO5")
    rm = pyvisa.ResourceManager()
    inst = rm.open_resource(visa_str)
    # Increase chunk size if available (some backends support)
    try:
        inst.chunk_size = 1024 * 64
    except Exception:
        pass
    inst.timeout = 30000  # ms

    # Reset and stop acquisition
    inst.write("*RST")
    inst.write(":STOP")

    # Measurement channel setup
    inst.write(f":{MEAS_CH}:DISP ON")
    inst.write(f":{MEAS_CH}:COUP DC")
    inst.write(f":{MEAS_CH}:PROB 1")
    inst.write(f":{MEAS_CH}:SCAL {VERTICAL_SCALE_V}")

    # Timebase
    inst.write(":TIM:MODE MAIN")
    inst.write(f":TIM:SCAL {TIME_PER_DIV}")

    # Segmented acquisition
    inst.write(":ACQ:MODE SEGM")
    inst.write(f":ACQ:MDEP {POINTS_PER_SEGMENT}")
    inst.write(f":ACQ:SEGM:COUN {total_segments}")

    # Trigger configuration
    if TRIGGER_MODE.lower() == "power":
        inst.write(":TRIG:MODE EDGE")
        inst.write(f":TRIG:EDGE:SOUR {MEAS_CH}")
        inst.write(":TRIG:EDGE:SLOP POS")
        inst.write(":TRIG:LEV 0.01")
    else:
        inst.write(":TRIG:MODE EDGE")
        inst.write(f":TRIG:EDGE:SOUR {TRIG_SRC}")
        inst.write(":TRIG:EDGE:SLOP POS")
        inst.write(f":TRIG:LEV {TRIG_LEVEL_V}")

    # Waveform readback settings
    inst.write(":WAV:FORM BYTE")
    inst.write(":WAV:MODE NORM")
    try:
        inst.write(":WAV:POIN:MODE RAW")
    except Exception:
        # some firmwares may not support
        pass
    inst.write(f":WAV:SOUR {MEAS_CH}")

    # Arm segmented capture (Single sequence)
    inst.write(":SING")
    time.sleep(0.05)
    return rm, inst

def rigol_select_segment(inst, seg_index: int):
    """
    Select the segment index on the Rigol and return the integer segment number used.
    Note: some firmwares expect 1-based indices, others 0-based. We'll use the provided index.
    """
    inst.write(f":WAV:SEGM {seg_index}")
    return seg_index

def rigol_get_points_for_selected_segment(inst) -> int | None:
    """
    Query how many points the selected segment will return.
    Returns int number of points, or None if the query fails.
    """
    try:
        # :WAV:POIN? is common; return can be float-like string so cast via float then int
        pts = inst.query(":WAV:POIN?")
        return int(float(pts))
    except Exception:
        try:
            pts = inst.query(":ACQ:MDEP?")
            return int(float(pts))
        except Exception:
            return None

def rigol_read_segment(inst, seg_index: int) -> np.ndarray:
    """
    Robust reading of one segment from the Rigol as calibrated volts (float32).
    - Selects the segment, queries :WAV:POIN? for expected points.
    - Temporarily increases timeout to RIGOL_READ_TIMEOUT_MS while reading binary data.
    - Retries up to MAX_SEGMENT_READ_ATTEMPTS times on VisaIOError.
    - If unable to read, returns a NaN-filled array of length POINTS_PER_SEGMENT.
    """
    # Select segment
    rigol_select_segment(inst, seg_index)

    # Query expected points
    n_points = rigol_get_points_for_selected_segment(inst)
    if n_points is None:
        n_points = POINTS_PER_SEGMENT  # fallback
    if n_points == 0:
        # Segment is empty — return NaNs (but warn)
        print(f"[WARN] Segment {seg_index} reports 0 points; returning NaN array.")
        return np.full((POINTS_PER_SEGMENT,), np.nan, dtype=np.float32)

    # Get scaling parameters (per-segment)
    try:
        yinc = float(inst.query(":WAV:YINC?"))
        yref = float(inst.query(":WAV:YREF?"))
        yor  = float(inst.query(":WAV:YOR?"))
    except Exception as e:
        # If queries fail, set safe defaults (will result in raw counts)
        print(f"[WARN] Failed to query YINC/YREF/YOR for segment {seg_index}: {e}")
        yinc, yref, yor = 1.0, 128.0, 0.0

    prev_timeout = getattr(inst, "timeout", 30000)
    # Set a long timeout for large transfers
    inst.timeout = max(prev_timeout, RIGOL_READ_TIMEOUT_MS)

    last_err = None
    for attempt in range(1, MAX_SEGMENT_READ_ATTEMPTS + 1):
        try:
            # Read raw binary block as unsigned bytes
            raw = np.array(
                inst.query_binary_values(":WAV:DATA?", datatype='B', container=np.array),
                dtype=np.float32
            )
            if raw.size == 0:
                # Empty read — warn & retry
                print(f"[WARN] Read returned 0 bytes for segment {seg_index} (attempt {attempt}/{MAX_SEGMENT_READ_ATTEMPTS}).")
                last_err = RuntimeError("Empty read")
                time.sleep(0.1)
                continue

            # If we got fewer points than expected, still proceed but warn
            if raw.size != n_points:
                print(f"[WARN] Segment {seg_index}: expected {n_points} points, got {raw.size} bytes.")

            volts = (raw - yref) * yinc + yor
            return volts.astype(np.float32)

        except pyvisa.errors.VisaIOError as e:
            last_err = e
            print(f"[ERROR] VisaIOError reading segment {seg_index} (attempt {attempt}/{MAX_SEGMENT_READ_ATTEMPTS}): {e}")
            time.sleep(0.2)
            continue
        except Exception as e:
            last_err = e
            print(f"[ERROR] Unexpected error reading segment {seg_index} (attempt {attempt}/{MAX_SEGMENT_READ_ATTEMPTS}): {e}")
            time.sleep(0.2)
            continue
        finally:
            # restore timeout only after final attempt or return
            inst.timeout = prev_timeout

    # If we reach here, all attempts failed — return NaNs so the capture can complete
    print(f"[ERROR] Failed to read segment {seg_index} after {MAX_SEGMENT_READ_ATTEMPTS} attempts. Returning NaN array.")
    return np.full((POINTS_PER_SEGMENT,), np.nan, dtype=np.float32)

# ---------------------------
# CW305 (standalone) helpers
# ---------------------------

def setup_cw305_standalone():
    """
    Connect to the CW305 target over USB only (no OpenADC),
    program the bitstream, set the core voltage and PLL.
    Also performs a core reset and sets bytecount size.
    """
    t = cw.targets.CW305()
    # Prefer modern API; fall back if older CW version
    try:
        t.con(bsfile=BITSTREAM, force=False, fpga_id='100t', slurp=False)
    except Exception:
        t._con(scope=None, bsfile=BITSTREAM, force=False, fpga_id='100t', slurp=False)

    # Core voltage and clocking
    t.vccint_set(1.0)

    t.pll.pll_enable_set(True)
    t.pll.pll_outenable_set(False, 0)
    t.pll.pll_outenable_set(True, 1)
    t.pll.pll_outenable_set(False, 2)
    t.pll.pll_outfreq_set(7.37E6, 1)  # Typical lab clock; adjust as needed

    t.clkusbautooff = True
    t.clksleeptime = 1

    # --- AES core initialization ---
    REG_DUT_RESET = 0x07
    # For AES-128 (16 bytes), many CW305 designs expect BYTECNT_SIZE = 7 (log2(128))
    t.bytecount_size = 7

    # Pulse reset
    t.fpga_write(REG_DUT_RESET, b"\x01")
    time.sleep(0.001)
    t.fpga_write(REG_DUT_RESET, b"\x00")
    # --------------------------------

    return t

def dut_write_go_wait_read(target: cw.targets.CW305, pt: bytes, key: bytes) -> bytes:
    """
    Write plaintext and key to CW305 registers, start computation (GO=1),
    wait until done (GO clears to 0), read ciphertext.
    Assumes the bitstream asserts a trigger pin during active encryption.
    """
    REG_DUT_KEYIN   = 0x08
    REG_DUT_DATAIN  = 0x09
    REG_DUT_DATAOUT = 0x0A
    REG_DUT_GO      = 0x05

    def fmt_wr(x: bytes) -> bytearray:
        return bytearray(x[::-1])  # CW305 registers are little-endian addressed
    def fmt_rd(x: bytearray) -> bytes:
        return bytes(x[::-1])

    # Write inputs
    target.fpga_write(REG_DUT_DATAIN, fmt_wr(pt))
    target.fpga_write(REG_DUT_KEYIN,  fmt_wr(key))

    # Start the AES engine: set GO = 1
    target.fpga_write(REG_DUT_GO, b"\x01")

    # Wait until done: GO returns to 0
    tries = 0
    while target.fpga_read(REG_DUT_GO, 1)[0] == 0x01:
        time.sleep(0.0005)
        tries += 1
        if tries > 20000:
            # Helpful debug print before raising
            print(f"[ERROR] AES core timeout. PT={pt.hex()} KEY={key.hex()}")
            raise RuntimeError("AES core timeout on CW305")

    # Read ciphertext
    ct = fmt_rd(target.fpga_read(REG_DUT_DATAOUT, 16))
    return ct

# =========================
# Main capture routine
# =========================

def main():
    total_segments = int(N_TRACES) * max(1, int(AVERAGE_OVER))
    os.makedirs(os.path.dirname(SAVE_PATH), exist_ok=True)

    rm = None
    rigol = None
    cw305 = None

    try:
        # Configure and arm the Rigol first (so it's ready before first encryption)
        rm, rigol = setup_rigol_segmode(total_segments, visa_override=RIGOL_VISA_FORCE)

        # Standalone CW305 setup
        cw305 = setup_cw305_standalone()

        # Allow the Rigol to fully arm
        print("[INFO] Waiting for Rigol to arm...")
        time.sleep(SCOPE_ARM_DELAY)

        # Fixed key for all traces
        key = bytes.fromhex(FIXED_KEY_HEX)

        # Generate plaintexts (one per logical trace)
        pts = make_plaintexts(N_TRACES)

        metas: list[TraceMeta] = []
        seg_count = 0

        # Drive exactly total_segments encryptions (grouped for averaging if requested)
        print(f"[INFO] Starting capture of {N_TRACES} logical traces ({total_segments} segments total).")
        for i_pt, pt in enumerate(pts, start=1):
            last_ct = None
            for _ in range(max(1, AVERAGE_OVER)):
                seg_count += 1
                # Write inputs and start AES on CW305 (assumes trigger pin toggles during encryption)
                last_ct = dut_write_go_wait_read(cw305, pt, key)
                # Optional small delay to help scope re-arm
                time.sleep(POST_TRIGGER_DELAY)
                if (seg_count % 100) == 0 or seg_count == total_segments:
                    print(f"[INFO] Triggered {seg_count}/{total_segments} segments")
            metas.append(TraceMeta(pt=pt, key=key, ct=last_ct))

        # Wait until the Rigol confirms segmented acquisition is complete (synchronization)
        try:
            print("[INFO] Waiting for instrument operations to complete...")
            rigol.query("*OPC?")
        except Exception:
            # Non-fatal; will attempt to read segments anyway
            print("[WARN] *OPC? query failed; continuing to read segments.")

        # Stop acquisition to allow waveform readout
        try:
            rigol.write(":STOP")
        except Exception:
            print("[WARN] Failed to send :STOP to Rigol; continuing anyway.")

        # Debug: print configured segment count and points per segment
        try:
            segcount_report = rigol.query(":ACQ:SEGM:COUN?")
            print(f"[DEBUG] Rigol configured segment count: {segcount_report}")
        except Exception:
            pass
        try:
            wpoints = rigol.query(":WAV:POIN?")
            print(f"[DEBUG] Rigol reports WAV:POIN? = {wpoints}")
        except Exception:
            pass

        print("[INFO] Acquisition complete. Reading segments from Rigol...")

        # Increase read timeout while we do bulk readback
        prev_timeout = getattr(rigol, "timeout", 30000)
        rigol.timeout = max(prev_timeout, RIGOL_READ_TIMEOUT_MS)

        segs = []
        for idx in range(1, total_segments + 1):
            seg = rigol_read_segment(rigol, idx)
            segs.append(seg)
            if (idx % 100) == 0 or idx == total_segments:
                print(f"[INFO] Transferred {idx}/{total_segments}")

        # Restore timeout
        rigol.timeout = prev_timeout

        segs = np.asarray(segs, dtype=np.float32)  # shape: (total_segments, points)

        # Average groups of AVERAGE_OVER, if requested
        if AVERAGE_OVER > 1:
            segs = segs.reshape(N_TRACES, AVERAGE_OVER, -1).mean(axis=1)

        # Save results: store plaintexts/keys/ciphertexts as uint8 arrays (N x 16)
        plaintexts_arr = np.array([list(m.pt) for m in metas], dtype=np.uint8).reshape(-1, 16)
        keys_arr = np.array([list(m.key) for m in metas], dtype=np.uint8).reshape(-1, 16)
        ciphertexts_arr = np.array([list(m.ct) for m in metas], dtype=np.uint8).reshape(-1, 16)

        np.savez_compressed(
            SAVE_PATH,
            waves=segs,
            plaintexts=plaintexts_arr,
            keys=keys_arr,
            ciphertexts=ciphertexts_arr,
            rigol=dict(
                trig_mode=TRIGGER_MODE,
                meas_ch=MEAS_CH,
                trig_src=(MEAS_CH if TRIGGER_MODE.lower() == "power" else TRIG_SRC),
                trig_level_v=(0.01 if TRIGGER_MODE.lower() == "power" else TRIG_LEVEL_V),
                points_per_segment=POINTS_PER_SEGMENT,
                time_per_div=TIME_PER_DIV,
                vertical_scale_v=VERTICAL_SCALE_V,
            ),
            cw305=dict(bitstream=BITSTREAM),
            meta=dict(n_traces=N_TRACES, average_over=AVERAGE_OVER),
        )

        print(f"[OK] Saved {segs.shape[0]} traces to {SAVE_PATH}")

    finally:
        # Close instruments cleanly even on error
        try:
            if rigol:
                rigol.close()
        except Exception:
            pass
        try:
            if cw305:
                cw305.dis()
        except Exception:
            pass
        try:
            if rm:
                rm.close()
        except Exception:
            pass

# Entry point
if __name__ == "__main__":
    main()
