`timescale 1ns / 1ps
`default_nettype none

// PSK I/Q Generator for RF-DAC Fine Mixer
//
// Outputs a constant I/Q pair on an AXI4-Stream interface. The hardened
// NCO/mixer in the RF-DAC tile multiplies this baseband value by an
// 80 MHz carrier, producing a tone whose phase is set by phase_select.
//
// phase_select  |  I       |  Q       |  Output Phase
// 2'b00         | +POS     |  0       |  0
// 2'b01         |  0       | +POS     |  pi/2
// 2'b10         | -POS     |  0       |  pi
// 2'b11         |  0       | -POS     |  3pi/2
//
// Each AXI-Stream beat packs SAMPLES_PER_BEAT I/Q pairs. Since the
// baseband is a constant, every sample slot holds the same value.

module axis_psk_gen #(
    parameter integer SAMPLES_PER_BEAT  = 4,
    parameter integer SAMPLE_WIDTH      = 16
) (
    input  wire        aclk,
    input  wire        aresetn,

    // Control (synchronous to aclk)
    input  wire        enable,
    input  wire [1:0]  phase_select,

    // AXI4-Stream master to RF-DAC
    output wire [SAMPLES_PER_BEAT*SAMPLE_WIDTH*2-1:0] m_axis_tdata,
    output wire        m_axis_tvalid,
    input  wire        m_axis_tready
);

    // ~91.5% full-scale; leaves headroom for the DUC mixer
    localparam signed [SAMPLE_WIDTH-1:0] POS_FULL = 16'sd30000;
    localparam signed [SAMPLE_WIDTH-1:0] NEG_FULL = -16'sd30000;
    localparam signed [SAMPLE_WIDTH-1:0] ZERO     = 16'sd0;

    // Registered I/Q pair
    logic signed [SAMPLE_WIDTH-1:0] iq_i, iq_q;

    always_ff @(posedge aclk) begin
        if (!aresetn || !enable) begin
            iq_i <= ZERO;
            iq_q <= ZERO;
        end else begin
            case (phase_select)
                2'b00: begin iq_i <= POS_FULL; iq_q <= ZERO;     end  // 0
                2'b01: begin iq_i <= ZERO;     iq_q <= POS_FULL; end  // pi/2
                2'b10: begin iq_i <= NEG_FULL; iq_q <= ZERO;     end  // pi
                2'b11: begin iq_i <= ZERO;     iq_q <= NEG_FULL; end  // 3pi/2
            endcase
        end
    end

    // Replicate the I/Q pair across all sample slots in the beat.
    // RFDC I/Q packing per PG269: each 32-bit slot is {I[15:0], Q[15:0]}.
    genvar s;
    generate
        for (s = 0; s < SAMPLES_PER_BEAT; s = s + 1) begin : gen_samples
            assign m_axis_tdata[s*SAMPLE_WIDTH*2 +: SAMPLE_WIDTH*2] = {iq_i, iq_q};
        end
    endgenerate

    // Always valid — DAC gets zeros when muted (enable=0), PSK when active
    assign m_axis_tvalid = 1'b1;

endmodule

`default_nettype wire
