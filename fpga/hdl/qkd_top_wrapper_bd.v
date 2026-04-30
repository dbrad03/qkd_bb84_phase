`timescale 1ns / 1ps

// Verilog wrapper for block design integration.
// Vivado's "Add Module" prefers .v files. This just passes
// all ports through to the SystemVerilog qkd_top_wrapper.

module qkd_top_wrapper_bd #(
    parameter integer C_S_AXI_DATA_WIDTH = 32,
    parameter integer C_S_AXI_ADDR_WIDTH = 5,
    parameter integer SAMPLES_PER_BEAT   = 2,
    parameter integer SAMPLE_WIDTH       = 16
) (
    // AXI-Lite slave
    input  wire        s_axi_aclk,
    input  wire        s_axi_aresetn,
    input  wire [C_S_AXI_ADDR_WIDTH-1:0]   s_axi_awaddr,
    input  wire [2:0]                       s_axi_awprot,
    input  wire                             s_axi_awvalid,
    output wire                             s_axi_awready,
    input  wire [C_S_AXI_DATA_WIDTH-1:0]   s_axi_wdata,
    input  wire [C_S_AXI_DATA_WIDTH/8-1:0] s_axi_wstrb,
    input  wire                             s_axi_wvalid,
    output wire                             s_axi_wready,
    output wire [1:0]                       s_axi_bresp,
    output wire                             s_axi_bvalid,
    input  wire                             s_axi_bready,
    input  wire [C_S_AXI_ADDR_WIDTH-1:0]   s_axi_araddr,
    input  wire [2:0]                       s_axi_arprot,
    input  wire                             s_axi_arvalid,
    output wire                             s_axi_arready,
    output wire [C_S_AXI_DATA_WIDTH-1:0]   s_axi_rdata,
    output wire [1:0]                       s_axi_rresp,
    output wire                             s_axi_rvalid,
    input  wire                             s_axi_rready,

    // RFDC data clock — Alice (DAC Tile 228, clk_dac0)
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 rfdc_aclk_0 CLK" *)
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF m_axis_alice, ASSOCIATED_RESET rfdc_aresetn_0" *)
    input  wire        rfdc_aclk_0,
    input  wire        rfdc_aresetn_0,

    // RFDC data clock — Bob (DAC Tile 230, clk_dac2)
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 rfdc_aclk_2 CLK" *)
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF m_axis_bob, ASSOCIATED_RESET rfdc_aresetn_2" *)
    input  wire        rfdc_aclk_2,
    input  wire        rfdc_aresetn_2,

    // AXI4-Stream to DAC Tile 228 (Alice)
    output wire [SAMPLES_PER_BEAT*SAMPLE_WIDTH*2-1:0] m_axis_alice_tdata,
    output wire                                       m_axis_alice_tvalid,
    input  wire                                       m_axis_alice_tready,

    // AXI4-Stream to DAC Tile 230 (Bob)
    output wire [SAMPLES_PER_BEAT*SAMPLE_WIDTH*2-1:0] m_axis_bob_tdata,
    output wire                                       m_axis_bob_tvalid,
    input  wire                                       m_axis_bob_tready,

    // Board I/O
    input  wire [3:0] sw,
    output wire [3:0] led
);

    qkd_top_wrapper #(
        .C_S_AXI_DATA_WIDTH(C_S_AXI_DATA_WIDTH),
        .C_S_AXI_ADDR_WIDTH(C_S_AXI_ADDR_WIDTH),
        .SAMPLES_PER_BEAT(SAMPLES_PER_BEAT),
        .SAMPLE_WIDTH(SAMPLE_WIDTH)
    ) u_core (
        .s_axi_aclk        (s_axi_aclk),
        .s_axi_aresetn     (s_axi_aresetn),
        .s_axi_awaddr      (s_axi_awaddr),
        .s_axi_awprot      (s_axi_awprot),
        .s_axi_awvalid     (s_axi_awvalid),
        .s_axi_awready     (s_axi_awready),
        .s_axi_wdata       (s_axi_wdata),
        .s_axi_wstrb       (s_axi_wstrb),
        .s_axi_wvalid      (s_axi_wvalid),
        .s_axi_wready      (s_axi_wready),
        .s_axi_bresp       (s_axi_bresp),
        .s_axi_bvalid      (s_axi_bvalid),
        .s_axi_bready      (s_axi_bready),
        .s_axi_araddr      (s_axi_araddr),
        .s_axi_arprot      (s_axi_arprot),
        .s_axi_arvalid     (s_axi_arvalid),
        .s_axi_arready     (s_axi_arready),
        .s_axi_rdata       (s_axi_rdata),
        .s_axi_rresp       (s_axi_rresp),
        .s_axi_rvalid      (s_axi_rvalid),
        .s_axi_rready      (s_axi_rready),
        .rfdc_aclk_0       (rfdc_aclk_0),
        .rfdc_aresetn_0    (rfdc_aresetn_0),
        .rfdc_aclk_2       (rfdc_aclk_2),
        .rfdc_aresetn_2    (rfdc_aresetn_2),
        .m_axis_alice_tdata (m_axis_alice_tdata),
        .m_axis_alice_tvalid(m_axis_alice_tvalid),
        .m_axis_alice_tready(m_axis_alice_tready),
        .m_axis_bob_tdata   (m_axis_bob_tdata),
        .m_axis_bob_tvalid  (m_axis_bob_tvalid),
        .m_axis_bob_tready  (m_axis_bob_tready),
        .sw                 (sw),
        .led                (led)
    );

endmodule
