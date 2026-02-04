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
7. Corner cases: swizzle increment, fdrop/ldrop flags, back-to-back
"""

from __future__ import annotations
import os
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, FallingEdge
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
    await ClockCycles(dut.nvdla_core_clk, 5)


@cocotb.test(timeout_time=100000, timeout_unit="ns")
async def test_reset_behavior(dut):
    """Test 1: Verify reset clears all outputs."""
    clock = Clock(dut.nvdla_core_clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    
    # Apply reset
    dut.nvdla_core_rstn.value = 0
    dut.spt2cvt_req_valid.value = 0
    dut.cq_wr_prdy.value = 1
    dut.mcif2noc_axi_ar_arready.value = 1
    dut.eg2ig_axi_vld.value = 0
    dut.reg2dp_rd_os_cnt.value = 255
    
    await ClockCycles(dut.nvdla_core_clk, 5)
    
    # Check outputs during reset - AXI valid should be 0
    assert int(dut.mcif2noc_axi_ar_arvalid.value) == 0, \
        "AXI AR valid should be 0 during reset"
    
    # Release reset
    dut.nvdla_core_rstn.value = 1
    await ClockCycles(dut.nvdla_core_clk, 5)
    
    # Module should be ready to accept commands
    assert int(dut.spt2cvt_req_ready.value) == 1, \
        "Should be ready to accept commands after reset"


@cocotb.test(timeout_time=100000, timeout_unit="ns")
async def test_basic_command_conversion(dut):
    """Test 2: Basic command to AXI conversion."""
    clock = Clock(dut.nvdla_core_clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    
    await reset_dut(dut)
    
    test_addr = 0x0000_0000_1000_0000
    test_axid = 3
    test_size = 2
    
    cmd = pack_cmd_packet(
        axid=test_axid,
        addr=test_addr,
        size=test_size,
        swizzle=0,
        odd=0,
        ltran=1,
        ftran=1
    )
    
    await FallingEdge(dut.nvdla_core_clk)
    dut.spt2cvt_req_valid.value = 1
    dut.spt2cvt_req_pd.value = cmd
    
    handshake_done = False
    cq_thread_id = 0
    for _ in range(20):
        await RisingEdge(dut.nvdla_core_clk)
        if int(dut.spt2cvt_req_ready.value) == 1:
            cq_thread_id = int(dut.cq_wr_thread_id.value)
            handshake_done = True
            break
    
    assert handshake_done, "Timeout waiting for req_ready"
    assert cq_thread_id == test_axid, f"CQ thread ID should be {test_axid}, got {cq_thread_id}"
    
    await FallingEdge(dut.nvdla_core_clk)
    dut.spt2cvt_req_valid.value = 0
    
    axi_valid_seen = False
    for _ in range(10):
        await RisingEdge(dut.nvdla_core_clk)
        if int(dut.mcif2noc_axi_ar_arvalid.value) == 1:
            axi_addr = int(dut.mcif2noc_axi_ar_araddr.value)
            axi_id = int(dut.mcif2noc_axi_ar_arid.value)
            
            assert (axi_addr & 0x3F) == 0, f"AXI address not 64-byte aligned: {hex(axi_addr)}"
            assert (axi_id & 0xF) == test_axid, f"AXI ID mismatch: {axi_id & 0xF}"
            axi_valid_seen = True
            break
    
    assert axi_valid_seen, "Timeout waiting for AXI AR valid"


@cocotb.test(timeout_time=100000, timeout_unit="ns")
async def test_address_alignment(dut):
    """Test 3: Verify address is masked to 64-byte boundary."""
    clock = Clock(dut.nvdla_core_clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    
    await reset_dut(dut)
    
    test_addr = 0x0000_0000_1000_0037
    expected_addr = test_addr & 0xFFFF_FFFF_FFFF_FFC0
    
    cmd = pack_cmd_packet(axid=5, addr=test_addr, size=4, swizzle=0, odd=0, ltran=1, ftran=1)
    
    await FallingEdge(dut.nvdla_core_clk)
    dut.spt2cvt_req_valid.value = 1
    dut.spt2cvt_req_pd.value = cmd
    
    for _ in range(20):
        await RisingEdge(dut.nvdla_core_clk)
        if int(dut.spt2cvt_req_ready.value) == 1:
            break
    
    await FallingEdge(dut.nvdla_core_clk)
    dut.spt2cvt_req_valid.value = 0
    
    for _ in range(10):
        await RisingEdge(dut.nvdla_core_clk)
        if int(dut.mcif2noc_axi_ar_arvalid.value) == 1:
            axi_addr = int(dut.mcif2noc_axi_ar_araddr.value)
            assert axi_addr == expected_addr, f"Expected {hex(expected_addr)}, got {hex(axi_addr)}"
            break


@cocotb.test(timeout_time=100000, timeout_unit="ns")
async def test_burst_length(dut):
    """Test 4: Verify AXI burst length calculation."""
    clock = Clock(dut.nvdla_core_clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    
    await reset_dut(dut)
    
    # size=4 -> axi_len = size[2:1] = 2
    cmd = pack_cmd_packet(axid=1, addr=0x0000_0000_2000_0000, size=4, swizzle=0, odd=0, ltran=1, ftran=1)
    
    await FallingEdge(dut.nvdla_core_clk)
    dut.spt2cvt_req_valid.value = 1
    dut.spt2cvt_req_pd.value = cmd
    
    for _ in range(20):
        await RisingEdge(dut.nvdla_core_clk)
        if int(dut.spt2cvt_req_ready.value) == 1:
            break
    
    await FallingEdge(dut.nvdla_core_clk)
    dut.spt2cvt_req_valid.value = 0
    
    for _ in range(10):
        await RisingEdge(dut.nvdla_core_clk)
        if int(dut.mcif2noc_axi_ar_arvalid.value) == 1:
            axi_len = int(dut.mcif2noc_axi_ar_arlen.value)
            assert axi_len == 2, f"AXI len should be 2, got {axi_len}"
            break


@cocotb.test(timeout_time=100000, timeout_unit="ns")
async def test_cq_packet_passthrough(dut):
    """Test 5: Verify CQ packet passes through swizzle, odd, ltran flags."""
    clock = Clock(dut.nvdla_core_clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    
    await reset_dut(dut)
    
    cmd = pack_cmd_packet(axid=7, addr=0x0000_0000_4000_0000, size=3, swizzle=1, odd=1, ltran=1, ftran=1)
    
    await FallingEdge(dut.nvdla_core_clk)
    dut.spt2cvt_req_valid.value = 1
    dut.spt2cvt_req_pd.value = cmd
    
    cq_sampled = False
    for _ in range(20):
        await RisingEdge(dut.nvdla_core_clk)
        if int(dut.spt2cvt_req_ready.value) == 1 and int(dut.cq_wr_pvld.value) == 1:
            cq_pd = int(dut.cq_wr_pd.value)
            cq_thread_id = int(dut.cq_wr_thread_id.value)
            cq = unpack_cq_packet(cq_pd)
            
            assert cq_thread_id == 7, f"Thread ID should be 7"
            assert cq['swizzle'] == 1, f"Swizzle should be 1"
            assert cq['odd'] == 1, f"Odd should be 1"
            assert cq['ltran'] == 1, f"Ltran should be 1"
            cq_sampled = True
            break
    
    await FallingEdge(dut.nvdla_core_clk)
    dut.spt2cvt_req_valid.value = 0
    
    assert cq_sampled, "Failed to sample CQ packet"


@cocotb.test(timeout_time=100000, timeout_unit="ns")
async def test_cq_backpressure(dut):
    """Test 6: Verify module blocks when CQ not ready."""
    clock = Clock(dut.nvdla_core_clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    
    await reset_dut(dut)
    
    dut.cq_wr_prdy.value = 0
    
    cmd = pack_cmd_packet(axid=2, addr=0x0000_0000_5000_0000, size=2, swizzle=0, odd=0, ltran=1, ftran=1)
    
    await FallingEdge(dut.nvdla_core_clk)
    dut.spt2cvt_req_valid.value = 1
    dut.spt2cvt_req_pd.value = cmd
    
    await ClockCycles(dut.nvdla_core_clk, 5)
    
    assert int(dut.spt2cvt_req_ready.value) == 0, "Should not accept when CQ blocked"
    
    await FallingEdge(dut.nvdla_core_clk)
    dut.cq_wr_prdy.value = 1
    
    await ClockCycles(dut.nvdla_core_clk, 3)
    
    assert int(dut.spt2cvt_req_ready.value) == 1, "Should accept when CQ unblocked"
    
    await FallingEdge(dut.nvdla_core_clk)
    dut.spt2cvt_req_valid.value = 0


@cocotb.test(timeout_time=100000, timeout_unit="ns")
async def test_axi_backpressure(dut):
    """Test 7: Verify pipeline backpressure when AXI not ready."""
    clock = Clock(dut.nvdla_core_clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    
    await reset_dut(dut)
    
    cmd = pack_cmd_packet(axid=1, addr=0x0000_0000_6000_0000, size=2, swizzle=0, odd=0, ltran=1, ftran=1)
    
    await FallingEdge(dut.nvdla_core_clk)
    dut.spt2cvt_req_valid.value = 1
    dut.spt2cvt_req_pd.value = cmd
    
    for _ in range(20):
        await RisingEdge(dut.nvdla_core_clk)
        if int(dut.spt2cvt_req_ready.value) == 1:
            break
    
    await FallingEdge(dut.nvdla_core_clk)
    dut.mcif2noc_axi_ar_arready.value = 0
    
    blocked = False
    for _ in range(15):
        await RisingEdge(dut.nvdla_core_clk)
        if int(dut.spt2cvt_req_ready.value) == 0:
            blocked = True
            break
    
    assert blocked, "Should block when AXI backpressures"
    
    await FallingEdge(dut.nvdla_core_clk)
    dut.mcif2noc_axi_ar_arready.value = 1
    dut.spt2cvt_req_valid.value = 0


@cocotb.test(timeout_time=200000, timeout_unit="ns")
async def test_outstanding_limit(dut):
    """Test 8: Verify outstanding counter limits transactions."""
    clock = Clock(dut.nvdla_core_clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    
    await reset_dut(dut)
    
    await FallingEdge(dut.nvdla_core_clk)
    dut.reg2dp_rd_os_cnt.value = 4
    dut.eg2ig_axi_vld.value = 0
    
    await ClockCycles(dut.nvdla_core_clk, 2)
    
    cmd = pack_cmd_packet(axid=1, addr=0x0000_0000_7000_0000, size=2, swizzle=0, odd=0, ltran=1, ftran=1)
    
    commands_accepted = 0
    for i in range(15):
        await FallingEdge(dut.nvdla_core_clk)
        dut.spt2cvt_req_valid.value = 1
        dut.spt2cvt_req_pd.value = cmd
        
        await RisingEdge(dut.nvdla_core_clk)
        
        if int(dut.spt2cvt_req_ready.value) == 1:
            commands_accepted += 1
        else:
            break
    
    await FallingEdge(dut.nvdla_core_clk)
    dut.spt2cvt_req_valid.value = 0
    
    assert 0 < commands_accepted < 15, f"Expected throttling, accepted {commands_accepted}"


@cocotb.test(timeout_time=200000, timeout_unit="ns")
async def test_multiple_transactions(dut):
    """Test 9: Process multiple transactions in sequence."""
    clock = Clock(dut.nvdla_core_clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    
    await reset_dut(dut)
    
    successful = 0
    
    for i in range(4):
        cmd = pack_cmd_packet(
            axid=i,
            addr=0x0000_0000_1000_0000 + i * 0x1000,
            size=2, swizzle=0, odd=0, ltran=1, ftran=1
        )
        
        await FallingEdge(dut.nvdla_core_clk)
        dut.spt2cvt_req_valid.value = 1
        dut.spt2cvt_req_pd.value = cmd
        
        for _ in range(20):
            await RisingEdge(dut.nvdla_core_clk)
            if int(dut.spt2cvt_req_ready.value) == 1:
                successful += 1
                await FallingEdge(dut.nvdla_core_clk)
                dut.eg2ig_axi_vld.value = 1
                break
        
        await FallingEdge(dut.nvdla_core_clk)
        dut.spt2cvt_req_valid.value = 0
        
        await RisingEdge(dut.nvdla_core_clk)
        await FallingEdge(dut.nvdla_core_clk)
        dut.eg2ig_axi_vld.value = 0
        await RisingEdge(dut.nvdla_core_clk)
    
    assert successful == 4, f"Should complete 4 transactions, got {successful}"


@cocotb.test(timeout_time=100000, timeout_unit="ns")
async def test_client_ids(dut):
    """Test 10: Verify different client IDs work correctly."""
    clock = Clock(dut.nvdla_core_clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    
    await reset_dut(dut)
    
    for client_id in [0, 5, 9]:
        cmd = pack_cmd_packet(
            axid=client_id,
            addr=0x0000_0000_8000_0000,
            size=2, swizzle=0, odd=0, ltran=1, ftran=1
        )
        
        await FallingEdge(dut.nvdla_core_clk)
        dut.spt2cvt_req_valid.value = 1
        dut.spt2cvt_req_pd.value = cmd
        
        for _ in range(20):
            await RisingEdge(dut.nvdla_core_clk)
            if int(dut.spt2cvt_req_ready.value) == 1 and int(dut.cq_wr_pvld.value) == 1:
                cq_thread_id = int(dut.cq_wr_thread_id.value)
                assert cq_thread_id == client_id, f"Thread ID mismatch for client {client_id}"
                await FallingEdge(dut.nvdla_core_clk)
                dut.eg2ig_axi_vld.value = 1
                break
        
        await FallingEdge(dut.nvdla_core_clk)
        dut.spt2cvt_req_valid.value = 0
        
        await RisingEdge(dut.nvdla_core_clk)
        await FallingEdge(dut.nvdla_core_clk)
        dut.eg2ig_axi_vld.value = 0
        await RisingEdge(dut.nvdla_core_clk)


# ============================================================================
# CORNER CASE TESTS
# ============================================================================

@cocotb.test(timeout_time=100000, timeout_unit="ns")
async def test_swizzle_burst_increment(dut):
    """Test 11: Verify burst length increments for swizzle edge case.
    
    When a single complete transfer (ftran=1 and ltran=1) has an odd size
    and swizzle is enabled, an extra AXI beat is needed.
    """
    clock = Clock(dut.nvdla_core_clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    
    await reset_dut(dut)
    
    # size=3 (odd), swizzle=1, ftran=1, ltran=1 -> should increment axi_len
    # Normal: axi_len = size[2:1] = 1
    # With increment: axi_len = 1 + 1 = 2
    cmd = pack_cmd_packet(
        axid=2,
        addr=0x0000_0000_A000_0000,
        size=3,      # Odd size (bit 0 = 1)
        swizzle=1,   # Swizzle enabled
        odd=0,
        ltran=1,     # Last transaction
        ftran=1      # First transaction (complete single transfer)
    )
    
    await FallingEdge(dut.nvdla_core_clk)
    dut.spt2cvt_req_valid.value = 1
    dut.spt2cvt_req_pd.value = cmd
    
    for _ in range(20):
        await RisingEdge(dut.nvdla_core_clk)
        if int(dut.spt2cvt_req_ready.value) == 1:
            break
    
    await FallingEdge(dut.nvdla_core_clk)
    dut.spt2cvt_req_valid.value = 0
    
    for _ in range(10):
        await RisingEdge(dut.nvdla_core_clk)
        if int(dut.mcif2noc_axi_ar_arvalid.value) == 1:
            axi_len = int(dut.mcif2noc_axi_ar_arlen.value)
            # With increment: size[2:1] + 1 = 1 + 1 = 2
            assert axi_len == 2, f"Swizzle case: AXI len should be 2, got {axi_len}"
            break


@cocotb.test(timeout_time=100000, timeout_unit="ns")
async def test_swizzle_no_increment_partial(dut):
    """Test 12: Verify no increment when not a complete single transfer.
    
    Even with odd size and swizzle, if not both ftran and ltran, no increment.
    """
    clock = Clock(dut.nvdla_core_clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    
    await reset_dut(dut)
    
    # size=3 (odd), swizzle=1, but ftran=0 -> no increment
    cmd = pack_cmd_packet(
        axid=2,
        addr=0x0000_0000_A000_0000,
        size=3,
        swizzle=1,
        odd=0,
        ltran=1,
        ftran=0      # Not first transaction -> no increment
    )
    
    await FallingEdge(dut.nvdla_core_clk)
    dut.spt2cvt_req_valid.value = 1
    dut.spt2cvt_req_pd.value = cmd
    
    for _ in range(20):
        await RisingEdge(dut.nvdla_core_clk)
        if int(dut.spt2cvt_req_ready.value) == 1:
            break
    
    await FallingEdge(dut.nvdla_core_clk)
    dut.spt2cvt_req_valid.value = 0
    
    for _ in range(10):
        await RisingEdge(dut.nvdla_core_clk)
        if int(dut.mcif2noc_axi_ar_arvalid.value) == 1:
            axi_len = int(dut.mcif2noc_axi_ar_arlen.value)
            # No increment: size[2:1] = 1
            assert axi_len == 1, f"Partial transfer: AXI len should be 1, got {axi_len}"
            break


@cocotb.test(timeout_time=100000, timeout_unit="ns")
async def test_fdrop_flag(dut):
    """Test 13: Verify first beat drop flag (fdrop).
    
    When the start address is 32-byte aligned within a 64-byte AXI beat
    and this is the first transaction (ftran=1), the first beat should
    be marked for dropping.
    """
    clock = Clock(dut.nvdla_core_clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    
    await reset_dut(dut)
    
    # Address where bit[5] = 1 means 32-byte offset within 64-byte beat
    # addr[7:5] = stt_offset, if stt_offset[0] = 1, then 32-byte aligned
    test_addr = 0x0000_0000_B000_0020  # bit[5] = 1
    
    cmd = pack_cmd_packet(
        axid=4,
        addr=test_addr,
        size=2,
        swizzle=0,
        odd=0,
        ltran=1,
        ftran=1      # First transaction -> fdrop applies
    )
    
    await FallingEdge(dut.nvdla_core_clk)
    dut.spt2cvt_req_valid.value = 1
    dut.spt2cvt_req_pd.value = cmd
    
    cq_sampled = False
    for _ in range(20):
        await RisingEdge(dut.nvdla_core_clk)
        if int(dut.spt2cvt_req_ready.value) == 1 and int(dut.cq_wr_pvld.value) == 1:
            cq_pd = int(dut.cq_wr_pd.value)
            cq = unpack_cq_packet(cq_pd)
            
            assert cq['fdrop'] == 1, f"fdrop should be 1 for 32-byte aligned start with ftran=1"
            cq_sampled = True
            break
    
    await FallingEdge(dut.nvdla_core_clk)
    dut.spt2cvt_req_valid.value = 0
    
    assert cq_sampled, "Failed to sample CQ packet for fdrop test"


@cocotb.test(timeout_time=100000, timeout_unit="ns")
async def test_fdrop_no_flag(dut):
    """Test 14: Verify fdrop is 0 when not 32-byte aligned or not ftran."""
    clock = Clock(dut.nvdla_core_clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    
    await reset_dut(dut)
    
    # Address where bit[5] = 0 -> not 32-byte aligned
    test_addr = 0x0000_0000_B000_0000  # bit[5] = 0
    
    cmd = pack_cmd_packet(
        axid=4,
        addr=test_addr,
        size=2,
        swizzle=0,
        odd=0,
        ltran=1,
        ftran=1
    )
    
    await FallingEdge(dut.nvdla_core_clk)
    dut.spt2cvt_req_valid.value = 1
    dut.spt2cvt_req_pd.value = cmd
    
    for _ in range(20):
        await RisingEdge(dut.nvdla_core_clk)
        if int(dut.spt2cvt_req_ready.value) == 1 and int(dut.cq_wr_pvld.value) == 1:
            cq_pd = int(dut.cq_wr_pd.value)
            cq = unpack_cq_packet(cq_pd)
            
            assert cq['fdrop'] == 0, f"fdrop should be 0 when not 32-byte aligned"
            break
    
    await FallingEdge(dut.nvdla_core_clk)
    dut.spt2cvt_req_valid.value = 0


@cocotb.test(timeout_time=100000, timeout_unit="ns")
async def test_ldrop_flag(dut):
    """Test 15: Verify last beat drop flag (ldrop).
    
    When the end address falls on a 32-byte boundary within a 64-byte beat
    and this is the last transaction (ltran=1), the last beat should
    be marked for dropping.
    """
    clock = Clock(dut.nvdla_core_clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    
    await reset_dut(dut)
    
    # end_offset = stt_offset + size
    # end_offset[0] = 0 means end is 32-byte aligned
    # stt_offset = addr[7:5], for addr=0 -> stt_offset=0
    # size=2 -> end_offset = 0 + 2 = 2, end_offset[0] = 0 -> ldrop=1
    test_addr = 0x0000_0000_C000_0000  # stt_offset = 0
    
    cmd = pack_cmd_packet(
        axid=5,
        addr=test_addr,
        size=2,       # end_offset = 0 + 2 = 2, bit[0] = 0 -> aligned
        swizzle=0,
        odd=0,
        ltran=1,      # Last transaction -> ldrop applies
        ftran=1
    )
    
    await FallingEdge(dut.nvdla_core_clk)
    dut.spt2cvt_req_valid.value = 1
    dut.spt2cvt_req_pd.value = cmd
    
    cq_sampled = False
    for _ in range(20):
        await RisingEdge(dut.nvdla_core_clk)
        if int(dut.spt2cvt_req_ready.value) == 1 and int(dut.cq_wr_pvld.value) == 1:
            cq_pd = int(dut.cq_wr_pd.value)
            cq = unpack_cq_packet(cq_pd)
            
            assert cq['ldrop'] == 1, f"ldrop should be 1 for 32-byte aligned end with ltran=1"
            cq_sampled = True
            break
    
    await FallingEdge(dut.nvdla_core_clk)
    dut.spt2cvt_req_valid.value = 0
    
    assert cq_sampled, "Failed to sample CQ packet for ldrop test"


@cocotb.test(timeout_time=100000, timeout_unit="ns")
async def test_ldrop_no_flag(dut):
    """Test 16: Verify ldrop is 0 when end is not 32-byte aligned."""
    clock = Clock(dut.nvdla_core_clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    
    await reset_dut(dut)
    
    # stt_offset = 0, size = 3 -> end_offset = 3, bit[0] = 1 -> not aligned
    test_addr = 0x0000_0000_C000_0000
    
    cmd = pack_cmd_packet(
        axid=5,
        addr=test_addr,
        size=3,       # end_offset = 0 + 3 = 3, bit[0] = 1 -> not aligned
        swizzle=0,
        odd=0,
        ltran=1,
        ftran=1
    )
    
    await FallingEdge(dut.nvdla_core_clk)
    dut.spt2cvt_req_valid.value = 1
    dut.spt2cvt_req_pd.value = cmd
    
    for _ in range(20):
        await RisingEdge(dut.nvdla_core_clk)
        if int(dut.spt2cvt_req_ready.value) == 1 and int(dut.cq_wr_pvld.value) == 1:
            cq_pd = int(dut.cq_wr_pd.value)
            cq = unpack_cq_packet(cq_pd)
            
            assert cq['ldrop'] == 0, f"ldrop should be 0 when end not 32-byte aligned"
            break
    
    await FallingEdge(dut.nvdla_core_clk)
    dut.spt2cvt_req_valid.value = 0


@cocotb.test(timeout_time=200000, timeout_unit="ns")
async def test_back_to_back_transactions(dut):
    """Test 17: Verify continuous back-to-back commands are handled.
    
    Commands are presented every cycle without gaps to test pipeline
    and flow control under sustained load.
    """
    clock = Clock(dut.nvdla_core_clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    
    await reset_dut(dut)
    
    # Ensure enough OS headroom and completions
    dut.reg2dp_rd_os_cnt.value = 255
    
    successful = 0
    target = 8
    
    for i in range(target):
        cmd = pack_cmd_packet(
            axid=i % 10,
            addr=0x0000_0000_D000_0000 + i * 0x40,
            size=2,
            swizzle=0,
            odd=0,
            ltran=1,
            ftran=1
        )
        
        await FallingEdge(dut.nvdla_core_clk)
        dut.spt2cvt_req_valid.value = 1
        dut.spt2cvt_req_pd.value = cmd
        
        # Only wait one cycle - back-to-back
        await RisingEdge(dut.nvdla_core_clk)
        
        if int(dut.spt2cvt_req_ready.value) == 1:
            successful += 1
            # Pulse completion to keep counter low
            dut.eg2ig_axi_vld.value = 1
        
        await FallingEdge(dut.nvdla_core_clk)
        dut.eg2ig_axi_vld.value = 0
    
    await FallingEdge(dut.nvdla_core_clk)
    dut.spt2cvt_req_valid.value = 0
    
    # Should handle most back-to-back commands
    assert successful >= target // 2, f"Should handle back-to-back, got {successful}/{target}"


# Pytest runner
def test_mcif_read_ig_cvt_hidden_runner():
    """Pytest entry point for HUD evaluation."""
    sim = os.getenv("SIM", "icarus")
    proj_path = Path(__file__).resolve().parent.parent
    
    sources = [
        proj_path / "tests/timescale.v",
        proj_path / "sources/NV_NVDLA_MCIF_READ_IG_cvt.v",
    ]
    
    context_files = [
        proj_path / "sources/NV_NVDLA_MCIF_READ_IG_spt.v",
        proj_path / "sources/NV_NVDLA_MCIF_READ_ig.v",
        proj_path / "sources/NV_NVDLA_XXIF_libs.v",
    ]
    
    for ctx_file in context_files:
        if ctx_file.exists():
            sources.append(ctx_file)
    
    include_dir = str(proj_path / "tests")
    
    runner = get_runner(sim)
    runner.build(
        sources=sources,
        hdl_toplevel="NV_NVDLA_MCIF_READ_IG_cvt",
        always=True,
        build_args=["-g2012", f"-I{include_dir}"],
    )
    runner.test(
        hdl_toplevel="NV_NVDLA_MCIF_READ_IG_cvt",
        test_module="test_mcif_read_ig_cvt_hidden"
    )
