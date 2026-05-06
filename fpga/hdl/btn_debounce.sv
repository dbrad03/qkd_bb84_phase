`timescale 1ns / 1ps
`default_nettype none

// Button debounce with single-cycle pulse output.
// Each raw button input is synchronized (2-FF), debounced with a counter,
// and produces a one-clock-cycle pulse on the rising edge of the stable state.
//
// Parameters are overridable for simulation (use small CLK_FREQ_HZ to keep
// debounce windows short in cocotb).

module btn_debounce #(
    parameter integer CLK_FREQ_HZ = 100_000_000,
    parameter integer DEBOUNCE_MS = 20,
    parameter integer N_BUTTONS   = 4
) (
    input  wire                    clk,
    input  wire                    resetn,
    input  wire  [N_BUTTONS-1:0]  btn_raw,
    output logic [N_BUTTONS-1:0]  btn_pulse
);

    localparam integer COUNT_MAX = (CLK_FREQ_HZ / 1000) * DEBOUNCE_MS;
    // Width needed to hold COUNT_MAX (at least 1 bit)
    localparam integer COUNT_W = (COUNT_MAX > 0) ? $clog2(COUNT_MAX + 1) : 1;

    genvar i;
    generate
        for (i = 0; i < N_BUTTONS; i = i + 1) begin : gen_btn

            // 2-FF synchronizer
            (* ASYNC_REG = "TRUE" *) logic sync_meta, sync_ff;
            always_ff @(posedge clk) begin
                sync_meta <= btn_raw[i];
                sync_ff   <= sync_meta;
            end

            // Debounce counter and stable state
            logic [COUNT_W-1:0] cnt;
            logic stable;
            logic stable_prev;

            always_ff @(posedge clk) begin
                if (!resetn) begin
                    cnt        <= '0;
                    stable     <= 1'b0;
                    stable_prev <= 1'b0;
                end else begin
                    stable_prev <= stable;

                    if (sync_ff != stable) begin
                        if (cnt >= COUNT_MAX) begin
                            stable <= sync_ff;
                            cnt    <= '0;
                        end else begin
                            cnt <= cnt + 1;
                        end
                    end else begin
                        cnt <= '0;
                    end
                end
            end

            // Rising-edge pulse
            assign btn_pulse[i] = stable & ~stable_prev;

        end
    endgenerate

endmodule

`default_nettype wire
