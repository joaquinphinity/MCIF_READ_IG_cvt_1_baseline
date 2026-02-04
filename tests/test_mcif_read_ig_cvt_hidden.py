"""
Hidden Cocotb Testbench for NV_NVDLA_MCIF_READ_IG_cvt
Tests the AXI Read Issue Converter module from NVDLA MCIF

This testbench validates:
1. Command packet unpacking
2. AXI AR signal generation (address alignment, burst length, ID)
3. Outstanding transaction tracking
4. Context queue packet generation
5. Three-way flow control interlock
6. Skid buffer/pipeline behavior
"""

from __future__ import annotations
import os
from pathlib import Path
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer, FallingEdge
from cocotb_tools.runner import get_runner


def pack_cmd_packet(axid, addr, size, swizzle, odd, ltran, ftran):
    """Pack a 75-bit command packet in the format expected by the module."""
    packet = 0
    packet |= (axid & 0xF)           # [3:0]
    packet |= (addr & 0xFFFFFFFFFFFFFFFF) << 4   # [67:4]
    packet |= (size & 0x7) << 68     # [70:68]
    packet |= (swizzle & 0x1) << 71  # [71]
    packet |= (odd & 0x1) << 72      # [72]
    packet |= (ltran & 0x1) << 73    # [73]
    packet |= (ftran & 0x1) << 74    # [74]
    return packet


def unpack_cq_packet(packet):
    """Unpack the 7-bit context queue packet."""
    return {
        'lens': packet & 0x3,
        'swizzle': (packet >> 2) & 0x1,
        'odd': (packet >> 3) & 0x1,
        'ltran': (packet >> 4) & 0x1,
        'fdrop': (packet >> 5) & 0x1,
        'ldrop': (packet >> 6) & 0x1,
    }


async def reset_dut(dut):
    """Reset the DUT properly."""
    dut.nvdla_core_rstn.value = 0
    dut.spt2cvt_req_valid.value = 0
    dut.spt2cvt_req_pd.value = 0
    dut.cq_wr_prdy.value = 1
    dut.mcif2noc_axi_ar_arready.value = 1
    dut.eg2ig_axi_vld.value = 0
    dut.reg2dp_rd_os_cnt.value = 255  # Max outstanding
    
    await ClockCycles(dut.nvdla_core_clk, 5)
    dut.nvdla_core_rstn.value = 1
    await ClockCycles(dut.nvdla_core_clk, 2)


@cocotb.test(timeout_time=500, timeout_unit="us")
async def test_reset_behavior(dut):
    """Test 1: Verify reset clears all outputs."""
    clock = Clock(dut.nvdla_core_clk, 10, units="ns")
    cocotb.start_soon(clock.start())
    
    # Apply reset
    dut.nvdla_core_rstn.value = 0
    dut.spt2cvt_req_valid.value = 0
    dut.cq_wr_prdy.value = 1
    dut.mcif2noc_axi_ar_arready.value = 1
    dut.eg2ig_axi_vld.value = 0
    dut.reg2dp_rd_os_cnt.value = 255
    
    await ClockCycles(dut.nvdla_core_clk, 5)
    
    # Check outputs during reset
    assert int(dut.mcif2noc_axi_ar_arvalid.value) == 0, \
        "AXI AR valid should be 0 during reset"
    assert int(dut.cq_wr_pvld.value) == 0, \
        "CQ write valid should be 0 during reset"
    
    # Release reset
    dut.nvdla_core_rstn.value = 1
    await ClockCycles(dut.nvdla_core_clk, 3)
    
    # Module should be ready to accept commands
    assert int(dut.spt2cvt_req_ready.value) == 1, \
        "Should be ready to accept commands after reset"


