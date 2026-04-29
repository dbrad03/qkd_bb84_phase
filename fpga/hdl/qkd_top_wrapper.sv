`timescale 1ns / 1ps
`default_nettype none

// QKD Phase-BB84 Top Wrapper
//
// Instantiates the AXI-Lite register file and two PSK generators (Alice, Bob).
// Handles switch/LED mapping, clock domain crossing, mode muxing, and
// the armed phase trigger for atomic Alice+Bob phase updates.
//
// Clock domains:
//   s_axi_aclk   (~100-150 MHz) — PS AXI register access
//   rfdc_aclk    (~307 MHz)     — PSK generators, DAC AXI-Stream outputs

module qkd_top_wrapper #(
    parameter integer C_S_AXI_DATA_WIDTH = 32,
    parameter integer C_S_AXI_ADDR_WIDTH = 5,
    parameter integer SAMPLES_PER_BEAT   = 4,
    parameter integer SAMPLE_WIDTH       = 16
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

    // RFDC data clock
    input  wire  rfdc_aclk,
    input  wire  rfdc_aresetn,

    // AXI4-Stream master to DAC Tile 229 Slice 0 (Alice)
    output wire [SAMPLES_PER_BEAT*SAMPLE_WIDTH*2-1:0] m_axis_alice_tdata,
    output wire                                       m_axis_alice_tvalid,
    input  wire                                       m_axis_alice_tready,

    // AXI4-Stream master to DAC Tile 229 Slice 1 (Bob)
    output wire [SAMPLES_PER_BEAT*SAMPLE_WIDTH*2-1:0] m_axis_bob_tdata,
    output wire                                       m_axis_bob_tvalid,
    input  wire                                       m_axis_bob_tready,

    // Board I/O
    input  wire [3:0] sw,
    output wire [3:0] led
);

    // ---------------------------------------------------------------
    // Register file (on s_axi_aclk domain)
    // ---------------------------------------------------------------
    wire [2:0] ctrl;
    wire [1:0] alice_phase_staged;
    wire [1:0] bob_phase_staged;
    wire       phase_apply;
    wire [2:0] status;
    wire [1:0] alice_phase_active_readback;
    wire [1:0] bob_phase_active_readback;

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
        .bob_phase_active   (bob_phase_active_readback)
    );

    // ---------------------------------------------------------------
    // CDC: s_axi_aclk → rfdc_aclk (2-FF synchronizers)
    // All signals are quasi-static (switches or ≤320 Hz protocol rate)
    // ---------------------------------------------------------------

    // Switches
    logic [3:0] sw_meta, sw_sync;
    always_ff @(posedge rfdc_aclk) begin
        sw_meta <= sw;
        sw_sync <= sw_meta;
    end

    // Staged phase values from registers
    logic [1:0] alice_staged_meta, alice_staged_sync;
    logic [1:0] bob_staged_meta,   bob_staged_sync;
    always_ff @(posedge rfdc_aclk) begin
        alice_staged_meta <= alice_phase_staged;
        alice_staged_sync <= alice_staged_meta;
        bob_staged_meta   <= bob_phase_staged;
        bob_staged_sync   <= bob_staged_meta;
    end

    // Phase apply pulse
    logic phase_apply_meta, phase_apply_sync, phase_apply_prev;
    always_ff @(posedge rfdc_aclk) begin
        phase_apply_meta <= phase_apply;
        phase_apply_sync <= phase_apply_meta;
        phase_apply_prev <= phase_apply_sync;
    end
    wire phase_apply_edge = phase_apply_sync & ~phase_apply_prev;

    // Control register
    logic [2:0] ctrl_meta, ctrl_sync;
    always_ff @(posedge rfdc_aclk) begin
        ctrl_meta <= ctrl;
        ctrl_sync <= ctrl_meta;
    end

    // ---------------------------------------------------------------
    // Switch decode (on rfdc_aclk domain, after sync)
    // ---------------------------------------------------------------
    wire sw_mode = ~sw_sync[3];  // SW3=0 → switch mode

    // Alice: SW0 = basis (Z/X), SW1 = bit (0/1)
    //   Z-basis bit 0 → phase 0     (2'b00)
    //   Z-basis bit 1 → phase pi    (2'b10)
    //   X-basis bit 0 → phase pi/2  (2'b01)
    //   X-basis bit 1 → phase 3pi/2 (2'b11)
    wire [1:0] sw_alice_phase = {sw_sync[1], sw_sync[0]};

    // Bob: SW2 = basis (Z/X), no bit selection
    //   Z-basis → phase 0    (2'b00)
    //   X-basis → phase pi/2 (2'b01)
    wire [1:0] sw_bob_phase = {1'b0, sw_sync[2]};

    // ---------------------------------------------------------------
    // Armed trigger and phase mux (on rfdc_aclk domain)
    // ---------------------------------------------------------------
    logic [1:0] reg_alice_active, reg_bob_active;

    always_ff @(posedge rfdc_aclk) begin
        if (!rfdc_aresetn) begin
            reg_alice_active <= 2'b00;
            reg_bob_active   <= 2'b00;
        end else if (phase_apply_edge) begin
            // Atomically latch both phases on the apply pulse
            reg_alice_active <= alice_staged_sync;
            reg_bob_active   <= bob_staged_sync;
        end
    end

    // Mode mux: switch mode vs register mode
    wire [1:0] alice_phase_active = sw_mode ? sw_alice_phase : reg_alice_active;
    wire [1:0] bob_phase_active   = sw_mode ? sw_bob_phase   : reg_bob_active;

    // ---------------------------------------------------------------
    // CDC: rfdc_aclk → s_axi_aclk (readback for STATUS and ACTIVE regs)
    // ---------------------------------------------------------------
    logic [1:0] alice_active_rd_meta, alice_active_rd_sync;
    logic [1:0] bob_active_rd_meta,   bob_active_rd_sync;
    logic       sw_mode_rd_meta,      sw_mode_rd_sync;

    always_ff @(posedge s_axi_aclk) begin
        alice_active_rd_meta <= alice_phase_active;
        alice_active_rd_sync <= alice_active_rd_meta;
        bob_active_rd_meta   <= bob_phase_active;
        bob_active_rd_sync   <= bob_active_rd_meta;
        sw_mode_rd_meta      <= sw_mode;
        sw_mode_rd_sync      <= sw_mode_rd_meta;
    end

    wire alice_en = ctrl_sync[1];
    wire bob_en   = ctrl_sync[2];
    wire global_en = ctrl_sync[0];

    wire alice_running = global_en & alice_en;
    wire bob_running   = global_en & bob_en;

    assign status = {sw_mode_rd_sync, bob_running, alice_running};
    assign alice_phase_active_readback = alice_active_rd_sync;
    assign bob_phase_active_readback   = bob_active_rd_sync;

    // ---------------------------------------------------------------
    // PSK generators (on rfdc_aclk domain)
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

    axis_psk_gen #(
        .SAMPLES_PER_BEAT(SAMPLES_PER_BEAT),
        .SAMPLE_WIDTH(SAMPLE_WIDTH)
    ) u_bob (
        .aclk         (rfdc_aclk),
        .aresetn      (rfdc_aresetn),
        .enable       (bob_running),
        .phase_select (bob_phase_active),
        .m_axis_tdata (m_axis_bob_tdata),
        .m_axis_tvalid(m_axis_bob_tvalid),
        .m_axis_tready(m_axis_bob_tready)
    );

    // ---------------------------------------------------------------
    // LEDs: show active phase select for both channels
    // ---------------------------------------------------------------
    assign led = {bob_phase_active, alice_phase_active};

endmodule

`default_nettype wire
