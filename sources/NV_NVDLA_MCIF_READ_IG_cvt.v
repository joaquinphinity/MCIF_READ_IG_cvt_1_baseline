// ================================================================
// NVDLA Open Source Project
// 
// Copyright(c) 2016 - 2017 NVIDIA Corporation.  Licensed under the
// NVDLA Open Hardware License; Check "LICENSE" which comes with 
// this distribution for more information.
// ================================================================

// File Name: NV_NVDLA_MCIF_READ_IG_cvt.v

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

input  nvdla_core_clk;
input  nvdla_core_rstn;

input         spt2cvt_req_valid;
output        spt2cvt_req_ready;
input  [74:0] spt2cvt_req_pd;

output       cq_wr_pvld;
input        cq_wr_prdy;
output [3:0] cq_wr_thread_id;
output [6:0] cq_wr_pd;

output        mcif2noc_axi_ar_arvalid;
input         mcif2noc_axi_ar_arready;
output  [7:0] mcif2noc_axi_ar_arid;
output  [3:0] mcif2noc_axi_ar_arlen;
output [63:0] mcif2noc_axi_ar_araddr;

input  [7:0] reg2dp_rd_os_cnt;
input        eg2ig_axi_vld;

// TODO: Implement internal logic

// Placeholder outputs
assign spt2cvt_req_ready = 1'b0;
assign cq_wr_pvld = 1'b0;
assign cq_wr_thread_id = 4'b0;
assign cq_wr_pd = 7'b0;
assign mcif2noc_axi_ar_arvalid = 1'b0;
assign mcif2noc_axi_ar_arid = 8'b0;
assign mcif2noc_axi_ar_arlen = 4'b0;
assign mcif2noc_axi_ar_araddr = 64'b0;

endmodule
