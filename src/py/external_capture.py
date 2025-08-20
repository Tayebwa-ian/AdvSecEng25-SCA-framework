"""
CW305 (standalone) + Rigol MSO5074 capture (One-Trace-at-a-Time Mode for SCA)
-----------------------------------------------------------------------------

Purpose
-------
Run AES-128 encryptions on a CW305 FPGA board and capture independent traces
(one per plaintext) with a Rigol MSO5074 oscilloscope. Each scope acquisition
is fully independent to avoid any influence from previous traces.

This implementation includes robust scope arming and diagnostic features:
- Configurable arming delay (SCOPE_ARM_DELAY) after :SING.
- Clears acquisition state (:STOP + :SING) before every trace.
- Uses *OPC? to ensure the scope processed :SING before polling status.
- Polls multiple possible trigger-status tokens (e.g. 'WAIT', 'ARM', 'READY').
- Retries arming several times and provides an option to skip traces on failure.

Notes
-----
- The script expects a Rigol MSO5xxx that supports the SCPI commands used
  (common for Rigol MSO5000 series). Minor firmware differences may require
  changing accepted trigger-status tokens or query commands.
- If you see unexpected trigger-status strings in debug logs, add them to
  _ACCEPTED_ARMED_STATES.

"""

import os
import time
from dataclasses import dataclass
from random import SystemRandom
from typing import Literal

import numpy as np
import pyvisa
import chipwhisperer as cw

# User Configuration

# Total number of traces to capture
N_TRACES = 2000

# Acquisition mode: 'AVERAGE' (hardware averages), 'HRES' (high-res), or
# 'NORMAL' (single-shot). AVERAGE will use HARDWARE_AVERAGES.
ACQUISITION_MODE: Literal['AVERAGE', 'HRES', 'NORMAL'] = 'HRES'
HARDWARE_AVERAGES = 16

# Bandwidth limit in MHz, or None to leave bandwidth off
BANDWIDTH_LIMIT_MHZ: int | None = 20

# Triggering configuration (digital or analog)
TRIGGER_MODE = "digital"  # 'digital' means TRIG_SRC is a digital input
MEAS_CH = "CHAN1"         # analog channel to record
TRIG_SRC = "CHAN2"        # trigger source (digital input or channel)

# Vertical/time settings for the analog channel we're recording
VERTICAL_SCALE_V = 0.01
TIME_PER_DIV = 2e-6
TRIG_LEVEL_V = 1.5
POINTS_PER_TRACE = 10000

# Force a specific VISA resource (e.g. 'USB0::0x1AB1::0x0588::DS1ZA170600000::INSTR')
RIGOL_VISA_FORCE = None

# FPGA bitstream and output save path
BITSTREAM = r"C:\Users\Admin\Desktop\Security\advseceng25-sca-framework\out\cw305.bit"
SAVE_PATH = r"C:\Users\Admin\Desktop\Security\advseceng25-sca-framework\src\py\data\traces_mso5074.npz"
FIXED_KEY_HEX = "10A58869D74BE5A374CF867CFB473859"

# ----------------------------
# Scope arming configuration
# ----------------------------
# Delay after the scope receives ':SING' before we issue the FPGA 'GO' trigger.
# Many scopes take a short time to arm and settle; tune this if needed.
SCOPE_ARM_DELAY = 0.05  # seconds; set to 0 to skip fixed delay

# Maximum time to wait for scope to enter an 'armed' state (per attempt)
SCOPE_ARM_TIMEOUT = 1.0  # seconds

# How many times to try the :SING -> poll sequence before giving up
SCOPE_ARM_RETRIES = 3

# Behavior if scope fails to arm after retries: skip trace or abort
SKIP_ON_ARM_FAIL = True

# Accept a set of possible 'armed' status tokens returned by :TRIG:STAT?
# Common tokens include 'WAIT', but firmware can differ; add tokens you
# observe in the debug output if necessary.
_ACCEPTED_ARMED_STATES = ('WAIT', 'ARM', 'READY', 'SINGLE', 'TRIG')

# Small delay after trigger completes before reading waveform (some scopes)
POST_TRIGGER_DELAY = 0.001

