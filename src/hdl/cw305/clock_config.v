`default_nettype none
`timescale 1ns / 1ns

module clock_config (
   input wire usb_clk,
   input wire cw_clkin,
   input wire pll_clk1,
   output wire usb_clk_buf,
   output wire dut_clk_buf,
   output wire cw_clkout
);

wire usb_clk_bufg;
wire dut_clk_bufg;

IBUF U_dut_clk_ibuf (
   .O(dut_clk_bufg),
   .I(pll_clk1) // alt: cw_clkin
);

BUFG U_dut_clk_buf (
   .O(dut_clk_buf),
   .I(dut_clk_bufg)
);

ODDR CWOUT_ODDR (
   .Q(cw_clkout), // 1-bit DDR output
   .C(dut_clk_buf), // 1-bit clock input
   .CE(1'b1), // 1-bit clock enable input, switch to 1'b0 to turn off
   .D1(1'b1), // 1-bit data input (positive edge)
   .D2(1'b0), // 1-bit data input (negative edge)
   .R(1'b0), // 1-bit reset
   .S(1'b0) // 1-bit set
);

IBUF U_usb_clk_ibuf (
   .O(usb_clk_bufg),
   .I(usb_clk)
);

BUFG U_usb_clk_buf(
   .O(usb_clk_buf),
   .I(usb_clk_bufg)
);

endmodule

`default_nettype wire
