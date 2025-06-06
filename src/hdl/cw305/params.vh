`ifndef __cw305_vh__
`define __cw305_vh__

// Chipwhisperer Parameters
`define pDONE_EDGE_SENSITIVE  1
`define pADDR_WIDTH           21
`define pREG_RDDLY_LEN        3
`define pSYNC_STAGES          2

// Chipwhisperer Registers
`define pBYTECNT_SIZE         7
`define pDUT_IN_WIDTH         256
`define pDUT_OUT_WIDTH        128
`define REG_DUT_GO            'h05
`define REG_DUT_RESET         'h07
`define REG_DUT_KEY_IN        'h08
`define REG_DUT_DATA_IN       'h09
`define REG_DUT_DATA_OUT      'h0a

`endif