# Rigol MSO5000 control helper
class RigolScope:
    """Rigol MSO5xxx control wrapper.

    Provides connect/configure/arm/read helpers and handles some
    firmware quirks (different :TRIG:STAT? strings, need for *OPC?).
    """

    def __init__(self, visa_resource: str | None = None):
        self.visa_resource = visa_resource
        self.rm = None
        self.inst = None

    def connect(self):
        """Connect to the oscilloscope via pyvisa.

        If no visa_resource is provided, auto-discovery looks for a device
        whose *IDN? contains 'RIGOL' and 'MSO5'.
        """
        if not self.visa_resource:
            self.visa_resource = self._find_device()
        print(f"[INFO] Connecting to Rigol at: {self.visa_resource}")
        self.rm = pyvisa.ResourceManager()
        self.inst = self.rm.open_resource(self.visa_resource)
        # Increase timeout to tolerate long operations (binary transfers, etc.)
        self.inst.timeout = 30000
        try:
            # allow larger binary transfers if supported by backend
            self.inst.chunk_size = 1024 * 1024
        except Exception:
            pass
        print(f"[INFO] Connected: {self.inst.query('*IDN?').strip()}")

    def _find_device(self, hint: str = "MSO5") -> str:
        """Scan VISA resources and return the first Rigol MSO5 device found."""
        rm = pyvisa.ResourceManager()
        for res in rm.list_resources():
            try:
                inst = rm.open_resource(res, open_timeout=2000)
                idn = inst.query("*IDN?").strip()
                inst.close()
                if "RIGOL" in idn and hint in idn:
                    return res
            except Exception:
                pass
        raise RuntimeError("Rigol scope not found. Provide RIGOL_VISA_FORCE to override.")

    def setup_for_single_trace(self):
        """Configure scope for one-trace capture.

        This function resets the instrument, configures acquisition type,
        channel scaling and trigger. It places the scope in STOP so callers
        can explicitly arm it prior to each trace.
        """
        # Reset instrument state and stop any running acquisitions
        self.inst.write("*RST")
        self.inst.write(":STOP")

        # Acquisition mode selection
        if ACQUISITION_MODE == 'AVERAGE':
            self.inst.write(":ACQ:TYPE AVER")
            self.inst.write(f":ACQ:COUN {HARDWARE_AVERAGES}")
        elif ACQUISITION_MODE == 'HRES':
            self.inst.write(":ACQ:TYPE NORM")
            self.inst.write(":ACQ:MODE HRES")
        else:
            self.inst.write(":ACQ:TYPE NORM")
            self.inst.write(":ACQ:MODE NORM")

        # Memory depth and points per trace
        self.inst.write(f":ACQ:MDEP {POINTS_PER_TRACE}")

        # Channel setup (display, coupling, probe factor, vertical scale)
        self.inst.write(f":{MEAS_CH}:DISP ON")
        self.inst.write(f":{MEAS_CH}:COUP DC")
        self.inst.write(f":{MEAS_CH}:PROB 1")
        self.inst.write(f":{MEAS_CH}:SCAL {VERTICAL_SCALE_V}")

        # Bandwidth limit (optional)
        if BANDWIDTH_LIMIT_MHZ:
            self.inst.write(f":{MEAS_CH}:BWL {BANDWIDTH_LIMIT_MHZ}M")
        else:
            self.inst.write(f":{MEAS_CH}:BWL OFF")

        # Timebase
        self.inst.write(":TIM:MODE MAIN")
        self.inst.write(f":TIM:SCAL {TIME_PER_DIV}")

        # Trigger setup
        trig_src = TRIG_SRC if TRIGGER_MODE == "digital" else MEAS_CH
        trig_lvl = TRIG_LEVEL_V if TRIGGER_MODE == "digital" else 0.01
        self.inst.write(":TRIG:MODE EDGE")
        self.inst.write(f":TRIG:EDGE:SOUR {trig_src}")
        self.inst.write(":TRIG:EDGE:SLOP POS")
        self.inst.write(f":TRIG:LEV {trig_lvl}")

        # Waveform transfer configuration
        self.inst.write(":WAV:SOUR " + MEAS_CH)
        self.inst.write(":WAV:MODE NORM")
        self.inst.write(":WAV:FORM BYTE")

    # Trigger / arming helpers
    def query_trigger_status(self) -> str:
        """Query the instrument trigger status string.

        Some firmwares support ':TRIG:STAT?' while others might expose
        slightly different variants. We try multiple possible queries and
        return the first non-empty normalized string.
        """
        candidates = [":TRIG:STAT?", ":TRIGGER:STATUS?", ":TRIGger:STAT?"]
        for q in candidates:
            try:
                stat = self.inst.query(q).strip()
                if stat:
                    return stat.upper()
            except Exception:
                # Ignore and try the next candidate
                pass
        # If none responded, return an empty string
        return ""

    def clear_and_arm(self, delay_after_sing: float = SCOPE_ARM_DELAY, timeout: float | None = None) -> bool:
        """Stop previous acquisition, issue :SING and wait for the scope to
        enter an accepted 'armed' state. Returns True if armed within the
        configured timeout and retries; False otherwise.

        Steps:
        1. Write ':STOP' to clear previous acquisitions.
        2. Write ':SING' to request a single acquisition.
        3. Block on '*OPC?' (if supported) so the scope processes commands.
        4. Wait fixed delay (delay_after_sing) to give hardware a moment.
        5. Poll ':TRIG:STAT?' until an accepted armed token appears or timeout.
        6. Retry the entire sequence up to SCOPE_ARM_RETRIES times.
        """
        if timeout is None:
            timeout = SCOPE_ARM_TIMEOUT

        # Ensure a clean starting point
        try:
            self.inst.write(":STOP")
        except Exception:
            pass

        for attempt in range(1, SCOPE_ARM_RETRIES + 1):
            try:
                # Request a single acquisition
                self.inst.write(":SING")

                # Block until the instrument has processed queued commands.
                # Some firmwares honor '*OPC?' and it returns '1' when all
                # operations are complete. If it fails, we fall back to a
                # small sleep below.
                try:
                    self.inst.query("*OPC?")
                except Exception:
                    # *OPC? may not be supported by all firmwares or may
                    # time out; nonetheless continue after a short delay.
                    pass

                # Allow a short fixed delay for hardware to settle
                if delay_after_sing > 0:
                    time.sleep(delay_after_sing)

                # Poll for an 'armed' state until timeout
                start = time.time()
                while (time.time() - start) < timeout:
                    st = self.query_trigger_status()
                    if any(tok in st for tok in _ACCEPTED_ARMED_STATES):
                        return True
                    time.sleep(0.02)

            except Exception as e:
                # Log exception and retry after a short backoff
                print(f"[WARN] Exception while trying to arm (attempt {attempt}): {e}")
                time.sleep(0.05)

        # If we exhausted retries and never saw an accepted 'armed' state
        return False

    # Acquisition helpers

    def wait_for_trace(self):
        """Block until the oscilloscope reports the previously requested
        operations are complete. Uses '*OPC?' which typically returns '1'
        once the device has finished processing queued commands.
        """
        try:
            self.inst.query("*OPC?")
        except Exception:
            # If query fails, continue; the caller may still attempt to read
            # the waveform, which will raise if no data is present.
            pass

    def read_single_trace(self) -> np.ndarray:
        """Read the last acquired waveform as a numpy array of voltages.

        The Rigol waveform binary transfer uses YINC/YOR/YREF to map bytes
        to voltages. We convert accordingly and return a float32 array.
        """
        y_inc = float(self.inst.query(':WAV:YINC?'))
        y_org = float(self.inst.query(':WAV:YOR?'))
        y_ref = float(self.inst.query(':WAV:YREF?'))
        raw = self.inst.query_binary_values(':WAV:DATA?', datatype='B', container=np.array)
        return (raw.astype(np.float32) - y_ref) * y_inc + y_org

    def disconnect(self):
        """Return the scope to RUN (if possible) and close VISA resources."""
        if self.inst:
            try:
                self.inst.write(":RUN")
                self.inst.close()
            except Exception:
                pass
        if self.rm:
            try:
                self.rm.close()
            except Exception:
                pass