@cocotb.test(timeout_time=500, timeout_unit="us")
async def test_basic_command_conversion(dut):
    """Test 2: Basic command to AXI conversion."""
    clock = Clock(dut.nvdla_core_clk, 10, units="ns")
    cocotb.start_soon(clock.start())
    
    await reset_dut(dut)
    
    # Send a simple command
    test_addr = 0x1000_0000_0000_0040  # 64-byte aligned
    test_axid = 3
    test_size = 2  # 2 units of 32 bytes = 64 bytes = 1 AXI beat
    
    cmd = pack_cmd_packet(
        axid=test_axid,
        addr=test_addr,
        size=test_size,
        swizzle=0,
        odd=0,
        ltran=1,
        ftran=1
    )
    
    dut.spt2cvt_req_valid.value = 1
    dut.spt2cvt_req_pd.value = cmd
    
    # Wait for handshake
    await RisingEdge(dut.nvdla_core_clk)
    while int(dut.spt2cvt_req_ready.value) == 0:
        await RisingEdge(dut.nvdla_core_clk)
    
    dut.spt2cvt_req_valid.value = 0
    
    # Wait for AXI transaction to appear
    for _ in range(10):
        await RisingEdge(dut.nvdla_core_clk)
        if int(dut.mcif2noc_axi_ar_arvalid.value) == 1:
            break
    
    # Verify AXI signals
    assert int(dut.mcif2noc_axi_ar_arvalid.value) == 1, \
        "AXI AR valid should be asserted"
    
    # Address should be 64-byte aligned (bits [5:0] = 0)
    axi_addr = int(dut.mcif2noc_axi_ar_araddr.value)
    assert (axi_addr & 0x3F) == 0, \
        f"AXI address should be 64-byte aligned, got {hex(axi_addr)}"
    
    # ID should be the client ID
    axi_id = int(dut.mcif2noc_axi_ar_arid.value)
    assert (axi_id & 0xF) == test_axid, \
        f"AXI ID should be {test_axid}, got {axi_id}"
    
    # Check CQ write
    assert int(dut.cq_wr_pvld.value) == 1, \
        "CQ write valid should be asserted"
    assert int(dut.cq_wr_thread_id.value) == test_axid, \
        f"CQ thread ID should be {test_axid}"


@cocotb.test(timeout_time=500, timeout_unit="us")
async def test_address_alignment(dut):
    """Test 3: Verify address alignment to 64-byte boundary."""
    clock = Clock(dut.nvdla_core_clk, 10, units="ns")
    cocotb.start_soon(clock.start())
    
    await reset_dut(dut)
    
    # Test with unaligned address (should be masked)
    test_addr = 0x1000_0000_0000_0077  # Not 64-byte aligned
    expected_aligned = test_addr & 0xFFFF_FFFF_FFFF_FFC0  # Mask bits [5:0]
    
    cmd = pack_cmd_packet(
        axid=5,
        addr=test_addr,
        size=4,
        swizzle=0,
        odd=0,
        ltran=1,
        ftran=1
    )
    
    dut.spt2cvt_req_valid.value = 1
    dut.spt2cvt_req_pd.value = cmd
    
    await RisingEdge(dut.nvdla_core_clk)
    while int(dut.spt2cvt_req_ready.value) == 0:
        await RisingEdge(dut.nvdla_core_clk)
    
    dut.spt2cvt_req_valid.value = 0
    
    # Wait for AXI valid
    for _ in range(10):
        await RisingEdge(dut.nvdla_core_clk)
        if int(dut.mcif2noc_axi_ar_arvalid.value) == 1:
            break
    
    axi_addr = int(dut.mcif2noc_axi_ar_araddr.value)
    assert axi_addr == expected_aligned, \
        f"Address should be aligned to {hex(expected_aligned)}, got {hex(axi_addr)}"


