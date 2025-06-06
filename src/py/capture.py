import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from random import SystemRandom

import chipwhisperer as cw
import numpy as np


_cryptgen = SystemRandom()


def _setup_cwlite_cw305_100t() -> tuple[cw.scopes.OpenADC, cw.targets.CW305]:
    scope = cw.scopes.OpenADC()
    scope.con(idProduct=0xace2, prog_speed=int(10E6))
    # Gain
    scope.gain.db = 20 # low-noise amplifier gain, [-6.5, 56] dB
    # ADC
    scope.adc.basic_mode = "rising_edge" # trigger settings
    scope.adc.offset = 0
    scope.adc.presamples = 0
    scope.adc.samples = 150
    #scope.adc.decimate  # Downsample facpture
    #fifo_fill_mode  # Buffer fill strategy
    #scope.adc.timeout  # Capture timeout
    #
    # Clocking
    # Scope-side ADC Settings
    scope.clock.adc_src = "extclk_x4"
    #scope.clock.adc_phase  # Clock/Sampling offset [-255, 255]
    #
    # Scope-side clock output Settings
    # scope.clock.clkgen_freq = 7.37E6 # [3.2MHz, ..]
    # scope.clock.extclk_freq
    # ...
    # IO
    scope.io.tio1 = "serial_rx"
    scope.io.tio2 = "serial_tx"
    scope.io.hs2 = "disabled"
    # ...
    # Trigger
    scope.trigger.triggers = "tio4"
    # ...
    #
    target = cw.targets.CW305()
    target._con(scope, bsfile=None, force=False, fpga_id='100t')
    target.vccint_set(1.0)
    #
    # Target-side clock generation
    target.pll.pll_enable_set(True)
    target.pll.pll_outenable_set(False, 0)
    target.pll.pll_outenable_set(True, 1)
    target.pll.pll_outenable_set(False, 2)
    target.pll.pll_outfreq_set(7.37E6, 1)
    # 1ms is plenty of idling time
    target.clkusbautooff = True
    target.clksleeptime = 1
    return scope, target


def _lock_adc(scope:cw.scopes.OpenADC):
    for _ in range (5):
        scope.clock.reset_adc()
        time.sleep(1)
        if scope.clock.adc_locked:
            return
    raise Exception("ADC failed to lock.")


@dataclass
class DutIO:
    REG_DUT_KEYIN = 0x08
    DUT_KEYIN_LEN_IN_BYTES = 16
    REG_DUT_DATAIN = 0x09
    DUT_DATAIN_LEN_IN_BYTES = 16
    REG_DUT_DATAOUT = 0x0a
    DUT_DATAOUT_LEN_IN_BYTES = 16
    REG_DUT_RESET = 0x07
    REG_DUT_GO = 0x05

    BYTECNT_SIZE = 7

    data:int
    key:int
    computed_data:int

    @staticmethod
    def format_write(bytes:list[int]) -> bytearray:
        assert all(0 <= val <= 255 for val in bytes)
        return bytearray(bytes)

    @staticmethod
    def format_read(data:bytearray) -> list[int]:
        return list(data)


class DutIOPattern(ABC):

    name = "TEMPLATE"

    def __init__(self, N_traces, average_over):
        self.N_traces = N_traces
        self.average_over = average_over

    @abstractmethod
    def next(self) -> DutIO:
        pass


class DutIOTestPattern(DutIOPattern):

    def __init__(self, N_traces, average_over, key):
        super().__init__(N_traces, average_over)
        self.key = key

    def next(self) -> DutIO:
        return DutIO(
            data=_cryptgen.randrange(stop=2**128),
            key= self.key,
            computed_data=None)


@dataclass
class TraceExt:
    wave:np.ndarray
    dut_io:DutIO
    trig_count:int