# CW305 Helpers
@dataclass
class TraceMeta:
    """Simple container for trace metadata."""
    pt: bytes
    key: bytes
    ct: bytes

_rng = SystemRandom()


def make_plaintexts(n: int) -> list[bytes]:
    """Generate `n` random 16-byte plaintexts using a secure RNG.

    Returns a list of bytes objects, each 16 bytes long.
    """
    return [bytes(_rng.getrandbits(8) for _ in range(16)) for _ in range(n)]


def setup_cw305():
    """Initialize and configure the CW305 target board.

    - Connects to the CW305 target provided by chipwhisperer.
    - Programs the provided bitstream (unless already loaded).
    - Sets VCCINT, PLL, and other tiny target-specific values.

    Returns the initialized target object.
    """
    print("[INFO] Initializing CW305...")
    t = cw.targets.CW305()
    # Program the FPGA if necessary. Set force=True to always reprogram.
    t.con(bsfile=BITSTREAM, force=False, fpga_id='100t')
    t.vccint_set(1.0)
    t.pll.pll_enable_set(True)
    t.pll.pll_outenable_set(True, 1)
    t.pll.pll_outfreq_set(7.37E6, 1)
    t.bytecount_size = 7
    # Toggle a register to place the target in a known state
    t.fpga_write(0x07, b"\x01")
    time.sleep(0.01)
    t.fpga_write(0x07, b"\x00")
    print("[INFO] CW305 ready.")
    return t


