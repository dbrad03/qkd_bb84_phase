## USER LEDS
set_property PACKAGE_PIN AR11 [ get_ports "led[0]" ]
set_property IOSTANDARD LVCMOS18 [ get_ports "led[0]" ]

set_property PACKAGE_PIN AW10 [ get_ports "led[1]" ]
set_property IOSTANDARD LVCMOS18 [ get_ports "led[1]" ]

set_property PACKAGE_PIN AT11 [ get_ports "led[2]" ]
set_property IOSTANDARD LVCMOS18 [ get_ports "led[2]" ]

set_property PACKAGE_PIN AU10 [ get_ports "led[3]" ]
set_property IOSTANDARD LVCMOS18 [ get_ports "led[3]" ]

## USER SLIDE SWITCH
set_property PACKAGE_PIN AN13 [ get_ports "sw[0]" ]
set_property IOSTANDARD LVCMOS18 [ get_ports "sw[0]" ]

set_property PACKAGE_PIN AU12 [ get_ports "sw[1]" ]
set_property IOSTANDARD LVCMOS18 [ get_ports "sw[1]" ]

set_property PACKAGE_PIN AW11 [ get_ports "sw[2]" ]
set_property IOSTANDARD LVCMOS18 [ get_ports "sw[2]" ]

set_property PACKAGE_PIN AV11 [ get_ports "sw[3]" ]
set_property IOSTANDARD LVCMOS18 [ get_ports "sw[3]" ]


## CDC false paths — quasi-static signals with 2-FF synchronizers in RTL
# clk_pl_0 <-> RFDAC0_CLK (Alice, Tile 228)
set_false_path -from [get_clocks clk_pl_0] -to [get_clocks RFDAC0_CLK]
set_false_path -from [get_clocks RFDAC0_CLK] -to [get_clocks clk_pl_0]
# clk_pl_0 <-> RFDAC2_CLK (Bob, Tile 230)
set_false_path -from [get_clocks clk_pl_0] -to [get_clocks RFDAC2_CLK]
set_false_path -from [get_clocks RFDAC2_CLK] -to [get_clocks clk_pl_0]
# RFDAC0_CLK <-> RFDAC2_CLK (no direct crossing, but related clocks)
set_false_path -from [get_clocks RFDAC0_CLK] -to [get_clocks RFDAC2_CLK]
set_false_path -from [get_clocks RFDAC2_CLK] -to [get_clocks RFDAC0_CLK]

set_property BITSTREAM.CONFIG.UNUSEDPIN PULLUP [current_design]
set_property BITSTREAM.CONFIG.OVERTEMPSHUTDOWN ENABLE [current_design]
set_property BITSTREAM.GENERAL.COMPRESS TRUE [current_design]
