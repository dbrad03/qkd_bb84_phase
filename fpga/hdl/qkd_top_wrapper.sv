`timescale 1ns / 1ps
`default_nettype none

// QKD Phase-BB84 Top Wrapper (Dual Channel + Auto-Cycle)
//
// Two PSK generators (Alice, Bob). Alice has three operating modes:
//   SW3=0                       → Switch mode (SW0/SW1 set phase)
//   SW3=1, SW2=0, auto_en=0    → Register mode (AXI-Lite staged/apply)
//   SW3=1, SW2=1 OR auto_en=1  → Auto-cycle (PL cycles phases, buttons control rate)
// Bob is always register-controlled (SW2 sets phase in switch mode).
//
// Clock domains:
//   s_axi_aclk   (~100 MHz) — PS AXI register access, button debounce, freq control
//   rfdc_aclk_0  (~307.2 MHz) — Alice PSK generator, auto-cycle phase counter
//   rfdc_aclk_2  (~307.2 MHz) — Bob PSK generator

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

    // RFDC data clocks (one per DAC tile)
    input  wire  rfdc_aclk_0,      // Alice
    input  wire  rfdc_aresetn_0,
    input  wire  rfdc_aclk_2,      // Bob
    input  wire  rfdc_aresetn_2,

    // AXI4-Stream master to DAC Tile 228 (Alice)
    output wire [SAMPLES_PER_BEAT*SAMPLE_WIDTH*2-1:0] m_axis_alice_tdata,
    output wire                                       m_axis_alice_tvalid,
    input  wire                                       m_axis_alice_tready,

    // AXI4-Stream master to DAC Tile 230 (Bob)
    output wire [SAMPLES_PER_BEAT*SAMPLE_WIDTH*2-1:0] m_axis_bob_tdata,
    output wire                                       m_axis_bob_tvalid,
    input  wire                                       m_axis_bob_tready,

    // Board I/O
    input  wire [3:0] sw,
    input  wire [3:0] btn,
    output wire [3:0] led
);

    // ---------------------------------------------------------------
    // Register file (s_axi_aclk domain)
    // ---------------------------------------------------------------
    wire [3:0]  ctrl;                    // {auto_cycle_en, bob_en, alice_en, global_en}
    wire [1:0]  alice_phase_staged;
    wire [1:0]  bob_phase_staged;
    wire        phase_apply;
    wire [3:0]  status;
    wire [1:0]  alice_phase_active_readback;
    wire [1:0]  bob_phase_active_readback;
    wire [31:0] auto_freq_hz;

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
        .bob_phase_staged   (bob_phase_staged),
        .phase_apply        (phase_apply),
        .status             (status),
        .alice_phase_active (alice_phase_active_readback),
        .bob_phase_active   (bob_phase_active_readback),
        .auto_freq_hz       (auto_freq_hz)
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
    // CDC: s_axi_aclk → rfdc_aclk_0 (Alice clock domain)
    // ---------------------------------------------------------------

    // Switches (synced to Alice clock)
    (* ASYNC_REG = "TRUE" *) logic [3:0] sw_meta_a, sw_sync_a;
    always_ff @(posedge rfdc_aclk_0) begin
        sw_meta_a <= sw;
        sw_sync_a <= sw_meta_a;
    end

    // Alice staged phase
    (* ASYNC_REG = "TRUE" *) logic [1:0] alice_staged_meta, alice_staged_sync;
    always_ff @(posedge rfdc_aclk_0) begin
        alice_staged_meta <= alice_phase_staged;
        alice_staged_sync <= alice_staged_meta;
    end

    // Phase apply pulse (Alice domain)
    (* ASYNC_REG = "TRUE" *) logic phase_apply_meta_a, phase_apply_sync_a, phase_apply_prev_a;
    always_ff @(posedge rfdc_aclk_0) begin
        phase_apply_meta_a <= phase_apply;
        phase_apply_sync_a <= phase_apply_meta_a;
        phase_apply_prev_a <= phase_apply_sync_a;
    end
    wire phase_apply_edge_a = phase_apply_sync_a & ~phase_apply_prev_a;

    // Control register (Alice domain)
    (* ASYNC_REG = "TRUE" *) logic [3:0] ctrl_meta_a, ctrl_sync_a;
    always_ff @(posedge rfdc_aclk_0) begin
        ctrl_meta_a <= ctrl;
        ctrl_sync_a <= ctrl_meta_a;
    end

    // ---------------------------------------------------------------
    // CDC: s_axi_aclk → rfdc_aclk_2 (Bob clock domain)
    // ---------------------------------------------------------------

    // Switches (synced to Bob clock)
    (* ASYNC_REG = "TRUE" *) logic [3:0] sw_meta_b, sw_sync_b;
    always_ff @(posedge rfdc_aclk_2) begin
        sw_meta_b <= sw;
        sw_sync_b <= sw_meta_b;
    end

    // Bob staged phase
    (* ASYNC_REG = "TRUE" *) logic [1:0] bob_staged_meta, bob_staged_sync;
    always_ff @(posedge rfdc_aclk_2) begin
        bob_staged_meta <= bob_phase_staged;
        bob_staged_sync <= bob_staged_meta;
    end

    // Phase apply pulse (Bob domain)
    (* ASYNC_REG = "TRUE" *) logic phase_apply_meta_b, phase_apply_sync_b, phase_apply_prev_b;
    always_ff @(posedge rfdc_aclk_2) begin
        phase_apply_meta_b <= phase_apply;
        phase_apply_sync_b <= phase_apply_meta_b;
        phase_apply_prev_b <= phase_apply_sync_b;
    end
    wire phase_apply_edge_b = phase_apply_sync_b & ~phase_apply_prev_b;

    // Control register (Bob domain)
    (* ASYNC_REG = "TRUE" *) logic [3:0] ctrl_meta_b, ctrl_sync_b;
    always_ff @(posedge rfdc_aclk_2) begin
        ctrl_meta_b <= ctrl;
        ctrl_sync_b <= ctrl_meta_b;
    end

    // ---------------------------------------------------------------
    // Switch decode (Alice domain)
    // ---------------------------------------------------------------
    wire sw_mode = ~sw_sync_a[3];  // SW3=0 → switch mode (active high)

    // Alice: SW0 = basis (Z/X), SW1 = bit (0/1)
    wire [1:0] sw_alice_phase = {sw_sync_a[1], sw_sync_a[0]};

    // SW2: auto-cycle hardware enable
    wire sw_auto_cycle = sw_sync_a[2];

    // Bob: SW2 = basis (in switch mode, no bit selection)
    wire [1:0] sw_bob_phase = {1'b0, sw_sync_b[2]};

    // ---------------------------------------------------------------
    // Armed trigger — Alice (rfdc_aclk_0 domain)
    // ---------------------------------------------------------------
    logic [1:0] reg_alice_active;

    always_ff @(posedge rfdc_aclk_0) begin
        if (!rfdc_aresetn_0)
            reg_alice_active <= 2'b00;
        else if (phase_apply_edge_a)
            reg_alice_active <= alice_staged_sync;
    end

    // ---------------------------------------------------------------
    // Armed trigger — Bob (rfdc_aclk_2 domain)
    // ---------------------------------------------------------------
    logic [1:0] reg_bob_active;
    wire sw_mode_b = ~sw_sync_b[3];

    always_ff @(posedge rfdc_aclk_2) begin
        if (!rfdc_aresetn_2)
            reg_bob_active <= 2'b00;
        else if (phase_apply_edge_b)
            reg_bob_active <= bob_staged_sync;
    end

    wire [1:0] bob_phase_active = sw_mode_b ? sw_bob_phase : reg_bob_active;

    // ---------------------------------------------------------------
    // Auto phase cycler (dual-clock, Alice only)
    // ---------------------------------------------------------------
    wire [1:0]  auto_phase_select;
    wire [31:0] cycler_freq_hz;
    wire [31:0] cycler_period_ticks;
    wire [1:0]  cycler_unit_sel;
    wire [1:0]  cycler_incr_sel;

    // Auto-cycle enable: SW2 (hardware) OR ctrl[3] (software)
    wire auto_cycle_en = sw_auto_cycle | ctrl_sync_a[3];

    // Toggle mode: SW1 selects 0/pi toggle vs full 4-phase cycle
    wire auto_toggle_mode = sw_sync_a[1];

    auto_phase_cycler #(
        .RFDC_CLK_FREQ_HZ(RFDC_CLK_FREQ_HZ)
    ) u_cycler (
        .axi_clk      (s_axi_aclk),
        .axi_resetn   (s_axi_aresetn),
        .btn_pulse    (btn_pulse),
        .rfdc_clk     (rfdc_aclk_0),
        .rfdc_resetn  (rfdc_aresetn_0),
        .enable       (auto_cycle_en),
        .toggle_mode  (auto_toggle_mode),
        .freq_hz      (cycler_freq_hz),
        .period_ticks (cycler_period_ticks),
        .unit_sel     (cycler_unit_sel),
        .incr_sel     (cycler_incr_sel),
        .phase_select (auto_phase_select)
    );

    assign auto_freq_hz = cycler_freq_hz;

    // ---------------------------------------------------------------
    // 3-way mode mux — Alice (rfdc_aclk_0 domain)
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
    // CDC: rfdc → s_axi_aclk (readback)
    // ---------------------------------------------------------------
    (* ASYNC_REG = "TRUE" *) logic [1:0] alice_active_rd_meta, alice_active_rd_sync;
    (* ASYNC_REG = "TRUE" *) logic [1:0] bob_active_rd_meta,   bob_active_rd_sync;
    (* ASYNC_REG = "TRUE" *) logic       sw_mode_rd_meta,      sw_mode_rd_sync;

    always_ff @(posedge s_axi_aclk) begin
        alice_active_rd_meta <= alice_phase_active;
        alice_active_rd_sync <= alice_active_rd_meta;
        bob_active_rd_meta   <= bob_phase_active;
        bob_active_rd_sync   <= bob_active_rd_meta;
        sw_mode_rd_meta      <= sw_mode;
        sw_mode_rd_sync      <= sw_mode_rd_meta;
    end

    // Enable / status
    wire alice_en   = ctrl_sync_a[1];
    wire bob_en     = ctrl_sync_b[2];
    wire global_en_a = ctrl_sync_a[0];
    wire global_en_b = ctrl_sync_b[0];
    wire alice_running  = global_en_a & alice_en;
    wire bob_running    = global_en_b & bob_en;
    wire auto_cycling   = auto_cycle_en & alice_running;

    assign status = {sw_mode_rd_sync, auto_cycling, bob_running, alice_running};
    assign alice_phase_active_readback = alice_active_rd_sync;
    assign bob_phase_active_readback   = bob_active_rd_sync;

    // ---------------------------------------------------------------
    // PSK generators
    // ---------------------------------------------------------------
    axis_psk_gen #(
        .SAMPLES_PER_BEAT(SAMPLES_PER_BEAT),
        .SAMPLE_WIDTH(SAMPLE_WIDTH)
    ) u_alice (
        .aclk         (rfdc_aclk_0),
        .aresetn      (rfdc_aresetn_0),
        .enable       (alice_running),
        .phase_select (alice_phase_active),
        .m_axis_tdata (m_axis_alice_tdata),
        .m_axis_tvalid(m_axis_alice_tvalid),
        .m_axis_tready(m_axis_alice_tready)
    );

    axis_psk_gen #(
        .SAMPLES_PER_BEAT(SAMPLES_PER_BEAT),
        .SAMPLE_WIDTH(SAMPLE_WIDTH)
    ) u_bob (
        .aclk         (rfdc_aclk_2),
        .aresetn      (rfdc_aresetn_2),
        .enable       (bob_running),
        .phase_select (bob_phase_active),
        .m_axis_tdata (m_axis_bob_tdata),
        .m_axis_tvalid(m_axis_bob_tvalid),
        .m_axis_tready(m_axis_bob_tready)
    );

    // ---------------------------------------------------------------
    // LEDs
    // ---------------------------------------------------------------
    assign led[3]   = auto_cycle_en;
    assign led[2]   = sw_mode;
    assign led[1:0] = alice_phase_active;

endmodule

`default_nettype wire
