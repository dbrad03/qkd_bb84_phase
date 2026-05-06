`timescale 1ns / 1ps
`default_nettype none

// Auto Phase Cycler — cycles phase_select through 0→1→2→3→0 at a
// configurable frequency controlled by four debounced buttons.
//
// Button mapping:
//   btn_pulse[3] — cycle unit:      Hz → KHz → MHz → Hz ...
//   btn_pulse[2] — cycle increment: 1  → 5   → 10  → 1  ...
//   btn_pulse[1] — increase frequency by (increment × unit)
//   btn_pulse[0] — decrease frequency by (increment × unit)
//
// Two clock domains:
//   axi_clk   — button logic, frequency register, sequential divider
//   rfdc_clk  — phase counter with CDC'd period
//
// The sequential divider computes period_ticks = RFDC_CLK_FREQ_HZ / freq_hz
// whenever freq_hz changes. It completes in 32 axi_clk cycles (~320 ns).

module auto_phase_cycler #(
    parameter integer RFDC_CLK_FREQ_HZ = 307_200_000
) (
    // AXI clock domain (button logic + divider)
    input  wire        axi_clk,
    input  wire        axi_resetn,
    input  wire [3:0]  btn_pulse,

    // RFDC clock domain (phase counter)
    input  wire        rfdc_clk,
    input  wire        rfdc_resetn,
    input  wire        enable,       // already in rfdc_clk domain
    input  wire        toggle_mode,  // already in rfdc_clk domain: 1=0/pi toggle, 0=full cycle

    // Readback (axi_clk domain)
    output logic [31:0] freq_hz,
    output logic [31:0] period_ticks,
    output logic [1:0]  unit_sel,
    output logic [1:0]  incr_sel,

    // Phase output (rfdc_clk domain)
    output logic [1:0]  phase_select
);

    // ---------------------------------------------------------------
    // Button logic (axi_clk domain)
    // ---------------------------------------------------------------

    // Step lookup: unit_sel × incr_sel → step value
    logic [31:0] step;
    always_comb begin
        case ({unit_sel, incr_sel})
            4'b00_00: step = 32'd1;           // 1 Hz
            4'b00_01: step = 32'd5;           // 5 Hz
            4'b00_10: step = 32'd10;          // 10 Hz
            4'b01_00: step = 32'd1_000;       // 1 KHz
            4'b01_01: step = 32'd5_000;       // 5 KHz
            4'b01_10: step = 32'd10_000;      // 10 KHz
            4'b10_00: step = 32'd1_000_000;   // 1 MHz
            4'b10_01: step = 32'd5_000_000;   // 5 MHz
            4'b10_10: step = 32'd10_000_000;  // 10 MHz
            default:  step = 32'd1;
        endcase
    end

    localparam [31:0] MAX_FREQ = RFDC_CLK_FREQ_HZ / 4;

    always_ff @(posedge axi_clk) begin
        if (!axi_resetn) begin
            freq_hz  <= 32'd1;
            unit_sel <= 2'd0;
            incr_sel <= 2'd0;
        end else begin
            if (btn_pulse[3]) begin
                unit_sel <= (unit_sel == 2'd2) ? 2'd0 : unit_sel + 1;
            end

            if (btn_pulse[2]) begin
                case (incr_sel)
                    2'd0:    incr_sel <= 2'd1;
                    2'd1:    incr_sel <= 2'd2;
                    default: incr_sel <= 2'd0;
                endcase
            end

            if (btn_pulse[1]) begin
                if (freq_hz + step > MAX_FREQ)
                    freq_hz <= MAX_FREQ;
                else
                    freq_hz <= freq_hz + step;
            end

            if (btn_pulse[0]) begin
                if (freq_hz <= step)
                    freq_hz <= 32'd1;
                else
                    freq_hz <= freq_hz - step;
            end
        end
    end

    // ---------------------------------------------------------------
    // Sequential divider: period_ticks = RFDC_CLK_FREQ_HZ / freq_hz
    // Restoring shift-subtract, 32 iterations, one per axi_clk cycle.
    // Triggered when freq_hz changes.
    // ---------------------------------------------------------------
    logic [31:0] freq_hz_prev;
    logic        div_start;

    always_ff @(posedge axi_clk) begin
        if (!axi_resetn) begin
            freq_hz_prev <= 32'd0;
            div_start    <= 1'b0;
        end else begin
            div_start <= 1'b0;
            // Only update prev when divider is idle so a change during
            // division retriggers automatically after completion.
            if (freq_hz != freq_hz_prev && !div_busy) begin
                freq_hz_prev <= freq_hz;
                div_start    <= 1'b1;
            end
        end
    end

    logic        div_busy;
    logic        div_done;
    logic [4:0]  div_step;
    logic [63:0] div_acc;     // {partial_remainder[31:0], shifted_dividend[31:0]}
    logic [31:0] div_divisor;

    // Combinational trial: shift left, compare upper half with divisor
    wire [63:0] div_shifted = {div_acc[62:0], 1'b0};
    wire [31:0] div_upper   = div_shifted[63:32];
    wire        div_fits    = (div_upper >= div_divisor);

    always_ff @(posedge axi_clk) begin
        if (!axi_resetn) begin
            div_busy     <= 1'b0;
            div_done     <= 1'b0;
            div_step     <= 5'd0;
            div_acc      <= 64'd0;
            div_divisor  <= 32'd1;
            period_ticks <= RFDC_CLK_FREQ_HZ;
        end else begin
            div_done <= 1'b0;

            if (div_start && !div_busy) begin
                div_busy    <= 1'b1;
                div_step    <= 5'd31;
                div_acc     <= {32'd0, RFDC_CLK_FREQ_HZ[31:0]};
                div_divisor <= (freq_hz == 0) ? 32'd1 : freq_hz;  // guard div-by-zero
            end else if (div_busy) begin
                // Restoring division step:
                //   shift acc left, if upper >= divisor then subtract and set LSB
                if (div_fits)
                    div_acc <= {div_upper - div_divisor, div_shifted[31:1], 1'b1};
                else
                    div_acc <= div_shifted;

                if (div_step == 5'd0) begin
                    div_busy <= 1'b0;
                    div_done <= 1'b1;
                end else begin
                    div_step <= div_step - 1;
                end
            end

            // Latch quotient one cycle after divider completes
            // (div_acc[31:0] holds the quotient at this point)
            if (div_done)
                period_ticks <= (div_acc[31:0] == 32'd0) ? 32'd1 : div_acc[31:0];
        end
    end

    // ---------------------------------------------------------------
    // CDC: period_ticks (axi_clk) → rfdc_clk
    // Toggle-based handshake. period_hold is stable long before the
    // toggle propagates through the 2-FF synchronizer.
    // ---------------------------------------------------------------
    logic        cdc_toggle_axi;
    logic [31:0] cdc_period_hold;
    logic [31:0] cdc_period_prev;

    always_ff @(posedge axi_clk) begin
        if (!axi_resetn) begin
            cdc_toggle_axi <= 1'b0;
            cdc_period_hold <= RFDC_CLK_FREQ_HZ;
            cdc_period_prev <= RFDC_CLK_FREQ_HZ;
        end else begin
            if (period_ticks != cdc_period_prev) begin
                cdc_period_prev <= period_ticks;
                cdc_period_hold <= period_ticks;
                cdc_toggle_axi <= ~cdc_toggle_axi;
            end
        end
    end

    (* ASYNC_REG = "TRUE" *) logic cdc_toggle_meta, cdc_toggle_sync;
    logic cdc_toggle_prev;
    logic [31:0] period_ticks_rfdc;

    always_ff @(posedge rfdc_clk) begin
        if (!rfdc_resetn) begin
            cdc_toggle_meta <= 1'b0;
            cdc_toggle_sync <= 1'b0;
            cdc_toggle_prev <= 1'b0;
            period_ticks_rfdc <= RFDC_CLK_FREQ_HZ;
        end else begin
            cdc_toggle_meta <= cdc_toggle_axi;
            cdc_toggle_sync <= cdc_toggle_meta;
            cdc_toggle_prev <= cdc_toggle_sync;

            if (cdc_toggle_sync != cdc_toggle_prev)
                period_ticks_rfdc <= cdc_period_hold;
        end
    end

    // ---------------------------------------------------------------
    // Phase counter (rfdc_clk domain)
    // Counts down from period_ticks_rfdc, advances phase on wrap.
    // toggle_mode=0: full cycle 0→1→2→3→0...
    // toggle_mode=1: 0/pi toggle 0→2→0→2...
    // ---------------------------------------------------------------
    logic [31:0] cycle_cnt;

    always_ff @(posedge rfdc_clk) begin
        if (!rfdc_resetn || !enable) begin
            phase_select <= 2'b00;
            cycle_cnt    <= 32'd0;
        end else if (toggle_mode && phase_select[0]) begin
            // Snap to nearest valid toggle phase immediately (clear bit 0).
            // Handles the case where toggle_mode asserts while phase is 1 or 3.
            phase_select[0] <= 1'b0;
        end else if (cycle_cnt == 32'd0) begin
            cycle_cnt <= period_ticks_rfdc - 1;
            if (toggle_mode)
                phase_select <= {~phase_select[1], 1'b0};  // 0↔pi toggle
            else
                phase_select <= phase_select + 1;          // full 4-phase cycle
        end else begin
            cycle_cnt <= cycle_cnt - 1;
        end
    end

endmodule

`default_nettype wire
