`include "params.vh"
`timescale 1ns / 1ps

module top (
   // USB Interface
   input wire usb_clk,
   inout wire [7:0] usb_data,
   input wire [`pADDR_WIDTH-1:0] usb_addr,
   input wire usb_rdn,
   input wire usb_wrn,
   input wire usb_cen,
   input wire usb_trigger,

   // PLL
   input wire pll_clk1,

   // 20-Pin Connector Stuff
   output wire tio_trigger,
   output wire tio_clkout,
   input wire tio_clkin
);

// Clocking
//
wire usb_clk_buf;
wire dut_clk_buf;
//
clock_config U_clock_config (
   .usb_clk (usb_clk),
   .usb_clk_buf (usb_clk_buf),
   .cw_clkin (tio_clkin),
   .pll_clk1 (pll_clk1),
   .cw_clkout (tio_clkout),
   .dut_clk_buf (dut_clk_buf)
);


// USB Adapter
//
wire [7:0] usb_din;
wire [7:0] usb_dout;
wire isout;
wire [`pADDR_WIDTH-`pBYTECNT_SIZE-1:0] reg_address;
wire [`pBYTECNT_SIZE-1:0] reg_bytecnt;
wire reg_addrvalid;
wire [7:0] write_data;
wire [7:0] read_data;
wire reg_read;
wire reg_write;
//
usb_reg_adapter U_usb_reg_adapter (
   .usb_clk (usb_clk_buf),
   .usb_din (usb_din),
   .usb_dout (usb_dout),
   .usb_rdn (usb_rdn),
   .usb_wrn (usb_wrn),
   .usb_cen (usb_cen),
   .usb_alen (1'b0), // unused
   .usb_addr (usb_addr),
   .usb_isout (isout),
   .reg_address (reg_address),
   .reg_bytecnt (reg_bytecnt),
   .reg_datao (write_data),
   .reg_datai (read_data),
   .reg_read (reg_read),
   .reg_write (reg_write),
   .reg_addrvalid (reg_addrvalid)
);
//
genvar i;
generate
   for (i=0; i<8; i=i+1) begin
      IOBUF #(
         .DRIVE(12),
         .IOSTANDARD("LVCMOS33")
      ) IOBUF_inst (
         .O(usb_din[i]),
         .IO(usb_data[i]),
         .I(usb_dout[i]),
         .T(~isout)
      );
   end
endgenerate


// DUT
//
wire dut_start;
wire dut_rst;
wire [`pDUT_IN_WIDTH-1:0] dut_in;
//
wire [`pDUT_OUT_WIDTH-1:0] dut_out;
wire dut_done;
wire dut_busy;
//
usb_reg U_usb_reg (
   .usb_clk(usb_clk_buf),
   .reg_address(reg_address[`pADDR_WIDTH-`pBYTECNT_SIZE-1:0]),
   .reg_bytecnt(reg_bytecnt),
   .read_data(read_data),
   .write_data(write_data),
   .reg_read(reg_read),
   .reg_write(reg_write),
   .reg_addrvalid(reg_addrvalid),
   .exttrigger_in(usb_trigger),
   //
   .dut_clk(dut_clk_buf),
   .dut_out(dut_out),
   .dut_done(dut_done),
   .dut_busy(dut_busy),
   //
   .dut_in(dut_in),
   .dut_start(dut_start),
   .dut_rst(dut_rst)
);
//
assign tio_trigger = dut_busy;
//
wire [127:0] aes_key = dut_in[255:128];
wire [127:0] aes_data_in = dut_in[127:0];
//
aes_core U_aes_core (
   .clk(dut_clk_buf),
   .load_i(dut_start),
   .key_i({aes_key, 128'h0}),
   .data_i(aes_data_in),
   .size_i(2'd0), // AES128
   .dec_i(1'b0), // enc mode
   .data_o(dut_out),
   .busy_o(dut_busy)
);

endmodule

