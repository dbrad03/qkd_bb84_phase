`timescale 1ns / 1ps
`default_nettype none

// QKD Phase-BB84 Top Wrapper (Single Channel + Auto-Cycle)
//
// Single PSK generator (Alice) with three operating modes:
//   SW3=0                       → Switch mode (SW0/SW1 set phase)
//   SW3=1, SW2=0, auto_en=0    → Register mode (AXI-Lite staged/apply)
//   SW3=1, SW2=1 OR auto_en=1  → Auto-cycle (PL cycles 0→1→2→3, buttons control rate)
//
// Clock domains:
//   s_axi_aclk   (~100 MHz) — PS AXI register access, button debounce, freq control
//   rfdc_aclk    (~307.2 MHz) — Alice PSK generator, auto-cycle phase counter

module qkd_top_wrapper #(
    parameter integer C_S_AXI_DATA_WIDTH = 32,
    parameter integer C_S_AXI_ADDR_WIDTH = 5,
    parameter integer SAMPLES_PER_BEAT   = 4,
    parameter integer SAMPLE_WIDTH       = 16,
    parameter integer BTN_CLK_FREQ_HZ    = 100_000_000,
    parameter integer RFDC_CLK_FREQ_HZ   = 307_200_000
) (
    // AXI-Lite slave (PS register access)
    input  wire  s_axi_aclk,
    input  wire  s_axi_aresetn,
    input  wire [C_S_AXI_ADDR_WIDTH-1:0] s_axi_awaddr,
    input  wire [2:0]                    s_axi_awprot,
    input  wire                          s_axi_awvalid,
    output wire                          s_axi_awready,
    input  wire [C_S_AXI_DATA_WIDTH-1:0] s_axi_wdata,
    input  wire [C_S_AXI_DATA_WIDTH/8-1:0] s_axi_wstrb,
    input  wire                          s_axi_wvalid,
    output wire                          s_axi_wready,
    output wire [1:0]                    s_axi_bresp,
    output wire                          s_axi_bvalid,
    input  wire                          s_axi_bready,
    input  wire [C_S_AXI_ADDR_WIDTH-1:0] s_axi_araddr,
    input  wire [2:0]                    s_axi_arprot,
    input  wire                          s_axi_arvalid,
    output wire                          s_axi_arready,
    output wire [C_S_AXI_DATA_WIDTH-1:0] s_axi_rdata,
    output wire [1:0]                    s_axi_rresp,
    output wire                          s_axi_rvalid,
    input  wire                          s_axi_rready,

    // RFDC data clock (single DAC tile)
    input  wire  rfdc_aclk,
    input  wire  rfdc_aresetn,

    // AXI4-Stream master to DAC (Alice)
    output wire [SAMPLES_PER_BEAT*SAMPLE_WIDTH*2-1:0] m_axis_alice_tdata,
    output wire                                       m_axis_alice_tvalid,
    input  wire                                       m_axis_alice_tready,

    // Board I/O
    input  wire [3:0] sw,
    input  wire [3:0] btn,
    output wire [3:0] led
);

    // ---------------------------------------------------------------
    // Register file (s_axi_aclk domain)
    // ---------------------------------------------------------------
    wire [2:0]  ctrl;                    // {auto_cycle_en, alice_en, global_en}
    wire [1:0]  alice_phase_staged;
    wire        phase_apply;
    wire [2:0]  status;
    wire [1:0]  alice_phase_active_readback;
    wire [31:0] auto_freq_hz;
    wire [31:0] auto_period_ticks;

    axi_lite_regs #(
        .C_S_AXI_DATA_WIDTH(C_S_AXI_DATA_WIDTH),
        .C_S_AXI_ADDR_WIDTH(C_S_AXI_ADDR_WIDTH)
    ) u_regs (
        .S_AXI_ACLK    (s_axi_aclk),
        .S_AXI_ARESETN (s_axi_aresetn),
        .S_AXI_AWADDR  (s_axi_awaddr),
        .S_AXI_AWPROT  (s_axi_awprot),
        .S_AXI_AWVALID (s_axi_awvalid),
        .S_AXI_AWREADY (s_axi_awready),
        .S_AXI_WDATA   (s_axi_wdata),
        .S_AXI_WSTRB   (s_axi_wstrb),
        .S_AXI_WVALID  (s_axi_wvalid),
        .S_AXI_WREADY  (s_axi_wready),
        .S_AXI_BRESP   (s_axi_bresp),
        .S_AXI_BVALID  (s_axi_bvalid),
        .S_AXI_BREADY  (s_axi_bready),
        .S_AXI_ARADDR  (s_axi_araddr),
        .S_AXI_ARPROT  (s_axi_arprot),
        .S_AXI_ARVALID (s_axi_arvalid),
        .S_AXI_ARREADY (s_axi_arready),
        .S_AXI_RDATA   (s_axi_rdata),
        .S_AXI_RRESP   (s_axi_rresp),
        .S_AXI_RVALID  (s_axi_rvalid),
        .S_AXI_RREADY  (s_axi_rready),
        .ctrl               (ctrl),
        .alice_phase_staged (alice_phase_staged),
        .phase_apply        (phase_apply),
        .status             (status),
        .alice_phase_active (alice_phase_active_readback),
        .auto_freq_hz       (auto_freq_hz),
        .auto_period_ticks  (auto_period_ticks)
    );

    // ---------------------------------------------------------------
    // Button debounce (s_axi_aclk domain)
    // ---------------------------------------------------------------
    wire [3:0] btn_pulse;

    btn_debounce #(
        .CLK_FREQ_HZ(BTN_CLK_FREQ_HZ),
        .DEBOUNCE_MS(20),
        .N_BUTTONS(4)
    ) u_debounce (
        .clk      (s_axi_aclk),
        .resetn   (s_axi_aresetn),
        .btn_raw  (btn),
        .btn_pulse(btn_pulse)
    );

    // ---------------------------------------------------------------
    // CDC: s_axi_aclk → rfdc_aclk
    // All signals are quasi-static (switches or ≤320 Hz protocol rate)
    // ---------------------------------------------------------------

    // Switches
    (* ASYNC_REG = "TRUE" *) logic [3:0] sw_meta, sw_sync;
    always_ff @(posedge rfdc_aclk) begin
        sw_meta <= sw;
        sw_sync <= sw_meta;
    end

    // Alice staged phase
    (* ASYNC_REG = "TRUE" *) logic [1:0] alice_staged_meta, alice_staged_sync;
    always_ff @(posedge rfdc_aclk) begin
        alice_staged_meta <= alice_phase_staged;
        alice_staged_sync <= alice_staged_meta;
    end

    // Phase apply pulse (edge detect in rfdc domain)
    (* ASYNC_REG = "TRUE" *) logic phase_apply_meta, phase_apply_sync, phase_apply_prev;
    always_ff @(posedge rfdc_aclk) begin
        phase_apply_meta <= phase_apply;
        phase_apply_sync <= phase_apply_meta;
        phase_apply_prev <= phase_apply_sync;
    end
    wire phase_apply_edge = phase_apply_sync & ~phase_apply_prev;

    // Control register
    (* ASYNC_REG = "TRUE" *) logic [2:0] ctrl_meta, ctrl_sync;
    always_ff @(posedge rfdc_aclk) begin
        ctrl_meta <= ctrl;
        ctrl_sync <= ctrl_meta;
    end

    // ---------------------------------------------------------------
    // Switch decode (rfdc domain)
    // ---------------------------------------------------------------
    wire sw_mode = ~sw_sync[3];  // SW3=0 → switch mode (active high)

    // Alice: SW0 = basis (Z/X), SW1 = bit (0/1)
    wire [1:0] sw_alice_phase = {sw_sync[1], sw_sync[0]};

    // SW2: auto-cycle hardware enable
    wire sw_auto_cycle = sw_sync[2];

    // ---------------------------------------------------------------
    // Armed trigger — register mode phase latch (rfdc_aclk domain)
    // ---------------------------------------------------------------
    logic [1:0] reg_alice_active;

    always_ff @(posedge rfdc_aclk) begin
        if (!rfdc_aresetn)
            reg_alice_active <= 2'b00;
        else if (phase_apply_edge)
            reg_alice_active <= alice_staged_sync;
    end

    // ---------------------------------------------------------------
    // Auto phase cycler (dual-clock)
    // ---------------------------------------------------------------
    wire [1:0]  auto_phase_select;
    wire [31:0] cycler_freq_hz;
    wire [31:0] cycler_period_ticks;
    wire [1:0]  cycler_unit_sel;
    wire [1:0]  cycler_incr_sel;

    // Auto-cycle enable: SW2 (hardware) OR ctrl[2] (software)
    wire auto_cycle_en = sw_auto_cycle | ctrl_sync[2];

    // Toggle mode: SW1 selects 0/pi toggle vs full 4-phase cycle during auto-cycle
    wire auto_toggle_mode = sw_sync[1];

    auto_phase_cycler #(
        .RFDC_CLK_FREQ_HZ(RFDC_CLK_FREQ_HZ)
    ) u_cycler (
        .axi_clk      (s_axi_aclk),
        .axi_resetn   (s_axi_aresetn),
        .btn_pulse    (btn_pulse),
        .rfdc_clk     (rfdc_aclk),
        .rfdc_resetn  (rfdc_aresetn),
        .enable       (auto_cycle_en),
        .toggle_mode  (auto_toggle_mode),
        .freq_hz      (cycler_freq_hz),
        .period_ticks (cycler_period_ticks),
        .unit_sel     (cycler_unit_sel),
        .incr_sel     (cycler_incr_sel),
        .phase_select (auto_phase_select)
    );

    // Connect auto-cycle readback to register file
    assign auto_freq_hz      = cycler_freq_hz;
    assign auto_period_ticks = cycler_period_ticks;

    // ---------------------------------------------------------------
    // 3-way mode mux (rfdc_aclk domain)
    // ---------------------------------------------------------------
    logic [1:0] alice_phase_active;

    always_comb begin
        if (sw_mode)                // SW3=0 → switch mode
            alice_phase_active = sw_alice_phase;
        else if (auto_cycle_en)     // SW3=1, auto-cycle active
            alice_phase_active = auto_phase_select;
        else                        // SW3=1, register mode
            alice_phase_active = reg_alice_active;
    end

    // ---------------------------------------------------------------
    // CDC: rfdc_aclk → s_axi_aclk (readback)
    // ---------------------------------------------------------------
    (* ASYNC_REG = "TRUE" *) logic [1:0] alice_active_rd_meta, alice_active_rd_sync;
    (* ASYNC_REG = "TRUE" *) logic       sw_mode_rd_meta,      sw_mode_rd_sync;

    always_ff @(posedge s_axi_aclk) begin
        alice_active_rd_meta <= alice_phase_active;
        alice_active_rd_sync <= alice_active_rd_meta;
        sw_mode_rd_meta      <= sw_mode;
        sw_mode_rd_sync      <= sw_mode_rd_meta;
    end

    // Enable / status
    wire alice_en   = ctrl_sync[1];
    wire global_en  = ctrl_sync[0];
    wire alice_running  = global_en & alice_en;
    wire auto_cycling   = auto_cycle_en & alice_running;

    assign status = {sw_mode_rd_sync, auto_cycling, alice_running};
    assign alice_phase_active_readback = alice_active_rd_sync;

    // ---------------------------------------------------------------
    // PSK generator (Alice)
    // ---------------------------------------------------------------
    axis_psk_gen #(
        .SAMPLES_PER_BEAT(SAMPLES_PER_BEAT),
        .SAMPLE_WIDTH(SAMPLE_WIDTH)
    ) u_alice (
        .aclk         (rfdc_aclk),
        .aresetn      (rfdc_aresetn),
        .enable       (alice_running),
        .phase_select (alice_phase_active),
        .m_axis_tdata (m_axis_alice_tdata),
        .m_axis_tvalid(m_axis_alice_tvalid),
        .m_axis_tready(m_axis_alice_tready)
    );

    // ---------------------------------------------------------------
    // LEDs
    // ---------------------------------------------------------------
    assign led[3]   = auto_cycle_en;
    assign led[2]   = sw_mode;
    assign led[1:0] = alice_phase_active;

endmodule

`default_nettype wire