def capture_trace(scope:cw.scopes.OpenADC, target:cw.targets.CW305, ktp:DutIOPattern):
    DISABLE_USB_CLK = False

    def trigger_target():
        scope.arm()
        if DISABLE_USB_CLK and target.clkusbautooff:
            target.usb_clk_setenabled(False)
        time.sleep(0.001)
        target.usb_trigger_toggle()
        if DISABLE_USB_CLK and target.clkusbautooff:
            time.sleep(target.clksleeptime/1000.0)
            target.usb_clk_setenabled(True)

    def verify_target_done():
        i_retry = 0
        while target.fpga_read(DutIO.REG_DUT_GO, 1)[0] == 0x01:
            i_retry += 1
            time.sleep(0.05)
            if i_retry > 100:
                return False
        return True

    target.bytecount_size = DutIO.BYTECNT_SIZE
    assert ktp.average_over >= 1
    waves = []
    dut_io = ktp.next()

    for i_rep in range(ktp.average_over):
        # Write Inputs
        data_in_bytes = list(dut_io.data.to_bytes(DutIO.DUT_DATAIN_LEN_IN_BYTES))
        key_in_bytes = list(dut_io.key.to_bytes(DutIO.DUT_KEYIN_LEN_IN_BYTES))
        target.fpga_write(DutIO.REG_DUT_DATAIN, DutIO.format_write(data_in_bytes))
        target.fpga_write(DutIO.REG_DUT_KEYIN, DutIO.format_write(key_in_bytes))
        # Start computation
        trigger_target()
        if scope.capture():
            raise Exception("Scope timed out.")
        if not verify_target_done():
            raise Exception("Target did not report done in time.")
        # Retrieve results
        dut_computed_data = DutIO.format_read(target.fpga_read(DutIO.REG_DUT_DATAOUT, DutIO.DUT_DATAOUT_LEN_IN_BYTES))
        dut_io.computed_data = int.from_bytes(dut_computed_data)
        # Verify output
        expected_out = None # TODO
        if dut_io.computed_data != expected_out:
            print(f"Output mismatch.\nExpected: {expected_out}\nActual: {dut_io.computed_data}")
        # Retrieve wave
        wave = scope.get_last_trace()
        if len(wave) == 0:
            raise Exception("Scope returned empty trace.")
        waves.append(wave)
    mean_wave = waves[0] if ktp.average_over==1 else np.mean(waves, axis=0)
    return TraceExt(mean_wave, dut_io, scope.adc.trig_count)


def _create_trace_writer():
    STORE_PATH = "data"
    TRACES_PER_FILE = 100000
    #
    file_counter = 0
    trace_batch:list[TraceExt] = []
    os.makedirs(STORE_PATH, exist_ok=True)
    #
    def write_traces_to_disk(trace:TraceExt, flush:bool):
        nonlocal file_counter
        trace_batch.append(trace)
        if flush or (len(trace_batch) >= TRACES_PER_FILE):
            np.savez_compressed(
                f"{STORE_PATH}/traces_{file_counter}.npz",
                wave=[trace.wave for trace in trace_batch],
                dut_io_data=[trace.dut_io.data for trace in trace_batch],
                dut_io_computed_data=[trace.dut_io.computed_data for trace in trace_batch])
            trace_batch.clear()
            file_counter += 1
    return write_traces_to_disk


if __name__ == "__main__":
    REPORT_INTERVAL = 500
    trace_writer = _create_trace_writer()
    #
    ktp:DutIOPattern = DutIOTestPattern(1, 1, key=0x10a5_8869_d74b_e5a3_74cf_867c_fb47_3859)
    try:
        scope, target = _setup_cwlite_cw305_100t()
        _lock_adc(scope)
        trigger_counts = set()
        for i_capture in range(ktp.N_traces):
            if not i_capture % REPORT_INTERVAL:
                print(f"Captured {i_capture}/{ktp.N_traces} traces.")
            trace = capture_trace(scope, target, ktp)
            trace_writer(trace, i_capture==ktp.N_traces-1)
            trigger_counts.add(trace.trig_count)
        print(f"Trigger counts: {trigger_counts}.")
    finally:
        if 'scope' in locals():
            scope.dis()
        if 'target' in locals():
            target.dis()











