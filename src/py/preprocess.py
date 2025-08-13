"""
Trace preprocessing helper (with metadata preservation and alignment)
- Loads raw traces saved by the external capture script (expected keys:
  'waves', 'plaintexts', 'ciphertexts' (optional), 'keys' (optional)).
- Auto-detects the encryption region across traces (activity-based).
- Aligns traces to a sharp edge inside that region (per-trace alignment).
- Crops, baseline-removes, detrends, normalizes, and smooths.
- Saves preprocessed traces along with corresponding plaintexts and ciphertexts.

Usage:
  Adjust PATH_IN / PATH_OUT and parameters below, then run this script.
"""

import os
from typing import Tuple
import numpy as np
import matplotlib.pyplot as plt

# -------------------------
# User configuration
# -------------------------
PATH_IN = "src/py/data/traces_mso5074.npz"           # input capture file
PATH_OUT = "src/py/data/traces_mso5074_preproc.npz"  # output preprocessed file

# Detection / alignment parameters (tune for your traces)
PRE_SAMPLES = 150    # samples at start considered baseline for activity detection
SMOOTH_K = 11        # smoothing window for activity detection (odd)
THRESH_STD = 3.0     # threshold in std-devs above baseline activity to detect event
MARGIN = 50          # samples to expand detected region on each side

# Alignment (always enabled per your request)
DO_ALIGN = True      # perform per-trace alignment
REF_ALIGNMENT = "median"  # "median" (use median event index) or an int sample index

# Cropping and normalization
BASELINE_WINDOW = (0, 20)   # within cropped window, used to compute baseline mean for removal
DETREND = True              # remove slow-varying trend (simple moving-average)
NORMALIZE = True            # divide each trace by its std
SMOOTH_K_FINAL = 3         # final smoothing kernel size (1 = none)

# Diagnostics
PLOT_DIAGNOSTICS = False    # set True to show mean traces before/after

# Internal helpers
def _moving_average(x: np.ndarray, k: int) -> np.ndarray:
    """Simple moving average (1D)."""
    if k <= 1:
        return x
    kernel = np.ones(k, dtype=float) / k
    return np.convolve(x, kernel, mode="same")


def load_capture_npz(path: str):
    """
    Load waves and optional metadata from capture .npz.
    Returns a dict with keys: 'waves' (float32, N x T) and optional 'plaintexts', 'ciphertexts', 'keys'.
    """
    dat = np.load(path, allow_pickle=True)
    out = {}
    if "waves" not in dat:
        raise KeyError(f"{path} does not contain 'waves'.")
    waves = np.asarray(dat["waves"], dtype=np.float32)
    if waves.ndim == 1:
        waves = waves[np.newaxis, :]
    out["waves"] = waves

    # load optional metadata and normalize representation to (N, 16) uint8 if possible
    for k in ("plaintexts", "ciphertexts", "keys"):
        if k in dat:
            arr = dat[k]
            arr = np.asarray(arr)
            if arr.dtype == np.object_:
                conv = np.array([list(x) for x in arr], dtype=np.uint8)
                out[k] = conv
            else:
                out[k] = arr.astype(np.uint8)
    return out