@cocotb.test(timeout_time=500, timeout_unit="us")
async def test_burst_length_calculation(dut):
    """Test 4: Verify AXI burst length calculation from cmd_size."""
    clock = Clock(dut.nvdla_core_clk, 10, units="ns")
    cocotb.start_soon(clock.start())
    
    await reset_dut(dut)
    
    # Test different sizes
    # axi_len = cmd_size[2:1] + inc
    # where inc = cmd_ftran & cmd_ltran & (cmd_size[0]==1) & cmd_swizzle
    
    # Simple case: size=4 (binary 100), no swizzle
    # axi_len = 4[2:1] = 2 (binary 10) = 2
    cmd = pack_cmd_packet(
        axid=1,
        addr=0x2000_0000_0000_0000,
        size=4,  # size[2:1] = 2
        swizzle=0,
        odd=0,
        ltran=1,
        ftran=1
    )
    
    dut.spt2cvt_req_valid.value = 1
    dut.spt2cvt_req_pd.value = cmd
    
    await RisingEdge(dut.nvdla_core_clk)
    while int(dut.spt2cvt_req_ready.value) == 0:
        await RisingEdge(dut.nvdla_core_clk)
    
    dut.spt2cvt_req_valid.value = 0
    
    # Wait for AXI valid
    for _ in range(10):
        await RisingEdge(dut.nvdla_core_clk)
        if int(dut.mcif2noc_axi_ar_arvalid.value) == 1:
            break
    
    axi_len = int(dut.mcif2noc_axi_ar_arlen.value)
    expected_len = 2  # size[2:1] = 4>>1 = 2
    assert axi_len == expected_len, \
        f"AXI len should be {expected_len}, got {axi_len}"


@cocotb.test(timeout_time=500, timeout_unit="us")
async def test_outstanding_counter_throttling(dut):
    """Test 5: Verify outstanding counter throttles when full."""
    clock = Clock(dut.nvdla_core_clk, 10, units="ns")
    cocotb.start_soon(clock.start())
    
    await reset_dut(dut)
    
    # Set very low outstanding limit
    dut.reg2dp_rd_os_cnt.value = 2
    await ClockCycles(dut.nvdla_core_clk, 2)
    
    # Send multiple commands without completing any
    dut.eg2ig_axi_vld.value = 0  # No completions
    
    commands_sent = 0
    for i in range(10):
        cmd = pack_cmd_packet(
            axid=i % 10,
            addr=0x3000_0000_0000_0000 + i * 0x100,
            size=2,  # Small size to not overflow quickly
            swizzle=0,
            odd=0,
            ltran=1,
            ftran=1
        )
        
        dut.spt2cvt_req_valid.value = 1
        dut.spt2cvt_req_pd.value = cmd
        
        await RisingEdge(dut.nvdla_core_clk)
        
        # Check if ready - should throttle after a few commands
        if int(dut.spt2cvt_req_ready.value) == 1:
            commands_sent += 1
        else:
            # Throttling detected
            dut.spt2cvt_req_valid.value = 0
            break
        
        await RisingEdge(dut.nvdla_core_clk)
    
    dut.spt2cvt_req_valid.value = 0
    
    # With limit of 2 and each command adding ~1-2 beats, should throttle
    assert commands_sent < 10, \
        f"Should throttle before 10 commands, sent {commands_sent}"


@cocotb.test(timeout_time=500, timeout_unit="us")
async def test_cq_packet_format(dut):
    """Test 6: Verify context queue packet format."""
    clock = Clock(dut.nvdla_core_clk, 10, units="ns")
    cocotb.start_soon(clock.start())
    
    await reset_dut(dut)
    
    # Test with specific flags to verify packing
    cmd = pack_cmd_packet(
        axid=7,
        addr=0x4000_0000_0000_0020,  # stt_offset[0] = 1 (32-byte aligned start)
        size=3,
        swizzle=1,
        odd=1,
        ltran=1,
        ftran=1
    )
    
    dut.spt2cvt_req_valid.value = 1
    dut.spt2cvt_req_pd.value = cmd
    
    await RisingEdge(dut.nvdla_core_clk)
    while int(dut.spt2cvt_req_ready.value) == 0:
        await RisingEdge(dut.nvdla_core_clk)
    
    # Sample CQ outputs
    cq_pd = int(dut.cq_wr_pd.value)
    cq_thread_id = int(dut.cq_wr_thread_id.value)
    
    dut.spt2cvt_req_valid.value = 0
    
    # Verify thread ID
    assert cq_thread_id == 7, \
        f"CQ thread_id should be 7, got {cq_thread_id}"
    
    # Unpack and verify CQ packet
    cq = unpack_cq_packet(cq_pd)
    
    assert cq['swizzle'] == 1, "CQ swizzle should be 1"
    assert cq['odd'] == 1, "CQ odd should be 1"
    assert cq['ltran'] == 1, "CQ ltran should be 1"


