// ================================================================
// NVDLA Open Source Project
// 
// Copyright(c) 2016 - 2017 NVIDIA Corporation.  Licensed under the
// NVDLA Open Hardware License; Check "LICENSE" which comes with 
// this distribution for more information.
// ================================================================

// File Name: NV_NVDLA_MCIF_READ_IG_cvt.v

`include "simulate_x_tick.vh"
module NV_NVDLA_MCIF_READ_IG_cvt (
   nvdla_core_clk          //|< i
  ,nvdla_core_rstn         //|< i
  ,cq_wr_prdy              //|< i
  ,eg2ig_axi_vld           //|< i
  ,mcif2noc_axi_ar_arready //|< i
  ,reg2dp_rd_os_cnt        //|< i
  ,spt2cvt_req_pd          //|< i
  ,spt2cvt_req_valid       //|< i
  ,cq_wr_pd                //|> o
  ,cq_wr_pvld              //|> o
  ,cq_wr_thread_id         //|> o
  ,mcif2noc_axi_ar_araddr  //|> o
  ,mcif2noc_axi_ar_arid    //|> o
  ,mcif2noc_axi_ar_arlen   //|> o
  ,mcif2noc_axi_ar_arvalid //|> o
  ,spt2cvt_req_ready       //|> o
  );

//
// Port Declarations
//
input  nvdla_core_clk;
input  nvdla_core_rstn;

// Input from Splitter (spt)
input         spt2cvt_req_valid;  /* data valid */
output        spt2cvt_req_ready;  /* data return handshake */
input  [74:0] spt2cvt_req_pd;

// Output to Context Queue (cq)
output       cq_wr_pvld;       /* data valid */
input        cq_wr_prdy;       /* data return handshake */
output [3:0] cq_wr_thread_id;
output [6:0] cq_wr_pd;

// AXI AR Channel Output
output        mcif2noc_axi_ar_arvalid;  /* data valid */
input         mcif2noc_axi_ar_arready;  /* data return handshake */
output  [7:0] mcif2noc_axi_ar_arid;
output  [3:0] mcif2noc_axi_ar_arlen;
output [63:0] mcif2noc_axi_ar_araddr;

// Configuration and Feedback
input  [7:0] reg2dp_rd_os_cnt;
input        eg2ig_axi_vld;

// ============================================================
// TODO: Implement the internal logic
// 
// This module converts internal NVDLA read commands from the 
// splitter (spt) into AXI4 AR channel transactions.
//
// Study the context files provided:
// - NV_NVDLA_MCIF_READ_IG_spt.v (upstream splitter)
// - NV_NVDLA_MCIF_READ_ig.v (parent module)
//
// Key functionality to implement:
// 1. Unpack the 75-bit input command packet (spt2cvt_req_pd)
// 2. Generate AXI AR signals (araddr, arid, arlen, arvalid)
// 3. Track outstanding transactions with os_cnt
// 4. Write context to CQ (cq_wr_pd, cq_wr_pvld, cq_wr_thread_id)
// 5. Implement three-way flow control interlock
// 6. Include an output pipeline stage (skid buffer)
// ============================================================

// Placeholder outputs to allow compilation
assign spt2cvt_req_ready = 1'b0;
assign cq_wr_pvld = 1'b0;
assign cq_wr_thread_id = 4'b0;
assign cq_wr_pd = 7'b0;
assign mcif2noc_axi_ar_arvalid = 1'b0;
assign mcif2noc_axi_ar_arid = 8'b0;
assign mcif2noc_axi_ar_arlen = 4'b0;
assign mcif2noc_axi_ar_araddr = 64'b0;

endmodule
