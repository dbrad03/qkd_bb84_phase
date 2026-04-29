`timescale 1ns / 1ps
`default_nettype none

// AXI4-Lite Register File for QKD Phase-BB84 Control
//
// Register Map (byte offsets):
//   0x00  CTRL              R/W  [0] global_en, [1] alice_en, [2] bob_en
//   0x04  ALICE_PHASE_STAGED R/W [1:0] Alice phase (staged, not active until PHASE_APPLY)
//   0x08  BOB_PHASE_STAGED  R/W  [1:0] Bob phase (staged)
//   0x0C  STATUS            R    [0] alice_running, [1] bob_running, [2] sw_mode
//   0x10  PHASE_APPLY       R/W  [0] apply trigger (auto-clears after 1 cycle)
//   0x14  ALICE_PHASE_ACTIVE R   [1:0] Currently active Alice phase
//   0x18  BOB_PHASE_ACTIVE  R    [1:0] Currently active Bob phase
//   0x1C  VERSION           R    32'h2026_0425
//
// AXI protocol state machine adapted from Xilinx auto-generated template.

module axi_lite_regs #(
    parameter integer C_S_AXI_DATA_WIDTH = 32,
    parameter integer C_S_AXI_ADDR_WIDTH = 5
) (
    input  wire  S_AXI_ACLK,
    input  wire  S_AXI_ARESETN,

    // AXI4-Lite slave interface
    input  wire [C_S_AXI_ADDR_WIDTH-1:0] S_AXI_AWADDR,
    input  wire [2:0]                    S_AXI_AWPROT,
    input  wire                          S_AXI_AWVALID,
    output wire                          S_AXI_AWREADY,
    input  wire [C_S_AXI_DATA_WIDTH-1:0] S_AXI_WDATA,
    input  wire [C_S_AXI_DATA_WIDTH/8-1:0] S_AXI_WSTRB,
    input  wire                          S_AXI_WVALID,
    output wire                          S_AXI_WREADY,
    output wire [1:0]                    S_AXI_BRESP,
    output wire                          S_AXI_BVALID,
    input  wire                          S_AXI_BREADY,
    input  wire [C_S_AXI_ADDR_WIDTH-1:0] S_AXI_ARADDR,
    input  wire [2:0]                    S_AXI_ARPROT,
    input  wire                          S_AXI_ARVALID,
    output wire                          S_AXI_ARREADY,
    output wire [C_S_AXI_DATA_WIDTH-1:0] S_AXI_RDATA,
    output wire [1:0]                    S_AXI_RRESP,
    output wire                          S_AXI_RVALID,
    input  wire                          S_AXI_RREADY,

    // Register outputs (directly accessible by wrapper)
    output wire [2:0]  ctrl,                // {bob_en, alice_en, global_en}
    output wire [1:0]  alice_phase_staged,
    output wire [1:0]  bob_phase_staged,
    output wire        phase_apply,         // single-cycle pulse

    // Read-only inputs from wrapper
    input  wire [2:0]  status,              // {sw_mode, bob_running, alice_running}
    input  wire [1:0]  alice_phase_active,
    input  wire [1:0]  bob_phase_active
);

    // ---------------------------------------------------------------
    // Internal AXI signals
    // ---------------------------------------------------------------
    logic [C_S_AXI_ADDR_WIDTH-1:0] axi_awaddr, axi_araddr;
    logic axi_awready, axi_wready;
    logic [1:0] axi_bresp;
    logic axi_bvalid;
    logic axi_arready;
    logic [1:0] axi_rresp;
    logic axi_rvalid;

    assign S_AXI_AWREADY = axi_awready;
    assign S_AXI_WREADY  = axi_wready;
    assign S_AXI_BRESP   = axi_bresp;
    assign S_AXI_BVALID  = axi_bvalid;
    assign S_AXI_ARREADY = axi_arready;
    assign S_AXI_RRESP   = axi_rresp;
    assign S_AXI_RVALID  = axi_rvalid;

    // ---------------------------------------------------------------
    // Register storage
    // ---------------------------------------------------------------
    localparam integer ADDR_LSB = (C_S_AXI_DATA_WIDTH / 32) + 1;  // = 2 for 32-bit
    localparam integer OPT_MEM_ADDR_BITS = 2;  // 8 registers → 3-bit index

    logic [C_S_AXI_DATA_WIDTH-1:0] reg_ctrl;            // 0x00
    logic [C_S_AXI_DATA_WIDTH-1:0] reg_alice_staged;    // 0x04
    logic [C_S_AXI_DATA_WIDTH-1:0] reg_bob_staged;      // 0x08
    // 0x0C STATUS is read-only (external input)
    logic [C_S_AXI_DATA_WIDTH-1:0] reg_phase_apply;     // 0x10
    // 0x14, 0x18, 0x1C are read-only

    assign ctrl               = reg_ctrl[2:0];
    assign alice_phase_staged = reg_alice_staged[1:0];
    assign bob_phase_staged   = reg_bob_staged[1:0];
    assign phase_apply        = reg_phase_apply[0];

    // ---------------------------------------------------------------
    // Write state machine (from Xilinx template)
    // ---------------------------------------------------------------
    logic [1:0] state_write;
    localparam [1:0] W_IDLE = 2'b00, W_ADDR = 2'b10, W_DATA = 2'b11;

    always_ff @(posedge S_AXI_ACLK) begin
        if (!S_AXI_ARESETN) begin
            axi_awready <= 0;
            axi_wready  <= 0;
            axi_bvalid  <= 0;
            axi_bresp   <= 0;
            axi_awaddr  <= 0;
            state_write <= W_IDLE;
        end else begin
            case (state_write)
                W_IDLE: begin
                    axi_awready <= 1'b1;
                    axi_wready  <= 1'b1;
                    state_write <= W_ADDR;
                end
                W_ADDR: begin
                    if (S_AXI_AWVALID && axi_awready) begin
                        axi_awaddr <= S_AXI_AWADDR;
                        if (S_AXI_WVALID) begin
                            axi_awready <= 1'b1;
                            axi_bvalid  <= 1'b1;
                            state_write <= W_ADDR;
                        end else begin
                            axi_awready <= 1'b0;
                            state_write <= W_DATA;
                            if (S_AXI_BREADY && axi_bvalid) axi_bvalid <= 1'b0;
                        end
                    end else begin
                        if (S_AXI_BREADY && axi_bvalid) axi_bvalid <= 1'b0;
                    end
                end
                W_DATA: begin
                    if (S_AXI_WVALID) begin
                        state_write <= W_ADDR;
                        axi_bvalid  <= 1'b1;
                        axi_awready <= 1'b1;
                    end else begin
                        if (S_AXI_BREADY && axi_bvalid) axi_bvalid <= 1'b0;
                    end
                end
                default: state_write <= W_IDLE;
            endcase
        end
    end

    // ---------------------------------------------------------------
    // Write data logic
    // ---------------------------------------------------------------
    wire [OPT_MEM_ADDR_BITS:0] wr_addr = (S_AXI_AWVALID)
        ? S_AXI_AWADDR[ADDR_LSB+OPT_MEM_ADDR_BITS : ADDR_LSB]
        : axi_awaddr[ADDR_LSB+OPT_MEM_ADDR_BITS : ADDR_LSB];

    always_ff @(posedge S_AXI_ACLK) begin
        if (!S_AXI_ARESETN) begin
            reg_ctrl        <= 0;
            reg_alice_staged <= 0;
            reg_bob_staged  <= 0;
            reg_phase_apply <= 0;
        end else begin
            // Auto-clear PHASE_APPLY after 1 cycle
            if (reg_phase_apply[0])
                reg_phase_apply <= 0;

            if (S_AXI_WVALID) begin
                case (wr_addr)
                    3'h0: reg_ctrl         <= S_AXI_WDATA;  // 0x00 CTRL
                    3'h1: reg_alice_staged <= S_AXI_WDATA;  // 0x04 ALICE_PHASE_STAGED
                    3'h2: reg_bob_staged   <= S_AXI_WDATA;  // 0x08 BOB_PHASE_STAGED
                    // 3'h3: STATUS is read-only, ignore writes
                    3'h4: reg_phase_apply  <= S_AXI_WDATA;  // 0x10 PHASE_APPLY
                    // 3'h5, 3'h6, 3'h7: read-only, ignore writes
                    default: ;
                endcase
            end
        end
    end

    // ---------------------------------------------------------------
    // Read state machine (from Xilinx template)
    // ---------------------------------------------------------------
    logic [1:0] state_read;
    localparam [1:0] R_IDLE = 2'b00, R_ADDR = 2'b10, R_DATA = 2'b11;

    always_ff @(posedge S_AXI_ACLK) begin
        if (!S_AXI_ARESETN) begin
            axi_arready <= 1'b0;
            axi_rvalid  <= 1'b0;
            axi_rresp   <= 1'b0;
            axi_araddr  <= 0;
            state_read  <= R_IDLE;
        end else begin
            case (state_read)
                R_IDLE: begin
                    axi_arready <= 1'b1;
                    state_read  <= R_ADDR;
                end
                R_ADDR: begin
                    if (S_AXI_ARVALID && axi_arready) begin
                        state_read  <= R_DATA;
                        axi_araddr  <= S_AXI_ARADDR;
                        axi_rvalid  <= 1'b1;
                        axi_arready <= 1'b0;
                    end
                end
                R_DATA: begin
                    if (axi_rvalid && S_AXI_RREADY) begin
                        axi_rvalid  <= 1'b0;
                        axi_arready <= 1'b1;
                        state_read  <= R_ADDR;
                    end
                end
                default: state_read <= R_IDLE;
            endcase
        end
    end

    // ---------------------------------------------------------------
    // Read data mux
    // ---------------------------------------------------------------
    logic [C_S_AXI_DATA_WIDTH-1:0] rdata;

    always_comb begin
        case (axi_araddr[ADDR_LSB+OPT_MEM_ADDR_BITS : ADDR_LSB])
            3'h0: rdata = reg_ctrl;                                        // 0x00
            3'h1: rdata = reg_alice_staged;                                // 0x04
            3'h2: rdata = reg_bob_staged;                                  // 0x08
            3'h3: rdata = {29'd0, status};                                 // 0x0C
            3'h4: rdata = reg_phase_apply;                                 // 0x10
            3'h5: rdata = {30'd0, alice_phase_active};                     // 0x14
            3'h6: rdata = {30'd0, bob_phase_active};                       // 0x18
            3'h7: rdata = 32'h2026_0425;                                   // 0x1C VERSION
            default: rdata = 0;
        endcase
    end

    assign S_AXI_RDATA = rdata;

endmodule

`default_nettype wire
