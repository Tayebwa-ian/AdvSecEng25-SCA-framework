## capture.py

This Python script provides functions to capture and store traces using the CW305 target and the CW1173 capture board. The FPGA on the CW305 is not programmed with a bitstream through this script, and must be programmed beforehand.

The dataclass `DutIO` contains all data relevant to the computations done in the device-under-test (DUT). As such, the variables and register addresses have to be adjusted to those required in the Verilog design.

The abstract class `DutIOPattern` is a factory base class, and is to be implemented to provide different patterns on how DutIO objects are generated for recording of the traces. For every trace, the capture script calls the `next()` function and passes the generated `DutIO` object to the DUT inputs (e.g. plaintext input and key for a crypto algorithm). As an example, the DutIOTestPattern class provides always the same key to the DUT and random data.

The dataclass `TraceExt` contains the captured traces through the fields:
- `wave` storing the power trace as a numpy array.
- `dut_io` storing the inputs to the DUT used when the respective power trace was recorded.
- `trig_count` the number of triggers the design was active for (translates to the number of clock cycles depending on how `scope.clock.adc_src` is set).

The function `capture_trace` is the brain of the module, controlling the capture process. It uses the `DutIOPattern` implementation to provide inputs to the DUT, triggers the DUT, retrieves and optionally checks the results. Finally, it returns a single trace as a `TraceExt` object.

## lock_fpga.py

This Python script provides a simple mechanism for managing access to an FPGA device in a multi-user environment. It uses a lock file in `/tmp/fpga_lock.json` to indicate that the FPGA is currently in use. The lock includes the username of the person who created it, the time it was created, and an estimated end time (in hours).

The estimated time is not enforced or automatically checked — it exists purely as information for other users to estimate when the FPGA might become available again, and to notice if someone might have forgotten to release the lock.

⚠️ **Important:** The lock is purely advisory and does not technically prohibit users from reprogramming or using the FPGA. **Users must always check the lock file before interacting with the FPGA to avoid interrupting someone else’s work.** It is the responsibility of the users to lock and unlock the FPGA appropriately.

### Usage
| Command                         | Description                                                                 |
|---------------------------------|-----------------------------------------------------------------------------|
| `python fpga_lock.py check`     | Displays the current lock status, including the user, creation time, and estimated end time. |
| `python fpga_lock.py lock <hours>` | Locks the FPGA for the specified number of hours. Prompts to overwrite if a lock from the same user already exists. |
| `python fpga_lock.py unlock`    | Removes the lock file and unlocks the FPGA. Requires confirmation if the lock was created by another user. |

### Lock Handling Guidelines

If you find that the FPGA is locked by another user and you need access:

- **Check the estimated end time** in the lock file using the `check` command.
- If the estimated end time has **been exceeded by multiple hours** and the lock has likely been forgotten, you may consider removing the lock using the `unlock` command.
- **If the estimated end time has not yet passed**, respect the lock and wait for it to expire, or contact the current user to coordinate.
- When removing a lock that belongs to someone else, the script will ask for confirmation before proceeding.

This lightweight system relies on mutual trust and consideration between users.


