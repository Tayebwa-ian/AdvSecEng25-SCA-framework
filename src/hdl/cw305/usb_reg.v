`include "params.vh"

`timescale 1ns / 1ps

module usb_reg(
   // Interface to usb_reg_adapter:
   input wire usb_clk,
   input wire dut_clk,
   input wire [`pADDR_WIDTH-`pBYTECNT_SIZE-1:0] reg_address, // Address of register
   input wire [`pBYTECNT_SIZE-1:0] reg_bytecnt, // Current byte count
   output reg [7:0] read_data, //
   input wire [7:0] write_data, //
   input wire reg_read, // Read flag. One clock cycle AFTER this flag is high
   // valid data must be present on the read_data bus
   input wire reg_write, // Write flag. When high on rising edge valid data is
   // present on write_data
   input wire reg_addrvalid, // Address valid flag

   // from top:
   input wire exttrigger_in,

   // register inputs:
   input wire [`pDUT_OUT_WIDTH-1:0] dut_out,
   input wire dut_done,
   input wire dut_busy,

   // register outputs:
   output wire dut_start,
   output wire dut_rst,
   output wire [`pDUT_IN_WIDTH-1:0] dut_in
);

// Reset signal
//
reg reg_dut_rst_usbclk;

// Module IO and Storage
//
reg [`pDUT_OUT_WIDTH-1:0] reg_dut_out_dutclk;
(* ASYNC_REG = "TRUE" *) reg [`pDUT_OUT_WIDTH-1:0] reg_dut_out_usbclk;
//
reg [`pDUT_IN_WIDTH-1:0] reg_dut_in_usbclk;
(* ASYNC_REG = "TRUE" *) reg [`pDUT_IN_WIDTH-1:0] reg_dut_in_dutclk;
//
assign dut_in = reg_dut_in_dutclk;
//
always @(posedge dut_clk) begin
   if (dut_done) begin
      reg_dut_out_dutclk <= dut_out;
   end
   reg_dut_in_dutclk <= reg_dut_in_usbclk;
end
//
always @(posedge usb_clk) begin
   reg_dut_out_usbclk <= reg_dut_out_dutclk;
end


// Go Signal
reg go_ext, reg_go_ext;
wire dut_go_ext;
reg reg_dut_go_usbclk;
wire dut_go_usb;
//
(* ASYNC_REG = "TRUE" *) reg [1:0] go_ext_buf;
//
assign dut_start = dut_go_ext || dut_go_usb;
assign dut_go_ext = go_ext & !reg_go_ext;
//
always @(posedge dut_clk) begin
   {reg_go_ext, go_ext, go_ext_buf} <= {go_ext, go_ext_buf, exttrigger_in};
end
//
cdc_pulse U_go_pulse (
   .src_clk (usb_clk),
   .src_pulse (reg_dut_go_usbclk),
   .dst_clk (dut_clk),
   .dst_pulse (dut_go_usb)
);

// Reset signal
//
cdc_pulse U_rst_pulse (
   .src_clk (usb_clk),
   .src_pulse (reg_dut_rst_usbclk),
   .dst_clk (dut_clk),
   .dst_pulse (dut_rst)
);

// Busy signal
reg reg_d1_dut_busy_usbclk;
(* ASYNC_REG = "TRUE" *) reg [1:0] reg_dut_busy_usbclk;
//
always @(posedge usb_clk) begin
   reg_dut_busy_usbclk <= dut_busy;
   {reg_d1_dut_busy_usbclk, reg_dut_busy_usbclk} <= {reg_dut_busy_usbclk, dut_busy};
end


// USB Interface
//
reg [7:0] reg_read_data;
//
//////////////////////////////////
// read logic:
//////////////////////////////////
always @(*) begin
   if (reg_addrvalid && reg_read) begin
      case (reg_address)
         `REG_DUT_GO: reg_read_data = reg_d1_dut_busy_usbclk;
         `REG_DUT_DATA_OUT: reg_read_data = reg_dut_out_usbclk[reg_bytecnt*8 +: 8];
         default: reg_read_data = 0;
      endcase
   end else begin
      reg_read_data = 0;
   end
end
//
// Register output read data to ease timing. If you need read data one clock
// cycle earlier, simply remove this stage:
always @(posedge usb_clk) begin
   read_data <= reg_read_data;
end
//
//////////////////////////////////
// write logic (USB clock domain):
//////////////////////////////////
always @(posedge usb_clk) begin
   if (reg_addrvalid && reg_write) begin
      reg_dut_go_usbclk <= (reg_address == `REG_DUT_GO); // Create pulse
      reg_dut_rst_usbclk <= (reg_address == `REG_DUT_RESET); // Create pulse
      case (reg_address)
         `REG_DUT_KEY_IN: reg_dut_in_usbclk[reg_bytecnt*8+128 +: 8] <= write_data;
         `REG_DUT_DATA_IN: reg_dut_in_usbclk[reg_bytecnt*8 +: 8] <= write_data;
      endcase
   end else begin
      reg_dut_go_usbclk <= 1'b0;
      reg_dut_rst_usbclk <= 1'b0;
   end
end

endmodule