@cocotb.test(timeout_time=500, timeout_unit="us")
async def test_three_way_interlock(dut):
    """Test 7: Verify three-way flow control (AXI rdy, CQ rdy, os_cnt)."""
    clock = Clock(dut.nvdla_core_clk, 10, units="ns")
    cocotb.start_soon(clock.start())
    
    await reset_dut(dut)
    
    # Prepare a command
    cmd = pack_cmd_packet(
        axid=2,
        addr=0x5000_0000_0000_0000,
        size=2,
        swizzle=0,
        odd=0,
        ltran=1,
        ftran=1
    )
    
    # Test 1: Block CQ ready - should stop accepting commands
    dut.cq_wr_prdy.value = 0
    dut.spt2cvt_req_valid.value = 1
    dut.spt2cvt_req_pd.value = cmd
    
    await ClockCycles(dut.nvdla_core_clk, 3)
    
    # Should not be ready when CQ is blocked
    assert int(dut.spt2cvt_req_ready.value) == 0, \
        "Should not accept commands when CQ not ready"
    
    # Release CQ, block AXI
    dut.cq_wr_prdy.value = 1
    dut.mcif2noc_axi_ar_arready.value = 0
    
    await ClockCycles(dut.nvdla_core_clk, 3)
    
    # Eventually should block due to pipeline backup
    # (may take a few cycles due to skid buffer)
    for _ in range(10):
        await RisingEdge(dut.nvdla_core_clk)
    
    # Release AXI
    dut.mcif2noc_axi_ar_arready.value = 1
    dut.spt2cvt_req_valid.value = 0


@cocotb.test(timeout_time=500, timeout_unit="us")
async def test_outstanding_counter_decrement(dut):
    """Test 8: Verify outstanding counter decrements on eg2ig_axi_vld."""
    clock = Clock(dut.nvdla_core_clk, 10, units="ns")
    cocotb.start_soon(clock.start())
    
    await reset_dut(dut)
    
    # Set low outstanding limit
    dut.reg2dp_rd_os_cnt.value = 4
    await ClockCycles(dut.nvdla_core_clk, 2)
    
    # Send commands until throttled
    cmd = pack_cmd_packet(
        axid=1,
        addr=0x6000_0000_0000_0000,
        size=2,
        swizzle=0,
        odd=0,
        ltran=1,
        ftran=1
    )
    
    # Fill up the outstanding counter
    for _ in range(5):
        dut.spt2cvt_req_valid.value = 1
        dut.spt2cvt_req_pd.value = cmd
        await RisingEdge(dut.nvdla_core_clk)
        if int(dut.spt2cvt_req_ready.value) == 0:
            break
    
    dut.spt2cvt_req_valid.value = 0
    
    # Now simulate completions
    dut.eg2ig_axi_vld.value = 1
    await ClockCycles(dut.nvdla_core_clk, 5)
    dut.eg2ig_axi_vld.value = 0
    
    await ClockCycles(dut.nvdla_core_clk, 3)
    
    # Should be ready again after decrements
    dut.spt2cvt_req_valid.value = 1
    await RisingEdge(dut.nvdla_core_clk)
    
    # With completions processed, should accept commands again
    ready_after_decrement = int(dut.spt2cvt_req_ready.value)
    dut.spt2cvt_req_valid.value = 0
    
    assert ready_after_decrement == 1, \
        "Should accept commands after outstanding counter decremented"


