`include "params.vh"

`default_nettype none
`timescale 1ns / 1ps

module cdc_pulse(
   input wire src_clk,
   input wire src_pulse,
   input wire dst_clk,
   output reg dst_pulse
   );

(* ASYNC_REG = "TRUE" *) reg [`pSYNC_STAGES-1:0] req_pipe = 0;
(* ASYNC_REG = "TRUE" *) reg [`pSYNC_STAGES-1:0] ack_pipe = 0;
reg src_req = 0;
reg dst_req = 0;
reg dst_req_r;
reg src_ack;
wire busy;

always @(posedge src_clk) begin
   {src_ack, ack_pipe} <= {ack_pipe, dst_req};
   if (~busy & src_pulse)
      src_req <= 1'b1;
   else if (src_ack)
      src_req <= 1'b0;
end

assign busy = src_req | src_ack;

always @(posedge dst_clk) begin
   {dst_req_r, dst_req, req_pipe} <= {dst_req, req_pipe, src_req};
   dst_pulse <= ~dst_req_r & dst_req;
end

endmodule

`default_nettype wire