def detect_encryption_window(traces: np.ndarray,
                             pre_samples: int = PRE_SAMPLES,
                             smooth_k: int = SMOOTH_K,
                             thresh_std: float = THRESH_STD,
                             margin: int = MARGIN) -> Tuple[int, int]:
    """
    Detect region of activity across traces using mean absolute derivative.
    Returns (start, end) sample indices (end is exclusive).
    """
    traces_clean = np.nan_to_num(traces, nan=0.0)
    deriv = np.abs(np.diff(traces_clean, axis=1))           # shape (N, T-1)
    activity = deriv.mean(axis=0)                           # length T-1
    activity_s = _moving_average(activity, smooth_k)

    baseline_end = max(1, pre_samples // 2)
    mu = activity_s[:baseline_end].mean()
    sigma = activity_s[:baseline_end].std() if activity_s[:baseline_end].std() > 0 else 1e-9
    thr = mu + thresh_std * sigma

    active_mask = activity_s > thr
    if not active_mask.any():
        # fallback to full trace
        return 0, traces.shape[1]

    idxs = np.where(active_mask)[0]
    start = max(0, idxs.min() - margin)
    end = min(traces.shape[1], idxs.max() + 2 + margin)  # +2 to convert deriv index -> sample index
    return start, end


def find_alignment_indices(traces: np.ndarray, search_window: Tuple[int, int]) -> np.ndarray:
    """
    For each trace, find a sharp edge within search_window by locating the max absolute derivative index.
    Returns per-trace event sample indices.
    """
    s, e = search_window
    s = max(0, s)
    e = min(traces.shape[1], e)
    if e <= s + 1:
        return np.full((traces.shape[0],), traces.shape[1] // 2, dtype=int)

    deriv = np.abs(np.diff(np.nan_to_num(traces, nan=0.0), axis=1))  # length T-1
    s_d = s
    e_d = max(s_d + 1, e - 1)
    rel = np.argmax(deriv[:, s_d:e_d], axis=1)
    inds = rel + s_d
    return inds + 1   # convert derivative index -> sample index


def align_traces_roll(traces: np.ndarray, event_indices: np.ndarray, ref_index: int) -> np.ndarray:
    """
    Align traces by rolling so that event_indices[i] moves to ref_index.
    Returns rolled array same shape as input.
    """
    aligned = np.empty_like(traces)
    for i, (t, idx) in enumerate(zip(traces, event_indices)):
        shift = int(ref_index) - int(idx)
        aligned[i] = np.roll(t, shift)
    return aligned


def crop_baseline_normalize(traces: np.ndarray,
                            start: int, end: int,
                            baseline_window: Tuple[int,int] = BASELINE_WINDOW,
                            detrend: bool = DETREND,
                            normalize: bool = NORMALIZE,
                            smooth_k: int = SMOOTH_K_FINAL) -> np.ndarray:
    """
    Crop traces to [start:end], subtract baseline (mean over baseline_window),
    optionally detrend by subtracting slow moving average, normalize by std, and smooth.
    """
    cropped = traces[:, start:end].astype(np.float32)
    a, b = baseline_window
    a = max(0, a)
    b = min(cropped.shape[1], b)
    if b <= a:
        baseline = cropped.mean(axis=1, keepdims=True)
    else:
        baseline = cropped[:, a:b].mean(axis=1, keepdims=True)
    cropped = cropped - baseline

    if detrend:
        slow_k = max(3, int(cropped.shape[1] * 0.05))
        slow = np.array([_moving_average(row, slow_k) for row in cropped])
        cropped = cropped - slow

    if normalize:
        std = cropped.std(axis=1, keepdims=True)
        std[std == 0] = 1.0
        cropped = cropped / std

    if smooth_k > 1:
        cropped = np.array([_moving_average(row, smooth_k) for row in cropped])

    return cropped


# Main preprocessing flow
def preprocess_and_save(path_in: str, path_out: str):
    data = load_capture_npz(path_in)
    waves = data["waves"]
    n_traces, n_points = waves.shape
    print(f"[INFO] Loaded {n_traces} traces with {n_points} points each.")

    plaintexts = data.get("plaintexts", None)
    ciphertexts = data.get("ciphertexts", None)
    keys = data.get("keys", None)

    if plaintexts is not None and plaintexts.shape[0] != n_traces:
        print("[WARN] plaintexts length differs from waves; ignoring plaintexts.")
        plaintexts = None
    if ciphertexts is not None and ciphertexts.shape[0] != n_traces:
        print("[WARN] ciphertexts length differs from waves; ignoring ciphertexts.")
        ciphertexts = None
    if keys is not None and keys.shape[0] != n_traces:
        print("[WARN] keys length differs from waves; ignoring keys.")
        keys = None

    # 1) Detect encryption window
    start, end = detect_encryption_window(waves)
    print(f"[INFO] Detected encryption window: start={start}, end={end}, length={end-start}")

    processed_waves = waves.copy()

    # 2) Align traces if requested (always enabled here)
    if DO_ALIGN:
        event_indices = find_alignment_indices(processed_waves, (start, end))
        if REF_ALIGNMENT == "median":
            ref_idx = int(np.median(event_indices))
        else:
            ref_idx = int(REF_ALIGNMENT)
        print(f"[INFO] Aligning traces to reference index {ref_idx} (median of events).")
        processed_waves = align_traces_roll(processed_waves, event_indices, ref_idx)

        # After roll, compute a new cropping window centered at ref_idx with previous window length
        win_len = end - start
        new_start = max(0, ref_idx - (win_len // 2))
        new_end = min(processed_waves.shape[1], new_start + win_len)
        # adjust if clipped
        if new_end - new_start < win_len and new_start > 0:
            new_start = max(0, new_end - win_len)
        start, end = new_start, new_end
        print(f"[INFO] After alignment, cropping window set to start={start}, end={end} (len={end-start})")

    # 3) Crop & process
    processed = crop_baseline_normalize(processed_waves, start, end,
                                        baseline_window=BASELINE_WINDOW,
                                        detrend=DETREND,
                                        normalize=NORMALIZE,
                                        smooth_k=SMOOTH_K_FINAL)

    # 4) Remove bad traces (NaN or constant)
    nan_mask = np.isnan(processed).any(axis=1)
    const_mask = np.isclose(processed.std(axis=1), 0.0)
    bad_mask = nan_mask | const_mask
    n_bad = int(bad_mask.sum())
    if n_bad > 0:
        print(f"[WARN] Detected {n_bad} bad traces (NaN or constant). They will be removed along with metadata.")
        good_idx = np.where(~bad_mask)[0]
        processed = processed[good_idx]
        if plaintexts is not None:
            plaintexts = plaintexts[good_idx]
        if ciphertexts is not None:
            ciphertexts = ciphertexts[good_idx]
        if keys is not None:
            keys = keys[good_idx]
    else:
        good_idx = np.arange(processed.shape[0])

    # 5) Save processed traces and metadata
    save_dict = {
        "waves": processed.astype(np.float32),
        "meta_start": np.int32(start),
        "meta_end": np.int32(end)
    }
    if plaintexts is not None:
        save_dict["plaintexts"] = plaintexts.astype(np.uint8)
    if ciphertexts is not None:
        save_dict["ciphertexts"] = ciphertexts.astype(np.uint8)
    if keys is not None:
        save_dict["keys"] = keys.astype(np.uint8)

    os.makedirs(os.path.dirname(path_out), exist_ok=True)
    np.savez_compressed(path_out, **save_dict)
    print(f"[OK] Saved preprocessed traces to {path_out} (N={processed.shape[0]}, L={processed.shape[1]})")

    # Optional diagnostics plot
    if PLOT_DIAGNOSTICS:
        mean_before = np.nanmean(waves, axis=0)
        mean_after = np.nanmean(processed, axis=0)
        plt.figure(figsize=(10, 4))
        plt.subplot(1, 2, 1)
        plt.plot(mean_before); plt.title("Mean before"); plt.axvspan(start, end, color="red", alpha=0.2)
        plt.subplot(1, 2, 2)
        plt.plot(mean_after); plt.title("Mean after (cropped & normalized)")
        plt.tight_layout()
        plt.show()


if __name__ == "__main__":
    preprocess_and_save(PATH_IN, PATH_OUT)
    print("Done. Preprocessed traces saved with corresponding plaintexts/ciphertexts (if present).")