def run_aes(target, pt, key) -> bytes:
    """Perform a single AES encryption on the target and return the
    resulting ciphertext bytes.

    The CW305 FPGA interface uses reversed byte order for writes/reads;
    therefore we reverse the plaintext/key when writing and reverse the
    ciphertext when reading back.
    """
    REG_KEY, REG_PT, REG_CT, REG_GO = 0x08, 0x09, 0x0A, 0x05
    # Helper functions to reverse byte order for the FPGA interface
    wr = lambda x: bytearray(x[::-1])
    rd = lambda x: bytes(x[::-1])

    # Write plaintext and key, then pulse GO
    target.fpga_write(REG_PT, wr(pt))
    target.fpga_write(REG_KEY, wr(key))
    target.fpga_write(REG_GO, b"\x01")

    # Wait for the hardware to clear GO (device sets GO=1 while busy)
    while target.fpga_read(REG_GO, 1)[0] == 0x01:
        time.sleep(0.0005)

    return rd(target.fpga_read(REG_CT, 16))

# ----------------------------
# Main capture loop
# ----------------------------

def main():
    """Run the capture session.

    Steps:
    1. Ensure output directory exists.
    2. Connect to scope and target and configure both.
    3. For each plaintext: clear+arm the scope, start AES on FPGA, wait and
       read the trace, store waveform and metadata.
    4. Save to a compressed .npz file.
    """
    os.makedirs(os.path.dirname(SAVE_PATH), exist_ok=True)

    scope = RigolScope(visa_resource=RIGOL_VISA_FORCE)
    cw305 = None
    try:
        # Connect and configure devices
        scope.connect()
        scope.setup_for_single_trace()
        cw305 = setup_cw305()

        key = bytes.fromhex(FIXED_KEY_HEX)
        pts = make_plaintexts(N_TRACES)
        traces = []
        metadata = []

        for i, pt in enumerate(pts):
            # Clear previous acquisition and arm the scope. The function
            # will retry internally up to SCOPE_ARM_RETRIES times.
            armed = scope.clear_and_arm(delay_after_sing=SCOPE_ARM_DELAY)
            if not armed:
                print(f"[WARN] Trace {i+1}: scope did not report armed within {SCOPE_ARM_TIMEOUT}s; retrying...")
                armed = scope.clear_and_arm(delay_after_sing=SCOPE_ARM_DELAY)

            if not armed:
                msg = f"Scope failed to arm for trace {i+1} after {SCOPE_ARM_RETRIES} attempts."
                if SKIP_ON_ARM_FAIL:
                    print("[WARN] " + msg + " Skipping this trace and continuing.")
                    # Skip this trace but keep loop running
                    continue
                else:
                    raise RuntimeError(msg + " Aborting.")

            # Scope is confirmed armed/waiting for trigger. Start AES on FPGA.
            ct = run_aes(cw305, pt, key)

            # Wait for scope to complete acquisition and collect waveform
            scope.wait_for_trace()
            if POST_TRIGGER_DELAY:
                time.sleep(POST_TRIGGER_DELAY)

            try:
                trace = scope.read_single_trace()
            except Exception as e:
                print(f"[ERROR] Failed to read trace {i+1}: {e}")
                if SKIP_ON_ARM_FAIL:
                    print("[WARN] Skipping this trace due to read error.")
                    continue
                else:
                    raise

            traces.append(trace)
            metadata.append(TraceMeta(pt=pt, key=key, ct=ct))

            if (i+1) % 100 == 0:
                print(f"[INFO] Captured {i+1}/{N_TRACES} traces")

        # Convert lists to numpy arrays and save
        waves = np.asarray(traces, dtype=np.float32)
        pts_arr = np.array([list(m.pt) for m in metadata], dtype=np.uint8)
        keys_arr = np.array([list(m.key) for m in metadata], dtype=np.uint8)
        cts_arr = np.array([list(m.ct) for m in metadata], dtype=np.uint8)

        rigol_meta = {
            'acq_mode': ACQUISITION_MODE,
            'hardware_averages': HARDWARE_AVERAGES if ACQUISITION_MODE == 'AVERAGE' else 1,
            'bandwidth_limit_mhz': BANDWIDTH_LIMIT_MHZ,
            'points_per_trace': POINTS_PER_TRACE,
            'time_per_div_s': TIME_PER_DIV,
            'vertical_scale_v': VERTICAL_SCALE_V,
            'scope_arm_delay_s': SCOPE_ARM_DELAY,
            'scope_arm_timeout_s': SCOPE_ARM_TIMEOUT,
            'scope_arm_retries': SCOPE_ARM_RETRIES,
        }

        np.savez_compressed(
            SAVE_PATH,
            waves=waves,
            plaintexts=pts_arr,
            keys=keys_arr,
            ciphertexts=cts_arr,
            rigol_config=rigol_meta,
            target_config={'bitstream': os.path.basename(BITSTREAM)}
        )
        print(f"[SUCCESS] Saved {waves.shape[0]} traces to {SAVE_PATH}")

    finally:
        # Always attempt to cleanly disconnect
        scope.disconnect()
        if cw305:
            try:
                cw305.dis()
            except Exception:
                pass

if __name__ == "__main__":
    main()