@cocotb.test(timeout_time=500, timeout_unit="us")
async def test_multiple_transactions_sequence(dut):
    """Test 9: Process multiple transactions in sequence."""
    clock = Clock(dut.nvdla_core_clk, 10, units="ns")
    cocotb.start_soon(clock.start())
    
    await reset_dut(dut)
    
    transactions = [
        {'axid': 0, 'addr': 0x1000_0000_0000_0000, 'size': 2},
        {'axid': 1, 'addr': 0x1000_0000_0000_0100, 'size': 4},
        {'axid': 2, 'addr': 0x1000_0000_0000_0200, 'size': 6},
        {'axid': 3, 'addr': 0x1000_0000_0000_0300, 'size': 2},
    ]
    
    successful_transactions = 0
    
    for txn in transactions:
        cmd = pack_cmd_packet(
            axid=txn['axid'],
            addr=txn['addr'],
            size=txn['size'],
            swizzle=0,
            odd=0,
            ltran=1,
            ftran=1
        )
        
        dut.spt2cvt_req_valid.value = 1
        dut.spt2cvt_req_pd.value = cmd
        
        # Wait for handshake
        timeout = 20
        while timeout > 0:
            await RisingEdge(dut.nvdla_core_clk)
            if int(dut.spt2cvt_req_ready.value) == 1:
                successful_transactions += 1
                break
            timeout -= 1
        
        dut.spt2cvt_req_valid.value = 0
        
        # Simulate some completions to prevent throttling
        dut.eg2ig_axi_vld.value = 1
        await RisingEdge(dut.nvdla_core_clk)
        dut.eg2ig_axi_vld.value = 0
        await RisingEdge(dut.nvdla_core_clk)
    
    assert successful_transactions == len(transactions), \
        f"Should complete all {len(transactions)} transactions, got {successful_transactions}"


@cocotb.test(timeout_time=500, timeout_unit="us")
async def test_fdrop_ldrop_flags(dut):
    """Test 10: Verify fdrop/ldrop flag generation based on alignment."""
    clock = Clock(dut.nvdla_core_clk, 10, units="ns")
    cocotb.start_soon(clock.start())
    
    await reset_dut(dut)
    
    # fdrop = cmd_ftran & stt_addr_is_32_align
    # ldrop = cmd_ltran & end_addr_is_32_align
    # stt_addr_is_32_align = (stt_offset[0] == 1) where stt_offset = cmd_addr[7:5]
    
    # Address with stt_offset[0] = 1: addr[7:5] = xxx1 -> addr[5] = 1
    # This means addr & 0x20 != 0
    test_addr = 0x7000_0000_0000_0020  # addr[5] = 1, so stt_offset[0] = 1
    
    cmd = pack_cmd_packet(
        axid=4,
        addr=test_addr,
        size=2,
        swizzle=0,
        odd=0,
        ltran=1,
        ftran=1
    )
    
    dut.spt2cvt_req_valid.value = 1
    dut.spt2cvt_req_pd.value = cmd
    
    await RisingEdge(dut.nvdla_core_clk)
    while int(dut.spt2cvt_req_ready.value) == 0:
        await RisingEdge(dut.nvdla_core_clk)
    
    # Sample CQ output
    cq_pd = int(dut.cq_wr_pd.value)
    cq = unpack_cq_packet(cq_pd)
    
    dut.spt2cvt_req_valid.value = 0
    
    # With ftran=1 and stt_addr_is_32_align=1, fdrop should be 1
    assert cq['fdrop'] == 1, \
        f"fdrop should be 1 when ftran=1 and stt_offset[0]=1, got {cq['fdrop']}"


# Pytest runner function - CRITICAL for HUD framework
def test_mcif_read_ig_cvt_hidden_runner():
    """Pytest entry point for HUD evaluation."""
    sim = os.getenv("SIM", "icarus")
    proj_path = Path(__file__).resolve().parent.parent
    
    sources = [
        proj_path / "sources/NV_NVDLA_MCIF_READ_IG_cvt.v",
    ]
    
    # Add context files if they exist (Scenario 1)
    context_files = [
        proj_path / "sources/NV_NVDLA_MCIF_READ_IG_spt.v",
        proj_path / "sources/NV_NVDLA_MCIF_READ_ig.v",
        proj_path / "sources/NV_NVDLA_XXIF_libs.v",
    ]
    
    for ctx_file in context_files:
        if ctx_file.exists():
            sources.append(ctx_file)
    
    # Include path for simulate_x_tick.vh
    include_dir = str(proj_path / "sources")
    
    runner = get_runner(sim)
    runner.build(
        sources=sources,
        hdl_toplevel="NV_NVDLA_MCIF_READ_IG_cvt",
        always=True,
        build_args=["-g2012", f"-I{include_dir}"],  # SystemVerilog + includes
    )
    runner.test(
        hdl_toplevel="NV_NVDLA_MCIF_READ_IG_cvt",
        test_module="test_mcif_read_ig_cvt_hidden"
    )
